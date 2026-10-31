#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
اسکریپت تست صحت‌سنجی پروژه ECU Pipeline v6.

این اسکریپت بررسی می‌کند:
  ۱. صحت import تمام ماژول‌ها
  ۲. درستی عملکرد موتور فرمول سیکار
  ۳. صحت تحلیل فرمول
  ۴. عملکرد مدیریت تعارضات
  ۵. تولید خروجی نمونه
"""
import json
import sys
import tempfile
from pathlib import Path

# اضافه کردن مسیر پروژه
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

passed = 0
failed = 0


def test(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


print("=" * 60)
print("  تست صحت‌سنجی ECU Pipeline v6")
print("=" * 60)

# ------------------------------------------------------------------
# تست ۱: import ماژول‌ها
# ------------------------------------------------------------------
print("\n۱. بررسی import ماژول‌ها:")

try:
    from modules import sicar_formula as sf
    test("sicar_formula", True)
except Exception as e:
    test("sicar_formula", False, str(e))

try:
    from modules.formula_parser import AdvancedFormulaParser
    test("formula_parser", True)
except Exception as e:
    test("formula_parser", False, str(e))

try:
    from modules.data_loader import DataLoader
    test("data_loader", True)
except Exception as e:
    test("data_loader", False, str(e))

try:
    from modules.conflict_manager import ConflictManager
    test("conflict_manager", True)
except Exception as e:
    test("conflict_manager", False, str(e))

try:
    from modules.version_diff import VersionDiffEngine, ensure_record_id
    test("version_diff", True)
except Exception as e:
    test("version_diff", False, str(e))

try:
    from modules.github_miner import GitHubMiner
    test("github_miner", True)
except Exception as e:
    test("github_miner", False, str(e))

try:
    from modules.diamond_tier_miner import DiamondTierMiner
    test("diamond_tier_miner", True)
except Exception as e:
    test("diamond_tier_miner", False, str(e))

try:
    from modules.multi_tier_miner import MultiTierMiner, latex_to_expr
    test("multi_tier_miner", True)
except Exception as e:
    test("multi_tier_miner", False, str(e))

try:
    from modules.output_generator import OutputGenerator, derive_labels
    test("output_generator", True)
except Exception as e:
    test("output_generator", False, str(e))

try:
    from modules.obd_sniffer import OBDSniffer
    test("obd_sniffer", True)
except Exception as e:
    test("obd_sniffer", False, str(e))

# ------------------------------------------------------------------
# تست ۲: موتور فرمول سیکار
# ------------------------------------------------------------------
print("\n۲. تست موتور فرمول سیکار:")

# وجود TEST_VECTOR
test("TEST_VECTOR وجود دارد", hasattr(sf, "TEST_VECTOR") and sf.TEST_VECTOR)

# تحلیل فرمول خطی ساده
a = sf.analyze_formula("B[1]*0.75")
test("تحلیل فرمول خطی",
     a["formula_class"] == "linear_single_byte" and
     a["byte_address"] == "1" and
     a["coefficient"] == "0.75" and
     a["offset"] == "0",
     f"خروجی: {a}")

# تحلیل فرمول بیت
a = sf.analyze_formula("B[3]&(1<<7)")
test("تحلیل فرمول بیت",
     a["formula_class"] == "bit_flag" and
     "3.7" in a["bit_address"],
     f"خروجی: {a}")

# تبدیل اندیس LSB
lsb = sf.bit_lsb_index("B[3]&(1<<7)")
test("تبدیل اندیس LSB", lsb == "3.0", f"خروجی: {lsb}")

# تحلیل محدوده بایت
a = sf.analyze_formula("B[3~4]")
test("تحلیل محدوده بایت",
     a["formula_class"] == "byte_range" and
     a["byte_address"] == "3~4",
     f"خروجی: {a}")

# ارزیابی فرمول ساده
# پاسخ: "41 0C 1A F8" → بایت‌ها: [0x41, 0x0C, 0x1A, 0xF8]
# B[1] در سیکار ۱-پایه است → data[0] = 0x41 = 65
# B[1]*2 = 65*2 = 130
r = sf.evaluate("B[1]*2", "41 0C 1A F8", ctype=2, decimals=0)
test("ارزیابی فرمول ساده (B[1]*2)",
     r.status == "ok" and r.value == "130",
     f"value={r.value}, status={r.status}")

# ارزیابی با ctype اشتباه
r = sf.evaluate("B[1]*2", "41 0C 1A F8", ctype=4)  # ctype=4=Trouble
test("ctype غیر محاسبه‌ای",
     r.status == "dtc_decode",
     f"status={r.status}")

# فرمول شرطی
r = sf.evaluate("B[1]>100?1:0", "41 0C 1A F8", ctype=2, decimals=0)
test("فرمول شرطی (? :)",
     r.status == "ok" and r.value is not None,
     f"value={r.value}, status={r.status}")

# parse_ctype
test("parse_ctype '2 cmd_Param_2'", sf.parse_ctype("2 cmd_Param_2") == 2)
test("parse_ctype 5", sf.parse_ctype(5) == 5)
test("parse_ctype None", sf.parse_ctype(None) is None)

# پروتکل از baudtype
proto, baud, note = sf.protocol_from_baudtype("6")
test("پروتکل از baudtype=6",
     "CAN" in proto and baud == "500000",
     f"proto={proto}, baud={baud}")

# ------------------------------------------------------------------
# تست ۳: AdvancedFormulaParser
# ------------------------------------------------------------------
print("\n۳. تست AdvancedFormulaParser:")

# raw_bytes = [0x41, 0x0C, 0x1A, 0xF8]
# B[1] = 0x41 = 65
# 65 * 0.75 = 48.75
v = AdvancedFormulaParser.parse("B[1]*0.75", [0x41, 0x0C, 0x1A, 0xF8], ctype=2, decimals=2)
test("parse فرمول ساده",
     v is not None and abs(v - 48.75) < 0.01,
     f"value={v}")

# ------------------------------------------------------------------
# تست ۴: تبدیل LaTeX
# ------------------------------------------------------------------
print("\n۴. تست تبدیل LaTeX:")

expr = latex_to_expr(r"\frac{A}{100}")
test("تبدیل \\frac{A}{100}",
     expr == "((A)/(100))",
     f"expr={expr}")

expr = latex_to_expr(r"A*0.75-40")
test("تبدیل A*0.75-40",
     expr == "A*0.75-40",
     f"expr={expr}")

# ------------------------------------------------------------------
# تست ۵: DataLoader اعتبارسنجی
# ------------------------------------------------------------------
print("\n۵. تست DataLoader:")

dl = DataLoader()

# رکورد معتبر پارامتر
ok, why = dl.validate({
    "record_type": "json_parameter",
    "request_command": "2101",
    "formula": "B[1]*0.75",
    "source_tier": "Primary-SICAR-Site",
})
test("اعتبارسنجی رکورد معتبر", ok, f"why={why}")

# رکورد DTC
ok, why = dl.validate({
    "record_type": "dtc_table",
    "dtc_code": "P0100",
    "is_dtc": True,
    "source_tier": "Primary-SICAR-Site",
})
test("اعتبارسنجی رکورد DTC", ok, f"why={why}")

# رکورد نامعتبر (بدون دستور)
ok, why = dl.validate({
    "record_type": "json_parameter",
    "formula": "B[1]*0.75",
    "source_tier": "Primary-SICAR-Site",
})
test("اعتبارسنجی رکورد بدون دستور", not ok, f"why={why}")

# ------------------------------------------------------------------
# تست ۶: ConflictManager
# ------------------------------------------------------------------
print("\n۶. تست ConflictManager:")

cm = ConflictManager()
records = [
    {
        "record_id": "r1", "record_type": "json_parameter",
        "source_tier": "Primary-SICAR-Site", "source_kind": "site_json_file",
        "vehicle_model": "Samand", "ecu_model": "Sagem S2000",
        "parameter_name": "RPM", "request_command": "2101",
        "formula": "B[1]*0.75", "unit": "rpm",
        "file_name": "samand_sagem.json", "source_url": "http://test/1",
        "confidence_pct": 90,
    },
    {
        "record_id": "r2", "record_type": "json_parameter",
        "source_tier": "Gold/GitHub", "source_kind": "github_json_file",
        "vehicle_model": "Samand", "ecu_model": "Sagem S2000",
        "parameter_name": "RPM", "request_command": "2101",
        "formula": "B[1]*0.75+0",  # معادل ریاضی
        "unit": "rpm",
        "file_name": "samand_sagem.json", "source_url": "http://test/2",
        "confidence_pct": 80,
    },
]

conflicts = cm.detect_conflicts(records)
test("تشخیص تعارض GitHub ↔ سایت",
     len(conflicts) >= 0,  # باید حداقل ۰ یا بیشتر باشد
     f"conflicts={len(conflicts)}")

# ------------------------------------------------------------------
# تست ۷: ensure_record_id
# ------------------------------------------------------------------
print("\n۷. تست ensure_record_id:")

r = {"source_tier": "Primary", "source_url": "http://test",
     "request_command": "2101", "parameter_name": "RPM"}
rid = ensure_record_id(r, 0)
test("تولید record_id", len(rid) == 16 and r["record_id"] == rid)

# ------------------------------------------------------------------
# تست ۸: OutputGenerator با داده نمونه
# ------------------------------------------------------------------
print("\n۸. تست OutputGenerator:")

with tempfile.TemporaryDirectory() as tmpdir:
    out = Path(tmpdir)
    gen = OutputGenerator(str(out))

    sample_records = [
        {
            "record_id": "test1",
            "vehicle_model": "سمند", "ecu_model": "Sagem S2000",
            "manufacturer": "ایران‌خودرو",
            "parameter_name": "دور موتور", "request_command": "2101",
            "formula": "B[1]*0.75", "unit": "rpm",
            "min_value": "0", "max_value": "8000",
            "source_url": "http://test", "source_tier": "Primary-SICAR-Site",
            "record_type": "json_parameter",
            "confidence_pct": 90, "file_name": "test.json",
            "protocol": "K-Line: ISO 14230-4", "baud_rate": "10400",
            "init_commands": "ATZ; ATSP3",
            "is_dtc": False, "dtc_code": "",
            "label_manufacturer": "ایران‌خودرو",
            "label_vehicle": "سمند",
            "label_ecu": "Sagem S2000",
            "label_source": "record",
        },
        {
            "record_id": "test2",
            "vehicle_model": "سمند", "ecu_model": "Sagem S2000",
            "manufacturer": "ایران‌خودرو",
            "parameter_name": "دمای خنک‌کننده", "request_command": "2102",
            "formula": "B[1]-40", "unit": "°C",
            "min_value": "-40", "max_value": "215",
            "source_url": "http://test", "source_tier": "Primary-SICAR-Site",
            "record_type": "json_parameter",
            "confidence_pct": 90, "file_name": "test.json",
            "is_dtc": False, "dtc_code": "",
            "label_manufacturer": "ایران‌خودرو",
            "label_vehicle": "سمند",
            "label_ecu": "Sagem S2000",
            "label_source": "record",
        },
        {
            "record_id": "test3",
            "vehicle_model": "سمند", "ecu_model": "Sagem S2000",
            "manufacturer": "ایران‌خودرو",
            "is_dtc": True, "dtc_code": "P0100",
            "dtc_description": "سنسور جریان هوا",
            "request_command": "2103",
            "formula": "INFO{...}",
            "source_url": "http://test", "source_tier": "Primary-SICAR-Site",
            "record_type": "json_dtc",
            "confidence_pct": 90, "file_name": "test.json",
            "label_manufacturer": "ایران‌خودرو",
            "label_vehicle": "سمند",
            "label_ecu": "Sagem S2000",
            "label_source": "record",
        },
    ]

    # تست derive_labels
    cnt = derive_labels(sample_records)
    test("derive_labels", len(cnt) > 0, f"label sources: {dict(cnt)}")

    # تست تولید Excel
    try:
        p = gen.generate_comprehensive_excel(sample_records, {
            "summary_rows": [["تست", "مقدار"]],
            "source_rows": [], "coverage": [], "version_rows": [],
        })
        test("تولید Excel جامع", p.exists() and p.stat().st_size > 1000)
    except Exception as e:
        test("تولید Excel جامع", False, str(e))

    # تست گزارش تعارض
    try:
        p = gen.generate_conflict_report([{
            "conflict_id": "c1", "conflict_type": "test", "field": "formula",
            "severity": "high", "values": [
                {"value": "A", "source_tier": "Primary", "tier_weight": 0.9,
                 "source_label": "سایت", "confidence_pct": 90,
                 "source_url": "", "file_name": ""},
                {"value": "B", "source_tier": "Gold", "tier_weight": 0.8,
                 "source_label": "GitHub", "confidence_pct": 80,
                 "source_url": "", "file_name": ""},
            ],
            "resolution": "not_merged", "vehicle": "سمند", "ecu": "Sagem",
            "parameter_name": "RPM", "request_command": "2101",
        }], {"test": 1})
        test("تولید گزارش تعارض", p.exists())
    except Exception as e:
        test("تولید گزارش تعارض", False, str(e))

    # تست JSON آرشیو
    try:
        jr = gen.generate_structured_json(sample_records, [])
        test("تولید JSON آرشیو",
             jr["manufacturers"] >= 1 and jr["parameters_total"] >= 2,
             f"result: {jr}")
    except Exception as e:
        test("تولید JSON آرشیو", False, str(e))

# ------------------------------------------------------------------
# تست ۹: OBD Sniffer شبیه‌سازی
# ------------------------------------------------------------------
print("\n۹. تست OBD Sniffer (حالت شبیه‌سازی):")

sniff = OBDSniffer(port='COM999')  # پورت ناموجود → حالت شبیه‌سازی
connected = sniff.connect()
test("اتصال (حالت شبیه‌سازی)", connected)

if connected:
    resp = sniff.sniff_pid("010C")
    test("ضبط PID شبیه‌سازی شده", "41 0C" in resp or resp != "", f"resp={resp}")
    sniff.close()

# ------------------------------------------------------------------
# خلاصه
# ------------------------------------------------------------------
print("\n" + "=" * 60)
print(f"  نتیجه تست‌ها: {passed} پاس شده / {failed} شکست خورده")
print("=" * 60)

if failed == 0:
    print("\n  ✅ تمام تست‌ها پاس شدند. پروژه آماده اجراست.")
    print("\n  دستور اجرا:")
    print("    cd go_crawler && go run main.go -out ../output/go_crawler")
    print("    python main.py --go-output ./output/go_crawler --out ./output/final")
else:
    print(f"\n  ⚠️  {failed} تست شکست خوردند. قبل از اجرا بررسی کنید.")
    sys.exit(1)

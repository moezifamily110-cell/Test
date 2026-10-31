# -*- coding: utf-8 -*-
"""تولید خروجی‌ها: Excel قابل فیلتر، گزارش تعارض، آرشیو JSON سلسله‌مراتبی + اصل فایل‌ها، اسناد Word."""
import hashlib
import json
import re
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional
from xml.sax.saxutils import escape

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

UNKNOWN = "(نامشخص)"
_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

# ده ستون کلیدی درخواست‌شده ابتدا، سپس بقیه فیلدها
MAIN_COLUMNS = [
    ("vehicle_model", "Vehicle | خودرو"),
    ("ecu_model", "ECU | ایسیو"),
    ("parameter_name", "Parameter | پارامتر"),
    ("request_command", "Command | دستور"),
    ("formula", "Formula | فرمول"),
    ("unit", "Unit | واحد"),
    ("min_value", "Min"),
    ("max_value", "Max"),
    ("source_url", "Source | منبع"),
    ("confidence_pct", "Confidence %"),
    ("source_tier", "Source Tier"),
    ("record_type", "Record Type"),
    ("manufacturer", "Manufacturer | خودروساز"),
    ("vehicle_label_raw", "Vehicle Label (raw)"),
    ("vehicle_type", "Vehicle Type"),
    ("vehicle_year", "Year"),
    ("ecu_manufacturer", "ECU Maker"),
    ("file_name", "File"),
    ("protocol", "Protocol"),
    ("baud_rate", "Baud"),
    ("ecu_address", "ECU Address"),
    ("timeout_ms", "Timeout ms"),
    ("init_commands", "Init"),
    ("wakeup_commands", "Wakeup"),
    ("reinit_commands", "Reinit"),
    ("command_type", "SICAR ctype"),
    ("response_header", "Expected Response"),
    ("formula_class", "Formula Class"),
    ("byte_address", "Byte Address"),
    ("bit_address", "Bit (formula k)"),
    ("sicar_bit_lsb_index", "Bit LSB index"),
    ("coefficient", "Coefficient"),
    ("offset", "Offset"),
    ("sicar_runtime_status", "SICAR runtime status"),
    ("sicar_runtime_note", "SICAR runtime note"),
    ("is_dtc", "Is DTC"),
    ("dtc_code", "DTC"),
    ("dtc_description", "DTC توضیح فارسی"),
    ("dtc_description_en", "DTC English"),
    ("label_manufacturer", "Manufacturer (hierarchy)"),
    ("label_vehicle", "Vehicle (hierarchy)"),
    ("label_source", "Hierarchy label source"),
    ("verification_status", "Verification"),
    ("site_json_check", "Site↔JSON check"),
    ("ecu_page_url", "ECU Page"),
    ("file_sha256", "File SHA256"),
    ("retrieved_at", "Retrieved"),
    ("record_id", "Record ID"),
    ("notes", "Notes"),
]

WIDTH = {
    "vehicle_model": 18, "ecu_model": 28, "parameter_name": 34,
    "request_command": 14, "formula": 32, "source_url": 40,
    "notes": 60, "init_commands": 40, "sicar_runtime_note": 40,
    "vehicle_label_raw": 34, "dtc_description": 40,
}

HDR_FILL = PatternFill("solid", fgColor="1F4E78")
HDR_FONT = Font(bold=True, color="FFFFFF")


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return v
    s = _ILLEGAL.sub("", str(v))
    return s[:32000]


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def _write_sheet(wb, title, header, rows, widths=None):
    ws = wb.create_sheet(title[:31])
    ws.sheet_view.rightToLeft = True
    ws.freeze_panes = "A2"
    ws.append(header)
    for c in ws[1]:
        c.fill, c.font = HDR_FILL, HDR_FONT
        c.alignment = Alignment(wrap_text=True, vertical="center")
    n = 1
    for r in rows:
        ws.append([_cell(x) for x in r])
        n += 1
    ws.auto_filter.ref = f"A1:{get_column_letter(max(1, len(header)))}{n}"
    for i in range(1, len(header) + 1):
        ws.column_dimensions[get_column_letter(i)].width = (widths or {}).get(i, 16)
    return ws


def derive_labels(records: List[Dict]) -> Counter:
    """برچسب سلسله‌مراتب (خودروساز/خودرو/ECU) فقط از داده منبع.
    مقدار اصلی هر رکورد تغییر نمی‌کند؛ فقط فیلدهای label_* پر می‌شوند."""
    page_mfr = defaultdict(set)
    page_vehicles = defaultdict(set)
    for r in records:
        if r.get("ecu_page_url"):
            if r.get("manufacturer"):
                page_mfr[r["ecu_page_url"]].add(r["manufacturer"])
            if r.get("vehicle_model"):
                page_vehicles[r["ecu_page_url"]].add(r["vehicle_model"])

    cnt = Counter()
    for r in records:
        mfr, veh, src = r.get("manufacturer") or "", r.get("vehicle_model") or "", "record"

        if not mfr and r.get("ecu_page_url") in page_mfr:
            mfr = " | ".join(sorted(page_mfr[r["ecu_page_url"]]))
            src = "ecu_page_json_links"

        if not veh:
            if r.get("applicable_vehicles"):
                veh = r["applicable_vehicles"]
                src = src + "+applicable_vehicles" if src != "record" else "applicable_vehicles_of_ecu_page"
            elif r.get("ecu_page_url") in page_vehicles:
                veh = " | ".join(sorted(page_vehicles[r["ecu_page_url"]]))
                src = src + "+ecu_page" if src != "record" else "ecu_page_peer_records"

        ecu = r.get("ecu_model") or ""
        if not ecu and r.get("is_dtc"):
            if r.get("source_kind") == "site_page_table":
                ecu = "(فهرست عمومی کد خطا)"
                src = src + "+dtc_inference" if src != "record" else "dtc_table_inference"

        r["label_manufacturer"] = mfr or UNKNOWN
        r["label_vehicle"] = veh or UNKNOWN
        r["label_ecu"] = ecu or UNKNOWN
        r["label_source"] = src
        cnt[src] += 1
    return cnt


class OutputGenerator:
    def __init__(self, output_dir: str):
        self.out = Path(output_dir).resolve()
        self.out.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ Excel جامع
    def generate_comprehensive_excel(self, records: List[Dict], extra: Dict) -> Path:
        wb = Workbook()
        wb.remove(wb.active)

        # شیت اصلی داده‌ها
        keys = [k for k, _ in MAIN_COLUMNS]
        widths = {i + 1: WIDTH.get(k, 14) for i, k in enumerate(keys)}
        _write_sheet(wb, "ECU_Data",
                     [h for _, h in MAIN_COLUMNS],
                     ([r.get(k, "") for k in keys] for r in records),
                     widths)

        # شیت DTC
        dtc = [r for r in records if r.get("is_dtc") or r.get("dtc_code")]
        _write_sheet(wb, "DTC", [
            "DTC", "Read Command | دستور خواندن",
            "Decode Formula | فرمول دیکد",
            "توضیح فارسی", "English description",
            "Vehicle", "ECU", "Parameter",
            "Source Tier", "Source", "Confidence %",
            "Record Type", "Notes",
        ], ([
            r.get("dtc_code", ""), r.get("request_command", ""),
            r.get("formula", ""),
            r.get("dtc_description", ""), r.get("dtc_description_en", ""),
            r.get("vehicle_model", ""), r.get("ecu_model", ""),
            r.get("parameter_name", ""),
            r.get("source_tier", ""), r.get("source_url", ""),
            r.get("confidence_pct", ""),
            r.get("record_type", ""), r.get("notes", ""),
        ] for r in dtc), {
            1: 10, 2: 14, 3: 32, 4: 45, 5: 45,
            10: 40, 13: 50,
        })

        # شیت خلاصه
        summ = extra.get("summary_rows", [])
        _write_sheet(wb, "Summary",
                     ["Metric | شاخص", "Value | مقدار"],
                     summ, {1: 60, 2: 25})

        # شیت منابع
        _write_sheet(wb, "Sources", [
            "Kind", "Name / Source", "Status", "Detail", "SHA256", "URL / Path",
        ], extra.get("source_rows", []), {
            2: 40, 4: 50, 5: 66, 6: 60,
        })

        # شیت پوشش (مواردی که در JSON نیستند)
        cov = extra.get("coverage", [])
        _write_sheet(wb, "Coverage_not_in_json", [
            "ECU", "Parameter", "Command", "Table Formula",
            "Min", "Max", "Unit", "File", "Vehicle", "ECU Page",
        ], ([
            c.get("ecu_title", ""), c.get("parameter", ""),
            c.get("command", ""), c.get("table_formula", ""),
            c.get("table_min", ""), c.get("table_max", ""),
            c.get("table_unit", ""), c.get("file", ""),
            c.get("vehicle", ""), c.get("ecu_page", ""),
        ] for c in cov), {
            1: 28, 2: 34, 10: 40,
        })

        # شیت تاریخچه نسخه
        vh = extra.get("version_rows", [])
        _write_sheet(wb, "Version_History", [
            "File", "From Version", "From Date",
            "To Version", "To Date", "Change",
            "From URL", "To URL",
        ], ([
            v["file"], v["from_version"], v["from_date"],
            v["to_version"], v["to_date"], v["change"],
            v["from_url"], v["to_url"],
        ] for v in vh), {
            1: 30, 6: 80, 7: 40, 8: 40,
        })

        # شیت اتصال (پروتکل‌ها و دستورات init)
        conn_rows = []
        seen_conn = set()
        for r in records:
            key = (r.get("label_manufacturer"), r.get("label_vehicle"),
                   r.get("label_ecu"), r.get("protocol"), r.get("baud_rate"),
                   r.get("init_commands"), r.get("file_name"))
            if key in seen_conn:
                continue
            seen_conn.add(key)
            if r.get("protocol") or r.get("init_commands"):
                conn_rows.append([
                    r.get("label_manufacturer", ""),
                    r.get("label_vehicle", ""),
                    r.get("label_ecu", ""),
                    r.get("protocol", ""),
                    r.get("baud_rate", ""),
                    r.get("ecu_address", ""),
                    r.get("init_commands", ""),
                    r.get("wakeup_commands", ""),
                    r.get("reinit_commands", ""),
                    r.get("file_name", ""),
                    r.get("source_tier", ""),
                ])
        _write_sheet(wb, "Connection", [
            "Manufacturer", "Vehicle", "ECU",
            "Protocol", "Baud Rate", "ECU Address",
            "Init Commands", "Wakeup Commands", "Reinit Commands",
            "File", "Source Tier",
        ], conn_rows, {
            1: 18, 2: 18, 3: 24, 4: 30, 6: 14,
            7: 50, 8: 40, 9: 40,
        })

        p = self.out / "ECU_Master_Database.xlsx"
        wb.save(p)
        return p

    # ------------------------------------------------------------------ گزارش تعارض
    def generate_conflict_report(self, conflicts: List[Dict], stats: Dict) -> Path:
        wb = Workbook()
        wb.remove(wb.active)

        maxv = max([len(c["values"]) for c in conflicts] or [2])
        hdr = [
            "Conflict ID", "Type", "Field", "Severity", "Go status",
            "Vehicle", "ECU", "Parameter", "Command",
            "Resolution", "Note",
        ]
        for i in range(1, maxv + 1):
            hdr += [
                f"Value {i}", f"Source {i}", f"Tier {i}",
                f"File {i}", f"URL {i}",
                f"Confidence {i}", f"Agreement {i}",
            ]

        rows = []
        for c in conflicts:
            row = [
                c["conflict_id"], c["conflict_type"], c["field"],
                c.get("severity", ""), c.get("go_status", ""),
                c.get("vehicle", ""), c.get("ecu", ""),
                c.get("parameter_name", ""), c.get("request_command", ""),
                c.get("resolution", ""), c.get("note", ""),
            ]
            for v in c["values"]:
                row += [
                    v.get("value"), v.get("source_label", v.get("source_tier", "")),
                    v.get("source_tier"), v.get("file_name"),
                    v.get("source_url"), v.get("confidence_pct"),
                    v.get("agreement_pct", ""),
                ]
            rows.append(row)

        _write_sheet(wb, "Conflicts", hdr, rows, {
            6: 18, 7: 28, 8: 34, 11: 40,
        })

        _write_sheet(wb, "Stats",
                     ["Metric", "Count"],
                     sorted(stats.items()), {1: 60})

        p = self.out / "Conflict_Report.xlsx"
        wb.save(p)

        (self.out / "conflicts.json").write_text(
            json.dumps(conflicts, ensure_ascii=False, indent=1), encoding="utf-8")
        return p

    # ------------------------------------------------------------------ آرشیو JSON ساختاریافته
    def generate_structured_json(self, records: List[Dict],
                                 originals: List[Dict]) -> Dict:
        arch = self.out / "JSON_Archive"
        arch.mkdir(parents=True, exist_ok=True)

        # اطمینان از وجود برچسب‌ها
        derive_labels(records)

        # ساخت درخت: Manufacturer → Vehicle → ECU → {connection, init, parameters, dtcs}
        tree: Dict = {}
        for r in records:
            mfr = r.get("label_manufacturer", UNKNOWN)
            veh = r.get("label_vehicle", UNKNOWN)
            ecu = r.get("label_ecu", UNKNOWN)

            node = tree.setdefault(mfr, {}).setdefault(veh, {}).setdefault(ecu, {
                "connection": [], "init": [], "parameters": [], "dtcs": [],
            })

            rt = r.get("record_type", "")
            rec = dict(r)

            if rt in ("json_init", "json_wakeup", "json_reinit"):
                node["init"].append(rec)
            elif r.get("is_dtc") or rt in ("json_dtc", "dtc_table"):
                node["dtcs"].append(rec)
            else:
                node["parameters"].append(rec)

            conn = {
                k: r.get(k, "") for k in (
                    "protocol", "baud_rate", "ecu_address",
                    "init_commands", "wakeup_commands", "reinit_commands",
                    "file_name", "source_url", "source_tier",
                )
            }
            if conn["protocol"] or conn["init_commands"]:
                if conn not in node["connection"]:
                    node["connection"].append(conn)

        # ذخیره درخت
        p = arch / "ECU_Hierarchy.json"
        p.write_text(json.dumps(tree, ensure_ascii=False, indent=1), encoding="utf-8")

        # آمار
        total_params = sum(
            len(node["parameters"])
            for mfr_dict in tree.values()
            for veh_dict in mfr_dict.values()
            for node in veh_dict.values()
        )
        total_dtcs = sum(
            len(node["dtcs"])
            for mfr_dict in tree.values()
            for veh_dict in mfr_dict.values()
            for node in veh_dict.values()
        )
        total_init = sum(
            len(node["init"])
            for mfr_dict in tree.values()
            for veh_dict in mfr_dict.values()
            for node in veh_dict.values()
        )

        # کپی اصل فایل‌ها بایت‌به‌بایت
        man = []
        for o in originals:
            src = Path(o["local_path"])
            if not src.is_file():
                man.append(dict(o, archive_path="", verified=False,
                                note="فایل مبدأ یافت نشد"))
                continue
            dest = arch / "originals" / o["group"] / o.get("rel", src.name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
            sha = _sha(dest)
            verified = (not o.get("sha256") or o["sha256"] == sha)
            man.append(dict(o, archive_path=str(dest.relative_to(arch)),
                            sha256=sha, verified=verified))

        (arch / "originals_manifest.json").write_text(
            json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")

        # شمارش رکوردهای نامشخص
        unknown_ecu_recs = 0
        unknown_mfr_recs = 0
        for mfr_name, mfr_dict in tree.items():
            for veh_name, veh_dict in mfr_dict.items():
                for ecu_name, node in veh_dict.items():
                    count = len(node["parameters"]) + len(node["dtcs"]) + len(node["init"])
                    if ecu_name == UNKNOWN:
                        unknown_ecu_recs += count
                    if mfr_name == UNKNOWN:
                        unknown_mfr_recs += count

        return {
            "path": str(p),
            "manufacturers": len(tree),
            "vehicles": sum(len(v) for v in tree.values()),
            "ecus": sum(len(e) for m in tree.values() for e in m.values()),
            "parameters_total": total_params,
            "dtcs_total": total_dtcs,
            "init_commands_total": total_init,
            "unknown_ecu_records": unknown_ecu_recs,
            "unknown_manufacturer_records": unknown_mfr_recs,
            "originals": len(man),
            "originals_verified": sum(1 for m in man if m.get("verified")),
        }

    # ------------------------------------------------------------------ مستندات Word مهندسی
    def generate_engineering_word(self, records: List[Dict]) -> List[Path]:
        try:
            from docx import Document
            from docx.enum.section import WD_ORIENT
            from docx.oxml import parse_xml
            from docx.oxml.ns import nsdecls
            from docx.shared import Cm, Pt
        except ImportError:
            print("[!] python-docx نصب نیست؛ خروجی Word تولید نمی‌شود")
            return []

        docs_dir = self.out / "Word_Docs"
        docs_dir.mkdir(parents=True, exist_ok=True)

        # اطمینان از وجود برچسب‌ها
        derive_labels(records)

        # گروه‌بندی بر اساس خودروساز (فقط سطوح Diamond, Primary, Gold)
        groups = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
        for r in records:
            if r.get("source_tier") in ("Silver", "Bronze"):
                continue
            groups[r.get("label_manufacturer", UNKNOWN)][
                r.get("label_vehicle", UNKNOWN)][
                r.get("label_ecu", UNKNOWN)
            ].append(r)

        def rtl(doc):
            st = doc.styles["Normal"]
            st.font.name = "Vazirmatn"
            st.font.size = Pt(9)
            ppr = st.element.get_or_add_pPr()
            ppr.append(parse_xml(f"<w:bidi {nsdecls('w')}/>"))

        def fast_table(doc, header, rows, widths):
            """جدول سریع با جهت راست‌به‌چپ."""
            def tc(v, w, bold=False):
                b = "<w:b/>" if bold else ""
                val = _ILLEGAL.sub("", str(v if v is not None else ""))
                return (
                    f"<w:tc><w:tcPr><w:tcW w:w=\"{w}\" w:type=\"dxa\"/></w:tcPr>"
                    f"<w:p><w:pPr><w:bidi/><w:spacing w:before=\"0\" w:after=\"0\"/></w:pPr>"
                    f"<w:r><w:rPr>{b}<w:sz w:val=\"14\"/></w:rPr>"
                    f"<w:t xml:space=\"preserve\">{escape(val)}</w:t></w:r></w:p></w:tc>"
                )

            grid = "".join(f"<w:gridCol w:w=\"{w}\"/>" for w in widths)
            hdr_xml = ("<w:tr><w:trPr><w:tblHeader/></w:trPr>" +
                       "".join(tc(h, w, True) for h, w in zip(header, widths)) +
                       "</w:tr>")
            body_xml = "".join(
                "<w:tr><w:trPr><w:cantSplit/></w:trPr>" +
                "".join(tc(v, w) for v, w in zip(row, widths)) +
                "</w:tr>" for row in rows
            )
            tbl_xml = (
                f"<w:tbl {nsdecls('w')}><w:tblPr>"
                f"<w:tblStyle w:val=\"TableGrid\"/><w:bidiVisual/>"
                f"<w:tblW w:w=\"{sum(widths)}\" w:type=\"dxa\"/>"
                f"<w:tblLayout w:type=\"fixed\"/>"
                f"<w:tblBorders>"
                f"<w:top w:val=\"single\" w:sz=\"4\"/>"
                f"<w:left w:val=\"single\" w:sz=\"4\"/>"
                f"<w:bottom w:val=\"single\" w:sz=\"4\"/>"
                f"<w:right w:val=\"single\" w:sz=\"4\"/>"
                f"<w:insideH w:val=\"single\" w:sz=\"4\"/>"
                f"<w:insideV w:val=\"single\" w:sz=\"4\"/>"
                f"</w:tblBorders></w:tblPr>"
                f"<w:tblGrid>{grid}</w:tblGrid>{hdr_xml}{body_xml}</w:tbl>"
            )
            anchor = doc.add_paragraph()
            anchor._p.addprevious(parse_xml(tbl_xml))

        paths = []

        for mfr in sorted(groups):
            doc = Document()
            rtl(doc)

            # تنظیم صفحه افقی
            sec = doc.sections[0]
            sec.orientation = WD_ORIENT.LANDSCAPE
            new_w, new_h = sec.page_height, sec.page_width
            sec.page_width, sec.page_height = new_w, new_h
            for m in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
                setattr(sec, m, Cm(1.0))

            # عنوان
            doc.add_heading(f"مرجع مهندسی ECU — {mfr}", level=0)

            # مقدمه
            p = doc.add_paragraph()
            run = p.add_run(
                "این سند به‌صورت خودکار از بانک اطلاعات ECU تولید شده است. "
                "تمام داده‌ها منبع مشخص دارند و هیچ مقداری حدس زده نشده است. "
                "در صورت وجود تعارض بین منابع، به گزارش Conflict_Report.xlsx مراجعه کنید."
            )

            doc.add_heading("فهرست محتویات", level=1)
            for veh in sorted(groups[mfr]):
                doc.add_paragraph(f"• {veh}", style="List Bullet")

            # بخش هر خودرو
            for veh in sorted(groups[mfr]):
                doc.add_page_break()
                doc.add_heading(f"خودرو: {veh}", level=1)

                for ecu in sorted(groups[mfr][veh]):
                    recs = groups[mfr][veh][ecu]
                    doc.add_heading(f"ECU: {ecu}", level=2)

                    # اطلاعات اتصال
                    conn_recs = [
                        r for r in recs
                        if r.get("protocol") or r.get("init_commands")
                    ]
                    if conn_recs:
                        doc.add_heading("مشخصات اتصال و پروتکل", level=3)
                        seen = set()
                        conn_rows = []
                        for r in conn_recs:
                            key = (r.get("protocol"), r.get("baud_rate"),
                                   r.get("ecu_address"), r.get("init_commands"))
                            if key in seen:
                                continue
                            seen.add(key)
                            conn_rows.append([
                                r.get("protocol", ""),
                                r.get("baud_rate", ""),
                                r.get("ecu_address", ""),
                                r.get("init_commands", ""),
                                r.get("wakeup_commands", ""),
                                r.get("source_tier", ""),
                                r.get("file_name", ""),
                            ])
                        if conn_rows:
                            fast_table(doc, [
                                "پروتکل", "Baud", "آدرس ECU",
                                "Init Commands", "Wakeup",
                                "منبع", "فایل",
                            ], conn_rows, [
                                1400, 700, 700, 2200, 1400, 700, 1000,
                            ])

                    # پارامترها
                    params = [
                        r for r in recs
                        if not r.get("is_dtc") and r.get("record_type") not in
                        ("json_init", "json_wakeup", "json_reinit")
                    ]
                    if params:
                        doc.add_heading(f"پارامترها ({len(params)})", level=3)
                        param_rows = []
                        for r in params[:500]:  # محدودیت برای عملکرد Word
                            param_rows.append([
                                r.get("parameter_name", ""),
                                r.get("request_command", ""),
                                r.get("response_header", ""),
                                r.get("formula", ""),
                                r.get("unit", ""),
                                r.get("min_value", ""),
                                r.get("max_value", ""),
                                r.get("source_tier", ""),
                                r.get("file_name", ""),
                            ])
                        if param_rows:
                            fast_table(doc, [
                                "پارامتر", "دستور", "پاسخ",
                                "فرمول", "واحد", "Min", "Max",
                                "منبع", "فایل",
                            ], param_rows, [
                                1500, 700, 700, 1800, 600, 500, 500, 600, 900,
                            ])
                        if len(params) > 500:
                            doc.add_paragraph(
                                f"... و {len(params) - 500} پارامتر دیگر در فایل Excel کامل است."
                            )

                    # DTCها
                    dtcs = [r for r in recs if r.get("is_dtc") or r.get("dtc_code")]
                    if dtcs:
                        doc.add_heading(f"کده‌های خطا DTC ({len(dtcs)})", level=3)
                        dtc_rows = []
                        for r in dtcs[:300]:
                            dtc_rows.append([
                                r.get("dtc_code", ""),
                                r.get("request_command", ""),
                                r.get("formula", ""),
                                r.get("dtc_description", ""),
                                r.get("dtc_description_en", ""),
                                r.get("source_tier", ""),
                            ])
                        if dtc_rows:
                            fast_table(doc, [
                                "کد DTC", "دستور خواندن", "فرمول دیکد",
                                "توضیح فارسی", "English", "منبع",
                            ], dtc_rows, [
                                800, 900, 1500, 2000, 1500, 600,
                            ])

                    # یادداشت‌ها
                    doc.add_heading("نکات پیاده‌سازی", level=3)
                    notes = [
                        "اندیس B[n] در فرمول‌ها مانند سورس سیکار ۱-پایه است (B[1] = اولین بایت پاسخ).",
                        "برای B[n]&(1<<k): سیکار بیت k را از سمت MSB می‌خواند؛ اندیس LSB = 7-k.",
                        "ctype=2 (cmd_Param_2) رایج‌ترین نوع دستور برای پارامترهاست.",
                        "در صورت وجود تعارض، منبع با tier بالاتر (Diamond > Primary > Gold) معتبرتر است.",
                        "تمام فایل‌های JSON اصلی در پوشه JSON_Archive/originals بایت‌به‌بایت نگهداری شده‌اند.",
                    ]
                    for n in notes:
                        doc.add_paragraph(n, style="List Bullet")

            # ذخیره سند
            safe_mfr = re.sub(r'[<>:"/\\|?*]', '_', mfr)[:80]
            doc_path = docs_dir / f"ECU_Engineering_{safe_mfr}.docx"
            doc.save(doc_path)
            paths.append(doc_path)

        # سند راهنمای کلی
        doc = Document()
        rtl(doc)
        doc.add_heading("راهنمای کلی بانک اطلاعات ECU", level=0)

        doc.add_heading("ساختار کلی", level=1)
        doc.add_paragraph(
            "این بانک اطلاعات شامل داده‌های ارتباطی و رمزگشایی ECU خودروهای ایرانی است که از منابع زیر جمع‌آوری شده:"
        )
        for tier, desc in [
            ("Diamond", "اسناد رسمی کارخانه (PDF/Excel)"),
            ("Primary-SICAR-Site", "وب‌سایت SICAR (جدول‌ها و فایل‌های JSON)"),
            ("Gold/GitHub", "مخزن GitHub Sisoog/sicar-diag"),
            ("Gold/Issues", "بحث‌ها و Issueهای GitHub"),
            ("Silver", "Reddit، Telegram، Kaggle (فقط سرنخ)"),
            ("Bronze", "Wikipedia، استانداردهای ISO/SAE"),
        ]:
            doc.add_paragraph(f"• {tier}: {desc}", style="List Bullet")

        doc.add_heading("فایل‌های خروجی", level=1)
        outputs = [
            ("ECU_Master_Database.xlsx", "بانک اطلاعات کامل Excel با قابلیت فیلتر"),
            ("Conflict_Report.xlsx", "گزارش تعارضات بین منابع"),
            ("JSON_Archive/ECU_Hierarchy.json", "ساختار درختی Manufacturer → Vehicle → ECU"),
            ("JSON_Archive/originals/", "اصل فایل‌های JSON/PDF بایت‌به‌بایت"),
            ("Word_Docs/", "مستندات مهندسی به‌ازای هر خودروساز"),
            ("pipeline_report.json", "گزارش کامل اجرای خط لوله"),
            ("rejected_records.json", "رکوردهای ردشده با دلیل"),
        ]
        for name, desc in outputs:
            doc.add_paragraph(f"• {name}: {desc}", style="List Bullet")

        doc.add_heading("نحوه استفاده از فرمول‌ها", level=1)
        doc.add_paragraph(
            "برای محاسبه مقدار یک پارامتر از پاسخ ECU، از موتور مرجع سیکار استفاده کنید:"
        )
        code = doc.add_paragraph()
        code.add_run(
            "from modules.sicar_formula import evaluate\n"
            "result = evaluate('B[1]*0.75', '41 0C 1A F8', ctype=2, decimals=2)\n"
            "print(result.value, result.status)"
        ).font.name = "Courier New"

        guide_path = docs_dir / "ECU_Engineering_Guide.docx"
        doc.save(guide_path)
        paths.append(guide_path)

        return paths

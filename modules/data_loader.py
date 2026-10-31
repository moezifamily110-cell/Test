# -*- coding: utf-8 -*-
"""بارگذاری خروجی خزنده Go (نسخه ۱۵) بدون از دست رفتن داده.

تغییرات:
  * json_dtc با request_command + (formula یا parameter_name) پذیرفته می‌شود.
  * dtc_table فقط dtc_code لازم دارد؛ json_init/json_wakeup/json_reinit فقط request_command.
  * رکوردهای ردشده همراه دلیل در rejected_records.json ذخیره می‌شوند.
  * load_go_reports گزارش‌های جانبی Go را بارگذاری می‌کند.
"""
import json
from pathlib import Path
from typing import Dict, List, Tuple

GO_REPORTS = ("site_vs_json.json", "github_vs_site.json", "version_history.json",
              "vehicles_tree.json", "crawl_report.json")


class DataLoader:
    BRONZE_TIERS = {"Bronze", "Silver"}
    VEHICLE_MAP = {"پژو": "Peugeot", "سمند": "Samand", "دنا": "Dena", "پراید": "Pride",
                   "تیبا": "Tiba", "ساینا": "Saina", "کوییک": "Quick", "شاهین": "Shahin",
                   "رانا": "Runna", "تارا": "Tara", "زارو": "Zarooq", "آریو": "Ario"}

    def __init__(self):
        self.rejected: List[Dict] = []

    def validate(self, r: Dict) -> Tuple[bool, str]:
        """اعتبارسنجی بر اساس نوع رکورد. هیچ داده‌ای بدون دلیل رد نمی‌شود."""
        rt = r.get("record_type", "") or ""

        if r.get("source_tier") in self.BRONZE_TIERS:
            return (bool(r.get("parameter_name")) or bool(r.get("dtc_code")),
                    "silver_bronze_without_parameter_or_dtc")

        if rt == "json_dtc":
            ok = bool(r.get("request_command")) and bool(r.get("formula") or r.get("parameter_name") or r.get("dtc_code"))
            return ok, "json_dtc_insufficient_fields"

        if rt == "dtc_table" or (r.get("is_dtc") and not rt.startswith("json")):
            return bool(r.get("dtc_code")), "dtc_without_code"

        if rt in ("json_init", "json_wakeup", "json_reinit"):
            return bool(r.get("request_command")), "init_without_command"

        if not r.get("request_command"):
            return False, "missing_request_command"

        if not (r.get("formula") or r.get("unit") or r.get("min_value") or
                r.get("max_value") or r.get("parameter_name") or r.get("dtc_code")):
            return False, "missing_any_meaningful_field"

        return True, ""

    def normalize(self, r: Dict) -> Dict:
        """نرمال‌سازی نام خودرو و خودروساز؛ مقدار اصلی تغییر نمی‌کند."""
        brand = r.get("vehicle_brand", "") or ""
        r["vehicle_brand_normalized"] = self.VEHICLE_MAP.get(brand, brand)

        mfr = r.get("manufacturer", "") or ""
        mfr_l = mfr.lower()
        if "ایران" in mfr_l or "ikco" in mfr_l or "iran khodro" in mfr_l:
            r["manufacturer_normalized"] = "Iran_Khodro"
        elif "سایپا" in mfr_l or "saipa" in mfr_l:
            r["manufacturer_normalized"] = "SAIPA"
        else:
            r["manufacturer_normalized"] = mfr

        # اطمینان از وجود confidence_pct
        if not r.get("confidence_pct"):
            tier = r.get("source_tier", "")
            if tier == "Diamond":
                r["confidence_pct"] = 100
            elif tier == "Primary-SICAR-Site":
                r["confidence_pct"] = 90
            elif tier in ("Gold/GitHub", "Gold"):
                r["confidence_pct"] = 80
            elif tier == "Gold/Issues":
                r["confidence_pct"] = 50
            elif tier == "Silver":
                r["confidence_pct"] = 40
            elif tier == "Bronze":
                r["confidence_pct"] = 30
            else:
                r["confidence_pct"] = 50

        return r

    def filter(self, records: List[Dict], origin: str) -> List[Dict]:
        ok = []
        for r in records:
            if not isinstance(r, dict):
                self.rejected.append({"origin": origin, "reason": "not_a_dict", "record": r})
                continue
            good, why = self.validate(r)
            if good:
                ok.append(self.normalize(r))
            else:
                self.rejected.append({"origin": origin, "reason": why, "record": r})
        return ok

    def load_go_data(self, go_output_dir: str) -> List[Dict]:
        p = Path(go_output_dir) / "raw_records.json"
        if not p.exists():
            print(f"[-] {p} یافت نشد")
            return []
        try:
            records = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"[-] JSON خراب در {p}: {e}")
            return []
        if not isinstance(records, list):
            print(f"[-] ساختار {p} لیست نیست")
            return []
        before = len(self.rejected)
        ok = self.filter(records, "go:raw_records.json")
        print(f"[i] Go: {len(records)} رکورد → {len(ok)} پذیرفته / {len(self.rejected) - before} رد (با دلیل ذخیره شد)")
        return ok

    @staticmethod
    def load_go_reports(go_output_dir: str) -> Dict[str, object]:
        out = {}
        for name in GO_REPORTS:
            p = Path(go_output_dir) / name
            try:
                out[name.rsplit(".", 1)[0]] = json.loads(p.read_text(encoding="utf-8"))
            except FileNotFoundError:
                out[name.rsplit(".", 1)[0]] = None
                print(f"[!] گزارش {name} موجود نیست؛ مرحله مربوط با داده کمتر اجرا می‌شود")
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                out[name.rsplit(".", 1)[0]] = None
                print(f"[!] گزارش {name} خراب است: {e}")
        return out

    def save_rejected(self, out_dir: str) -> Path:
        p = Path(out_dir) / "rejected_records.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.rejected, ensure_ascii=False, indent=1), encoding="utf-8")
        return p

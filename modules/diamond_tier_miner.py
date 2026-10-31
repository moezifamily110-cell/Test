# -*- coding: utf-8 -*-
"""سطح Diamond: فقط اسناد رسمی کارخانه که کاربر در --diamond-dir قرار می‌دهد.

  * نگاشت ستون فقط با کلیدواژه سرستون (فارسی/انگلیسی)؛ هیچ حدس بر اساس جایگاه ستون انجام نمی‌شود.
  * جدولی که ستون «پارامتر» و یکی از «دستور/فرمول» را نداشته باشد، استخراج نمی‌شود.
  * برچسب خودرو/ECU فقط از manifest.json اختیاری (کاربر) خوانده می‌شود.
  * PDFهای دانلودشده از سایت سیکار اسناد رسمی کارخانه نیستند؛ فقط در فهرست ثبت می‌شوند.
"""
import hashlib
import json
from pathlib import Path
from typing import Dict, List, Optional

HEADER_KEYS = {
    "parameter_name": ("پارامتر", "نام پارامتر", "parameter", "name", "description", "شرح"),
    "request_command": ("دستور", "فرمان", "command", "request", "cmd", "pid", "service"),
    "response_header": ("پاسخ", "response", "answer"),
    "formula": ("فرمول", "formula", "equation", "conversion", "محاسبه"),
    "unit": ("واحد", "unit"),
    "min_value": ("حداقل", "کمینه", "min", "minimum"),
    "max_value": ("حداکثر", "بیشینه", "max", "maximum"),
    "byte_address": ("بایت", "byte", "آدرس بایت"),
    "bit_address": ("بیت", "bit", "آدرس بیت"),
    "dtc_code": ("کد خطا", "dtc", "fault code", "trouble code", "کد اشکال"),
    "dtc_description": ("توضیح خطا", "شرح خطا", "fault description", "توضیح"),
    "ecu_model": ("ایسیو", "ecu", "مدل ایسیو"),
    "vehicle_model": ("خودرو", "مدل خودرو", "vehicle", "مدل"),
    "protocol": ("پروتکل", "protocol"),
    "baud_rate": ("بودرت", "baud", "baud rate"),
}


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def map_header(cells: List) -> Dict[int, str]:
    """هر ستون فقط وقتی نگاشت می‌شود که متن سرستون شامل یکی از کلیدواژه‌ها باشد."""
    out, used = {}, set()
    for i, c in enumerate(cells):
        t = str(c or "").strip().lower()
        if not t:
            continue
        for field in ("dtc_code", "parameter_name", "request_command", "response_header",
                      "formula", "unit", "min_value", "max_value", "byte_address",
                      "bit_address", "dtc_description", "ecu_model", "vehicle_model",
                      "protocol", "baud_rate"):
            if field in used:
                continue
            if any(k in t for k in HEADER_KEYS[field]):
                out[i] = field
                used.add(field)
                break
    return out


class DiamondTierMiner:
    def __init__(self, diamond_dir: Optional[str] = None,
                 site_files_dir: Optional[str] = None):
        self.dir = Path(diamond_dir).resolve() if diamond_dir else None
        self.site_files_dir = Path(site_files_dir).resolve() if site_files_dir else None
        self.manifest = {}
        if self.dir and (self.dir / "manifest.json").exists():
            try:
                self.manifest = json.loads(
                    (self.dir / "manifest.json").read_text(encoding="utf-8"))
            except Exception as e:
                print(f"[!] manifest.json خراب: {e}")
        self.report: List[Dict] = []
        self.documents: List[Dict] = []

    def mine_all_diamond_docs(self) -> List[Dict]:
        recs = []
        if not self.dir or not self.dir.is_dir():
            self.report.append({
                "file": "", "status": "no_diamond_dir",
                "note": "سند رسمی کارخانه ارائه نشده (--diamond-dir)؛ سطح Diamond خالی است",
            })
        else:
            for p in sorted(self.dir.rglob("*")):
                if not p.is_file():
                    continue
                ext = p.suffix.lower()
                if ext == ".pdf":
                    recs += self._pdf(p)
                elif ext in (".xlsx", ".xlsm", ".xls", ".csv"):
                    recs += self._sheet(p)
                elif ext == ".json":
                    recs += self._json_file(p)

        self._inventory_site_docs()
        return recs

    def _rows_to_records(self, rows: List[List], p: Path, where: str) -> List[Dict]:
        if not rows:
            return []
        hdr = map_header(rows[0])
        fields = set(hdr.values())

        has_param = "parameter_name" in fields
        has_dtc = "dtc_code" in fields
        has_meaningful = bool(fields & {"request_command", "formula", "dtc_code"})

        if not (has_param or has_dtc) or not has_meaningful:
            self.report.append({
                "file": p.name, "where": where,
                "status": "table_skipped_header_not_recognized",
                "header": [str(c) for c in rows[0]],
            })
            return []

        meta = self.manifest.get(p.name, {})
        sha = _sha(p)
        out = []

        for ri, row in enumerate(rows[1:], 2):
            rec = {
                f: str(row[i]).strip() if i < len(row) and row[i] is not None else ""
                for i, f in hdr.items()
            }
            if not any(rec.values()):
                continue

            rec.update({
                "record_id": hashlib.sha1(
                    f"{sha}|{where}|{ri}".encode()
                ).hexdigest()[:16],
                "record_type": "diamond_table_row",
                "is_dtc": bool(rec.get("dtc_code")),
                "manufacturer": meta.get("manufacturer", ""),
                "vehicle_model": meta.get("vehicle_model", rec.get("vehicle_model", "")),
                "ecu_manufacturer": meta.get("ecu_manufacturer", ""),
                "ecu_model": meta.get("ecu_model", rec.get("ecu_model", "")),
                "protocol": rec.get("protocol", ""),
                "baud_rate": rec.get("baud_rate", ""),
                "file_name": p.name,
                "file_sha256": sha,
                "source_url": meta.get("source_url", str(p)),
                "page_title": meta.get("document_title", p.name),
                "source_kind": "official_document",
                "source_tier": "Diamond",
                "confidence_score": 10,
                "confidence_pct": 100 if meta else 90,
                "verification_status": (
                    "official_document_with_manifest" if meta
                    else "official_document_labels_missing"
                ),
                "notes": (
                    f"{where}، ردیف {ri}; سرستون: " +
                    " | ".join(f"{rows[0][i]}→{f}" for i, f in hdr.items())
                ),
                "retrieved_at": meta.get("document_date", ""),
            })
            out.append(rec)

        self.report.append({
            "file": p.name, "where": where, "status": "ok", "records": len(out),
        })
        return out

    def _pdf(self, p: Path) -> List[Dict]:
        try:
            import pdfplumber
        except ImportError:
            self.report.append({
                "file": p.name, "status": "pdfplumber_missing",
                "note": "pip install pdfplumber لازم است",
            })
            return []
        out = []
        try:
            with pdfplumber.open(p) as pdf:
                for n, page in enumerate(pdf.pages, 1):
                    tables = page.extract_tables() or []
                    for ti, t in enumerate(tables, 1):
                        out += self._rows_to_records(t, p, f"صفحه {n} جدول {ti}")
        except Exception as e:
            self.report.append({"file": p.name, "status": f"error: {e}"})
        return out

    def _sheet(self, p: Path) -> List[Dict]:
        out = []
        try:
            if p.suffix.lower() == ".csv":
                import csv
                with open(p, encoding="utf-8-sig", newline="") as f:
                    out += self._rows_to_records(list(csv.reader(f)), p, "CSV")
            else:
                import pandas as pd
                for sh, df in pd.read_excel(
                        p, sheet_name=None, header=None, dtype=str).items():
                    rows = df.fillna("").values.tolist()
                    out += self._rows_to_records(rows, p, f"شیت {sh}")
        except Exception as e:
            self.report.append({"file": p.name, "status": f"error: {e}"})
        return out

    def _json_file(self, p: Path) -> List[Dict]:
        """JSON رسمی کارخانه (اگر در manifest تعریف شده باشد)."""
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            self.report.append({"file": p.name, "status": f"invalid_json: {e}"})
            return []

        meta = self.manifest.get(p.name)
        if not meta:
            self.report.append({
                "file": p.name, "status": "skipped_no_manifest",
                "note": "JSON بدون manifest در سطح Diamond پردازش نمی‌شود",
            })
            return []

        # سعی در استخراج به‌صورت طرح‌واره SICAR
        from modules.github_miner import GitHubMiner
        try:
            # استفاده از همان منطق parse
            sha = _sha(p)
            records = []
            # ساخت یک شیء موقت برای استفاده از _parse
            gm = GitHubMiner.__new__(GitHubMiner)
            gm.labels = {p.name: meta}
            records = gm._parse(data, p.name, sha)
            for r in records:
                r["source_tier"] = "Diamond"
                r["confidence_pct"] = 100
                r["source_kind"] = "official_json"
                r["verification_status"] = "official_document_json"
                r["source_url"] = meta.get("source_url", str(p))
            self.report.append({
                "file": p.name, "status": "ok", "records": len(records),
            })
            return records
        except Exception as e:
            self.report.append({"file": p.name, "status": f"parse_error: {e}"})
            return []

    def _inventory_site_docs(self):
        """فهرست PDFهای دانلودشده از سایت (غیر رسمی، فقط برای اطلاع)."""
        if not self.site_files_dir or not self.site_files_dir.is_dir():
            return
        for p in sorted(self.site_files_dir.rglob("*.pdf")):
            pages = ""
            try:
                import pdfplumber
                with pdfplumber.open(p) as pdf:
                    pages = len(pdf.pages)
            except Exception:
                pass
            self.documents.append({
                "name": p.name,
                "local_path": str(p),
                "sha256": _sha(p),
                "size": p.stat().st_size,
                "pages": pages,
                "classification": "site_document_not_official_factory",
                "note": "سند دانلودشده از سایت سیکار؛ سند رسمی کارخانه نیست",
            })

# -*- coding: utf-8 -*-
"""Version History:
  ۱) تاریخچه نسخه‌های فایل در سایت سیکار (version_history.json خزنده Go) → ردیف‌های تخت.
  ۲) مقایسه اجرای فعلی با اجرای قبلی همین خط لوله بر اساس record_id.
"""
import hashlib
import json
from pathlib import Path
from typing import Dict, List, Optional

TRACKED = (
    "formula", "unit", "min_value", "max_value", "request_command",
    "response_header", "protocol", "baud_rate", "init_commands",
    "dtc_description", "dtc_description_en", "file_sha256",
    "coefficient", "offset", "byte_address", "bit_address",
)


def ensure_record_id(r: Dict, occurrence: int = 0) -> str:
    if not r.get("record_id"):
        key = "|".join(str(r.get(k, "")) for k in (
            "source_tier", "source_url", "request_command", "response_header",
            "parameter_name", "dtc_code", "file_name"
        )) + f"|{occurrence}"
        r["record_id"] = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return r["record_id"]


def flatten_site_history(vh: Optional[List[Dict]]) -> List[Dict]:
    rows = []
    for e in vh or []:
        vers = {v.get("timestamp"): v for v in e.get("versions", [])}
        for ch in e.get("changes") or []:
            fv, tv = vers.get(ch.get("from"), {}), vers.get(ch.get("to"), {})
            diffs = ch.get("diff") or ["content changed"]
            for d in diffs:
                rows.append({
                    "file": e.get("file", ""),
                    "from_version": ch.get("from", ""),
                    "from_date": fv.get("date_text", ""),
                    "from_url": fv.get("url", ""),
                    "from_sha256": fv.get("sha256", ""),
                    "to_version": ch.get("to", ""),
                    "to_date": tv.get("date_text", ""),
                    "to_url": tv.get("url", ""),
                    "to_sha256": tv.get("sha256", ""),
                    "change": d,
                })
    return rows


class VersionDiffEngine:
    def __init__(self, archive_dir: str):
        self.dir = Path(archive_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def compare_versions(self, current: List[Dict],
                         snapshot_name: str = "previous_run_snapshot.json") -> Dict:
        snap = self.dir / snapshot_name
        prev = {}
        if snap.exists():
            try:
                prev = json.loads(snap.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"[!] snapshot قبلی خراب: {e}")

        cur = {}
        for r in current:
            cur[r["record_id"]] = {
                k: r.get(k, "") for k in
                TRACKED + ("parameter_name", "file_name", "source_tier", "ecu_model")
            }

        added = [k for k in cur if k not in prev]
        removed = [k for k in prev if k not in cur]
        changed = []

        for k in cur.keys() & prev.keys():
            d = {
                f: {"old": prev[k].get(f, ""), "new": cur[k].get(f, "")}
                for f in TRACKED
                if str(prev[k].get(f, "")) != str(cur[k].get(f, ""))
            }
            if d:
                changed.append({
                    "record_id": k,
                    "parameter_name": cur[k].get("parameter_name"),
                    "ecu_model": cur[k].get("ecu_model"),
                    "file_name": cur[k].get("file_name"),
                    "source_tier": cur[k].get("source_tier"),
                    "changes": d,
                })

        # ذخیره snapshot جدید
        tmp = snap.with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, ensure_ascii=False), encoding="utf-8")
        tmp.replace(snap)

        return {
            "first_run": not prev,
            "previous_count": len(prev),
            "current_count": len(cur),
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
            "added_ids": added[:5000],
            "removed_ids": removed[:5000],
            "changed_detail": changed[:5000],
        }

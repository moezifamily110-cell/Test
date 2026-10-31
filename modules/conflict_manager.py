# -*- coding: utf-8 -*-
"""شناسایی تعارض بدون ادغام.

هر تعارض فهرست «values» دارد؛ هر عضو مقدار یک منبع با tier / url / file / confidence است.
هیچ مقداری انتخاب یا ترکیب نمی‌شود؛ فقط ترتیب پیشنهادی بر اساس وزن سطح منبع ارائه می‌شود.

انواع تعارض:
  site_table_vs_json        : جدول صفحه ویکی سیکار ↔ فایل JSON همان ECU
  github_vs_site_file       : فایل JSON مخزن GitHub ↔ فایل هم‌نام سایت
  cross_file_same_vehicle_ecu: دو فایل متفاوت سایت برای همان خودرو + ECU + دستور + پارامتر
"""
import hashlib
import re
from collections import Counter, defaultdict
from typing import Dict, List, Optional

_FA = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩يكىي", "01234567890123456789یکیی")

TIER_WEIGHT = {
    "Diamond": 1.0,
    "Primary-SICAR-Site": 0.9,
    "Gold/GitHub": 0.8,
    "Gold": 0.8,
    "Gold/Issues": 0.6,
    "Silver": 0.5,
    "Bronze": 0.4,
}

COMPARE_FIELDS = ("formula", "unit", "min_value", "max_value", "response_header",
                  "command_type", "timeout_ms", "byte_address", "bit_address")
CONN_FIELDS = ("protocol", "baud_rate", "init_commands", "wakeup_commands",
               "reinit_commands", "ecu_address")


def norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "").translate(_FA)).strip().lower()


def norm_cmd(s) -> str:
    return re.sub(r"\s+", "", str(s or "")).upper()


def norm_formula(s) -> str:
    return re.sub(r"\s+", "", str(s or "").translate(_FA))


def tier_weight(t: str) -> float:
    if t in TIER_WEIGHT:
        return TIER_WEIGHT[t]
    for k, v in TIER_WEIGHT.items():
        if t and t.startswith(k):
            return v
    return 0.3


def _cid(*parts) -> str:
    return hashlib.sha1("|".join(map(str, parts)).encode("utf-8")).hexdigest()[:16]


def _val(r: Dict, field: str, value=None) -> Dict:
    return {
        "value": r.get(field, "") if value is None else value,
        "source_tier": r.get("source_tier", ""),
        "tier_weight": tier_weight(r.get("source_tier", "")),
        "source_url": r.get("source_url", ""),
        "file_name": r.get("file_name", ""),
        "file_sha256": r.get("file_sha256", ""),
        "record_id": r.get("record_id", ""),
        "confidence_pct": r.get("confidence_pct", ""),
        "retrieved_at": r.get("retrieved_at", ""),
    }


class ConflictManager:
    def __init__(self):
        self.stats = Counter()
        self.coverage: List[Dict] = []

    def detect_conflicts(self, records: List[Dict],
                         site_vs_json: Optional[List[Dict]] = None) -> List[Dict]:
        out = []
        out += self._site_table_vs_json(records, site_vs_json or [])
        out += self._github_vs_site(records)
        out += self._cross_file(records)

        for c in out:
            # مرتب‌سازی بر اساس وزن tier (ابتدا منابع معتبرتر)
            c["values"].sort(key=lambda v: -v["tier_weight"])
            c["resolution"] = "not_merged"
            c["severity"] = c.get("severity", "medium")

            # محاسبه درصد اطمینان برای هر مقدار بر اساس تکرار در منابع
            val_counts = Counter()
            for v in c["values"]:
                val_counts[norm(str(v["value"]))] += 1
            total = sum(val_counts.values())
            if total > 0:
                for v in c["values"]:
                    cnt = val_counts[norm(str(v["value"]))]
                    base_conf = float(v.get("confidence_pct") or 50)
                    v["agreement_pct"] = round(100.0 * cnt / total, 1)
                    v["combined_confidence"] = round(base_conf * 0.6 + (100.0 * cnt / total) * 0.4, 1)

            self.stats[c["conflict_type"] + ":" + c["field"]] += 1

        return out

    # ------------------------------------------------------------------ ۱: جدول صفحه ↔ فایل JSON
    def _site_table_vs_json(self, records, rows):
        url_by_file = {}
        for r in records:
            if r.get("source_tier") == "Primary-SICAR-Site" and r.get("file_name"):
                url_by_file.setdefault(r["file_name"], (
                    r.get("source_url", ""),
                    r.get("file_sha256", ""),
                    r.get("retrieved_at", ""),
                ))

        out = []
        for row in rows:
            st = row.get("status", "")
            if st == "not_in_json":
                self.coverage.append(row)
                continue
            if st not in ("formula_differs", "range_differs", "formula_equivalent",
                          "formula_identical"):
                self.stats["site_vs_json:" + str(st)] += 1
                continue
            if st == "formula_identical":
                self.stats["site_vs_json:identical"] += 1
                continue

            furl, fsha, fat = url_by_file.get(row.get("file"), ("", "", ""))
            pairs = []
            if st == "range_differs":
                pairs = [("min_value", "table_min", "json_min"),
                         ("max_value", "table_max", "json_max")]
            else:
                pairs = [("formula", "table_formula", "json_formula")]

            for field, tk, jk in pairs:
                tv, jv = row.get(tk, ""), row.get(jk, "")
                if st == "range_differs" and norm(tv) == norm(jv):
                    continue
                if field == "formula" and norm_formula(tv) == norm_formula(jv):
                    continue

                out.append({
                    "conflict_id": _cid("stj", row.get("ecu_page"), row.get("file"),
                                        row.get("command"), row.get("parameter"),
                                        field, tv, jv),
                    "conflict_type": "site_table_vs_json",
                    "field": field,
                    "severity": "low" if st == "formula_equivalent" else "high",
                    "go_status": st,
                    "vehicle": row.get("vehicle", ""),
                    "ecu": row.get("ecu_title", ""),
                    "parameter_name": row.get("parameter", ""),
                    "request_command": row.get("command", ""),
                    "values": [
                        dict(_val({}, field, tv),
                             source_tier="Primary-SICAR-Site",
                             tier_weight=tier_weight("Primary-SICAR-Site"),
                             source_url=row.get("ecu_page", ""),
                             file_name="(جدول صفحه ویکی)",
                             confidence_pct=85,
                             source_label="جدول صفحه ECU سایت سیکار"),
                        dict(_val({}, field, jv),
                             source_tier="Primary-SICAR-Site",
                             tier_weight=tier_weight("Primary-SICAR-Site"),
                             source_url=furl,
                             file_name=row.get("file", ""),
                             file_sha256=fsha,
                             confidence_pct=90,
                             retrieved_at=fat,
                             source_label="فایل JSON سایت سیکار"),
                    ],
                    "note": ("formula_equivalent: پس از حذف عملگرهای همانی یکسان‌اند"
                             if st == "formula_equivalent" else ""),
                })
        return out

    # ------------------------------------------------------------------ ۲: GitHub ↔ سایت
    @staticmethod
    def _keyed(recs):
        seen, out = Counter(), {}
        for r in recs:
            k = (r.get("record_type", ""),
                 norm_cmd(r.get("request_command")),
                 norm(r.get("parameter_name")),
                 norm(r.get("dtc_code")))
            out[k + (seen[k],)] = r
            seen[k] += 1
        return out

    def _github_vs_site(self, records):
        gh = defaultdict(list)
        site = defaultdict(list)
        for r in records:
            if r.get("source_tier") == "Gold/GitHub" and r.get("file_name"):
                gh[r["file_name"]].append(r)
            elif (r.get("source_tier") == "Primary-SICAR-Site" and
                  r.get("source_kind") == "site_json_file"):
                site[r.get("file_name", "")].append(r)

        out = []
        for fn, grecs in sorted(gh.items()):
            srecs = site.get(fn)
            if not srecs:
                self.stats["github_file_not_on_site:" + fn] += 1
                continue

            G, S = self._keyed(grecs), self._keyed(srecs)
            g0, s0 = grecs[0], srecs[0]

            # مقایسه فیلدهای اتصال
            for f in CONN_FIELDS:
                if norm(g0.get(f)) != norm(s0.get(f)):
                    out.append(self._mk("github_vs_site_file", f, s0, g0, fn, "", "(اتصال)"))

            # مقایسه رکوردبه‌رکورد
            for k in sorted(set(G) | set(S), key=str):
                g, s = G.get(k), S.get(k)
                if g and s:
                    for f in COMPARE_FIELDS:
                        a, b = s.get(f, ""), g.get(f, "")
                        same = (norm_formula(a) == norm_formula(b) if f == "formula"
                                else norm(a) == norm(b))
                        if not same:
                            out.append(self._mk(
                                "github_vs_site_file", f, s, g, fn,
                                s.get("request_command"),
                                s.get("parameter_name") or s.get("dtc_code")))
                else:
                    r = g or s
                    out.append(self._mk(
                        "github_vs_site_file", "presence",
                        s if s else {}, g if g else {}, fn,
                        r.get("request_command"),
                        r.get("parameter_name") or r.get("dtc_code"),
                        presence=True))
        return out

    def _mk(self, ctype, field, s, g, fn, cmd, param, presence=False):
        sv = _val(s, field, ("موجود" if s else "ناموجود") if presence else None)
        gv = _val(g, field, ("موجود" if g else "ناموجود") if presence else None)
        sv["source_label"], gv["source_label"] = "فایل JSON سایت سیکار", "فایل JSON مخزن GitHub"
        if not s:
            sv.update(source_tier="Primary-SICAR-Site", tier_weight=0.9, file_name=fn)
        if not g:
            gv.update(source_tier="Gold/GitHub", tier_weight=0.8, file_name=fn)
        r = s or g
        return {
            "conflict_id": _cid(ctype, fn, field, cmd, param, sv["value"], gv["value"]),
            "conflict_type": ctype,
            "field": field,
            "severity": "medium" if presence else "high",
            "go_status": "",
            "vehicle": r.get("vehicle_model", ""),
            "ecu": r.get("ecu_model", ""),
            "parameter_name": param or "",
            "request_command": cmd or "",
            "values": [sv, gv],
            "note": ("رکورد فقط در یکی از دو فایل هم‌نام وجود دارد"
                     if presence else ""),
        }

    # ------------------------------------------------------------------ ۳: تعارض بین دو فایل مختلف سایت برای همان خودرو+ECU
    def _cross_file(self, records):
        groups = defaultdict(list)
        dup_same_file = Counter()

        for r in records:
            if r.get("source_kind") != "site_json_file":
                continue
            if not (r.get("vehicle_model") and r.get("ecu_model")):
                continue
            k = (r["vehicle_model"], r["ecu_model"],
                 norm_cmd(r.get("request_command")),
                 norm_cmd(r.get("response_header")),
                 norm(r.get("parameter_name")))
            groups[k].append(r)

        out = []
        for k, rs in groups.items():
            files = defaultdict(list)
            for r in rs:
                files[r.get("file_name", "unknown")].append(r)

            for fn, lst in files.items():
                if len(lst) > 1:
                    dup_same_file[fn] += len(lst) - 1

            if len(files) < 2:
                continue

            for f in ("formula", "unit", "min_value", "max_value"):
                vals = {}
                for fn, lst in files.items():
                    v = norm_formula(lst[0].get(f)) if f == "formula" else norm(lst[0].get(f))
                    vals.setdefault(v, lst[0])
                if len(vals) > 1:
                    vs = [dict(_val(r, f), source_label=f"فایل JSON سایت: {r.get('file_name')}")
                          for r in vals.values()]
                    out.append({
                        "conflict_id": _cid("xf", *k, f),
                        "conflict_type": "cross_file_same_vehicle_ecu",
                        "field": f,
                        "severity": "medium",
                        "go_status": "",
                        "vehicle": k[0],
                        "ecu": k[1],
                        "parameter_name": rs[0].get("parameter_name", ""),
                        "request_command": rs[0].get("request_command", ""),
                        "values": vs,
                        "note": "فایل‌ها: " + " | ".join(sorted(files.keys())),
                    })

        self.stats["same_file_duplicate_parameter_names"] = sum(dup_same_file.values())
        return out

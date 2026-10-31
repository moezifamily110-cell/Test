# -*- coding: utf-8 -*-
"""
MultiTierMiner v2 - استخراج داده از منابع چندلایه (Bronze / Silver / Gold)

اصول طراحی:
  * هیچ داده‌ای حدس زده نمی‌شود. هر فیلد یا مستقیماً از منبع خوانده می‌شود، یا خالی می‌ماند.
  * فرمول و دستور اصلی بدون تغییر نگه داشته می‌شوند.
  * داده منابع مختلف با هم ادغام نمی‌شوند؛ فقط در فیلد cross_check ثبت می‌شود.
  * فایل خام هر منبع با پسوند تاریخ و هش SHA-256 بایگانی می‌شود.
  * Resume: اگر فایل خام امروز موجود باشد دوباره دانلود نمی‌شود.
  * Graceful degradation: خطای هر منبع ثبت می‌شود و بقیه ادامه می‌دهند.
  * منابع Silver فقط «سرنخ» (lead) ثبت می‌کنند تا بعداً تأیید شوند.
"""
from __future__ import annotations

import ast
import csv
import hashlib
import io
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import requests

try:
    from bs4 import BeautifulSoup, NavigableString
except ImportError as _e:
    raise ImportError("pip install beautifulsoup4 لازم است") from _e

try:
    import lxml  # noqa: F401
    _HTML_PARSER = "lxml"
except ImportError:
    _HTML_PARSER = "html.parser"

USER_AGENT = "ECU-Reference-Miner/2.0 (engineering research; python-requests)"

TIER_BASE_SCORE = {"Diamond": 10, "Gold": 7, "Silver": 3, "Bronze": 5}

RECORD_FIELDS = [
    "vehicle_brand", "vehicle_model", "vehicle_type", "vehicle_year",
    "manufacturer", "ecu_manufacturer", "ecu_model",
    "protocol", "baud_rate", "init_commands",
    "parameter_name", "request_command", "response_header", "formula",
    "byte_address", "bit_address", "coefficient", "offset", "unit",
    "min_value", "max_value", "is_dtc", "dtc_code", "dtc_description",
    "source_url", "page_title", "confidence_score", "source_tier",
    "source_id", "retrieved_at", "source_sha256",
    "formula_expr", "verification_status", "confidence_pct", "notes",
]


def _empty_record() -> Dict:
    rec = {k: "" for k in RECORD_FIELDS}
    rec["is_dtc"] = False
    return rec


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# تبدیل LaTeX ویکی‌پدیا به عبارت قابل ارزیابی
# ---------------------------------------------------------------------------
_ALLOWED_AST = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name, ast.Load,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.USub, ast.UAdd,
)
_BYTE_VARS = tuple("ABCDEFGH")


def _match_brace(s: str, i: int) -> int:
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return j
    return -1


def _strip_displaystyle(tex: str) -> str:
    tex = tex.strip()
    m = re.match(r"^\{\\displaystyle\s*(.*)\}$", tex, re.S)
    return m.group(1).strip() if m else tex


def latex_to_expr(tex: str) -> Optional[str]:
    """تبدیل محافظه‌کارانه LaTeX به عبارت پایتون. اگر هر نماد ناشناخته باشد None."""
    s = _strip_displaystyle(tex)
    s = s.replace("−", "-").replace("\\left", "").replace("\\right", "")
    s = s.replace("\\cdot", "*").replace("\\times", "*").replace("\\,", "").replace("\\ ", "")

    for _ in range(50):
        m = re.search(r"\\[dt]?frac\s*\{", s)
        if not m:
            break
        a0 = m.end() - 1
        a1 = _match_brace(s, a0)
        if a1 < 0 or a1 + 1 >= len(s) or s[a1 + 1] != "{":
            return None
        b0 = a1 + 1
        b1 = _match_brace(s, b0)
        if b1 < 0:
            return None
        s = (s[:m.start()] + "((" + s[a0 + 1:a1] + ")/(" + s[b0 + 1:b1] + "))" + s[b1 + 1:])

    if "frac" in s:
        return None

    s = re.sub(r"\^\s*\{([^{}]*)\}", r"**(\1)", s)
    s = re.sub(r"\^\s*(\d+)", r"**\1", s)
    s = s.replace("{", "(").replace("}", ")").replace(" ", "")

    if re.search(r"[^0-9A-H+\-*/().]", s):
        return None

    s = re.sub(r"(?<=[0-9A-H)])(?=[A-H(])", "*", s)

    try:
        tree = ast.parse(s, mode="eval")
    except SyntaxError:
        return None

    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_AST):
            return None
        if isinstance(node, ast.Name) and node.id not in _BYTE_VARS:
            return None
    return s


# ---------------------------------------------------------------------------
# جدول HTML با پشتیبانی rowspan / colspan
# ---------------------------------------------------------------------------
def table_to_grid(table) -> List[List]:
    grid: List[List] = []
    pending: Dict[int, Tuple[object, int]] = {}
    for tr in table.find_all("tr"):
        row: List = []
        cells = tr.find_all(["th", "td"], recursive=False)
        col = 0
        ci = 0
        while ci < len(cells) or col in pending:
            if col in pending:
                cell, left = pending[col]
                row.append(cell)
                if left <= 1:
                    del pending[col]
                else:
                    pending[col] = (cell, left - 1)
                col += 1
                continue
            cell = cells[ci]
            ci += 1
            rs = int(re.sub(r"\D", "", cell.get("rowspan", "1") or "1") or 1)
            cs = int(re.sub(r"\D", "", cell.get("colspan", "1") or "1") or 1)
            for _ in range(cs):
                row.append(cell)
                if rs > 1:
                    pending[col] = (cell, rs - 1)
                col += 1
        grid.append(row)
    return grid


def cell_text_with_latex(cell) -> Tuple[str, List[str]]:
    cell = BeautifulSoup(str(cell), _HTML_PARSER)
    for sup in cell.select("sup.reference"):
        sup.decompose()
    latex: List[str] = []
    for m in cell.select("span.mwe-math-element"):
        ann = m.find("annotation", attrs={"encoding": "application/x-tex"})
        tex = _strip_displaystyle(ann.get_text()) if ann else ""
        latex.append(tex)
        m.replace_with(NavigableString(f" {tex} " if tex else " "))
    for br in cell.find_all("br"):
        br.replace_with(NavigableString(" ; "))
    text = re.sub(r"\s+", " ", cell.get_text(" ")).strip()
    return text, latex


# ---------------------------------------------------------------------------
class MultiTierMiner:
    def __init__(self,
                 output_dir: str = "output/multi_tier",
                 refresh: bool = False,
                 timeout: int = 20,
                 retries: int = 3,
                 max_workers: int = 4,
                 silver_keywords: Optional[List[str]] = None):
        self.out = Path(output_dir)
        self.raw_dir = self.out / "raw"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.refresh = refresh
        self.timeout = timeout
        self.retries = retries
        self.max_workers = max_workers
        self.silver_keywords = silver_keywords or [
            "SICAR ECU", "ایران خودرو ECU", "سایپا ECU", "K-Line OBD Iran",
        ]
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT, "Accept-Encoding": "gzip",
        })
        self.today = _now_utc().strftime("%Y%m%d")
        self.manifest_path = self.out / "manifest.json"
        self.manifest = self._load_json(self.manifest_path, {})
        self.records: List[Dict] = []
        self.leads: List[Dict] = []
        self.source_status: List[Dict] = []

    @staticmethod
    def _load_json(path: Path, default):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    @staticmethod
    def _atomic_write(path: Path, data: bytes):
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)

    def _status(self, source_id: str, tier: str, status: str,
                url: str = "", detail: str = "", count: int = 0):
        self.source_status.append({
            "source_id": source_id, "tier": tier, "status": status,
            "url": url, "detail": detail, "records": count,
            "checked_at": _now_utc().isoformat(timespec="seconds"),
        })
        print(f"[{tier}] {source_id}: {status} {detail} ({count})")

    def _http_get(self, url: str, params: Optional[Dict] = None,
                  headers: Optional[Dict] = None) -> Tuple[Optional[int], Optional[bytes], str]:
        last_err = ""
        for attempt in range(1, self.retries + 1):
            try:
                r = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
                if r.status_code == 200:
                    return 200, r.content, ""
                last_err = f"HTTP {r.status_code}"
                if r.status_code not in (429, 500, 502, 503, 504):
                    return r.status_code, None, last_err
                wait = 2 ** attempt
            except requests.RequestException as e:
                last_err = f"{type(e).__name__}: {e}"
                wait = 2 ** attempt
            if attempt < self.retries:
                time.sleep(min(wait, 10))
        return None, None, last_err

    def _fetch_raw(self, source_id: str, url: str, ext: str,
                   params: Optional[Dict] = None,
                   meta: Optional[Dict] = None) -> Tuple[Optional[bytes], Dict]:
        sdir = self.raw_dir / source_id
        sdir.mkdir(parents=True, exist_ok=True)
        today_file = sdir / f"{source_id}__{self.today}.{ext}"
        entry = self.manifest.setdefault(source_id, {"url": url, "history": []})

        if today_file.exists() and not self.refresh:
            data = today_file.read_bytes()
            sha = hashlib.sha256(data).hexdigest()
            return data, {
                "mode": "cache_today", "file": str(today_file),
                "sha256": sha,
                "retrieved_at": self._history_date(entry, today_file),
            }

        code, data, err = self._http_get(url, params=params)
        if data is not None:
            sha = hashlib.sha256(data).hexdigest()
            prev = entry["history"][-1] if entry["history"] else None
            self._atomic_write(today_file, data)
            changed = bool(prev and prev.get("sha256") != sha)
            entry["history"].append({
                "date": self.today, "url": url,
                "retrieved_at": _now_utc().isoformat(timespec="seconds"),
                "sha256": sha, "size": len(data), "file": str(today_file),
                "changed_from_previous": changed, **(meta or {}),
            })
            entry["url"] = url
            return data, {
                "mode": "downloaded", "file": str(today_file),
                "sha256": sha,
                "retrieved_at": entry["history"][-1]["retrieved_at"],
            }

        older = sorted(sdir.glob(f"{source_id}__*.{ext}"))
        if older:
            d = older[-1].read_bytes()
            return d, {
                "mode": "stale_cache", "file": str(older[-1]),
                "error": err, "sha256": hashlib.sha256(d).hexdigest(),
                "retrieved_at": self._history_date(entry, older[-1]),
            }
        return None, {"mode": "failed", "error": err, "http_status": code}

    @staticmethod
    def _history_date(entry: Dict, file: Path) -> str:
        for h in reversed(entry.get("history", [])):
            if h.get("file") == str(file):
                return h.get("retrieved_at", "")
        return ""

    # ------------------------------------------------------------- pipeline
    def mine_all_tiers(self) -> List[Dict]:
        t0 = time.time()
        self.records, self.leads = [], []

        miners = [
            ("bronze_wikipedia", self._mine_wikipedia_obd, "Bronze"),
            ("bronze_iso_sae", self._mine_standards, "Bronze"),
            ("gold_python_obd", self._mine_python_obd, "Gold"),
            ("gold_dtc_repo", self._mine_dtc_repo, "Gold"),
            ("silver_reddit", self._mine_reddit_leads, "Silver"),
            ("silver_kaggle", self._mine_kaggle_leads, "Silver"),
        ]

        with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            futures = {ex.submit(fn): (sid, tier) for sid, fn, tier in miners}
            for fut in as_completed(futures):
                sid, tier = futures[fut]
                try:
                    recs = fut.result()
                    if recs:
                        if tier == "Silver":
                            self.leads.extend(recs)
                        else:
                            self.records.extend(recs)
                except Exception as e:
                    self._status(sid, tier, f"error: {e}")

        # ذخیره manifest
        self.manifest_path.write_text(
            json.dumps(self.manifest, ensure_ascii=False, indent=1), encoding="utf-8")

        # ذخیره سرنخ‌های Silver
        if self.leads:
            (self.out / "silver_leads.json").write_text(
                json.dumps(self.leads, ensure_ascii=False, indent=1), encoding="utf-8")

        print(f"[*] Multi-tier: {len(self.records)} records, "
              f"{len(self.leads)} leads in {time.time() - t0:.1f}s")
        return self.records

    # ------------------------------------------------------------- Bronze: Wikipedia OBD-II PIDs
    def _mine_wikipedia_obd(self) -> List[Dict]:
        sid = "bronze_wikipedia_obd"
        url = "https://en.wikipedia.org/wiki/OBD-II_PIDs"
        data, info = self._fetch_raw(sid, url, "html")
        if data is None:
            self._status(sid, "Bronze", info.get("mode", "failed"), url,
                         info.get("error", ""))
            return []

        records: List[Dict] = []
        try:
            soup = BeautifulSoup(data, _HTML_PARSER)
            for ti, table in enumerate(soup.select("table.wikitable")):
                grid = table_to_grid(table)
                if len(grid) < 2:
                    continue
                header = [cell_text_with_latex(c)[0] for c in grid[0]]
                hdr_lower = [h.lower() for h in header]

                # پیدا کردن ستون‌ها
                def col(*keys):
                    for k in keys:
                        for i, h in enumerate(hdr_lower):
                            if k in h:
                                return i
                    return -1

                pid_col = col("pid", "service")
                if pid_col < 0:
                    continue

                for ri, row in enumerate(grid[1:], 2):
                    cells = [cell_text_with_latex(c) for c in row]
                    texts = [c[0] for c in cells]
                    latexes = [c[1] for c in cells]

                    if len(texts) <= pid_col:
                        continue

                    pid = texts[pid_col].strip()
                    if not pid or pid.startswith("—") or pid == "PID":
                        continue

                    rec = _empty_record()
                    rec["source_id"] = sid
                    rec["source_tier"] = "Bronze"
                    rec["source_url"] = url
                    rec["page_title"] = f"Wikipedia OBD-II PIDs - جدول {ti + 1}"
                    rec["retrieved_at"] = info.get("retrieved_at", "")
                    rec["source_sha256"] = info.get("sha256", "")
                    rec["confidence_score"] = TIER_BASE_SCORE["Bronze"]
                    rec["confidence_pct"] = 40
                    rec["verification_status"] = "bronze_general_standard"
                    rec["notes"] = "استاندارد عمومی OBD-II؛ مختص خودروهای ایرانی نیست"

                    # استخراج PID و سرویس
                    rec["request_command"] = pid

                    # پارامتر
                    desc_col = col("description", "parameter", "name")
                    if desc_col >= 0 and desc_col < len(texts):
                        rec["parameter_name"] = texts[desc_col]

                    # فرمول (LaTeX)
                    formula_col = col("formula", "equation", "conversion")
                    if formula_col >= 0 and formula_col < len(latexes) and latexes[formula_col]:
                        tex = latexes[formula_col][0] if latexes[formula_col] else ""
                        rec["formula"] = tex
                        expr = latex_to_expr(tex)
                        if expr:
                            rec["formula_expr"] = expr
                            # تحلیل ساده
                            from modules.sicar_formula import analyze_formula
                            # تبدیل به فرمت B[n] برای تحلیل
                            bfmt = re.sub(r"\b([A-H])\b",
                                          lambda m: f"B[{ord(m.group(1)) - ord('A') + 1}]",
                                          expr)
                            a = analyze_formula(bfmt)
                            rec["byte_address"] = a["byte_address"]
                            rec["coefficient"] = a["coefficient"]
                            rec["offset"] = a["offset"]

                    # واحد
                    unit_col = col("unit")
                    if unit_col >= 0 and unit_col < len(texts):
                        rec["unit"] = texts[unit_col]

                    # حداقل/حداکثر
                    min_col = col("min ")
                    max_col = col("max ")
                    if min_col >= 0 and min_col < len(texts):
                        rec["min_value"] = texts[min_col]
                    if max_col >= 0 and max_col < len(texts):
                        rec["max_value"] = texts[max_col]

                    rec["record_id"] = hashlib.sha1(
                        f"wiki|{ti}|{ri}|{pid}".encode()
                    ).hexdigest()[:16]
                    records.append(rec)

            self._status(sid, "Bronze", info.get("mode", "ok"), url,
                         count=len(records))
        except Exception as e:
            self._status(sid, "Bronze", f"parse_error: {e}", url)
        return records

    # ------------------------------------------------------------- Bronze: ISO/SAE (فقط سرنخ)
    def _mine_standards(self) -> List[Dict]:
        sid = "bronze_iso_sae"
        standards = [
            ("ISO 15765", "CAN bus for road vehicles",
             "https://en.wikipedia.org/wiki/ISO_15765"),
            ("ISO 14230", "KWP2000",
             "https://en.wikipedia.org/wiki/ISO_14230"),
            ("ISO 9141", "K-Line",
             "https://en.wikipedia.org/wiki/ISO_9141"),
            ("SAE J1979", "OBD-II diagnostic services",
             "https://en.wikipedia.org/wiki/OBD-II_PIDs"),
        ]
        records = []
        for name, desc, url in standards:
            rec = _empty_record()
            rec["source_id"] = sid
            rec["source_tier"] = "Bronze"
            rec["source_url"] = url
            rec["page_title"] = f"{name}: {desc}"
            rec["parameter_name"] = name
            rec["notes"] = f"استاندارد مرجع: {desc}"
            rec["verification_status"] = "standard_reference_only"
            rec["confidence_score"] = 5
            rec["confidence_pct"] = 50
            rec["record_id"] = hashlib.sha1(f"std|{name}".encode()).hexdigest()[:16]
            records.append(rec)
        self._status(sid, "Bronze", "ok", count=len(records))
        return records

    # ------------------------------------------------------------- Gold: python-OBD
    def _mine_python_obd(self) -> List[Dict]:
        sid = "gold_python_obd"
        url = ("https://raw.githubusercontent.com/brendan-w/python-OBD/"
               "master/obd/commands.py")
        data, info = self._fetch_raw(sid, url, "py")
        if data is None:
            self._status(sid, "Gold", info.get("mode", "failed"), url,
                         info.get("error", ""))
            return []

        records: List[Dict] = []
        try:
            text = data.decode("utf-8", errors="replace")
            # پیدا کردن تعاریف PID
            # الگو: OBDCommand("NAME", "Desc", b"010C", 4, _formula, unit)
            pattern = re.compile(
                r'OBDCommand\s*\(\s*"([^"]+)"\s*,\s*"([^"]*)"\s*,\s*'
                r'b?"([0-9A-Fa-f]+)"\s*,\s*(\d+)\s*,\s*([^,]+)\s*,\s*"([^"]*)"',
            )
            for mi, m in enumerate(pattern.finditer(text)):
                name, desc, pid, nbytes, formula, unit = m.groups()
                rec = _empty_record()
                rec["source_id"] = sid
                rec["source_tier"] = "Gold"
                rec["source_url"] = url
                rec["page_title"] = "python-OBD commands.py"
                rec["retrieved_at"] = info.get("retrieved_at", "")
                rec["source_sha256"] = info.get("sha256", "")
                rec["parameter_name"] = desc or name
                rec["request_command"] = pid.upper()
                rec["formula"] = formula.strip()
                rec["unit"] = unit
                rec["confidence_score"] = TIER_BASE_SCORE["Gold"]
                rec["confidence_pct"] = 70
                rec["verification_status"] = "gold_open_source_reference"
                rec["notes"] = "کتابخانه python-OBD؛ استاندارد عمومی OBD-II"
                rec["record_id"] = hashlib.sha1(
                    f"pyobd|{name}|{pid}".encode()
                ).hexdigest()[:16]
                records.append(rec)

            self._status(sid, "Gold", info.get("mode", "ok"), url,
                         count=len(records))
        except Exception as e:
            self._status(sid, "Gold", f"parse_error: {e}", url)
        return records

    # ------------------------------------------------------------- Gold: DTC repository
    def _mine_dtc_repo(self) -> List[Dict]:
        sid = "gold_dtc_repo"
        url = ("https://raw.githubusercontent.com/mytrile/"
               "obd-trouble-codes/master/obd-trouble-codes.csv")
        data, info = self._fetch_raw(sid, url, "csv")
        if data is None:
            self._status(sid, "Gold", info.get("mode", "failed"), url,
                         info.get("error", ""))
            return []

        records: List[Dict] = []
        try:
            reader = csv.reader(io.StringIO(data.decode("utf-8-sig")))
            header = next(reader, None)
            for ri, row in enumerate(reader):
                if len(row) < 2:
                    continue
                code, desc = row[0].strip(), row[1].strip()
                if not code or not re.match(r"^[PUCB]\d{4}$", code):
                    continue
                rec = _empty_record()
                rec["source_id"] = sid
                rec["source_tier"] = "Gold"
                rec["source_url"] = url
                rec["page_title"] = "obd-trouble-codes.csv"
                rec["retrieved_at"] = info.get("retrieved_at", "")
                rec["source_sha256"] = info.get("sha256", "")
                rec["is_dtc"] = True
                rec["dtc_code"] = code
                rec["dtc_description_en"] = desc
                rec["dtc_description"] = ""
                rec["confidence_score"] = TIER_BASE_SCORE["Gold"]
                rec["confidence_pct"] = 70
                rec["verification_status"] = "gold_dtc_database"
                rec["notes"] = "پایگاه داده عمومی DTC؛ توضیح فارسی موجود نیست"
                rec["record_id"] = hashlib.sha1(
                    f"dtc|{code}".encode()
                ).hexdigest()[:16]
                records.append(rec)

            self._status(sid, "Gold", info.get("mode", "ok"), url,
                         count=len(records))
        except Exception as e:
            self._status(sid, "Gold", f"parse_error: {e}", url)
        return records

    # ------------------------------------------------------------- Silver: Reddit (سرنخ)
    def _mine_reddit_leads(self) -> List[Dict]:
        sid = "silver_reddit"
        leads: List[Dict] = []
        for kw in self.silver_keywords[:2]:
            url = f"https://www.reddit.com/search.json?q={requests.utils.quote(kw)}&limit=10"
            data, info = self._fetch_raw(
                f"{sid}_{kw[:10]}", url, "json",
                headers={"Accept": "application/json"})
            if data is None:
                continue
            try:
                j = json.loads(data)
                for post in j.get("data", {}).get("children", []):
                    d = post.get("data", {})
                    leads.append({
                        "source_id": sid,
                        "source_tier": "Silver",
                        "source_url": "https://reddit.com" + d.get("permalink", ""),
                        "title": d.get("title", ""),
                        "keyword": kw,
                        "score": d.get("score", 0),
                        "created_utc": d.get("created_utc", 0),
                        "retrieved_at": info.get("retrieved_at", ""),
                        "verification_status": "silver_lead_requires_manual_verification",
                        "note": "بحث کاربران؛ نیاز به تأیید دستی دارد",
                    })
            except Exception:
                pass
        self._status(sid, "Silver", "ok", count=len(leads))
        return leads

    # ------------------------------------------------------------- Silver: Kaggle (سرنخ)
    def _mine_kaggle_leads(self) -> List[Dict]:
        sid = "silver_kaggle"
        url = "https://www.kaggle.com/datasets?search=OBD+vehicle"
        data, info = self._fetch_raw(sid, url, "html")
        leads: List[Dict] = []
        if data is not None:
            try:
                soup = BeautifulSoup(data, _HTML_PARSER)
                for li in soup.select("li")[:10]:
                    txt = li.get_text(strip=True)
                    if "OBD" in txt or "vehicle" in txt.lower():
                        leads.append({
                            "source_id": sid,
                            "source_tier": "Silver",
                            "source_url": url,
                            "title": txt[:200],
                            "verification_status": "silver_lead_requires_manual_verification",
                            "retrieved_at": info.get("retrieved_at", ""),
                            "note": "مجموعه داده Kaggle؛ نیاز به بررسی و تأیید دستی دارد",
                        })
            except Exception:
                pass
        self._status(sid, "Silver", info.get("mode", "checked"), count=len(leads))
        return leads

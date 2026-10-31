# -*- coding: utf-8 -*-
"""استخراج سطح Gold از مخزن Sisoog/sicar-diag.

  * فایل‌های JSON بایت‌به‌بایت ذخیره می‌شوند؛ هیچ بازنویسی json.dump انجام نمی‌شود.
  * طرح‌واره خواندن دقیقاً همان parseSicarJSON خزنده Go است.
  * برچسب خودرو/ECU فقط از رکوردهای سایت با همان نام فایل گرفته می‌شود (بدون نگاشت حدسی).
  * حالت آفلاین: فایل‌های github_json خروجی Go استفاده می‌شوند.
"""
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import requests

from modules.sicar_formula import (
    CTYPE_NAMES, analyze_formula, bit_lsb_index,
    parse_ctype, protocol_from_baudtype,
)

REPO = "Sisoog/sicar-diag"
API = f"https://api.github.com/repos/{REPO}"
RAW = f"https://raw.githubusercontent.com/{REPO}/main/jsons/"
ALLOWED_HOSTS = ("github.com", "raw.githubusercontent.com", "api.github.com")


def _s(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    return str(v)


def _cmd_list(items) -> str:
    parts = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        s = _s(it.get("cmdtext"))
        if _s(it.get("cresp")):
            s += " → " + _s(it.get("cresp"))
        if _s(it.get("ctimewait")):
            s += " (" + _s(it.get("ctimewait")) + "ms)"
        parts.append(s)
    return " ; ".join(parts)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class GitHubMiner:
    def __init__(self, output_dir: str, go_output_dir: Optional[str] = None,
                 offline: bool = False, site_records: Optional[List[Dict]] = None,
                 timeout: int = 20):
        self.out = Path(output_dir).resolve() / "github_json"
        self.out.mkdir(parents=True, exist_ok=True)
        self.go_dir = Path(go_output_dir).resolve() if go_output_dir else None
        self.offline = offline
        self.timeout = timeout
        self.status: List[Dict] = []
        self.files: List[Dict] = []
        self.headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "ECU-Miner-Industrial/6.0",
        }
        if os.getenv("GITHUB_TOKEN"):
            self.headers["Authorization"] = f"token {os.getenv('GITHUB_TOKEN')}"

        # برچسب‌ها از سایت (فقط فایل هم‌نام)
        self.labels: Dict[str, Dict] = {}
        for r in site_records or []:
            fn = r.get("file_name")
            if fn and str(r.get("record_type", "")).startswith("json") and fn not in self.labels:
                self.labels[fn] = {k: r.get(k, "") for k in (
                    "vehicle_model", "manufacturer", "ecu_manufacturer", "ecu_model",
                    "vehicle_label_raw", "ecu_page_url", "vehicle_brand",
                    "vehicle_type", "vehicle_year",
                )}

    def mine_all(self) -> List[Dict]:
        recs = self._mine_json_files()
        recs += self._mine_issues()
        recs += self._mine_pulls()
        return recs

    def _get(self, url: str):
        if not url.startswith("https://") or url.split("/")[2] not in ALLOWED_HOSTS:
            raise ValueError(f"host not allowed in URL: {url}")
        return requests.get(url, headers=self.headers, timeout=self.timeout)

    def _list_remote(self) -> List[str]:
        r = self._get(f"{API}/contents/jsons")
        r.raise_for_status()
        return [x["name"] for x in r.json()
                if x.get("name", "").lower().endswith(".json")]

    def _mine_json_files(self) -> List[Dict]:
        blobs: Dict[str, bytes] = {}
        origin = "live"

        if not self.offline:
            try:
                names = self._list_remote()
                for name in names:
                    r = self._get(RAW + name)
                    if r.status_code == 200:
                        blobs[name] = r.content
                    else:
                        self.status.append({
                            "source": RAW + name,
                            "status": f"http_{r.status_code}",
                        })
            except Exception as e:
                self.status.append({"source": API, "status": f"error: {e}"})
                print(f"[!] دسترسی زنده GitHub ناموفق ({e}); استفاده از نسخه ذخیره‌شده Go")

        if not blobs and self.go_dir:
            gh_cache = self.go_dir / "files" / "github_json"
            if gh_cache.is_dir():
                origin = "go_cache"
                for p in sorted(gh_cache.glob("*.json")):
                    blobs[p.name] = p.read_bytes()

        records = []
        for name, data in sorted(blobs.items()):
            dest = self.out / name
            dest.write_bytes(data)  # بایت‌به‌بایت
            sha = hashlib.sha256(data).hexdigest()
            self.files.append({
                "name": name, "url": RAW + name, "sha256": sha,
                "size": len(data), "origin": origin, "local_path": str(dest),
            })

            try:
                root = json.loads(data.decode("utf-8-sig"))
            except Exception as e:
                self.status.append({
                    "source": RAW + name, "status": f"invalid_json: {e}",
                })
                continue

            records += self._parse(root, name, sha)

        self.status.append({
            "source": f"github:{REPO}/jsons", "status": "ok",
            "files": len(blobs), "origin": origin, "records": len(records),
        })
        return records

    def _parse(self, root: Dict, name: str, sha: str) -> List[Dict]:
        conn = root.get("CONNECTION") or {}
        specs = conn.get("SPECS") or {}
        init = conn.get("INITCMDLIST") or []
        wake = conn.get("WAKEUPCMDLIST") or []
        reinit = conn.get("REINITCMDLIST") or []

        proto, baud, pnote = "", "", ""
        if "baudtype" in specs:
            proto, baud, pnote = protocol_from_baudtype(specs.get("baudtype"))

        lab = self.labels.get(name)
        vstatus = ("vehicle_label_from_site_page_of_same_filename"
                   if lab else "vehicle_unknown_filename_only")
        lab = lab or {}

        base = {
            "vehicle_brand": lab.get("vehicle_brand", ""),
            "vehicle_model": lab.get("vehicle_model", ""),
            "vehicle_type": lab.get("vehicle_type", ""),
            "vehicle_year": lab.get("vehicle_year", ""),
            "manufacturer": lab.get("manufacturer", ""),
            "ecu_manufacturer": lab.get("ecu_manufacturer", ""),
            "ecu_model": lab.get("ecu_model", ""),
            "vehicle_label_raw": lab.get("vehicle_label_raw", "") or name,
            "ecu_page_url": lab.get("ecu_page_url", ""),
            "protocol": proto, "baud_rate": baud,
            "init_commands": _cmd_list(init),
            "wakeup_commands": _cmd_list(wake),
            "reinit_commands": _cmd_list(reinit),
            "ecu_address": _s(specs.get("d")),
            "connection_specs": json.dumps(specs, ensure_ascii=False, sort_keys=True),
            "file_name": name, "file_sha256": sha,
            "source_url": RAW + name,
            "source_kind": "github_json_file",
            "source_tier": "Gold/GitHub",
            "confidence_score": 8,
            "confidence_pct": 80,
            "verification_status": vstatus,
            "retrieved_at": _now(),
        }

        out = []

        def mk(section, key, i, it, rtype):
            f = _s(it.get("cformula"))
            a = analyze_formula(f)
            ct = _s(it.get("ctype"))
            r = dict(base)
            r.update(a)
            ct_name = CTYPE_NAMES.get(parse_ctype(ct) or -1, "")
            r.update({
                "record_id": hashlib.sha1(
                    "|".join([name, sha, section, key, str(i)]).encode()
                ).hexdigest()[:16],
                "record_type": rtype,
                "command_type": (ct + " " + ct_name).strip() if ct else "",
                "parameter_name": _s(it.get("cdesc")),
                "request_command": _s(it.get("cmdtext")),
                "response_header": _s(it.get("cresp")),
                "formula": f,
                "unit": _s(it.get("unit_sim")),
                "min_value": _s(it.get("cmin")),
                "max_value": _s(it.get("cmax")),
                "timeout_ms": _s(it.get("ctimewait")),
                "is_dtc": rtype == "json_dtc",
                "dtc_code": "",
                "dtc_description": "",
                "dtc_description_en": "",
                "sicar_bit_lsb_index": bit_lsb_index(f),
                "github_section": section,
                "github_group": key,
                "notes": "؛ ".join(x for x in [
                    pnote,
                    "cheader=" + _s(it.get("cheader")),
                    "cunit=" + _s(it.get("cunit")),
                    "decimalp=" + _s(it.get("decimalp")),
                ] if x),
            })
            return r

        for sec, items, rt in (("INIT", init, "json_init"),
                                ("WAKEUP", wake, "json_wakeup"),
                                ("REINIT", reinit, "json_reinit")):
            for i, it in enumerate(items):
                if isinstance(it, dict):
                    out.append(mk(sec, "", i, it, rt))

        for sec in ("PARAMETER", "DTC"):
            m = root.get(sec) or {}
            for k in sorted(m.keys()):
                items = m[k] if isinstance(m[k], list) else []
                for i, it in enumerate(items):
                    if isinstance(it, dict):
                        r = mk(sec, k, i, it,
                               "json_parameter" if sec == "PARAMETER" else "json_dtc")
                        if sec == "DTC":
                            r["dtc_code"] = k
                            r["dtc_description"] = _s(it.get("cdesc"))
                        out.append(r)

        return out

    def _mine_issues(self) -> List[Dict]:
        if self.offline:
            self.status.append({
                "source": f"{API}/issues", "status": "skipped_offline",
            })
            return []

        recs = []
        try:
            r = self._get(f"{API}/issues?state=all&per_page=100")
            if r.status_code != 200:
                self.status.append({
                    "source": f"{API}/issues", "status": f"http_{r.status_code}",
                })
                return recs

            raw = self.out.parent / "github_issues_raw.json"
            raw.write_bytes(r.content)

            for issue in r.json():
                title, body = issue.get("title") or "", issue.get("body") or ""
                codes = sorted(set(re.findall(
                    r"\b[PUCB][0-9A-F]\d{3}\b", title + " " + body)))
                for code in codes:
                    recs.append({
                        "record_id": hashlib.sha1(
                            f"{issue.get('html_url')}|{code}".encode()
                        ).hexdigest()[:16],
                        "record_type": "github_issue_dtc_mention",
                        "parameter_name": title,
                        "is_dtc": True,
                        "dtc_code": code,
                        "dtc_description": "",
                        "dtc_description_en": "",
                        "notes": "کد DTC فقط در متن Issue ذکر شده؛ توضیح رسمی ندارد",
                        "source_url": issue.get("html_url", ""),
                        "source_kind": "github_issue",
                        "source_tier": "Gold/Issues",
                        "confidence_score": 5,
                        "confidence_pct": 50,
                        "verification_status": "unverified_mention",
                        "retrieved_at": _now(),
                        "vehicle_label_raw": "",
                        "manufacturer": "",
                        "ecu_model": "",
                    })

            self.status.append({
                "source": f"{API}/issues", "status": "ok",
                "issues": len(r.json()), "records": len(recs),
            })
        except Exception as e:
            self.status.append({
                "source": f"{API}/issues", "status": f"error: {e}",
            })
        return recs

    def _mine_pulls(self) -> List[Dict]:
        """استخراج سرنخ از Pull Requests (سطح Gold/Issues)."""
        if self.offline:
            return []
        recs = []
        try:
            r = self._get(f"{API}/pulls?state=all&per_page=50")
            if r.status_code != 200:
                return recs
            for pr in r.json():
                title = pr.get("title") or ""
                if any(k in title.lower() for k in ("ecu", "dtc", "pid", "formula", "فرمول")):
                    recs.append({
                        "record_id": hashlib.sha1(
                            f"pr|{pr.get('html_url')}".encode()
                        ).hexdigest()[:16],
                        "record_type": "github_pr_lead",
                        "parameter_name": title,
                        "is_dtc": False,
                        "notes": f"PR #{pr.get('number')}: {pr.get('body', '')[:300]}",
                        "source_url": pr.get("html_url", ""),
                        "source_kind": "github_pull",
                        "source_tier": "Gold/Issues",
                        "confidence_score": 4,
                        "confidence_pct": 40,
                        "verification_status": "lead_only",
                        "retrieved_at": _now(),
                    })
        except Exception:
            pass
        return recs

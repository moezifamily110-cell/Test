#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
خط لوله اصلی ECU نسخه ۶.۰ — ورودی: خروجی خزنده Go نسخه ۱۵.

هدف: ساخت مرجع مهندسی جامع، دقیق و صنعتی از اطلاعات ارتباطی و رمزگشایی ECU
خودروهای ایرانی، قابل استفاده در طراحی و توسعه تجهیزات و نرم‌افزارهای تخصصی خودرو.

اصول کلیدی:
  ❌ هیچ حدس و گمانی مجاز نیست
  ✅ هر داده باید منبع مشخص داشته باشد
  ✅ هر داده باید به خودرو و ECU صحیح منتسب شود
  ✅ اصل فرمول و فرمان باید حفظ شود
  ✅ موارد نامشخص باید مشخص شوند
  ✅ تطبیق با سورس اصلی GitHub انجام می‌شود

اجرا:
  # مرحله ۱: خزیدن سایت با Go
  cd go_crawler && go run main.go -out ../output/go_crawler

  # مرحله ۲: پردازش و تولید خروجی با Python
  python main.py --go-output ../output/go_crawler --out ../output/final

  # با اسناد رسمی Diamond:
  python main.py --go-output ../output/go_crawler --out ../output/final --diamond-dir ./diamond_docs

  # حالت آفلاین (بدون دسترسی شبکه):
  python main.py --go-output ../output/go_crawler --out ../output/final --offline
"""
import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

# اضافه کردن مسیر پروژه به sys.path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from modules import sicar_formula as sf  # noqa: E402
from modules.conflict_manager import ConflictManager  # noqa: E402
from modules.data_loader import DataLoader  # noqa: E402
from modules.diamond_tier_miner import DiamondTierMiner  # noqa: E402
from modules.github_miner import GitHubMiner  # noqa: E402
from modules.output_generator import OutputGenerator, derive_labels  # noqa: E402
from modules.version_diff import (  # noqa: E402
    VersionDiffEngine, ensure_record_id, flatten_site_history,
)


class Stage:
    """مدیریت زمان‌بندی و نمایش پیشرفت مراحل."""
    def __init__(self, timings):
        self.t = timings

    def __call__(self, name):
        self.name = name
        return self

    def __enter__(self):
        self.t0 = time.time()
        print(f"[>] {self.name} ...", flush=True)

    def __exit__(self, *a):
        dt = time.time() - self.t0
        self.t[self.name] = round(dt, 2)
        print(f"[✓] {self.name}: {dt:.1f}s", flush=True)


def _load_cache(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_cache(p: Path, v):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(v, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def annotate_runtime(records):
    """ارزیابی وضعیت اجرای موتور سیکار برای هر فرمول با وکتور تست استاندارد."""
    cache, cnt = {}, Counter()
    evaluated = 0

    for r in records:
        f = r.get("formula") or ""
        if not f:
            continue
        if r.get("source_tier") in ("Silver", "Bronze"):
            continue
        if r.get("record_type") == "multi_tier":
            continue

        ct = sf.parse_ctype(r.get("command_type")) or 2
        dp = sf.decimals_from_notes(r.get("notes", ""))
        k = (f, ct, dp)

        if k not in cache:
            cache[k] = sf.evaluate(f, sf.TEST_VECTOR, ct, dp)
            evaluated += 1

        e = cache[k]
        r["sicar_runtime_status"] = e.status
        r["sicar_runtime_note"] = e.note
        if e.expression:
            r["sicar_expression"] = e.expression
        if not r.get("sicar_bit_lsb_index"):
            r["sicar_bit_lsb_index"] = sf.bit_lsb_index(f)
        cnt[e.status] += 1

    return cnt, len(cache), evaluated


def build_originals_list(go_dir: Path, out: Path, gh_files: list) -> list:
    """ساخت فهرست اصل فایل‌ها برای آرشیو JSON."""
    originals = []

    for grp, base in [
        ("site_json", go_dir / "files" / "json"),
        ("site_pdf", go_dir / "files" / "pdf"),
        ("site_external", go_dir / "files" / "other"),
        ("github_json", out / "github_json"),
    ]:
        if base.is_dir():
            for p in sorted(base.rglob("*")):
                if p.is_file():
                    originals.append({
                        "group": grp,
                        "rel": str(p.relative_to(base)),
                        "local_path": str(p),
                        "sha256": "",
                    })

    # افزودن SHA از فایل‌های GitHub
    shas = {f["name"]: f["sha256"] for f in gh_files}
    for o in originals:
        if o["group"] == "github_json":
            o["sha256"] = shas.get(Path(o["rel"]).name, "")

    return originals


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="ECU Pipeline v6 - خط لوله پردازش داده ECU خودروهای ایرانی",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--go-output", default=str(HERE.parent / "output" / "go_crawler"),
                    help="مسیر خروجی خزنده Go")
    ap.add_argument("--out", default=str(HERE.parent / "output" / "final"),
                    help="مسیر خروجی نهایی")
    ap.add_argument("--diamond-dir", default=os.environ.get("ECU_DIAMOND_DIR", ""),
                    help="پوشه اسناد رسمی کارخانه (سطح Diamond)")
    ap.add_argument("--offline", action="store_true",
                    help="حالت آفلاین؛ فقط داده محلی پردازش می‌شود")
    ap.add_argument("--skip-multi-tier", action="store_true",
                    help="رد کردن جست‌وجوی چندلایه (Silver/Bronze)")
    ap.add_argument("--skip-github", action="store_true",
                    help="رد کردن استخراج از GitHub")
    ap.add_argument("--skip-diamond", action="store_true",
                    help="رد کردن پردازش سطح Diamond")
    ap.add_argument("--skip-word", action="store_true",
                    help="رد کردن تولید مستندات Word")
    ap.add_argument("--refresh", action="store_true",
                    help="نادیده گرفتن کش مراحل استخراج")
    ap.add_argument("--limit", type=int, default=0,
                    help="محدودیت تعداد رکورد برای تست (0 = نامحدود)")

    a = ap.parse_args(argv)

    t_start = time.time()
    timings = {}
    st = Stage(timings)

    go_dir = Path(a.go_output).resolve()
    out = Path(a.out).resolve()
    state = out / "state"
    out.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("  ECU Pipeline v6.0 — پردازش داده ECU خودروهای ایرانی")
    print("=" * 60)
    print(f"  Go output: {go_dir}")
    print(f"  Final out: {out}")
    print(f"  Offline:   {a.offline}")
    print(f"  Refresh:   {a.refresh}")
    print("=" * 60)

    report = {
        "pipeline_version": "6.0",
        "go_output": str(go_dir),
        "out": str(out),
        "offline": a.offline,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    loader = DataLoader()
    source_rows = []
    gh_files = []

    # ------------------------------------------------------------------
    # مرحله ۱: بارگذاری خروجی خزنده Go
    # ------------------------------------------------------------------
    with st("۱. بارگذاری داده Go"):
        go_recs = loader.load_go_data(str(go_dir))
        reports = loader.load_go_reports(str(go_dir))

    report["go_records_accepted"] = len(go_recs)
    report["go_records_rejected"] = len(loader.rejected)
    report["go_rejected_reasons"] = dict(
        Counter(x["reason"] for x in loader.rejected))

    if a.limit > 0:
        go_recs = go_recs[:a.limit]
        print(f"[i] محدودیت تست: {len(go_recs)} رکورد از Go")

    # ------------------------------------------------------------------
    # مرحله ۲: استخراج از GitHub (سطح Gold)
    # ------------------------------------------------------------------
    gh_recs = []
    if not a.skip_github:
        with st("۲. استخراج GitHub (سطح Gold)"):
            cache = None if a.refresh else _load_cache(state / "github_records.json")
            if cache and (a.offline or not a.refresh):
                gh_recs, gh_files = cache["records"], cache["files"]
                source_rows.append([
                    "GitHub", "cache", "reused",
                    f"{len(gh_recs)} records", "", str(state),
                ])
                print(f"[i] از کش استفاده شد: {len(gh_recs)} رکورد")
            else:
                gm = GitHubMiner(
                    str(out), str(go_dir),
                    offline=a.offline, site_records=go_recs,
                )
                gh_recs = gm.mine_all()
                gh_files = gm.files
                _save_cache(state / "github_records.json", {
                    "records": gh_recs, "files": gh_files,
                })
                for s in gm.status:
                    source_rows.append([
                        "GitHub", s.get("source", ""), s.get("status", ""),
                        json.dumps({
                            k: v for k, v in s.items()
                            if k not in ("source", "status")
                        }, ensure_ascii=False)[:500],
                        "", s.get("source", ""),
                    ])

            for f in gh_files:
                source_rows.append([
                    "GitHub file", f["name"], f["origin"],
                    f"{f['size']} bytes", f["sha256"], f["url"],
                ])

        gh_recs = loader.filter(gh_recs, "github")
        report["github_records"] = len(gh_recs)

    # ------------------------------------------------------------------
    # مرحله ۳: جست‌وجوی چندلایه (Silver/Bronze)
    # ------------------------------------------------------------------
    mt_recs = []
    if not (a.offline or a.skip_multi_tier):
        with st("۳. جست‌وجوی چندلایه (Silver/Bronze)"):
            try:
                from modules.multi_tier_miner import MultiTierMiner
                mt = MultiTierMiner(
                    output_dir=str(out / "multi_tier"),
                    refresh=a.refresh,
                )
                mt_recs = mt.mine_all_tiers()
                for s in mt.source_status:
                    source_rows.append([
                        "Multi-tier",
                        s.get("source_id", s.get("source", "")),
                        s.get("status", s.get("mode", "")),
                        json.dumps(s, ensure_ascii=False)[:500],
                        "", s.get("url", ""),
                    ])
            except Exception as e:
                print(f"[!] multi_tier ناموفق: {e}")
                source_rows.append([
                    "Multi-tier", "all", f"error: {e}", "", "", "",
                ])

        mt_recs = loader.filter(mt_recs, "multi_tier")
        report["multi_tier_records"] = len(mt_recs)
    else:
        source_rows.append([
            "Multi-tier", "all", "skipped",
            "offline/skip flag", "", "",
        ])

    # ------------------------------------------------------------------
    # مرحله ۴: اسناد رسمی Diamond
    # ------------------------------------------------------------------
    dia_recs = []
    if not a.skip_diamond:
        with st("۴. اسناد رسمی Diamond"):
            dm = DiamondTierMiner(
                a.diamond_dir or None,
                str(go_dir / "files"),
            )
            dia_recs = loader.filter(dm.mine_all_diamond_docs(), "diamond")
            for r in dm.report:
                source_rows.append([
                    "Diamond", r.get("file", ""), r.get("status", ""),
                    json.dumps(r, ensure_ascii=False)[:500], "", "",
                ])
            for d in dm.documents:
                source_rows.append([
                    "Site document", d["name"], d["classification"],
                    f"pages={d['pages']} size={d['size']}",
                    d["sha256"], d["local_path"],
                ])
        report["diamond_records"] = len(dia_recs)

    # ------------------------------------------------------------------
    # ادغام همه رکوردها و تضمین یکتایی record_id
    # ------------------------------------------------------------------
    records = go_recs + gh_recs + mt_recs + dia_recs
    print(f"\n[i] کل رکوردها قبل از یکتاسازی: {len(records)}")

    occ = Counter()
    for r in records:
        k = (
            r.get("source_tier"), r.get("source_url"),
            r.get("request_command"), r.get("parameter_name"),
            r.get("dtc_code"), r.get("file_name"),
        )
        ensure_record_id(r, occ[k])
        occ[k] += 1

    # تضمین نهایی یکتایی
    seen = Counter()
    for r in records:
        seen[r["record_id"]] += 1
        if seen[r["record_id"]] > 1:
            r["record_id"] = f"{r['record_id']}-{seen[r['record_id']]}"

    # ------------------------------------------------------------------
    # مرحله ۵: ارزیابی زمان اجرای فرمول‌ها
    # ------------------------------------------------------------------
    with st("۵. ارزیابی زمان اجرای فرمول‌ها"):
        rt_counts, uniq_formulas, evaluated = annotate_runtime(records)

    report["sicar_runtime_status"] = dict(rt_counts)
    report["unique_formula_ctype_decimal"] = uniq_formulas
    report["formulas_evaluated"] = evaluated

    # ------------------------------------------------------------------
    # مرحله ۶: شناسایی تعارضات
    # ------------------------------------------------------------------
    with st("۶. شناسایی تعارضات"):
        cm = ConflictManager()
        conflicts = cm.detect_conflicts(
            records, reports.get("site_vs_json"),
        )

    report["conflicts_total"] = len(conflicts)
    report["conflicts_by_type"] = dict(
        Counter(c["conflict_type"] for c in conflicts))
    report["conflict_stats"] = dict(cm.stats)
    report["coverage_gaps"] = len(cm.coverage)

    # ------------------------------------------------------------------
    # مرحله ۷: مقایسه نسخه‌ها
    # ------------------------------------------------------------------
    with st("۷. مقایسه نسخه‌ها"):
        vrows = flatten_site_history(reports.get("version_history"))
        vd = VersionDiffEngine(str(state)).compare_versions(records)
        (out / "version_diff_run.json").write_text(
            json.dumps(vd, ensure_ascii=False, indent=1), encoding="utf-8",
        )

    report["site_history_change_rows"] = len(vrows)
    report["run_diff"] = {
        k: vd[k] for k in (
            "first_run", "previous_count", "current_count",
            "added", "removed", "changed",
        )
    }

    # ------------------------------------------------------------------
    # ساخت برچسب‌های سلسله‌مراتب
    # ------------------------------------------------------------------
    label_sources = derive_labels(records)
    report["hierarchy_label_sources"] = dict(label_sources)

    # آمار خلاصه
    tier_counts = Counter(r.get("source_tier") for r in records)
    type_counts = Counter(r.get("record_type") for r in records)
    vehicle_counts = Counter(r.get("label_vehicle") for r in records)
    ecu_counts = Counter(r.get("label_ecu") for r in records)
    manufacturer_counts = Counter(r.get("label_manufacturer") for r in records)

    summary = [
        ["زمان شروع اجرا (UTC)", report["started_at"]],
        ["نسخه خط لوله", report["pipeline_version"]],
        ["کل رکوردها", len(records)],
        ["خودروسازان شناسایی‌شده", len(manufacturer_counts)],
        ["خودروهای شناسایی‌شده", len(vehicle_counts)],
        ["ECUهای شناسایی‌شده", len(ecu_counts)],
    ]
    for k, v in sorted(tier_counts.items(), key=lambda x: str(x[0])):
        summary.append([f"رکورد سطح {k}", v])
    for k, v in sorted(type_counts.items(), key=lambda x: str(x[0])):
        summary.append([f"نوع رکورد: {k}", v])
    for k, v in sorted(rt_counts.items()):
        summary.append([f"وضعیت اجرای سیکار: {k}", v])
    for k, v in sorted(report.get("conflicts_by_type", {}).items()):
        summary.append([f"تعارض: {k}", v])
    summary.append([
        "رکورد ردشده (در rejected_records.json)",
        len(loader.rejected),
    ])
    summary.append(["شکاف پوشش (جدول در JSON نیست)", len(cm.coverage)])

    # پر خودروسازان
    summary.append(["", ""])
    summary.append(["خودروسازان", ""])
    for k, v in manufacturer_counts.most_common(20):
        summary.append([f"  {k}", v])

    # ------------------------------------------------------------------
    # مرحله ۸: تولید Excel
    # ------------------------------------------------------------------
    gen = OutputGenerator(str(out))

    with st("۸. تولید Excel جامع"):
        excel_path = gen.generate_comprehensive_excel(records, {
            "summary_rows": summary,
            "source_rows": source_rows,
            "coverage": cm.coverage,
            "version_rows": vrows,
        })
        print(f"    → {excel_path}")

    with st("   تولید گزارش تعارضات"):
        conflict_path = gen.generate_conflict_report(conflicts, dict(cm.stats))
        print(f"    → {conflict_path}")

    with st("   ذخیره رکوردهای ردشده"):
        rejected_path = loader.save_rejected(str(out))
        print(f"    → {rejected_path}")

    # ------------------------------------------------------------------
    # مرحله ۹: تولید آرشیو JSON
    # ------------------------------------------------------------------
    with st("۹. تولید آرشیو JSON ساختاریافته"):
        originals = build_originals_list(go_dir, out, gh_files)
        jr = gen.generate_structured_json(records, originals)
        print(f"    → {jr['path']}")
        print(f"    → {jr['manufacturers']} خودروساز, "
              f"{jr['vehicles']} خودرو, {jr['ecus']} ECU")
        print(f"    → {jr['parameters_total']} پارامتر, "
              f"{jr['dtcs_total']} DTC, {jr['init_commands_total']} Init")
        print(f"    → {jr['originals']} فایل اصل "
              f"({jr['originals_verified']} تأییدشده)")

    report["json_archive"] = jr

    # ------------------------------------------------------------------
    # مرحله ۱۰: تولید مستندات Word
    # ------------------------------------------------------------------
    if not a.skip_word:
        with st("۱۰. تولید مستندات Word مهندسی"):
            docs = gen.generate_engineering_word(records)
            print(f"    → {len(docs)} سند Word")
            for d in docs:
                print(f"      • {d.name}")
        report["word_docs"] = [str(p) for p in docs]

    # ------------------------------------------------------------------
    # خاتمه و گزارش نهایی
    # ------------------------------------------------------------------
    report["timings_sec"] = timings
    report["total_sec"] = round(time.time() - t_start, 1)
    report["records_total"] = len(records)
    report["manufacturers"] = dict(manufacturer_counts)
    report["vehicles"] = dict(vehicle_counts.most_common(50))
    report["ecus"] = dict(ecu_counts.most_common(100))
    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    report_path = out / "pipeline_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    # ------------------------------------------------------------------
    # نمایش خلاصه نهایی
    # ------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("  خلاصه اجرای خط لوله")
    print("=" * 60)
    print(f"  زمان کل اجرا:     {report['total_sec']:.1f} ثانیه")
    print(f"  کل رکوردها:       {len(records)}")
    print(f"  خودروسازان:        {len(manufacturer_counts)}")
    print(f"  خودروها:          {len(vehicle_counts)}")
    print(f"  ECUها:             {len(ecu_counts)}")
    print(f"  تعارضات شناسایی‌شده: {len(conflicts)}")
    print(f"  شکاف پوشش:         {len(cm.coverage)}")
    print(f"  رکوردهای ردشده:    {len(loader.rejected)}")
    print("-" * 60)
    print("  توزیع بر اساس سطح منبع:")
    for tier, cnt in sorted(tier_counts.items(), key=lambda x: -x[1]):
        print(f"    {tier:25s} {cnt:>6d}")
    print("-" * 60)
    print("  وضعیت اجرای فرمول‌ها:")
    for status, cnt in sorted(rt_counts.items(), key=lambda x: -x[1]):
        print(f"    {status:35s} {cnt:>6d}")
    print("-" * 60)
    print("  فایل‌های خروجی:")
    print(f"    • {excel_path.name}")
    print(f"    • {conflict_path.name}")
    print(f"    • JSON_Archive/ECU_Hierarchy.json")
    print(f"    • pipeline_report.json")
    print(f"    • rejected_records.json")
    if not a.skip_word:
        print(f"    • Word_Docs/ ({len(report.get('word_docs', []))} سند)")
    print("=" * 60)

    # هشدار در صورت وجود مشکل
    warnings = []
    if len(conflicts) > 100:
        warnings.append(
            f"تعداد تعارضات بالا ({len(conflicts)}): بررسی Conflict_Report.xlsx توصیه می‌شود")
    if rt_counts.get("reference_runtime_error", 0) > 50:
        warnings.append(
            f"فرمول‌هایی با خطا در زمان اجرا: {rt_counts['reference_runtime_error']}")
    if jr.get("unknown_ecu_records", 0) > 100:
        warnings.append(
            f"رکوردهای بدون ECU مشخص: {jr['unknown_ecu_records']}")
    if jr.get("unknown_manufacturer_records", 0) > 50:
        warnings.append(
            f"رکوردهای بدون خودروساز مشخص: {jr['unknown_manufacturer_records']}")

    if warnings:
        print("\n  ⚠️  هشدارها:")
        for w in warnings:
            print(f"    • {w}")

    print("\n  نکته مهم: این پروژه بدون ادعای تکمیل بودن ارائه می‌شود.")
    print("  تمام داده‌ها منبع مشخص دارند و موارد نامشخص مشخص شده‌اند.")
    print(f"  گزارش کامل در: {report_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

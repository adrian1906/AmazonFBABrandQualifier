"""
Batch-run the R&T Brand Acquisition workflow across many brands at once,
e.g. the ~100 candidates that already passed your SmartScout filters.

This does NOT show the interactive human-approval gate per brand - that
doesn't scale to 100 companies. Instead it:

  1. Runs Research -> Qualification -> Outreach -> Manager for every row
     in the input CSV (concurrency-limited, one bad row doesn't kill the
     batch)
  2. Saves each full result to batch_results/<company>_<timestamp>.json
     (see persistence.py)
  3. Writes a single ranked summary CSV so you can scan all ~100 at a
     glance and decide which ones are worth opening individually

Once you've picked a promising one from the summary, use review_one.py to
load its saved result and run the normal APPROVE / EDIT / REGENERATE /
REJECT gate on it - with no further agent calls unless you choose REGENERATE.

Already-scored brands are skipped by default (see persistence.scored_company_names)
- safe to re-run this against a CSV that overlaps a previous run, e.g. after
merging a new SmartScout export with an old one (see merge_smartscout_exports.py).
"Already scored" only counts a result saved within the last
config.BRAND_STALE_DATA_DAYS days (default 90) - older than that, SmartScout's
own numbers have likely drifted, so it's re-processed rather than skipped
forever. Pass --rescore to process every row regardless of age.

A brand whose name has no ASCII-representable characters at all (e.g.
written only in Japanese/Chinese/etc. script) is also skipped, unconditionally
- see persistence.has_usable_name. Such a name collapses to the same
generic filename as every other one of its kind, making saved results
indistinguishable from each other, so these are excluded BEFORE any API
call is made rather than researched and then have nowhere distinguishable
to save the result.

Usage:
    python batch_runner.py --csv my_smartscout_export.csv
    python batch_runner.py --csv my_smartscout_export.csv --limit 5   # cheap test run
    python batch_runner.py --csv my_smartscout_export.csv --concurrency 3
    python batch_runner.py --csv my_smartscout_export.csv --no-web-search
    python batch_runner.py --csv my_smartscout_export.csv --rescore   # re-process even already-scored brands
"""

import argparse
import asyncio
import csv as csv_module
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from config import BRAND_STALE_DATA_DAYS
from entity_resolution import normalize_company_name
from persistence import RESULTS_DIR, save_result, scored_company_names, has_usable_name
from smartscout_import import load_prospects_from_csv
from workflow import WorkflowResult, run_brand_acquisition

load_dotenv(override=True)


async def _run_one(
    semaphore: asyncio.Semaphore,
    prospect,
    notes: str,
    allow_web_search: bool,
    index: int,
    total: int,
) -> tuple[WorkflowResult | None, tuple[str, str] | None]:
    async with semaphore:
        print(f"[{index}/{total}] Running: {prospect.company_name} ...")
        try:
            result = await run_brand_acquisition(
                prospect, manual_research_notes=notes, allow_web_search=allow_web_search
            )
            saved_path = save_result(result)
            print(
                f"[{index}/{total}] Done: {prospect.company_name} "
                f"-> score {result.qualification.overall_score}, {result.qualification.recommendation} "
                f"(saved: {saved_path.name})"
            )
            return result, None
        except Exception as exc:  # one bad row must not take down the whole batch
            print(f"[{index}/{total}] FAILED: {prospect.company_name}: {exc}")
            return None, (prospect.company_name, str(exc))


def _write_summary_csv(results: list[WorkflowResult]) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"summary_{timestamp}.csv"

    ranked = sorted(results, key=lambda r: r.qualification.overall_score, reverse=True)

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv_module.writer(f)
        writer.writerow([
            "rank", "company_name", "qualification_score", "recommendation",
            "winning_strategy", "winning_subject", "recommended_next_action",
        ])
        for rank, result in enumerate(ranked, start=1):
            winner = result.winning_draft
            writer.writerow([
                rank,
                result.prospect.company_name,
                result.qualification.overall_score,
                result.qualification.recommendation,
                result.manager_decision.winning_strategy,
                winner.subject,
                result.qualification.recommended_next_action,
            ])

    return path


async def run_batch(
    csv_path: str,
    limit: int | None = None,
    concurrency: int = 5,
    allow_web_search: bool = True,
    skip_scored: bool = True,
) -> list[WorkflowResult]:
    rows = load_prospects_from_csv(csv_path)

    unusable = [p.company_name for p, _notes in rows if not has_usable_name(p.company_name)]
    if unusable:
        rows = [(p, notes) for p, notes in rows if has_usable_name(p.company_name)]
        print(f"Skipping {len(unusable)} brand(s) with no ASCII-representable characters in their name "
              f"(can't be saved under a distinguishable filename): {', '.join(unusable)}")

    total_loaded = len(rows)

    skipped = 0
    if skip_scored:
        already = scored_company_names(max_age_days=BRAND_STALE_DATA_DAYS)
        before = len(rows)
        rows = [(p, notes) for p, notes in rows if normalize_company_name(p.company_name) not in already]
        skipped = before - len(rows)
        if skipped:
            print(f"Skipping {skipped} brand(s) already scored within the last {BRAND_STALE_DATA_DAYS} days "
                  f"(pass --rescore to process them anyway).")

    if limit is not None:
        rows = rows[:limit]

    if not rows:
        if total_loaded and skipped == total_loaded:
            print("Nothing new to process - every brand in this CSV has already been scored (pass --rescore to re-run anyway).")
        else:
            print("No prospects found in CSV - nothing to do.")
        return []

    print(f"Loaded {len(rows)} prospect(s) from {csv_path}. Concurrency: {concurrency}. "
          f"Web search: {'on' if allow_web_search else 'off'}.\n")

    semaphore = asyncio.Semaphore(concurrency)
    tasks = [
        _run_one(semaphore, prospect, notes, allow_web_search, i, len(rows))
        for i, (prospect, notes) in enumerate(rows, start=1)
    ]
    outcomes = await asyncio.gather(*tasks)

    results = [r for r, _err in outcomes if r is not None]
    failures = [err for _r, err in outcomes if err is not None]

    print(f"\nBatch complete: {len(results)} succeeded, {len(failures)} failed.")
    if failures:
        print("Failures:")
        for name, err in failures:
            print(f"  - {name}: {err}")

    if results:
        summary_path = _write_summary_csv(results)
        print(f"\nRanked summary written to: {summary_path}")
        print(f"Full per-brand results saved under: {RESULTS_DIR}")
        print("\nNext step: open the summary CSV, pick a brand, then run:")
        print("    python review_one.py \"<company name or partial match>\"")

    return results


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch-run the R&T Brand Acquisition workflow over a SmartScout export.")
    parser.add_argument("--csv", required=True, help="Path to a SmartScout-style CSV export")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N rows (useful for a cheap test run)")
    parser.add_argument("--concurrency", type=int, default=5, help="Max brands processed in parallel (default: 5)")
    parser.add_argument("--no-web-search", action="store_true", help="Disable WebSearchTool; rely only on the CSV/manual notes")
    parser.add_argument("--rescore", action="store_true",
                         help="Process every row even if already scored in a previous run (default: skip already-scored brands)")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(run_batch(
        csv_path=args.csv,
        limit=args.limit,
        concurrency=args.concurrency,
        skip_scored=not args.rescore,
        allow_web_search=not args.no_web_search,
    ))

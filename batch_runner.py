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

Usage:
    python batch_runner.py --csv my_smartscout_export.csv
    python batch_runner.py --csv my_smartscout_export.csv --limit 5   # cheap test run
    python batch_runner.py --csv my_smartscout_export.csv --concurrency 3
    python batch_runner.py --csv my_smartscout_export.csv --no-web-search
"""

import argparse
import asyncio
import csv as csv_module
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from persistence import RESULTS_DIR, save_result
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
) -> list[WorkflowResult]:
    rows = load_prospects_from_csv(csv_path)
    if limit is not None:
        rows = rows[:limit]

    if not rows:
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
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(run_batch(
        csv_path=args.csv,
        limit=args.limit,
        concurrency=args.concurrency,
        allow_web_search=not args.no_web_search,
    ))

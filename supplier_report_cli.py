"""
Render Supplier Qualifier reports from already-persisted research - makes
no paid research calls, since it only reads supplier_data/ (see
supplier_persistence.py).

Usage:
    python supplier_report_cli.py --batch supbatch_20260920_101500
    python supplier_report_cli.py --brand "Trailhead"   # every supplier researched for brands matching this name
    python supplier_report_cli.py --batch supbatch_20260920_101500 --save   # also write to supplier_reports/
"""

import argparse
from datetime import datetime
from pathlib import Path

import supplier_persistence
from config import SUPPLIER_REPORTS_DIR_NAME
from supplier_report import format_full_supplier_report

REPORTS_DIR = Path(__file__).parent / SUPPLIER_REPORTS_DIR_NAME


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render Supplier Qualifier reports from already-persisted research - no paid research calls."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--batch", help="Supplier batch id (printed by supplier_batch_runner.py when a run finishes)")
    group.add_argument("--brand", help="Render every supplier researched for brand names matching this fragment")
    parser.add_argument("--save", action="store_true", help=f"Also write the report to {SUPPLIER_REPORTS_DIR_NAME}/")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    if args.batch:
        relationships = supplier_persistence.relationships_for_batch(args.batch)
        if not relationships:
            print(f"No relationships found for supplier batch {args.batch!r}.")
            return
        label = args.batch
    else:
        paths = supplier_persistence.find_relationships(brand_fragment=args.brand)
        relationships = [supplier_persistence.load_relationship(p) for p in paths]
        if not relationships:
            print(f"No relationships found for brand fragment {args.brand!r}.")
            return
        label = args.brand

    report_text = format_full_supplier_report(relationships)
    print(report_text)

    if args.save:
        REPORTS_DIR.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_label = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)
        path = REPORTS_DIR / f"{safe_label}_{timestamp}.txt"
        path.write_text(report_text, encoding="utf-8")
        print(f"\n(Saved to {path})")


if __name__ == "__main__":
    main()

"""
Batch-run the FBA Catalog Analyzer over every row of an imported
distributor catalog - mirrors batch_runner.py's pattern (preview first,
then commit; one bad row doesn't kill the batch; a ranked CSV summary at
the end).

IMPORTANT - what this can and can't tell you today: ASIN resolution
prefers Amazon's own SP-API Catalog Items (sp_api_adapter.py) when it's
configured - live-verified 2026-10-08 against R&T's real account - and
falls back to Keepa (keepa_adapter.py; fixture/demo mode unless
KEEPA_API_KEY is also set) otherwise. Either way, a row NEVER auto-
qualifies from resolution alone: both adapters deliberately return
match_status="needs_review" even for a single clean candidate, since
neither actually confirms the retail pack count - a human (or a future
pack-verification step) still has to confirm that before cost-ledger math
runs. So every row from a plain run is still an honest INCOMPLETE/
needs_review screening candidate, not a profit claim - but with SP-API
configured, the candidates shown are real Amazon ASINs, not placeholders.

Usage:
    python catalog_batch.py --preview catalogs/my_catalog.xlsx --supplier "DC 55"
    python catalog_batch.py --run catalogs/my_catalog.xlsx --supplier "DC 55" --limit 50
    python catalog_batch.py --run catalogs/my_catalog.xlsx --supplier "DC 55" --price-basis case
"""

import argparse
import csv as csv_module
from datetime import datetime, timezone
from pathlib import Path

from catalog_import import load_catalog_rows, preview_catalog
from catalog_models import CatalogScanRun, ColumnMapping
from catalog_persistence import RUNS_DIR, new_run_id, save_run
from catalog_scan import scan_row
from config import (
    CATALOG_DEFAULT_HISTORY_DAYS, CATALOG_HIGHLIGHT_ROI_THRESHOLD, CATALOG_MIN_MONTHLY_SALES,
    CATALOG_ROI_CONVENTION, CATALOG_TARGET_ROI,
)
import sp_api_adapter
from keepa_adapter import get_keepa_provider

CATALOG_REPORTS_DIR = Path(__file__).parent / "batch_reports"


def _select_provider():
    """Prefers Keepa when it's live - it's the only one of the two that
    does BOTH ASIN resolution AND real price history (see
    keepa_adapter.LiveKeepaProvider.get_pricing_snapshot, live-verified
    2026-10-08), so picking it gets strictly more capability from one
    provider. Falls back to Amazon's own SP-API Catalog Items (official,
    ToS-sanctioned resolution - also live-verified, but no price history
    yet) when Keepa isn't configured, then to fixture/demo mode. SP-API's
    OTHER real capability - get_fees_estimate()'s referral/FBA fee
    numbers - isn't tied to this choice at all; it's a separate call a
    caller can make regardless of which provider is active here (not yet
    wired into catalog_scan.py's cost ledger automatically - still manual
    via known_costs=)."""
    keepa = get_keepa_provider()
    if keepa.is_live:
        return keepa
    if sp_api_adapter.is_configured():
        return sp_api_adapter.SpApiCatalogProvider()
    return keepa


def run_preview(path: str, supplier_name: str, price_basis: str, sample_size: int, thorough: bool) -> None:
    mapping = ColumnMapping(supplier_name=supplier_name, mapping={}, price_basis=price_basis) if price_basis != "each" else None
    preview = preview_catalog(path, supplier_name, mapping=mapping, sample_size=sample_size, thorough=thorough)

    print(f"\n{preview.total_row_count} total row(s) detected in {path}\n")
    print("Detected column mapping:")
    for field, header in preview.detected_mapping.items():
        print(f"  {field:35s} -> {header}")
    if preview.unmapped_required_fields:
        print(f"\nWARNING - required fields not found: {preview.unmapped_required_fields}")
    print(f"\n{preview.price_basis_note}\n")
    if preview.duplicate_upc_count is not None:
        print(f"Duplicate UPCs found: {preview.duplicate_upc_count}")
    else:
        print("Duplicate UPCs: not checked (pass --thorough to check before committing)")
    if preview.warnings:
        print(f"\n{len(preview.warnings)} warning(s), first 10:")
        for w in preview.warnings[:10]:
            print(f"  row {w.source_row}: {w.message}")

    print(f"\nFirst {len(preview.sample_rows)} row(s):")
    for row in preview.sample_rows:
        print(f"  [{row.source_row}] {row.description[:50]:50s} upc={row.upc} price={row.purchase_price} "
              f"units_per_purchase_unit={row.units_per_purchase_unit} order_multiple={row.order_multiple}")

    print("\nThis is a PREVIEW only - nothing was saved. Review the mapping/price-basis above, then run with --run.")


def run_batch(
    path: str, supplier_name: str, price_basis: str, limit: int | None,
    history_days: int, target_roi, highlight_threshold, minimum_monthly_sales,
) -> CatalogScanRun:
    mapping = ColumnMapping(supplier_name=supplier_name, mapping={}, price_basis=price_basis) if price_basis != "each" else None
    rows, warnings = load_catalog_rows(path, supplier_name, mapping=mapping, limit=limit)

    provider = _select_provider()
    if isinstance(provider, sp_api_adapter.SpApiCatalogProvider):
        mode_note = "LIVE Amazon SP-API Catalog Items (ASIN resolution only - no price history yet)"
    elif provider.is_live:
        mode_note = "LIVE Keepa data"
    else:
        mode_note = "FIXTURE/DEMO mode (no KEEPA_API_KEY or SP-API credentials configured)"
    print(f"Loaded {len(rows)} row(s) from {path}. Resolution mode: {mode_note}.")
    if warnings:
        print(f"{len(warnings)} import warning(s) - first 5:")
        for w in warnings[:5]:
            print(f"  row {w.source_row}: {w.message}")

    run = CatalogScanRun(
        run_id=new_run_id(supplier_name), supplier_name=supplier_name, catalog_path=str(path),
        started_at=datetime.now(timezone.utc), history_window_days=history_days,
        target_roi=target_roi, highlight_threshold=highlight_threshold,
        minimum_monthly_sales=minimum_monthly_sales, roi_convention=CATALOG_ROI_CONVENTION,
        is_live_data=provider.is_live,
    )

    for i, row in enumerate(rows, start=1):
        try:
            result = scan_row(
                row, provider=provider, history_days=history_days, target_roi=target_roi,
                highlight_threshold=highlight_threshold, minimum_monthly_sales=minimum_monthly_sales,
            )
            run.results.append(result)
        except Exception as exc:  # one bad row must not take down the whole batch
            print(f"[{i}/{len(rows)}] FAILED: {row.description}: {exc}")

    run.finished_at = datetime.now(timezone.utc)
    saved_path = save_run(run)

    summary_path = _write_summary_csv(run)
    tiers = {}
    for r in run.results:
        tiers[r.roi.tier] = tiers.get(r.roi.tier, 0) + 1
    print(f"\nScanned {len(run.results)} row(s). Tier breakdown: {tiers}")
    print(f"Full run saved to: {saved_path}")
    print(f"Summary CSV: {summary_path}")
    if not provider.is_live:
        print(
            "\nEvery row above is INCOMPLETE/needs_review because this ran in fixture/demo mode - "
            "that's expected, not a bug. Set KEEPA_API_KEY for live ASIN resolution, or confirm a "
            "match/price manually (see catalog_scan.scan_row's override_* parameters) to get a real ROI."
        )
    elif isinstance(provider, sp_api_adapter.SpApiCatalogProvider):
        print(
            "\nResolution was live (real Amazon ASIN candidates above), but every row is still "
            "INCOMPLETE/needs_review - by design, neither this nor a Keepa match auto-confirms the "
            "retail pack count, and SP-API price history isn't wired in yet. Review the resolved ASINs "
            "in the summary CSV, then re-run a specific row with override_asin_candidate (match_status="
            "'verified') and override_selling_price to get a real ROI for it."
        )
    return run


def _write_summary_csv(run: CatalogScanRun) -> Path:
    CATALOG_REPORTS_DIR.mkdir(exist_ok=True)
    path = CATALOG_REPORTS_DIR / f"{run.run_id}_summary.csv"

    def sort_key(r):
        roi = r.roi.qualifying_roi
        return (r.qualifies, roi if roi is not None else -999)

    ranked = sorted(run.results, key=sort_key, reverse=True)

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv_module.writer(f)
        writer.writerow([
            "supplier", "sku", "upc", "description", "asin", "match_status", "tier", "qualifies",
            "purchase_price", "normalized_cogs", "selling_price", "profit", "landed_roi", "merchandise_roi",
            "margin", "target_supplier_price", "discount_dollars", "discount_percent",
            "demand_status", "monthly_units_estimate", "incomplete_reasons",
        ])
        for r in ranked:
            c = r.resolution.candidates[0] if r.resolution.candidates else None
            writer.writerow([
                r.row.supplier_name, r.row.supplier_sku, r.row.upc, r.row.description,
                c.asin if c else "", c.match_status if c else "unmatched", r.roi.tier, r.qualifies,
                r.row.purchase_price, r.roi.normalized_cogs, r.roi.selling_price, r.roi.profit,
                r.roi.landed_cost_roi, r.roi.merchandise_cost_roi, r.roi.margin,
                r.roi.target_supplier_price_per_purchase_unit, r.roi.required_discount_dollars,
                r.roi.required_discount_percent, r.demand.status, r.demand.monthly_units_estimate,
                "; ".join(r.roi.incomplete_reasons),
            ])
    return path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the FBA Catalog Analyzer over a distributor catalog file.")
    parser.add_argument("--preview", help="Path to a catalog file - show column mapping/sample rows, no commitment")
    parser.add_argument("--run", help="Path to a catalog file - run the full scan and save results")
    parser.add_argument("--supplier", required=True, help="Supplier/distributor name this catalog is from")
    parser.add_argument("--price-basis", default="each", choices=["each", "inner_pack", "case"],
                         help="ASSUMPTION about the purchase-price column (default: each) - see catalog_import.py")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N rows (cheap test run)")
    parser.add_argument("--sample-size", type=int, default=10, help="--preview only: how many rows to show")
    parser.add_argument("--thorough", action="store_true", help="--preview only: also run the full duplicate-UPC scan")
    parser.add_argument("--history-days", type=int, default=CATALOG_DEFAULT_HISTORY_DAYS)
    parser.add_argument("--target-roi", type=float, default=float(CATALOG_TARGET_ROI))
    parser.add_argument("--highlight-threshold", type=float, default=float(CATALOG_HIGHLIGHT_ROI_THRESHOLD))
    parser.add_argument("--min-monthly-sales", type=float, default=float(CATALOG_MIN_MONTHLY_SALES))
    return parser.parse_args()


if __name__ == "__main__":
    from decimal import Decimal

    args = _parse_args()
    if not args.preview and not args.run:
        raise SystemExit("Pass --preview <file> or --run <file>")
    if args.preview:
        run_preview(args.preview, args.supplier, args.price_basis, args.sample_size, args.thorough)
    if args.run:
        run_batch(
            args.run, args.supplier, args.price_basis, args.limit, args.history_days,
            Decimal(str(args.target_roi)), Decimal(str(args.highlight_threshold)), Decimal(str(args.min_monthly_sales)),
        )

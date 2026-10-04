"""
Feeds distributor_master_list.csv from real Stage 2 research, instead of
leaving it frozen at its one-time PDF import. Every distributor Stage 2
discovers and qualifies as NOT DO_NOT_PURSUE (CONTACT_NOW or
INVESTIGATE_FURTHER) is a real candidate worth keeping on record as a
known distributor - this closes that loop:

    SmartScout brands -> vet brands -> discover distributors -> vet
    distributors -> grow distributor_master_list.csv

Append-only: existing rows are never modified or removed, and a
distributor already in the master list (matched by normalized name, same
normalization used everywhere else in this project) is never duplicated.
Called automatically at the end of every supplier_batch_runner.py run (see
its final step) - also runnable standalone to backfill from every
relationship ever saved, not just the most recent batch:

    python grow_distributor_master_list.py                  # backfill from every saved relationship
    python grow_distributor_master_list.py --dry-run         # preview without writing
"""

import argparse
import csv
from pathlib import Path

from entity_resolution import normalize_company_name
from models import BrandSupplierRelationship
import supplier_persistence

DEFAULT_MASTER_LIST = Path(__file__).parent / "distributor_master_list.csv"
_NAME_COL = "Distributor"
_CATEGORIES_COL = "Primary Categories"


def _existing_names(master_list_path: Path) -> set[str]:
    if not master_list_path.exists():
        return set()
    with master_list_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        name_col = next((f for f in (reader.fieldnames or []) if f.strip().lower() == _NAME_COL.lower()), None)
        if not name_col:
            return set()
        return {normalize_company_name(row[name_col]) for row in reader if row.get(name_col, "").strip()}


def _candidate_row(rel: BrandSupplierRelationship) -> dict:
    c = rel.assessment.candidate
    brands = ", ".join(c.brands_carried) if c.brands_carried else rel.brand_name
    return {
        _NAME_COL: c.legal_business_name,
        _CATEGORIES_COL: f"Carries: {brands} ({c.role})",
    }


def grow_master_list(
    relationships: list[BrandSupplierRelationship],
    master_list_path: Path = DEFAULT_MASTER_LIST,
    dry_run: bool = False,
) -> list[dict]:
    """Appends newly-qualified (not DO_NOT_PURSUE) distributors not already
    in the master list. Returns the rows added (or that WOULD be added, if
    dry_run). Never touches an existing row."""
    known = _existing_names(master_list_path)
    seen_this_call: set[str] = set()
    new_rows: list[dict] = []

    for rel in relationships:
        if rel.assessment.recommendation == "DO_NOT_PURSUE":
            continue
        key = normalize_company_name(rel.assessment.candidate.legal_business_name)
        if not key or key in known or key in seen_this_call:
            continue
        seen_this_call.add(key)
        new_rows.append(_candidate_row(rel))

    if new_rows and not dry_run:
        file_exists = master_list_path.exists()
        with master_list_path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[_NAME_COL, _CATEGORIES_COL])
            if not file_exists:
                writer.writeheader()
            writer.writerows(new_rows)

    return new_rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--master-list", default=str(DEFAULT_MASTER_LIST), help="Path to distributor_master_list.csv")
    parser.add_argument("--dry-run", action="store_true", help="Preview what would be added without writing")
    args = parser.parse_args()

    relationships = supplier_persistence.all_relationships()
    added = grow_master_list(relationships, Path(args.master_list), dry_run=args.dry_run)

    verb = "Would add" if args.dry_run else "Added"
    if not added:
        print(f"{verb} 0 new distributor(s) - every qualified (not DO_NOT_PURSUE) candidate is already in {args.master_list}.")
        return

    print(f"{verb} {len(added)} new distributor(s) to {args.master_list}:")
    for row in added:
        print(f"  - {row[_NAME_COL]}  ({row[_CATEGORIES_COL]})")


if __name__ == "__main__":
    main()

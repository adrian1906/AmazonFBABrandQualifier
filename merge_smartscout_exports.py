"""
Merge multiple raw SmartScout brand exports (same column schema) into one
deduplicated file - for combining separate pulls (e.g. an Oct 1 and an
Oct 4 export) before narrowing with filter_smartscout_categories.py.

Dedup is by normalized brand name (entity_resolution.normalize_company_name,
the same normalization used elsewhere in this project). Later files win on
a conflict - pass your most recent export last, since SmartScout's numbers
for a given brand change between pulls and the newer one is more current.

Usage:
    python merge_smartscout_exports.py --csv smartscout-brands_20261001.csv --csv smartscout-brands_20261004.csv -o smartscout_merged.csv
"""

import argparse
import csv

from entity_resolution import normalize_company_name

_NAME_COL_CANDIDATES = ["Brand Name", "Brand", "Company", "Company Name", "Seller Name"]


def _find_col(fieldnames: list[str], candidates: list[str]) -> str | None:
    lower = {f.strip().lower(): f for f in fieldnames}
    for c in candidates:
        if c.lower() in lower:
            return lower[c.lower()]
    return None


def merge_csvs(paths: list[str]) -> tuple[list[dict], list[str], dict]:
    """Returns (merged_rows, fieldnames, stats). Stats tracks how many rows
    from each file were kept vs. overwritten by a later file."""
    fieldnames: list[str] | None = None
    by_key: dict[str, dict] = {}
    order: list[str] = []
    stats = {"files": {}, "total_rows_read": 0, "unique_brands": 0}

    for path in paths:
        kept_from_this_file = 0
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if fieldnames is None:
                fieldnames = reader.fieldnames
            elif reader.fieldnames != fieldnames:
                raise ValueError(
                    f"{path} has different columns than the first file:\n"
                    f"  first file's columns: {fieldnames}\n"
                    f"  {path}'s columns:     {reader.fieldnames}\n"
                    "All files passed to --csv must share the same SmartScout export schema."
                )
            name_col = _find_col(reader.fieldnames or [], _NAME_COL_CANDIDATES)
            if not name_col:
                raise ValueError(f"{path}: couldn't find a brand-name column among {_NAME_COL_CANDIDATES}")

            rows_this_file = 0
            for row in reader:
                rows_this_file += 1
                name = (row.get(name_col) or "").strip()
                if not name:
                    continue
                key = normalize_company_name(name)
                if key not in by_key:
                    order.append(key)
                    kept_from_this_file += 1
                by_key[key] = row  # later file/row wins - overwrites any earlier entry for the same brand
            stats["total_rows_read"] += rows_this_file
        stats["files"][path] = {"rows_read": rows_this_file, "newly_added": kept_from_this_file}

    stats["unique_brands"] = len(order)
    return [by_key[k] for k in order], fieldnames or [], stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", action="append", required=True, dest="csv_paths",
                         help="A SmartScout export CSV - pass multiple times, in order (last wins on a name conflict)")
    parser.add_argument("-o", "--output", required=True, help="Output merged CSV path")
    args = parser.parse_args()

    rows, fieldnames, stats = merge_csvs(args.csv_paths)

    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    for path, s in stats["files"].items():
        print(f"{path}: {s['rows_read']} row(s) read, {s['newly_added']} were new brands not seen in an earlier file")
    overwritten = stats["total_rows_read"] - stats["unique_brands"]
    print(f"\n{stats['unique_brands']} unique brand(s) total ({overwritten} duplicate row(s) collapsed - the newer file's data won each time)")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()

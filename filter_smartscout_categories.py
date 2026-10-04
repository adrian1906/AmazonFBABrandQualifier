"""
Narrow a raw SmartScout brand export down to replenishable-product-family
rows BEFORE running it through the (paid) Brand Qualifier - the cost-saving
step R&T's sourcing advice calls for explicitly: "narrow to the product
families... before sending brands through your distributor-vetting
program."

Matches against the SmartScout export's "Main Category" and "Primary
Subcategory" columns (the same columns smartscout_import.py already reads -
see its COLUMN_ALIASES). A row is kept only if:
  1. Main Category matches the preset's category_match list, AND
  2. At least one include_keyword appears in Primary Subcategory (or Notes,
     if present), AND
  3. No exclude_keyword appears in Primary Subcategory (or Notes) - this
     check wins even if an include_keyword also matched.

This is a blunt, cheap, local keyword filter, not a judgment call - it
exists purely to cut an expensive agent batch down to the rows worth
spending API budget on. Expect to still eyeball the output before running
batch_runner.py on it, especially for categories 6-8 below, which the
underlying advice itself flags as needing more screening.

Usage:
    python filter_smartscout_categories.py --list-categories
    python filter_smartscout_categories.py --csv smartscout_export.csv --category office_products
    python filter_smartscout_categories.py --csv smartscout_export.csv --category office_products -o office_filtered.csv
"""

import argparse
import csv
import re
from pathlib import Path

# Ordered per R&T's sourcing advice: best current-stage fit first. Each
# preset's include/exclude lists are short, lowercase keyword fragments
# (not full phrases) matched as substrings, for the best real-world recall
# against SmartScout's actual (short, specific) subcategory labels.
PRODUCT_FAMILY_FILTERS = {
    "office_products": {
        "rank": 1,
        "category_match": ["Office Products"],
        "include_keywords": ["label", "pen", "marker", "refill", "correction tape", "shipping tape", "mailing", "envelope", "sticker", "tape"],
        "exclude_keywords": ["furniture", "desk", "chair", "paper case", "ream", "printer ink", "toner", "ink cartridge"],
        "note": "Best starting fit. Screen inexpensive singles carefully for weak margins.",
    },
    "home_kitchen": {
        "rank": 2,
        "category_match": ["Home & Kitchen"],
        "include_keywords": ["coffee filter", "vacuum bag", "mop pad", "lint roller", "sponge", "cleaning accessory", "cleaning pad", "scrub"],
        "exclude_keywords": ["furniture", "decor", "appliance", "cookware", "dinnerware"],
        "note": "Focus on replacement supplies and consumables, not furniture/appliances/décor.",
    },
    "pet_supplies": {
        "rank": 3,
        "category_match": ["Pet Supplies"],
        "include_keywords": ["waste bag", "poop bag", "fountain filter", "aquarium filter", "filter cartridge", "filter media", "grooming"],
        "exclude_keywords": ["food", "treat", "supplement", "flea", "tick", "litter", "bed"],
        "note": "Nonfood products only for this first pass - food/treats/supplements/flea-tick/litter deferred.",
    },
    "industrial_scientific": {
        "rank": 4,
        "category_match": ["Industrial & Scientific"],
        "include_keywords": ["tape", "label", "janitorial", "disposable", "packaging"],
        "exclude_keywords": ["chemical", "medical", "safety equipment", "lab"],
        "note": "Worth a focused second search for business-use replenishment.",
    },
    "tools_home_improvement": {
        "rank": 5,
        "category_match": ["Tools & Home Improvement"],
        "include_keywords": ["sandpaper", "sanding disc", "blade refill", "replacement filter", "painting"],
        "exclude_keywords": ["power tool", "battery", "aerosol", "solvent"],
        "note": "More attention needed to compatibility/specifications than categories 1-4.",
    },
    "grocery_gourmet_food": {
        "rank": 6,
        "category_match": ["Grocery & Gourmet Food"],
        "include_keywords": ["tea", "spice", "seasoning", "dry", "shelf-stable", "shelf stable"],
        "exclude_keywords": ["refrigerat", "frozen", "glass", "perishable"],
        "note": "Shelf life and handling add complexity - defer refrigerated/meltable/glass-packed/short-dated.",
    },
    "health_household": {
        "rank": 7,
        "category_match": ["Health & Household"],
        "include_keywords": ["sponge", "cloth", "wipe", "filter", "refill", "pad"],
        "exclude_keywords": ["supplement", "medicine", "disinfect", "aerosol", "liquid", "fluid", "detergent", "spray"],
        "note": "Broad category - needs substantial additional filtering beyond this script. "
                "Narrowed to dry/physical replacement items; excludes liquids explicitly, not just the phrase 'liquid cleaner'.",
    },
    "beauty_personal_care": {
        "rank": 8,
        "category_match": ["Beauty & Personal Care"],
        "include_keywords": ["accessory", "nonmedicated", "brush", "applicator"],
        "exclude_keywords": ["liquid", "cream", "lotion", "serum"],
        "note": "Lowest priority - liquids/shelf-life/authenticity/brand-restriction screening work is heaviest here.",
    },
}

_CATEGORY_COL_CANDIDATES = ["Main Category", "Category"]
_SUBCATEGORY_COL_CANDIDATES = ["Primary Subcategory", "Subcategory"]
_NOTES_COL_CANDIDATES = ["Notes"]


def _contains_keyword(text: str, keyword: str) -> bool:
    """Word-start match, not a raw substring - a plain `in` check would
    match "tea" inside "steak", "pen" inside "suspend", etc. Only the left
    edge requires a word boundary (not both), so "sponge" still matches
    "Sponges"/"sponge-free" - a stricter \\bkeyword\\b would miss ordinary
    plurals like that."""
    return re.search(r"\b" + re.escape(keyword), text) is not None


def _find_col(fieldnames: list[str], candidates: list[str]) -> str | None:
    lower = {f.strip().lower(): f for f in fieldnames}
    for c in candidates:
        if c.lower() in lower:
            return lower[c.lower()]
    return None


def filter_rows(csv_path: str | Path, category: str) -> tuple[list[dict], list[str], dict]:
    """Returns (kept_rows, fieldnames_in_order, stats)."""
    preset = PRODUCT_FAMILY_FILTERS[category]
    csv_path = Path(csv_path)

    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    cat_col = _find_col(fieldnames, _CATEGORY_COL_CANDIDATES)
    subcat_col = _find_col(fieldnames, _SUBCATEGORY_COL_CANDIDATES)
    notes_col = _find_col(fieldnames, _NOTES_COL_CANDIDATES)
    if not cat_col or not subcat_col:
        raise ValueError(
            f"Could not find a category/subcategory column in {csv_path}. "
            f"Found headers: {fieldnames}. Looked for one of {_CATEGORY_COL_CANDIDATES} "
            f"and one of {_SUBCATEGORY_COL_CANDIDATES}."
        )

    category_matches = {c.lower() for c in preset["category_match"]}
    include_kw = [k.lower() for k in preset["include_keywords"]]
    exclude_kw = [k.lower() for k in preset["exclude_keywords"]]

    kept = []
    stats = {"total": len(rows), "wrong_category": 0, "no_include_match": 0, "excluded": 0, "kept": 0}
    for row in rows:
        cat = (row.get(cat_col) or "").strip().lower()
        text = " ".join([row.get(subcat_col) or "", row.get(notes_col) or "" if notes_col else ""]).lower()

        if cat not in category_matches:
            stats["wrong_category"] += 1
            continue
        if any(_contains_keyword(text, kw) for kw in exclude_kw):
            stats["excluded"] += 1
            continue
        if not any(_contains_keyword(text, kw) for kw in include_kw):
            stats["no_include_match"] += 1
            continue

        kept.append(row)
        stats["kept"] += 1

    return kept, fieldnames, stats


_DISTRIBUTOR_NAME_COL_CANDIDATES = ["Distributor", "Brand", "Brand Name", "Company", "Company Name"]
_DISTRIBUTOR_CATEGORY_COL_CANDIDATES = ["Primary Categories", "Category", "Categories"]


def load_distributor_rows_as_smartscout_rows(path: str | Path, fieldnames: list[str]) -> list[dict]:
    """Reads a distributor_master_list.csv-shaped file (Distributor, Primary
    Categories) and maps each row into the SmartScout export's column shape,
    so it can be appended to a filtered brand list and fed to the same
    batch_runner.py pipeline. Deliberately NOT passed through the
    category/keyword filter above - a hand-curated distributor list is
    already a small, vetted set of broad-line wholesalers, not an
    undifferentiated product-brand list that needs narrowing down."""
    path = Path(path)
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        src_fields = reader.fieldnames or []
        name_col = _find_col(src_fields, _DISTRIBUTOR_NAME_COL_CANDIDATES)
        cat_col = _find_col(src_fields, _DISTRIBUTOR_CATEGORY_COL_CANDIDATES)
        if not name_col:
            raise ValueError(f"{path}: couldn't find a distributor-name column among {_DISTRIBUTOR_NAME_COL_CANDIDATES}")
        rows = list(reader)

    name_dst = _find_col(fieldnames, ["Brand Name"]) or "Brand Name"
    notes_dst = _find_col(fieldnames, ["Notes"]) or "Notes"

    out = []
    for row in rows:
        name = (row.get(name_col) or "").strip()
        if not name:
            continue
        blank = {f: "" for f in fieldnames}
        blank[name_dst] = name
        if cat_col:
            blank[notes_dst] = f"From {path.name}: {row.get(cat_col, '').strip()}"
        out.append(blank)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", help="Path to the raw SmartScout brand export")
    parser.add_argument("--category", choices=sorted(PRODUCT_FAMILY_FILTERS, key=lambda k: PRODUCT_FAMILY_FILTERS[k]["rank"]))
    parser.add_argument("-o", "--output", help="Output CSV path (default: <input>_<category>.csv)")
    parser.add_argument("--list-categories", action="store_true", help="Print the available category presets and exit")
    parser.add_argument("--append-distributors", help="Also append rows from a distributor_master_list.csv-shaped file "
                                                        "(Distributor, Primary Categories) - added AFTER filtering, not through it")
    args = parser.parse_args()

    if args.list_categories:
        for key, preset in sorted(PRODUCT_FAMILY_FILTERS.items(), key=lambda kv: kv[1]["rank"]):
            print(f"{preset['rank']}. {key}  (SmartScout category: {', '.join(preset['category_match'])})")
            print(f"   include: {', '.join(preset['include_keywords'])}")
            print(f"   exclude: {', '.join(preset['exclude_keywords'])}")
            print(f"   {preset['note']}")
            print()
        return

    if not args.csv or not args.category:
        parser.error("--csv and --category are required (or pass --list-categories)")

    kept, fieldnames, stats = filter_rows(args.csv, args.category)

    appended = []
    if args.append_distributors:
        appended = load_distributor_rows_as_smartscout_rows(args.append_distributors, fieldnames)
        kept = kept + appended

    output = Path(args.output) if args.output else Path(args.csv).with_name(f"{Path(args.csv).stem}_{args.category}.csv")
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(kept)

    print(f"Read {stats['total']} row(s) from {args.csv}.")
    print(f"  {stats['wrong_category']} not in category '{PRODUCT_FAMILY_FILTERS[args.category]['category_match']}'")
    print(f"  {stats['excluded']} matched an exclude keyword")
    print(f"  {stats['no_include_match']} matched the category but no include keyword")
    print(f"  -> {stats['kept']} kept from the filter")
    if appended:
        print(f"  + {len(appended)} distributor(s) appended from {args.append_distributors} (not filtered - see note above)")
    print(f"  = {len(kept)} total rows written")
    print(f"\nWrote {output}")
    print(f"Next: eyeball {output.name} for anything that slipped through, then run:")
    print(f"  python batch_runner.py --csv {output} --limit 5   # cheap test first")


if __name__ == "__main__":
    main()

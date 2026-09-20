"""
Import candidate brands from a SmartScout (or similar) CSV export.

IMPORTANT - column names are a best guess:
This project has no live access to SmartScout's data or its export format
(it's a paid, external tool). The COLUMN_ALIASES map below is a best-effort
guess at common SmartScout export headers, based on publicly described
SmartScout features. Once you export a real file, open it and compare its
actual header row against COLUMN_ALIASES - if a column isn't being picked
up, just add its exact header text to the matching alias list below. No
other code needs to change.

Design principle (matches config.AI_RESTRICTIONS): whatever SmartScout told
you about a brand (seller count, whether Amazon is a seller, revenue, etc.)
is treated as a VERIFIED, user-supplied fact - it goes straight into the
Prospect's research_notes and into the manual notes handed to the Research
Agent, rather than being re-derived or guessed at. If a column is missing
for a row, that field is simply left out - never filled with a guess.
"""

import csv
from pathlib import Path

from models import Prospect

# canonical_field -> list of header names (case-insensitive) that might
# appear in a real export. Add to these lists once you see your real file.
COLUMN_ALIASES: dict[str, list[str]] = {
    "company_name": ["Brand", "Brand Name", "Company", "Company Name", "Seller Name"],
    "website": ["Website", "Brand Website", "URL", "Domain"],
    "category": ["Category", "Subcategory", "Amazon Category"],
    "seller_count": ["Seller Count", "Number of Sellers", "# Sellers", "Total Sellers", "Sellers"],
    "amazon_is_seller": ["Amazon Sells", "Amazon Is Seller", "Amazon Seller", "Sold by Amazon", "Amazon Competes"],
    "asin_count": ["Number of ASINs", "ASIN Count", "# ASINs", "ASINs"],
    "monthly_revenue": ["Monthly Revenue", "Est. Monthly Revenue", "Revenue", "Estimated Revenue"],
    "avg_price": ["Average Price", "Avg Price", "Price"],
    "margin_ratio": ["Margin Ratio", "ROI", "Profit Ratio", "Return Ratio"],
    "ungating_notes": ["Gating", "Ungating Requirement", "Ungating", "Restricted", "Gated Category"],
}


def _build_header_lookup(fieldnames: list[str]) -> dict[str, str]:
    """Map canonical_field -> the actual header text found in this CSV (or skip if absent)."""
    lower_fieldnames = {fn.strip().lower(): fn for fn in fieldnames}
    lookup: dict[str, str] = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            match = lower_fieldnames.get(alias.strip().lower())
            if match:
                lookup[canonical] = match
                break
    return lookup


def _format_smartscout_notes(row: dict, lookup: dict[str, str]) -> str:
    """Render whatever SmartScout columns were found for this row as plain-text verified facts."""
    lines = ["SmartScout export data (source: SmartScout, provided by user - treat as verified facts):"]
    label_map = {
        "category": "Category",
        "seller_count": "Seller count",
        "amazon_is_seller": "Amazon is a seller on this brand",
        "asin_count": "Number of ASINs",
        "monthly_revenue": "Estimated monthly revenue",
        "avg_price": "Average price",
        "margin_ratio": "Margin/ROI ratio",
        "ungating_notes": "Ungating requirement",
    }
    found_any = False
    for canonical, label in label_map.items():
        header = lookup.get(canonical)
        if header and row.get(header, "").strip():
            lines.append(f"- {label}: {row[header].strip()}")
            found_any = True
    if not found_any:
        lines.append("(No recognized SmartScout metric columns found for this row - "
                      "check COLUMN_ALIASES in smartscout_import.py against your export's headers.)")
    return "\n".join(lines)


def load_prospects_from_csv(csv_path: str | Path) -> list[tuple[Prospect, str]]:
    """
    Read a SmartScout-style CSV export and return a list of
    (Prospect, manual_research_notes) pairs, ready to feed into
    workflow.run_brand_acquisition() - one pair per row.

    Rows missing a recognizable company-name column are skipped with a
    printed warning rather than silently dropped.
    """
    csv_path = Path(csv_path)
    results: list[tuple[Prospect, str]] = []

    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"{csv_path} has no header row")
        lookup = _build_header_lookup(reader.fieldnames)

        if "company_name" not in lookup:
            raise ValueError(
                f"Could not find a company-name column in {csv_path}. "
                f"Found headers: {reader.fieldnames}. "
                f"Add your file's actual brand-name header to COLUMN_ALIASES['company_name']."
            )

        for i, row in enumerate(reader, start=2):  # start=2: row 1 is the header
            name = row.get(lookup["company_name"], "").strip()
            if not name:
                print(f"Skipping row {i}: no company name found")
                continue

            website = row.get(lookup.get("website", ""), "").strip() or None
            notes = _format_smartscout_notes(row, lookup)

            prospect = Prospect(company_name=name, website=website, research_notes=notes)
            results.append((prospect, notes))

    return results

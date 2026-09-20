"""
Standalone-mode brand input (Mode B): accepts the simplest formats already
used elsewhere in this project - a CLI comma-separated list (like
outreach's plain args), a CSV (reusing the same "guess the header, fall
back to first column" spirit as smartscout_import.py), a JSON list, or a
newline-separated text file.

Deduplication never silently merges an uncertain alias into another brand -
see dedupe_and_flag_aliases(). Exact duplicates (same name after
normalization) are the only thing collapsed automatically.
"""

import csv
import json
from pathlib import Path

from entity_resolution import normalize_company_name

_NAME_HEADER_CANDIDATES = {"brand", "brand name", "company", "company name"}


def brands_from_cli_list(raw: str) -> list[str]:
    return [name.strip() for name in raw.split(",") if name.strip()]


def brands_from_file(path: str | Path) -> list[str]:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _from_csv(path)
    if suffix == ".json":
        return _from_json(path)
    return _from_text(path)  # .txt or unspecified: newline-separated


def _from_csv(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        name_col = None
        if reader.fieldnames:
            name_col = next(
                (c for c in reader.fieldnames if c.strip().lower() in _NAME_HEADER_CANDIDATES), None
            )
        if name_col:
            return [row[name_col].strip() for row in reader if row.get(name_col, "").strip()]

    # No recognizable header - treat every non-empty first column as a name.
    with path.open(newline="", encoding="utf-8-sig") as f:
        return [row[0].strip() for row in csv.reader(f) if row and row[0].strip()]


def _from_json(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [str(item).strip() for item in data if str(item).strip()]
    if isinstance(data, dict) and isinstance(data.get("brands"), list):
        return [str(item).strip() for item in data["brands"] if str(item).strip()]
    raise ValueError(f"{path} must be a JSON list of brand names, or an object with a 'brands' list.")


def _from_text(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def dedupe_and_flag_aliases(brands: list[str]) -> tuple[list[str], list[tuple[str, str]]]:
    """
    Returns (deduped_brands, flagged_alias_pairs).

    Exact duplicates (identical after normalization) are collapsed to one
    entry - that's not an alias judgment call. Near-duplicates (one
    normalized name contains the other, e.g. "Acme" / "Acme Outdoor") are
    kept as SEPARATE entries in the output and returned in flagged_alias_pairs
    for a human to confirm - never silently merged.
    """
    seen: dict[str, str] = {}
    deduped: list[str] = []
    flagged: list[tuple[str, str]] = []

    for name in brands:
        key = normalize_company_name(name)
        if not key or key in seen:
            continue
        for existing_key, existing_name in seen.items():
            if key in existing_key or existing_key in key:
                flagged.append((existing_name, name))
        seen[key] = name
        deduped.append(name)

    return deduped, flagged

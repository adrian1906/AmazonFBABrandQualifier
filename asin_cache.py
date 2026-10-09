"""
Persistent UPC -> ASIN resolution cache, independent of any one catalog
file or supplier. A UPC-to-ASIN mapping is a fact about the PRODUCT, not
about this month's price list - once resolved, it's reused for free on
any future scan (this catalog re-run, a newer price list from the same
supplier, or even a different supplier selling the same product), rather
than re-paying a resolution call (whichever provider it costs - Keepa's
tight 1-token/minute default tier makes this especially valuable there,
but it saves calls against SP-API too).

Does NOT cache price/ROI data - that's deliberately NOT durable (prices
change; see config.CATALOG_DEFAULT_HISTORY_DAYS and the staleness
reasoning elsewhere in this project). Only the resolution itself is
cached, and even that expires after ASIN_CACHE_STALE_DAYS - rare but real
events (a listing merge, a UPC getting reused for a different product)
mean a mapping shouldn't be trusted forever without ever re-checking.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from catalog_models import AsinResolution

ASIN_CACHE_PATH = Path(__file__).parent / "catalog_data" / "asin_cache.json"
ASIN_CACHE_STALE_DAYS = 180  # see module docstring - resolution is far more stable than price, but not eternal


def _load_all() -> dict:
    if not ASIN_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(ASIN_CACHE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}  # a corrupt cache file degrades to "nothing cached," never a crash


def get_cached(upc: str, max_age_days: float = ASIN_CACHE_STALE_DAYS) -> Optional[AsinResolution]:
    """The cached AsinResolution for this UPC, or None if there's no
    entry or it's older than max_age_days. Pass max_age_days=None to
    accept any cached entry regardless of age."""
    if not upc:
        return None
    entry = _load_all().get(upc)
    if not entry:
        return None
    if max_age_days is not None:
        cached_at = datetime.fromisoformat(entry["cached_at"])
        if datetime.now(timezone.utc) - cached_at > timedelta(days=max_age_days):
            return None
    return AsinResolution.model_validate(entry["resolution"])


def save(upc: str, resolution: AsinResolution) -> None:
    """Caches a resolution result, keyed by the EXACT upc string passed in
    (the raw catalog value, or its leading-zero-corrected form if that's
    what actually resolved - catalog_scan.py caches whichever one worked,
    so a future lookup of either the raw or corrected UPC can hit)."""
    if not upc:
        return
    ASIN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    all_entries = _load_all()
    all_entries[upc] = {
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "resolution": resolution.model_dump(mode="json"),
    }
    ASIN_CACHE_PATH.write_text(json.dumps(all_entries, indent=2), encoding="utf-8")


def cache_size() -> int:
    return len(_load_all())

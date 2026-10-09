"""
Tests for asin_cache.py's persistent UPC -> ASIN resolution cache.
isolated_asin_cache (conftest.py, autouse) already redirects
ASIN_CACHE_PATH to a tmp_path, so these never touch the real project
cache file.
"""

from datetime import datetime, timedelta, timezone

import asin_cache
from catalog_models import AsinCandidate, AsinResolution


def _resolution(upc: str, asin: str = "B0TEST0001") -> AsinResolution:
    return AsinResolution(
        upc=upc,
        candidates=[AsinCandidate(asin=asin, match_status="needs_review", confidence="low")],
        resolution_reason="test",
        is_live_data=True,
    )


def test_get_cached_returns_none_when_nothing_saved():
    assert asin_cache.get_cached("034463016148") is None


def test_save_and_get_cached_round_trip():
    resolution = _resolution("034463016148")
    asin_cache.save("034463016148", resolution)

    cached = asin_cache.get_cached("034463016148")
    assert cached is not None
    assert cached.candidates[0].asin == "B0TEST0001"
    assert cached.is_live_data is True


def test_get_cached_returns_none_for_empty_upc():
    assert asin_cache.get_cached("") is None
    assert asin_cache.get_cached(None) is None


def test_save_ignores_empty_upc():
    asin_cache.save("", _resolution(""))
    assert asin_cache.cache_size() == 0


def test_stale_entry_is_not_returned(monkeypatch):
    asin_cache.save("034463016148", _resolution("034463016148"))

    # Force the cached entry to look old by rewriting its cached_at directly.
    import json
    data = json.loads(asin_cache.ASIN_CACHE_PATH.read_text(encoding="utf-8"))
    old = datetime.now(timezone.utc) - timedelta(days=200)
    data["034463016148"]["cached_at"] = old.isoformat()
    asin_cache.ASIN_CACHE_PATH.write_text(json.dumps(data), encoding="utf-8")

    assert asin_cache.get_cached("034463016148", max_age_days=180) is None
    assert asin_cache.get_cached("034463016148", max_age_days=None) is not None  # any age accepted


def test_cache_size_reflects_entries():
    assert asin_cache.cache_size() == 0
    asin_cache.save("111111111111", _resolution("111111111111"))
    asin_cache.save("222222222222", _resolution("222222222222"))
    assert asin_cache.cache_size() == 2


def test_corrupt_cache_file_degrades_to_empty_not_a_crash():
    asin_cache.ASIN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    asin_cache.ASIN_CACHE_PATH.write_text("{not valid json", encoding="utf-8")
    assert asin_cache.get_cached("034463016148") is None
    assert asin_cache.cache_size() == 0


def test_negative_no_match_resolution_is_also_cached():
    # Caching a genuine "no candidates" result is valuable too - avoids
    # re-querying a known dead-end UPC every scan.
    empty = AsinResolution(upc="999999999999", candidates=[], resolution_reason="No match.", is_live_data=True)
    asin_cache.save("999999999999", empty)
    cached = asin_cache.get_cached("999999999999")
    assert cached is not None
    assert cached.candidates == []

"""
Tests for keepa_adapter.py's rate limiter and 429 backoff/retry behavior.
No test here makes a real network call or a real time.sleep - both
requests.get and time.sleep are monkeypatched, so the suite stays fast
and deterministic regardless of what this logic would actually wait for.
"""

from decimal import Decimal

import pytest

import keepa_adapter as ka


# ---------------------------------------------------------------------------
# KeepaRateLimiter - pure pacing logic, no network involved.
# ---------------------------------------------------------------------------

def test_wait_for_tokens_noop_with_no_baseline_yet():
    limiter = ka.KeepaRateLimiter()
    limiter.wait_for_tokens(5)  # tokens_left/refill_rate still None - must not raise or hang


def test_wait_for_tokens_noop_when_enough_tokens(monkeypatch):
    sleeps = []
    monkeypatch.setattr(ka.time, "sleep", lambda s: sleeps.append(s))
    limiter = ka.KeepaRateLimiter()
    limiter.update_from_response({"tokensLeft": 10, "refillRate": 1})
    limiter.wait_for_tokens(5)
    assert sleeps == []


def test_wait_for_tokens_sleeps_proportionally_to_deficit(monkeypatch):
    sleeps = []
    monkeypatch.setattr(ka.time, "sleep", lambda s: sleeps.append(s))
    limiter = ka.KeepaRateLimiter()
    limiter.update_from_response({"tokensLeft": 0, "refillRate": 1})  # 1 token/min -> 60s/token
    limiter.wait_for_tokens(2)
    assert sleeps == [120.0]
    assert limiter.tokens_left == 2  # optimistic bump after the wait


def test_wait_for_tokens_caps_at_max_wait_seconds(monkeypatch):
    sleeps = []
    monkeypatch.setattr(ka.time, "sleep", lambda s: sleeps.append(s))
    limiter = ka.KeepaRateLimiter(max_wait_seconds=30.0)
    limiter.update_from_response({"tokensLeft": 0, "refillRate": 1})  # would need 600s uncapped
    limiter.wait_for_tokens(10)
    assert sleeps == [30.0]  # capped, not 600


def test_update_from_response_ignores_missing_or_malformed_fields():
    limiter = ka.KeepaRateLimiter()
    limiter.update_from_response({})
    assert limiter.tokens_left is None and limiter.refill_rate is None
    limiter.update_from_response({"tokensLeft": "not-an-int", "refillRate": -1})
    assert limiter.tokens_left is None and limiter.refill_rate is None
    limiter.update_from_response({"tokensLeft": 5, "refillRate": 20})
    assert limiter.tokens_left == 5 and limiter.refill_rate == 20


# ---------------------------------------------------------------------------
# LiveKeepaProvider._get - 429 backoff/retry, using fake requests.get.
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr(ka.time, "sleep", lambda s: None)


def test_get_succeeds_immediately_on_200(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(params)
        return _FakeResponse({"tokensLeft": 10, "refillRate": 1, "products": []})

    monkeypatch.setattr(ka.requests, "get", fake_get)
    provider = ka.LiveKeepaProvider("fake-key")
    data = provider._get("product", {"domain": 1, "code": "123"})

    assert data["products"] == []
    assert len(calls) == 1
    assert provider.rate_limiter.tokens_left == 10


def test_get_retries_on_429_then_succeeds(monkeypatch):
    responses = [
        _FakeResponse({"tokensLeft": -2, "refillRate": 1, "refillIn": 5000}, status_code=429),
        _FakeResponse({"tokensLeft": -1, "refillRate": 1, "refillIn": 3000}, status_code=429),
        _FakeResponse({"tokensLeft": 3, "refillRate": 1, "products": []}, status_code=200),
    ]
    call_count = {"n": 0}

    def fake_get(url, params=None, timeout=None):
        resp = responses[call_count["n"]]
        call_count["n"] += 1
        return resp

    monkeypatch.setattr(ka.requests, "get", fake_get)
    provider = ka.LiveKeepaProvider("fake-key")
    data = provider._get("product", {"domain": 1, "code": "123"})

    assert data["products"] == []
    assert call_count["n"] == 3  # two 429s, then success
    assert provider.rate_limiter.tokens_left == 3  # reflects the final real response, not a guess


def test_get_raises_after_exhausting_retries(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        return _FakeResponse({"tokensLeft": -5, "refillRate": 1, "refillIn": 1000}, status_code=429)

    monkeypatch.setattr(ka.requests, "get", fake_get)
    provider = ka.LiveKeepaProvider("fake-key", rate_limiter=ka.KeepaRateLimiter(max_retries=2))

    with pytest.raises(RuntimeError, match="HTTP 429"):
        provider._get("product", {"domain": 1, "code": "123"})


def test_resolve_upc_works_through_the_rate_limited_pipeline(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        return _FakeResponse({
            "tokensLeft": 50, "refillRate": 20,
            "products": [{"asin": "B0CLNWM532", "title": "1057 Extra Mature Scottish Cheddar, 7 OZ", "itemPackageQuantity": 1}],
        })

    monkeypatch.setattr(ka.requests, "get", fake_get)
    provider = ka.LiveKeepaProvider("fake-key")
    resolution = provider.resolve_upc("034463016148")

    assert resolution.is_live_data is True
    assert len(resolution.candidates) == 1
    assert resolution.candidates[0].asin == "B0CLNWM532"
    assert resolution.candidates[0].match_status == "needs_review"


# ---------------------------------------------------------------------------
# get_pricing_snapshot - using the REAL field shapes confirmed live
# 2026-10-08 (stats object keys, -1 sentinel, [time, price] pairs,
# Keepa Time Minutes), not a guessed schema.
# ---------------------------------------------------------------------------

def _stats_fixture(**overrides):
    base = {
        "current": [-1] * 36,
        "avg": [-1] * 36,
        "minInInterval": [None] * 36,
        "maxInInterval": [None] * 36,
        "outOfStockPercentageInInterval": [-1] * 36,
    }
    base.update(overrides)
    return base


def test_get_pricing_snapshot_no_data_in_window_is_none_not_zero(monkeypatch):
    # Real observed shape on a low-traffic ASIN: everything -1/None.
    def fake_get(url, params=None, timeout=None):
        return _FakeResponse({"products": [{"stats": _stats_fixture()}]})

    monkeypatch.setattr(ka.requests, "get", fake_get)
    provider = ka.LiveKeepaProvider("fake-key")
    snapshot = provider.get_pricing_snapshot("B0CLNWM532", 30)

    assert snapshot.planning_price is None
    assert snapshot.window_stats == {}
    assert snapshot.requires_review is True


def test_get_pricing_snapshot_uses_buy_box_when_available(monkeypatch):
    # keepa minutes 8209181 -> real UTC datetime via the confirmed formula.
    stats = _stats_fixture()
    stats["current"][18] = 1599  # $15.99
    stats["avg"][18] = 1550
    stats["minInInterval"][18] = [8209181, 1200]  # $12.00 low
    stats["outOfStockPercentageInInterval"][18] = 10

    def fake_get(url, params=None, timeout=None):
        return _FakeResponse({"products": [{"stats": stats}]})

    monkeypatch.setattr(ka.requests, "get", fake_get)
    provider = ka.LiveKeepaProvider("fake-key")
    snapshot = provider.get_pricing_snapshot("B0TEST", 30)

    assert snapshot.requires_review is False  # buy box IS the preferred series
    assert snapshot.current_buy_box == Decimal("15.99")
    assert snapshot.planning_price == Decimal("12.00")  # lower of current (15.99) and the 30-day low (12.00)
    ws = snapshot.window_stats[30]
    assert ws.raw_minimum == Decimal("12.00")
    assert ws.raw_minimum_at is not None
    assert ws.out_of_stock_days == 3.0  # 10% of 30 days


def test_get_pricing_snapshot_falls_back_and_flags_review(monkeypatch):
    # Buy Box (18) and NEW_FBA (10) empty; NEW (1) has real data - a real
    # pattern confirmed live on the sample catalog's own ASINs.
    stats = _stats_fixture()
    stats["current"][1] = 8952
    stats["minInInterval"][1] = [8190872, 8900]

    def fake_get(url, params=None, timeout=None):
        return _FakeResponse({"products": [{"stats": stats}]})

    monkeypatch.setattr(ka.requests, "get", fake_get)
    provider = ka.LiveKeepaProvider("fake-key")
    snapshot = provider.get_pricing_snapshot("B0TEST", 30)

    assert snapshot.requires_review is True  # fell back off buy_box_new_fba
    assert "FALLBACK" in snapshot.planning_price_basis
    assert snapshot.planning_price == Decimal("89.00")  # lower of current 89.52 and low 89.00


def test_get_pricing_snapshot_three_tuple_shipping_series_unwraps_to_price(monkeypatch):
    # BUY_BOX_SHIPPING's minInInterval entry can carry [price, shipping] as
    # its second element rather than a bare price - must still unwrap to
    # just the price, not crash or treat the pair as the price itself.
    stats = _stats_fixture()
    stats["minInInterval"][18] = [8209181, [1200, 599]]  # $12.00 price, $5.99 shipping

    def fake_get(url, params=None, timeout=None):
        return _FakeResponse({"products": [{"stats": stats}]})

    monkeypatch.setattr(ka.requests, "get", fake_get)
    provider = ka.LiveKeepaProvider("fake-key")
    snapshot = provider.get_pricing_snapshot("B0TEST", 30)
    assert snapshot.window_stats[30].raw_minimum == Decimal("12.00")


def test_keepa_time_conversion_matches_confirmed_formula():
    # Confirmed live: unix_seconds = (keepaMinutes + 21564000) * 60.
    dt = ka._keepa_time_to_datetime(8209181)
    assert dt is not None
    expected_unix = (8209181 + 21_564_000) * 60
    assert int(dt.timestamp()) == expected_unix


def test_cents_to_decimal_sentinel_and_shapes():
    assert ka._cents_to_decimal(-1) is None  # the sentinel - never a real $0
    assert ka._cents_to_decimal(None) is None
    assert ka._cents_to_decimal(1599) == Decimal("15.99")
    assert ka._cents_to_decimal([1200, 599]) == Decimal("12.00")  # 3-tuple collapse


def test_get_keepa_provider_shares_one_limiter_across_calls(monkeypatch):
    monkeypatch.setenv("KEEPA_API_KEY", "fake-key")
    provider = ka.get_keepa_provider()
    assert isinstance(provider, ka.LiveKeepaProvider)

    def fake_get(url, params=None, timeout=None):
        return _FakeResponse({"tokensLeft": 7, "refillRate": 1, "products": []})

    monkeypatch.setattr(ka.requests, "get", fake_get)
    provider.resolve_upc("111111111111")
    provider.resolve_upc("222222222222")

    # Same provider instance -> same limiter instance -> state persists
    # across both calls, which is what makes pacing work at all.
    assert provider.rate_limiter.tokens_left == 7

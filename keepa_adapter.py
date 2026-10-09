"""
ASIN resolution (UPC -> candidate ASINs) and price-history retrieval, via
Keepa's documented product-code lookup and historical-data APIs (FBA
Catalog Analyzer spec sections 2-3).

No KEEPA_API_KEY is configured in this environment. get_keepa_provider()
returns FixtureKeepaProvider by default, which synthesizes clearly-labeled
demo data (is_live_data=False everywhere, every candidate needs_review)
rather than making a real call or fabricating a confident match. This is
the spec's own explicit fallback: "Missing credentials should leave manual
imports and a labeled fixture/demo mode usable, without presenting mock
data as live." Set KEEPA_API_KEY in .env to switch to LiveKeepaProvider -
no other code change needed.

LiveKeepaProvider.resolve_upc calls Keepa's documented `/product` endpoint
(domain + code lookup). get_pricing_snapshot uses the same endpoint's
`stats=<days>` parameter (an arbitrary day count - not just 30/60/90/365 -
so a custom or seasonal window works too) to get Keepa's own server-side
min/max/avg over that window, rather than hand-decoding the raw per-minute
`csv` history arrays - more reliable, and verified live 2026-10-08 against
R&T's real account:
  - Confirmed real structure: `stats.minInInterval[idx]` is
    `[keepaTimeMinutes, priceCents]` (or `None` with zero data points in
    the window), `stats.current`/`stats.avg` are flat per-index arrays,
    -1 means no data at that index (never a real $0 price).
  - Confirmed Keepa Time Minutes conversion: unix_seconds = (keepaMinutes
    + 21564000) * 60.
  - Confirmed CSV series index mapping relevant here: 0=AMAZON,
    1=NEW (lowest 3rd-party new, any fulfillment), 10=NEW_FBA,
    18=BUY_BOX_SHIPPING (shipping-inclusive - the spec's preferred
    planning series). 7/18-29/32 are 3-element [time, price, shipping]
    entries where the 2-element docs phrasing doesn't quite apply - this
    module unwraps that to the price alone.
  - Confirmed on two real sample-catalog ASINs that a LOW-traffic item can
    legitimately have zero data in a 30-day window at indices 10/18 while
    its raw `csv` field still holds hundreds of real historical points at
    index 1 further back - i.e. "no data in this window" is a real,
    common outcome, not a parsing bug, and must stay None/reviewed rather
    than be read as "$0" or "out of stock forever."
Falls back through buy_box_new_fba -> lowest_new_fba -> lowest_new_all ->
amazon_retail (spec: "Any fallback to a different series must be visible
and require review before qualification" - see requires_review below).
"""

import os
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import requests

from catalog_models import AsinCandidate, AsinResolution, PriceSeries, PriceWindowStats, PricingSnapshot

KEEPA_BASE_URL = "https://api.keepa.com"
AMAZON_US_DOMAIN_ID = 1  # Keepa's documented domainId for amazon.com
KEEPA_EPOCH_OFFSET_MINUTES = 21_564_000  # confirmed live - see module docstring

# Price-series fallback order for the conservative planning price (spec
# section 3). Index values confirmed live - see module docstring.
_PLANNING_SERIES_FALLBACK = [
    ("buy_box_new_fba", 18),
    ("lowest_new_fba", 10),
    ("lowest_new_all", 1),
    ("amazon_retail", 0),
]


def _keepa_time_to_datetime(keepa_minutes: Optional[int]) -> Optional[datetime]:
    if keepa_minutes is None or keepa_minutes < 0:
        return None
    return datetime.fromtimestamp((keepa_minutes + KEEPA_EPOCH_OFFSET_MINUTES) * 60, tz=timezone.utc)


def _cents_to_decimal(cents) -> Optional[Decimal]:
    if isinstance(cents, list):  # 3-element [time, price, shipping] entries collapse to price alone
        cents = cents[0] if cents else None
    if cents is None or cents < 0:  # -1 sentinel = no data, never a real $0
        return None
    return Decimal(cents) / 100


class KeepaRateLimiter:
    """Paces LiveKeepaProvider's requests against Keepa's own reported
    token-bucket state, instead of a locally-guessed one. Every Keepa
    response carries tokensLeft/refillRate/refillIn at its root - even
    error responses - per https://keepa.com/api-docs/request-basics.html:
    "Every response will contain information about your current API
    access" and "For any status code other than 200, you will still
    receive the token bucket information in the error stream." This is
    the authoritative source of truth (it self-corrects if something else
    used the same key, or if the subscription tier changes), so this
    class never maintains its own independent token count.

    On a real 429 ("you are out of tokens"), waits for the server-reported
    refillIn and retries, up to max_retries - this is what makes a very
    slow plan (e.g. 1 token/minute) USABLE for a small batch rather than
    erroring out immediately, at the cost of the batch simply taking
    longer. A batch sized well beyond what the plan can refill in a
    reasonable time will still take that long - this paces requests, it
    doesn't make tokens appear faster.
    """

    def __init__(self, max_retries: int = 5, max_wait_seconds: float = 300.0):
        self.tokens_left: Optional[int] = None
        self.refill_rate: Optional[int] = None  # tokens generated per minute
        self.max_retries = max_retries
        # A cap on any single wait - refuses to silently block a batch run
        # for e.g. an hour because of one slow-tier token deficit; raises
        # instead so the caller sees it rather than a script that looks hung.
        self.max_wait_seconds = max_wait_seconds

    def update_from_response(self, data: dict) -> None:
        if isinstance(data.get("tokensLeft"), int):
            self.tokens_left = data["tokensLeft"]
        if isinstance(data.get("refillRate"), int) and data["refillRate"] > 0:
            self.refill_rate = data["refillRate"]

    def wait_for_tokens(self, needed: int) -> None:
        """Blocks until this object BELIEVES at least `needed` tokens are
        available, based on the last response seen. No-ops until the
        first real call establishes a baseline - deliberately optimistic
        on the very first request rather than refusing to start at all."""
        if self.tokens_left is None or self.refill_rate is None or self.tokens_left >= needed:
            return
        deficit = needed - self.tokens_left
        seconds_per_token = 60.0 / self.refill_rate
        wait_seconds = min(deficit * seconds_per_token, self.max_wait_seconds)
        print(f"[keepa_adapter] Pacing for rate limit: {self.tokens_left} token(s) left, "
              f"{self.refill_rate}/min refill rate - waiting {wait_seconds:.1f}s for {needed} needed.")
        time.sleep(wait_seconds)
        # Optimistic local bump so a batch of several calls in a row doesn't
        # re-wait on the same stale count - the next real response corrects
        # this either way (update_from_response always overwrites it).
        self.tokens_left += int(wait_seconds / seconds_per_token)


class KeepaProvider(ABC):
    is_live: bool

    @abstractmethod
    def resolve_upc(self, upc: str) -> AsinResolution: ...

    @abstractmethod
    def get_pricing_snapshot(self, asin: str, window_days: int) -> PricingSnapshot: ...


class FixtureKeepaProvider(KeepaProvider):
    """Deterministic, clearly-labeled demo data - the same UPC always maps
    to the same fake ASIN, so a repeat scan is stable to test against, but
    every object returned is explicitly unverified: is_live_data=False,
    match_status="needs_review", confidence="low". A fixture has no real
    listing to check a pack count or bundle composition against, so it
    never claims one - callers must override (see catalog_scan.py's
    override_asin_candidate) to get past needs_review in demo mode."""

    is_live = False

    def resolve_upc(self, upc: str) -> AsinResolution:
        if not upc:
            return AsinResolution(upc=upc or "", candidates=[], resolution_reason="No UPC supplied - nothing to resolve.", is_live_data=False)
        fake_asin = "B" + str(abs(hash(upc)) % 10**9).zfill(9)
        candidate = AsinCandidate(
            asin=fake_asin,
            is_variation_child=False,
            title=f"[FIXTURE/DEMO DATA] placeholder listing for UPC {upc}",
            listing_pack_quantity=None,
            item_package_quantity_raw=None,
            pack_relationship=None,
            match_status="needs_review",
            confidence="low",
            match_reasons=[
                "FIXTURE/DEMO MODE - no live Keepa data. Set KEEPA_API_KEY to resolve real candidates, "
                "or supply override_asin_candidate with a human-confirmed match.",
            ],
            evidence_sources=[],
        )
        return AsinResolution(
            upc=upc, candidates=[candidate], resolved_asin=None,
            resolution_reason="Fixture/demo mode - not a real resolution; requires manual confirmation or override.",
            is_live_data=False,
        )

    def get_pricing_snapshot(self, asin: str, window_days: int) -> PricingSnapshot:
        return PricingSnapshot(
            asin=asin, series=[], current_buy_box=None, window_stats={}, planning_price=None,
            planning_price_basis="FIXTURE/DEMO MODE - no price data available; set KEEPA_API_KEY for live pricing.",
            requires_review=True,
        )


class LiveKeepaProvider(KeepaProvider):
    is_live = True

    def __init__(self, api_key: str, rate_limiter: Optional[KeepaRateLimiter] = None):
        self.api_key = api_key
        # One limiter per provider INSTANCE, shared across every call made
        # through it - get_keepa_provider() returns one instance per batch
        # run, so this correctly tracks the token budget across the whole
        # run's sequential calls (catalog_batch.py's run_batch loop isn't
        # concurrent today - see its docstring - so no locking is needed).
        self.rate_limiter = rate_limiter or KeepaRateLimiter()

    def _get(self, path: str, params: dict, token_cost: int = 1) -> dict:
        self.rate_limiter.wait_for_tokens(token_cost)

        for attempt in range(self.rate_limiter.max_retries + 1):
            response = requests.get(f"{KEEPA_BASE_URL}/{path}", params={**params, "key": self.api_key}, timeout=30)
            try:
                data = response.json()
            except ValueError:
                data = {}
            self.rate_limiter.update_from_response(data)

            if response.status_code == 429:
                if attempt >= self.rate_limiter.max_retries:
                    response.raise_for_status()  # retries exhausted - raise the real HTTPError
                refill_in_seconds = min((data.get("refillIn") or 60_000) / 1000.0, self.rate_limiter.max_wait_seconds)
                print(f"[keepa_adapter] 429 - out of tokens (attempt {attempt + 1}/{self.rate_limiter.max_retries}). "
                      f"Keepa reports refill in {refill_in_seconds:.1f}s - retrying after that.")
                time.sleep(max(refill_in_seconds, 1.0))
                continue

            response.raise_for_status()
            return data

        raise RuntimeError("unreachable - the retry loop above always returns or raises")

    def resolve_upc(self, upc: str) -> AsinResolution:
        data = self._get("product", {"domain": AMAZON_US_DOMAIN_ID, "code": upc}, token_cost=1)
        products = data.get("products") or []
        candidates = [
            AsinCandidate(
                asin=p.get("asin", ""),
                title=p.get("title"),
                item_package_quantity_raw=p.get("itemPackageQuantity") or p.get("numberOfItems"),
                match_status="needs_review",  # never auto-verified - pack/variation still needs human confirmation
                confidence="low",
                match_reasons=["Live Keepa product-code match - pack count and variation status still require manual confirmation."],
                evidence_sources=["keepa:product"],
            )
            for p in products
        ]
        reason = (
            "Exactly one Keepa candidate returned - still needs_review until pack/variation is confirmed."
            if len(candidates) == 1
            else f"{len(candidates)} Keepa candidate(s) returned - cannot auto-resolve; needs manual review."
        )
        return AsinResolution(upc=upc, candidates=candidates, resolved_asin=None, resolution_reason=reason, is_live_data=True)

    def get_pricing_snapshot(self, asin: str, window_days: int) -> PricingSnapshot:
        """window_days is passed straight through as Keepa's `stats`
        parameter - any positive integer works (30/60/90/365/730/a custom
        number), not just the fixed buckets Keepa also separately reports
        (avg30/avg90/avg180/avg365) - so a seasonal product can use 365 or
        730 (config.CATALOG_SEASONAL_HISTORY_DAYS/_EXTENDED_HISTORY_DAYS)
        exactly as easily as a 30-day window."""
        data = self._get("product", {"domain": AMAZON_US_DOMAIN_ID, "asin": asin, "stats": window_days}, token_cost=1)
        products = data.get("products") or []
        stats = (products[0].get("stats") if products else None) or {}
        if not stats:
            return PricingSnapshot(
                asin=asin, series=[], window_stats={}, planning_price=None,
                planning_price_basis="No Keepa stats available for this ASIN/window.", requires_review=True,
            )

        def min_in_interval(idx: int) -> tuple[Optional[datetime], Optional[Decimal]]:
            arr = stats.get("minInInterval")
            entry = arr[idx] if arr and idx < len(arr) else None
            if not entry:
                return None, None
            return _keepa_time_to_datetime(entry[0]), _cents_to_decimal(entry[1])

        def flat(field: str, idx: int) -> Optional[Decimal]:
            arr = stats.get(field)
            return _cents_to_decimal(arr[idx]) if arr and idx < len(arr) else None

        def out_of_stock_days(idx: int) -> Optional[float]:
            arr = stats.get("outOfStockPercentageInInterval")
            pct = arr[idx] if arr and idx < len(arr) else None
            return (pct / 100 * window_days) if isinstance(pct, (int, float)) and pct >= 0 else None

        chosen_name = chosen_idx = min_at = min_price = None
        for name, idx in _PLANNING_SERIES_FALLBACK:
            at, price = min_in_interval(idx)
            if price is not None:
                chosen_name, chosen_idx, min_at, min_price = name, idx, at, price
                break

        current = flat("current", chosen_idx) if chosen_idx is not None else None
        mean = flat("avg", chosen_idx) if chosen_idx is not None else None
        oos_days = out_of_stock_days(chosen_idx) if chosen_idx is not None else None
        # Spec: "Any fallback to a different series must be visible and
        # require review before qualification" - the default/preferred
        # series is buy_box_new_fba; anything else used is a fallback.
        used_fallback = chosen_name is not None and chosen_name != "buy_box_new_fba"

        window_stats: dict[int, PriceWindowStats] = {}
        planning_price = planning_basis = None
        if min_price is not None or current is not None:
            window_stats[window_days] = PriceWindowStats(
                window_days=window_days,
                coverage_days=max(window_days - (oos_days or 0.0), 0.0),
                raw_minimum=min_price, raw_minimum_at=min_at,
                time_weighted_mean=mean, time_weighted_median=None,  # Keepa's stats object has no median field
                out_of_stock_days=oos_days or 0.0,
                # Neither "most recent point older than the window" nor
                # "minimum held for a suspiciously short time" is computed
                # yet - both need the raw per-point csv series, not just
                # Keepa's aggregated stats object. Left False, not guessed.
                is_stale=False, suspicious_brief_low=False,
            )
            candidates = [v for v in (current, min_price) if v is not None]
            planning_price = min(candidates) if candidates else None
            series_label = chosen_name or "unknown"
            fallback_note = " [FALLBACK series - not the preferred Buy Box]" if used_fallback else ""
            planning_basis = (
                f"Lower of current ({current if current is not None else 'unavailable'}) and "
                f"{window_days}-day low ({min_price if min_price is not None else 'unavailable'}) "
                f"from Keepa's '{series_label}' series{fallback_note}"
            )

        return PricingSnapshot(
            asin=asin,
            series=[PriceSeries(name=chosen_name or "buy_box_new_fba", retrieved_at=datetime.now(timezone.utc),
                                 points=[], source="keepa", is_live_data=True)],
            current_buy_box=current if chosen_name == "buy_box_new_fba" else None,
            window_stats=window_stats,
            planning_price=planning_price,
            planning_price_basis=planning_basis or "No valid price data available from Keepa for this ASIN/window.",
            requires_review=used_fallback or not window_stats,
        )


def get_keepa_provider() -> KeepaProvider:
    """FixtureKeepaProvider unless an API key is set in the environment -
    see module docstring. This is the ONE place that decision is made.
    Checks KEEPA_API_KEY (the documented/.env.example name) first, then
    KEEPAAPIKEY as a fallback, since that's the name that ended up in this
    project's real .env - no reason to force a rename for this one."""
    api_key = os.getenv("KEEPA_API_KEY") or os.getenv("KEEPAAPIKEY")
    if api_key:
        return LiveKeepaProvider(api_key)
    return FixtureKeepaProvider()

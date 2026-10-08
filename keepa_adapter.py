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
(domain + code lookup) - straightforward and low-risk to implement without
a live key to test against. get_pricing_snapshot is intentionally left as
a documented NotImplementedError: Keepa's historical-data encoding (minutes
since epoch, price in cents, -1 out-of-stock sentinel, per-series CSV
index) is exactly the kind of thing this project's own conventions say to
live-test before trusting (see README's "live-test real API behavior"
discipline) - implementing it from documentation alone, with no account to
verify a real response against, risks silently misdecoding real price
history. Finish this once a Keepa key is available; verify current docs
at https://keepa.com/api-docs/ first, since plans/schemas can change.
"""

import os
from abc import ABC, abstractmethod
from typing import Optional

import requests

from catalog_models import AsinCandidate, AsinResolution, PricingSnapshot

KEEPA_BASE_URL = "https://api.keepa.com"
AMAZON_US_DOMAIN_ID = 1  # Keepa's documented domainId for amazon.com


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

    def __init__(self, api_key: str):
        self.api_key = api_key

    def _get(self, path: str, params: dict) -> dict:
        response = requests.get(f"{KEEPA_BASE_URL}/{path}", params={**params, "key": self.api_key}, timeout=30)
        response.raise_for_status()
        return response.json()

    def resolve_upc(self, upc: str) -> AsinResolution:
        data = self._get("product", {"domain": AMAZON_US_DOMAIN_ID, "code": upc})
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
        raise NotImplementedError(
            "LiveKeepaProvider.get_pricing_snapshot isn't implemented - Keepa's historical-price encoding "
            "(minutes-since-epoch timestamps, price in cents, -1 out-of-stock sentinel, per-series CSV index) "
            "needs to be verified against a real response before trusting it with financial calculations. "
            "See this module's docstring. Until then, supply override_selling_price to catalog_scan.scan_row "
            "for a manually-confirmed price instead."
        )


def get_keepa_provider() -> KeepaProvider:
    """FixtureKeepaProvider unless KEEPA_API_KEY is set in the environment -
    see module docstring. This is the ONE place that decision is made."""
    api_key = os.getenv("KEEPA_API_KEY")
    if api_key:
        return LiveKeepaProvider(api_key)
    return FixtureKeepaProvider()

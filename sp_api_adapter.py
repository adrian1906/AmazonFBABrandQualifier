"""
Amazon Selling Partner API (SP-API) adapter - Catalog Items (UPC -> ASIN
lookup) and Product Fees (real referral/FBA fee estimates), as an
official, ToS-compliant supplement to keepa_adapter.py's Keepa-only path.

Configured via the three env vars already in this project's .env (not
renamed here, to avoid touching that file):

    AMAZONSOLUTIONCLIENTID      - LWA Client ID
    AMAZONSOLUTIONSECRET        - LWA Client Secret
    AMAZONSOLUTIONREFRESHTOKEN  - long-lived refresh token from the
                                   one-time self-authorization/consent flow

is_configured() is False unless all three are set. Every other function
here raises a clear RuntimeError if called while unconfigured - it never
silently falls back to fixture data pretending to be a live SP-API call;
a caller wanting a graceful fallback (see catalog_scan.py) checks
is_configured() first.

As of October 2, 2023, SP-API no longer requires AWS IAM/SigV4 request
signing - only the LWA access token (as the x-amz-access-token header)
plus a User-Agent header. Source:
https://developer-docs.amazon.com/sp-api/changelog/sp-api-will-no-longer-require-aws-iam-or-aws-signature-version-4

Endpoint paths/schemas verified 2026-10-08 against Amazon's own model
files at https://github.com/amzn/selling-partner-api-models, AND live-
tested the same day against R&T's real SP-API account:
  - get_access_token(): confirmed working (real LWA token obtained).
  - resolve_upc(): confirmed working - a real UPC from the DC 55 sample
    catalog correctly resolved to a real ASIN (B0CLNWM532, "1057 Extra
    Mature Scottish Cheddar, 7 OZ" - matching the catalog row exactly).
  - get_fees_estimate(): confirmed working for IsAmazonFulfilled=False
    (FBM) - a real ReferralFee came back ($2.40 on a $15.99 price, i.e.
    ~15.0%, matching config.CATALOG_REFERRAL_RATE_FALLBACK almost
    exactly for this category). IsAmazonFulfilled=True (FBA) returned
    "Status": "ClientError"/"InvalidParameterValue" for this SPECIFIC
    ASIN - not a bug in this adapter, but a real finding: this product
    is refrigerated/perishable, and Amazon's FBA fee estimate correctly
    refuses an item that isn't eligible for standard (non-temperature-
    controlled) FBA. extract_fee_components() surfaces that as a
    status="unknown" result with the reason attached, not a crash and
    not a fabricated number - a real seller should expect the same
    failure mode for other FBA-ineligible items (hazmat, oversize, etc).
"""

import os
import time
from decimal import Decimal
from typing import Optional

import requests

from catalog_models import AsinCandidate, AsinResolution

LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"
SPAPI_NA_HOST = "https://sellingpartnerapi-na.amazon.com"
US_MARKETPLACE_ID = "ATVPDKIKX0DER"

_CLIENT_ID_VAR = "AMAZONSOLUTIONCLIENTID"
_CLIENT_SECRET_VAR = "AMAZONSOLUTIONSECRET"
_REFRESH_TOKEN_VAR = "AMAZONSOLUTIONREFRESHTOKEN"

_cached_token: Optional[str] = None
_cached_token_expiry: float = 0.0


def is_configured() -> bool:
    return all(os.getenv(v) for v in (_CLIENT_ID_VAR, _CLIENT_SECRET_VAR, _REFRESH_TOKEN_VAR))


def get_access_token(force_refresh: bool = False) -> str:
    """Exchanges the refresh token for a short-lived (~1hr) LWA access
    token - cached in-memory, only re-requested once it's within 60s of
    expiring rather than on every single API call."""
    global _cached_token, _cached_token_expiry

    if not is_configured():
        missing = [v for v in (_CLIENT_ID_VAR, _CLIENT_SECRET_VAR, _REFRESH_TOKEN_VAR) if not os.getenv(v)]
        raise RuntimeError(f"SP-API not configured - missing env var(s): {', '.join(missing)}")

    if not force_refresh and _cached_token and time.time() < _cached_token_expiry - 60:
        return _cached_token

    response = requests.post(
        LWA_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": os.getenv(_REFRESH_TOKEN_VAR),
            "client_id": os.getenv(_CLIENT_ID_VAR),
            "client_secret": os.getenv(_CLIENT_SECRET_VAR),
        },
        headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    _cached_token = data["access_token"]
    _cached_token_expiry = time.time() + data.get("expires_in", 3600)
    return _cached_token


def _headers() -> dict:
    return {
        "x-amz-access-token": get_access_token(),
        "user-agent": "RT-Distribution-Catalog-Analyzer/1.0 (Language=Python)",
        "content-type": "application/json",
    }


def resolve_upc(upc: str, marketplace_id: str = US_MARKETPLACE_ID) -> AsinResolution:
    """UPC -> candidate ASIN(s) via the official Catalog Items API -
    Amazon's own sanctioned equivalent to Keepa's product-code lookup.
    This still never auto-verifies a pack match (this endpoint doesn't
    return that), so every candidate stays needs_review until confirmed
    some other way - same honesty rule as keepa_adapter.py."""
    response = requests.get(
        f"{SPAPI_NA_HOST}/catalog/2022-04-01/items",
        headers=_headers(),
        params={
            "identifiers": upc, "identifiersType": "UPC",
            "marketplaceIds": marketplace_id, "includedData": "summaries",
        },
        timeout=30,
    )
    response.raise_for_status()
    items = response.json().get("items", [])

    candidates = []
    for item in items:
        summaries = item.get("summaries") or []
        title = summaries[0].get("itemName") if summaries else None
        candidates.append(AsinCandidate(
            asin=item.get("asin", ""),
            title=title,
            match_status="needs_review",
            confidence="low",
            match_reasons=["Live SP-API Catalog Items match by UPC - pack count and variation status still require manual confirmation."],
            evidence_sources=["sp-api:catalog-items"],
        ))

    if not candidates:
        reason = "No SP-API catalog match found for this UPC."
    elif len(candidates) == 1:
        reason = "Exactly one SP-API candidate - still needs_review until pack/variation is confirmed."
    else:
        reason = f"{len(candidates)} SP-API candidate(s) - cannot auto-resolve; needs manual review."

    return AsinResolution(upc=upc, candidates=candidates, resolved_asin=None, resolution_reason=reason, is_live_data=True)


def get_fees_estimate(
    asin: str, listing_price: Decimal, *, is_amazon_fulfilled: bool = True,
    marketplace_id: str = US_MARKETPLACE_ID, currency_code: str = "USD",
) -> dict:
    """Real referral + FBA fulfillment fee estimate for one ASIN at one
    price, from Amazon's own Product Fees API - replaces this project's
    15% referral-rate GUESS and its unknown fba_fulfillment_fee cost-ledger
    line with verified numbers once wired into catalog_scan.py. Returns
    the raw FeesEstimateResult dict; the caller extracts the specific
    components it needs (this function doesn't interpret the response, to
    avoid silently mis-mapping a field before it's been live-verified)."""
    body = {
        "FeesEstimateRequest": {
            "MarketplaceId": marketplace_id,
            "IsAmazonFulfilled": is_amazon_fulfilled,
            "PriceToEstimateFees": {"ListingPrice": {"CurrencyCode": currency_code, "Amount": float(listing_price)}},
            "Identifier": f"catalog-analyzer-{asin}",
        }
    }
    response = requests.post(
        f"{SPAPI_NA_HOST}/products/fees/v0/items/{asin}/feesEstimate",
        headers=_headers(), json=body, timeout=30,
    )
    response.raise_for_status()
    return response.json()


def extract_fee_components(fees_response: dict) -> dict[str, Optional[Decimal]]:
    """Pulls {"referral_fee": Decimal, "fba_fulfillment_fee": Decimal} out
    of a get_fees_estimate() response, for feeding straight into
    catalog_scan.py's cost ledger as verified CostComponents. Returns
    {"referral_fee": None, "fba_fulfillment_fee": None} - never a
    fabricated 0 - whenever the result's Status isn't "Success" (e.g. the
    real ClientError case live-tested on an FBA-ineligible refrigerated
    item - see this module's docstring).

    FeeType has no documented fixed enum (Amazon's own model schema
    defines it as a free-form string with no exhaustive value list), and
    this project's one live test used IsAmazonFulfilled=False, which
    never returns an FBA fulfillment fee line at all - only "ReferralFee"
    is confirmed against a real response. The FBA line is matched by
    "fba" appearing anywhere in the FeeType string (case-insensitive)
    rather than one specific unverified exact constant - broader than a
    guessed exact string, but still flagged here as NOT live-verified.
    Re-check this against a real IsAmazonFulfilled=True success response
    (an FBA-eligible ASIN) before trusting it unattended."""
    result = (fees_response.get("payload") or {}).get("FeesEstimateResult") or {}
    if result.get("Status") != "Success":
        return {"referral_fee": None, "fba_fulfillment_fee": None}

    fees: dict[str, Optional[Decimal]] = {"referral_fee": None, "fba_fulfillment_fee": None}
    for detail in (result.get("FeesEstimate") or {}).get("FeeDetailList") or []:
        fee_type = detail.get("FeeType") or ""
        amount = (detail.get("FinalFee") or {}).get("Amount")
        if amount is None:
            continue
        if fee_type == "ReferralFee":
            fees["referral_fee"] = Decimal(str(amount))
        elif "fba" in fee_type.lower():  # NOT live-verified - see docstring above
            fees["fba_fulfillment_fee"] = Decimal(str(amount))
    return fees


class SpApiCatalogProvider:
    """Implements the same interface as keepa_adapter.KeepaProvider
    (resolve_upc / get_pricing_snapshot / is_live), so catalog_scan.py can
    take this as a drop-in `provider=` instead of a Keepa one - see
    catalog_batch.py's provider selection, which prefers this when SP-API
    is configured (it's the official, ToS-sanctioned source) and falls
    back to Keepa otherwise. Only resolve_upc is implemented for real;
    SP-API's equivalent of Keepa's historical price tracking (the
    Product Pricing API) isn't wired in yet - get_pricing_snapshot says so
    explicitly rather than silently returning nothing useful."""

    is_live = True

    def resolve_upc(self, upc: str) -> AsinResolution:
        return resolve_upc(upc)

    def get_pricing_snapshot(self, asin: str, window_days: int):
        raise NotImplementedError(
            "SpApiCatalogProvider.get_pricing_snapshot isn't implemented - SP-API's current-price data "
            "(Product Pricing API) hasn't been wired in or live-tested yet. Use keepa_adapter's provider "
            "for pricing, or supply override_selling_price to catalog_scan.scan_row directly."
        )

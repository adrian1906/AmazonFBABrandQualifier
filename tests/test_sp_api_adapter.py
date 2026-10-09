"""
Tests for sp_api_adapter.py. No test here makes a real network call (same
rule as the rest of this project) - requests.post/get are monkeypatched.
The response fixtures below are the ACTUAL shapes captured live against
R&T's real SP-API account on 2026-10-08 (see sp_api_adapter.py's module
docstring), not guessed-at schemas.
"""

from decimal import Decimal

import pytest

import sp_api_adapter as spa

# Real response captured live: UPC 034463016148 -> ASIN B0CLNWM532.
REAL_CATALOG_RESPONSE = {
    "items": [{
        "asin": "B0CLNWM532",
        "summaries": [{"itemName": "1057 Extra Mature Scottish Cheddar, 7 OZ", "marketplaceId": "ATVPDKIKX0DER"}],
    }]
}

# Real response captured live: get_fees_estimate(B0CLNWM532, $15.99, IsAmazonFulfilled=False).
REAL_FEES_SUCCESS_RESPONSE = {
    "payload": {
        "FeesEstimateResult": {
            "Status": "Success",
            "FeesEstimateIdentifier": {"IsAmazonFulfilled": False},
            "FeesEstimate": {
                "TotalFeesEstimate": {"CurrencyCode": "USD", "Amount": 2.4},
                "FeeDetailList": [
                    {"FeeType": "ReferralFee", "FeeAmount": {"CurrencyCode": "USD", "Amount": 2.4},
                     "FinalFee": {"CurrencyCode": "USD", "Amount": 2.4}, "FeePromotion": {"CurrencyCode": "USD", "Amount": 0.0}},
                    {"FeeType": "VariableClosingFee", "FeeAmount": {"CurrencyCode": "USD", "Amount": 0.0},
                     "FinalFee": {"CurrencyCode": "USD", "Amount": 0.0}, "FeePromotion": {"CurrencyCode": "USD", "Amount": 0.0}},
                    {"FeeType": "PerItemFee", "FeeAmount": {"CurrencyCode": "USD", "Amount": 0.0},
                     "FinalFee": {"CurrencyCode": "USD", "Amount": 0.0}, "FeePromotion": {"CurrencyCode": "USD", "Amount": 0.0}},
                ],
            },
        }
    }
}

# Real response captured live: same call with IsAmazonFulfilled=True - this
# specific item (refrigerated cheese) isn't FBA-eligible.
REAL_FEES_CLIENT_ERROR_RESPONSE = {
    "payload": {
        "FeesEstimateResult": {
            "Status": "ClientError",
            "FeesEstimateIdentifier": {"IsAmazonFulfilled": True},
            "Error": {"Type": "Sender", "Code": "InvalidParameterValue", "Message": "There is an client-side error. Please verify your inputs.", "Detail": []},
        }
    }
}


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
def _reset_token_cache():
    spa._cached_token = None
    spa._cached_token_expiry = 0.0
    yield
    spa._cached_token = None
    spa._cached_token_expiry = 0.0


@pytest.fixture
def configured_env(monkeypatch):
    monkeypatch.setenv(spa._CLIENT_ID_VAR, "fake-client-id")
    monkeypatch.setenv(spa._CLIENT_SECRET_VAR, "fake-client-secret")
    monkeypatch.setenv(spa._REFRESH_TOKEN_VAR, "fake-refresh-token")


def test_is_configured_false_when_any_var_missing(monkeypatch):
    monkeypatch.delenv(spa._CLIENT_ID_VAR, raising=False)
    monkeypatch.delenv(spa._CLIENT_SECRET_VAR, raising=False)
    monkeypatch.delenv(spa._REFRESH_TOKEN_VAR, raising=False)
    assert spa.is_configured() is False


def test_is_configured_true_when_all_vars_present(configured_env):
    assert spa.is_configured() is True


def test_get_access_token_raises_clear_error_when_unconfigured(monkeypatch):
    monkeypatch.delenv(spa._CLIENT_ID_VAR, raising=False)
    monkeypatch.delenv(spa._CLIENT_SECRET_VAR, raising=False)
    monkeypatch.delenv(spa._REFRESH_TOKEN_VAR, raising=False)
    with pytest.raises(RuntimeError, match="not configured"):
        spa.get_access_token()


def test_get_access_token_exchanges_and_caches(configured_env, monkeypatch):
    calls = []

    def fake_post(url, data=None, headers=None, timeout=None):
        calls.append(url)
        return _FakeResponse({"access_token": "Atza|fake", "expires_in": 3600})

    monkeypatch.setattr(spa.requests, "post", fake_post)

    token1 = spa.get_access_token()
    token2 = spa.get_access_token()  # should hit the cache, not request again

    assert token1 == token2 == "Atza|fake"
    assert calls == [spa.LWA_TOKEN_URL]  # only one real exchange happened


def test_resolve_upc_parses_real_captured_response(configured_env, monkeypatch):
    monkeypatch.setattr(spa, "get_access_token", lambda: "fake-token")
    monkeypatch.setattr(spa.requests, "get", lambda *a, **k: _FakeResponse(REAL_CATALOG_RESPONSE))

    resolution = spa.resolve_upc("034463016148")

    assert resolution.is_live_data is True
    assert len(resolution.candidates) == 1
    candidate = resolution.candidates[0]
    assert candidate.asin == "B0CLNWM532"
    assert candidate.title == "1057 Extra Mature Scottish Cheddar, 7 OZ"
    assert candidate.match_status == "needs_review"  # never auto-verified, even on a clean single match


def test_resolve_upc_no_match(configured_env, monkeypatch):
    monkeypatch.setattr(spa, "get_access_token", lambda: "fake-token")
    monkeypatch.setattr(spa.requests, "get", lambda *a, **k: _FakeResponse({"items": []}))

    resolution = spa.resolve_upc("000000000000")
    assert resolution.candidates == []
    assert "No SP-API catalog match" in resolution.resolution_reason


def test_extract_fee_components_success_case():
    fees = spa.extract_fee_components(REAL_FEES_SUCCESS_RESPONSE)
    assert fees["referral_fee"] == Decimal("2.4")
    assert fees["fba_fulfillment_fee"] is None  # correct absence - this call was FBM


def test_extract_fee_components_client_error_never_fabricates_zero():
    fees = spa.extract_fee_components(REAL_FEES_CLIENT_ERROR_RESPONSE)
    assert fees["referral_fee"] is None
    assert fees["fba_fulfillment_fee"] is None


def test_extract_fee_components_empty_response():
    assert spa.extract_fee_components({}) == {"referral_fee": None, "fba_fulfillment_fee": None}


def test_sp_api_catalog_provider_resolve_upc(configured_env, monkeypatch):
    monkeypatch.setattr(spa, "get_access_token", lambda: "fake-token")
    monkeypatch.setattr(spa.requests, "get", lambda *a, **k: _FakeResponse(REAL_CATALOG_RESPONSE))

    provider = spa.SpApiCatalogProvider()
    assert provider.is_live is True
    resolution = provider.resolve_upc("034463016148")
    assert resolution.candidates[0].asin == "B0CLNWM532"


def test_sp_api_catalog_provider_pricing_not_implemented():
    provider = spa.SpApiCatalogProvider()
    with pytest.raises(NotImplementedError):
        provider.get_pricing_snapshot("B0CLNWM532", 90)

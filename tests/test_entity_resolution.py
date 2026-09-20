from entity_resolution import (
    EntityIndex, normalize_company_name, normalize_domain, normalize_phone,
)
from models import SupplierCandidate


def test_normalize_company_name_strips_legal_suffix_and_punctuation():
    assert normalize_company_name("Acme Wholesale, LLC") == "acme wholesale"
    assert normalize_company_name("ACME WHOLESALE Inc.") == "acme wholesale"


def test_normalize_domain_strips_www_and_path():
    assert normalize_domain("https://www.acmewholesale.com/wholesale-app") == "acmewholesale.com"
    assert normalize_domain("acmewholesale.com") == "acmewholesale.com"
    assert normalize_domain(None) is None


def test_normalize_phone_strips_formatting_and_country_code():
    assert normalize_phone("(410) 555-0100") == "4105550100"
    assert normalize_phone("+1 410-555-0100") == "4105550100"


def test_same_domain_resolves_to_same_supplier_id_despite_name_variation():
    index = EntityIndex()
    a = SupplierCandidate(legal_business_name="Acme Wholesale LLC", website="https://www.acmewholesale.com")
    b = SupplierCandidate(legal_business_name="ACME WHOLESALE, Inc.", website="https://acmewholesale.com/apply")
    assert index.resolve(a) == index.resolve(b)


def test_different_companies_get_different_supplier_ids():
    index = EntityIndex()
    a = SupplierCandidate(legal_business_name="Acme Wholesale LLC", website="https://acmewholesale.com")
    b = SupplierCandidate(legal_business_name="Totally Different Co", website="https://totallydifferent.com")
    assert index.resolve(a) != index.resolve(b)


def test_index_round_trips_through_dict():
    index = EntityIndex()
    a = SupplierCandidate(legal_business_name="Acme Wholesale LLC", website="https://acmewholesale.com")
    supplier_id = index.resolve(a)

    restored = EntityIndex.from_dict(index.to_dict())
    b = SupplierCandidate(legal_business_name="Acme Wholesale LLC", website="https://acmewholesale.com")
    assert restored.resolve(b) == supplier_id

"""
Tests for catalog_import.py: column-mapping detection, UPC checksum
validation (including the leading-zero reconstruction found for real in
the DC 55 sample catalog), and the required-before-commit preview.
"""

from decimal import Decimal
from pathlib import Path

import pytest

from catalog_import import (
    load_catalog_rows, pdf_conversion_instructions, preview_catalog,
    suggest_leading_zero_correction, validate_gtin_checksum,
)

SAMPLE_CATALOG = Path(__file__).parent.parent / "catalogs" / "DC 55 July Price List- copy.xlsx"


def _write_csv(path, rows, header):
    import csv
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# GS1 checksum / leading-zero reconstruction
# ---------------------------------------------------------------------------

def test_validate_gtin_checksum_valid_upc_a():
    assert validate_gtin_checksum("034463016148") is True


def test_validate_gtin_checksum_invalid_upc_a():
    assert validate_gtin_checksum("034463016149") is False  # wrong check digit


def test_validate_gtin_checksum_unrecognized_length_is_none_not_false():
    assert validate_gtin_checksum("34463016148") is None  # 11 digits - not a standard GTIN length
    assert validate_gtin_checksum("") is None
    assert validate_gtin_checksum("abc123") is None


def test_suggest_leading_zero_correction_finds_real_truncation():
    # Real example from the DC 55 sample file - an 11-digit code that is a
    # checksum-valid UPC-A once zero-padded.
    assert suggest_leading_zero_correction("34463016148") == "034463016148"


def test_suggest_leading_zero_correction_returns_none_when_no_valid_padding():
    # A code whose zero-padding never produces a valid checksum shouldn't
    # be "corrected" into a wrong value.
    assert suggest_leading_zero_correction("99999999999") is None


def test_suggest_leading_zero_correction_does_not_touch_standard_length_codes():
    assert suggest_leading_zero_correction("034463016148") is None  # already 12 digits


# ---------------------------------------------------------------------------
# Real sample file
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not SAMPLE_CATALOG.exists(), reason="sample catalog not present")
def test_preview_detects_dc55_columns():
    preview = preview_catalog(str(SAMPLE_CATALOG), supplier_name="DC 55 Test Supplier", sample_size=10)
    assert preview.unmapped_required_fields == []
    assert preview.detected_mapping["upc"] == "UPC"
    assert preview.detected_mapping["purchase_price"] == "WHOLESALE"
    assert preview.detected_mapping["description"] == "DESCRIPTION"
    assert preview.total_row_count > 90_000
    assert len(preview.sample_rows) == 10


@pytest.mark.skipif(not SAMPLE_CATALOG.exists(), reason="sample catalog not present")
def test_preview_flags_leading_zero_upcs_for_review():
    preview = preview_catalog(str(SAMPLE_CATALOG), supplier_name="DC 55 Test Supplier", sample_size=10)
    flagged = [r for r in preview.sample_rows if r.upc_suggested_correction]
    assert flagged, "expected at least one leading-zero-truncated UPC in the first 10 rows"
    for row in flagged:
        assert len(row.upc_suggested_correction) == 12
        assert row.upc == row.upc_suggested_correction.lstrip("0") or row.upc_suggested_correction.endswith(row.upc)


@pytest.mark.skipif(not SAMPLE_CATALOG.exists(), reason="sample catalog not present")
def test_preview_price_basis_is_each_and_pack_becomes_order_multiple():
    preview = preview_catalog(str(SAMPLE_CATALOG), supplier_name="DC 55 Test Supplier", sample_size=5)
    assert preview.price_basis_assumption == "each"
    for row in preview.sample_rows:
        assert row.units_per_purchase_unit == 1
        assert row.order_multiple is not None and row.order_multiple >= 1


@pytest.mark.skipif(not SAMPLE_CATALOG.exists(), reason="sample catalog not present")
def test_load_catalog_rows_full_import_respects_limit():
    rows, warnings = load_catalog_rows(str(SAMPLE_CATALOG), supplier_name="DC 55 Test Supplier", limit=50)
    assert len(rows) == 50
    assert all(r.supplier_name == "DC 55 Test Supplier" for r in rows)
    assert all(r.purchase_price is not None for r in rows)  # every DC 55 sample row has a wholesale price


# ---------------------------------------------------------------------------
# Synthetic CSV - mapping, duplicates, missing required fields
# ---------------------------------------------------------------------------

def test_preview_on_csv_with_custom_headers(tmp_path):
    csv_path = tmp_path / "catalog.csv"
    _write_csv(
        csv_path,
        header=["Item Number", "UPC Code", "Brand Name", "Item Description", "Our Price", "Case Qty"],
        rows=[
            ["SKU1", "034463016148", "Acme", "Widget", "4.50", "12"],
            ["SKU2", "012005000589", "Acme", "Gadget", "9.99", "6"],
        ],
    )
    preview = preview_catalog(str(csv_path), supplier_name="Test Supplier")
    assert preview.unmapped_required_fields == []
    assert preview.detected_mapping["upc"] == "UPC Code"
    assert preview.detected_mapping["purchase_price"] == "Our Price"
    assert len(preview.sample_rows) == 2
    assert preview.sample_rows[0].purchase_price == Decimal("4.50")
    assert preview.sample_rows[0].upc == "034463016148"
    assert preview.sample_rows[0].upc_valid is True


def test_preview_flags_missing_required_fields(tmp_path):
    csv_path = tmp_path / "catalog.csv"
    _write_csv(csv_path, header=["Some Column", "Another"], rows=[["x", "y"]])
    preview = preview_catalog(str(csv_path), supplier_name="Test Supplier")
    assert "description" in preview.unmapped_required_fields
    assert "purchase_price" in preview.unmapped_required_fields


def test_preview_counts_duplicate_upcs(tmp_path):
    csv_path = tmp_path / "catalog.csv"
    _write_csv(
        csv_path,
        header=["UPC", "Description", "Price"],
        rows=[
            ["034463016148", "Widget A", "4.50"],
            ["034463016148", "Widget A (reprint)", "4.60"],
            ["012005000589", "Gadget", "9.99"],
        ],
    )
    preview = preview_catalog(str(csv_path), supplier_name="Test Supplier", thorough=True)
    assert preview.duplicate_upc_count == 1


def test_row_missing_description_is_skipped_with_warning(tmp_path):
    csv_path = tmp_path / "catalog.csv"
    _write_csv(
        csv_path,
        header=["UPC", "Description", "Price"],
        rows=[["034463016148", "", "4.50"], ["012005000589", "Gadget", "9.99"]],
    )
    rows, warnings = load_catalog_rows(str(csv_path), supplier_name="Test Supplier")
    assert len(rows) == 1
    assert rows[0].description == "Gadget"
    assert any("skipped" in w.message for w in warnings)


def test_leading_zero_preserved_from_csv_text(tmp_path):
    # A UPC stored with its leading zero intact in the source file must
    # survive the round trip exactly - this is the "don't destroy it in
    # the first place" half of the requirement.
    csv_path = tmp_path / "catalog.csv"
    _write_csv(csv_path, header=["UPC", "Description", "Price"], rows=[["012345678905", "Widget", "1.00"]])
    rows, _ = load_catalog_rows(str(csv_path), supplier_name="Test Supplier")
    assert rows[0].upc == "012345678905"
    assert rows[0].upc_valid is True


def test_pdf_raises_clear_manual_conversion_error(tmp_path):
    pdf_path = tmp_path / "catalog.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    with pytest.raises(ValueError, match="PDF catalog intake isn't implemented"):
        load_catalog_rows(str(pdf_path), supplier_name="Test Supplier")
    # Same message is available without needing to trigger the exception.
    assert "manual" in pdf_conversion_instructions().lower()


def test_case_price_basis_pack_becomes_units_per_purchase_unit(tmp_path):
    from catalog_models import ColumnMapping

    csv_path = tmp_path / "catalog.csv"
    _write_csv(
        csv_path,
        header=["UPC", "Description", "Price", "Case Qty"],
        rows=[["034463016148", "Widget", "48.00", "12"]],
    )
    mapping = ColumnMapping(
        supplier_name="Test Supplier",
        mapping={"upc": "UPC", "description": "Description", "purchase_price": "Price", "pack": "Case Qty"},
        price_basis="case",
    )
    rows, _ = load_catalog_rows(str(csv_path), supplier_name="Test Supplier", mapping=mapping)
    assert rows[0].price_basis == "case"
    assert rows[0].units_per_purchase_unit == 12
    assert rows[0].order_multiple is None

"""Tests for filter_smartscout_categories.py - pure local CSV filtering,
no API calls."""

import csv

import pytest

from filter_smartscout_categories import PRODUCT_FAMILY_FILTERS, _contains_keyword, filter_rows


def _write_csv(path, rows):
    fieldnames = ["Brand Name", "Main Category", "Primary Subcategory"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_contains_keyword_avoids_midword_false_positive():
    # "tea" must not match inside "steak" - this was a real bug.
    assert not _contains_keyword("steak sauce", "tea")
    assert _contains_keyword("black tea", "tea")


def test_contains_keyword_matches_plurals():
    # Left-boundary-only matching so ordinary plurals still match.
    assert _contains_keyword("household cleaning sponges", "sponge")
    assert _contains_keyword("pens", "pen")


def test_filter_rows_keeps_matching_category_and_keyword(tmp_path):
    csv_path = _write_csv(tmp_path / "export.csv", [
        {"Brand Name": "Acme Labels", "Main Category": "Office Products", "Primary Subcategory": "Shipping Labels"},
        {"Brand Name": "Acme Desks", "Main Category": "Office Products", "Primary Subcategory": "Office Desks"},
        {"Brand Name": "Acme Staplers", "Main Category": "Office Products", "Primary Subcategory": "Staplers"},
        {"Brand Name": "Other Co", "Main Category": "Pet Supplies", "Primary Subcategory": "Waste Bag Refills"},
    ])
    kept, fieldnames, stats = filter_rows(csv_path, "office_products")

    names = {r["Brand Name"] for r in kept}
    assert names == {"Acme Labels"}
    assert stats["total"] == 4
    assert stats["kept"] == 1
    assert stats["wrong_category"] == 1  # the Pet Supplies row
    assert stats["excluded"] == 1  # "Office Desks" - hits the "desk" exclude keyword
    assert stats["no_include_match"] == 1  # "Staplers" - right category, no include keyword, no exclude keyword


def test_filter_rows_exclude_wins_over_include(tmp_path):
    csv_path = _write_csv(tmp_path / "export.csv", [
        # "shipping tape" (include) AND "furniture" (exclude) both present -
        # exclude must win.
        {"Brand Name": "Tricky Co", "Main Category": "Office Products", "Primary Subcategory": "Furniture Shipping Tape Dispensers"},
    ])
    kept, _, stats = filter_rows(csv_path, "office_products")

    assert kept == []
    assert stats["excluded"] == 1


def test_all_presets_have_consistent_structure():
    for key, preset in PRODUCT_FAMILY_FILTERS.items():
        assert preset["category_match"], f"{key} has no category_match"
        assert preset["include_keywords"], f"{key} has no include_keywords"
        assert preset["exclude_keywords"], f"{key} has no exclude_keywords"
        assert isinstance(preset["rank"], int)


def test_filter_rows_unknown_category_raises():
    with pytest.raises(KeyError):
        PRODUCT_FAMILY_FILTERS["not_a_real_category"]

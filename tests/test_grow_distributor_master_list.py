"""Tests for grow_distributor_master_list.py - pure local CSV append logic,
no API calls."""

import csv

from grow_distributor_master_list import grow_master_list
from tests.factories import make_candidate, make_qualification, make_relationship


def _read_rows(path):
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def test_grow_appends_qualified_new_distributor(tmp_path):
    master = tmp_path / "distributor_master_list.csv"
    master.write_text("Distributor,Primary Categories\nExisting Co,Some category\n", encoding="utf-8")

    candidate = make_candidate(legal_business_name="New Distributor Co", role="authorized_distributor", brands_carried=["Widget"])
    rel = make_relationship(brand_name="Widget", supplier_id="sup_new", candidate=candidate, qualification=make_qualification("New Distributor Co", score=80))

    added = grow_master_list([rel], master_list_path=master)

    assert len(added) == 1
    assert added[0]["Distributor"] == "New Distributor Co"
    rows = _read_rows(master)
    names = {r["Distributor"] for r in rows}
    assert names == {"Existing Co", "New Distributor Co"}


def test_grow_skips_do_not_pursue(tmp_path):
    master = tmp_path / "distributor_master_list.csv"
    master.write_text("Distributor,Primary Categories\n", encoding="utf-8")

    candidate = make_candidate(legal_business_name="Prohibited Co", marketplace_policies=[])
    # score=0 plus a PROHIBITED-style setup isn't needed here - directly
    # force DO_NOT_PURSUE via a qualification that scores everything 0.
    qualification = make_qualification("Prohibited Co", score=0)
    rel = make_relationship(brand_name="Widget", supplier_id="sup_bad", candidate=candidate, qualification=qualification)
    assert rel.assessment.recommendation == "DO_NOT_PURSUE"

    added = grow_master_list([rel], master_list_path=master)

    assert added == []
    assert _read_rows(master) == []


def test_grow_does_not_duplicate_existing_distributor(tmp_path):
    master = tmp_path / "distributor_master_list.csv"
    master.write_text("Distributor,Primary Categories\nUNFI,Health & beauty\n", encoding="utf-8")

    candidate = make_candidate(legal_business_name="UNFI", role="stocking_wholesaler")
    rel = make_relationship(brand_name="Widget", supplier_id="sup_unfi", candidate=candidate, qualification=make_qualification("UNFI", score=80))

    added = grow_master_list([rel], master_list_path=master)

    assert added == []  # already known - never duplicated
    rows = _read_rows(master)
    assert len(rows) == 1
    assert rows[0]["Primary Categories"] == "Health & beauty"  # existing row untouched


def test_grow_never_modifies_existing_rows(tmp_path):
    master = tmp_path / "distributor_master_list.csv"
    original = "Distributor,Primary Categories\nExisting Co,Original notes - do not touch\n"
    master.write_text(original, encoding="utf-8")

    candidate = make_candidate(legal_business_name="Another New Co")
    rel = make_relationship(brand_name="Widget", supplier_id="sup_x", candidate=candidate, qualification=make_qualification("Another New Co", score=75))
    grow_master_list([rel], master_list_path=master)

    rows = _read_rows(master)
    existing = next(r for r in rows if r["Distributor"] == "Existing Co")
    assert existing["Primary Categories"] == "Original notes - do not touch"


def test_grow_dry_run_does_not_write(tmp_path):
    master = tmp_path / "distributor_master_list.csv"
    master.write_text("Distributor,Primary Categories\n", encoding="utf-8")

    candidate = make_candidate(legal_business_name="Preview Only Co")
    rel = make_relationship(brand_name="Widget", supplier_id="sup_preview", candidate=candidate, qualification=make_qualification("Preview Only Co", score=75))

    added = grow_master_list([rel], master_list_path=master, dry_run=True)

    assert len(added) == 1  # still reported
    assert _read_rows(master) == []  # but nothing written


def test_grow_dedupes_within_same_call(tmp_path):
    master = tmp_path / "distributor_master_list.csv"
    master.write_text("Distributor,Primary Categories\n", encoding="utf-8")

    candidate = make_candidate(legal_business_name="Dual Brand Distributor Co")
    rel1 = make_relationship(brand_name="Widget A", supplier_id="sup_dual", candidate=candidate, qualification=make_qualification("Dual Brand Distributor Co", score=75))
    rel2 = make_relationship(brand_name="Widget B", supplier_id="sup_dual", candidate=candidate, qualification=make_qualification("Dual Brand Distributor Co", score=75))

    added = grow_master_list([rel1, rel2], master_list_path=master)

    assert len(added) == 1  # same distributor, carried via two brands in this one call - added once

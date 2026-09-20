"""
Shared pytest fixtures for the R&T Brand/Supplier Qualifier test suite.

No test in this project makes a real network or paid-API call: agent
calls go through agents.Runner.run, and tests replace that with a fake
(see tests/helpers.py's FakeRunner) that returns frozen, hand-written
responses. isolated_supplier_data redirects every on-disk path
supplier_persistence.py / supplier_approval.py would touch into a fresh
tmp_path per test, so tests never read or write real project data.
"""

import pytest

import supplier_approval
import supplier_persistence


@pytest.fixture(autouse=True)
def isolated_supplier_data(tmp_path, monkeypatch):
    data_dir = tmp_path / "supplier_data"
    monkeypatch.setattr(supplier_persistence, "SUPPLIER_DATA_DIR", data_dir)
    monkeypatch.setattr(supplier_persistence, "SUPPLIERS_DIR", data_dir / "suppliers")
    monkeypatch.setattr(supplier_persistence, "RELATIONSHIPS_DIR", data_dir / "relationships")
    monkeypatch.setattr(supplier_persistence, "RUNS_DIR", data_dir / "runs")
    monkeypatch.setattr(supplier_persistence, "BATCHES_DIR", data_dir / "batches")
    monkeypatch.setattr(supplier_persistence, "CACHE_DIR", data_dir / "cache")
    monkeypatch.setattr(supplier_persistence, "ENTITY_INDEX_PATH", data_dir / "entity_index.json")

    outbox_dir = tmp_path / "outbox"
    monkeypatch.setattr(supplier_approval, "OUTBOX_DIR", outbox_dir)

    yield data_dir

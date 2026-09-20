"""
Shared pytest fixtures for the R&T Brand/Supplier Qualifier test suite.

No test in this project makes a real network or paid-API call: agent
calls go through agents.Runner.run, and tests replace that with a fake
(see tests/helpers.py's FakeRunner) that returns frozen, hand-written
responses. isolated_supplier_data redirects every on-disk path either
stage's persistence/approval modules would touch into a fresh tmp_path per
test, so tests never read or write real project data (including the
gui_actions.py wrappers, which call straight into these same modules).
"""

import pytest
from agents import set_tracing_disabled

import approval
import persistence
import supplier_approval
import supplier_persistence

# Runner.run is mocked in every test (see tests/helpers.py), but the SDK's
# `trace(...)` context manager (used by workflow.py / supplier_workflow.py)
# tries to submit trace data to OpenAI's servers independently of that -
# including a real network attempt if a real OPENAI_API_KEY happens to be
# loaded from .env. Disabling tracing for the whole test session is what
# actually makes "no test makes a network call" true.
set_tracing_disabled(True)


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

    # Brand Qualifier side (persistence.save_result/find_results resolve
    # their `directory` default dynamically now - see persistence.py - so
    # monkeypatching RESULTS_DIR here is enough to isolate them too).
    monkeypatch.setattr(persistence, "RESULTS_DIR", tmp_path / "batch_results")
    monkeypatch.setattr(approval, "OUTBOX_DIR", outbox_dir)

    yield data_dir

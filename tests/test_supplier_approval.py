"""
Human approval gate tests: confirms the no-send rule (nothing is ever sent;
APPROVE only writes a local outbox file) and that lifecycle state only
advances on an explicit human APPROVE - never automatically.
"""

import inspect

import supplier_approval
from tests.factories import make_relationship


def _queue_input(monkeypatch, responses):
    it = iter(responses)
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(it))


async def test_approve_writes_outbox_file_and_advances_lifecycle(monkeypatch):
    rel = make_relationship()
    assert rel.lifecycle_state == "RESEARCHED"
    _queue_input(monkeypatch, ["approve"])

    result = await supplier_approval.run_supplier_approval_gate(rel)

    files = list(supplier_approval.OUTBOX_DIR.glob("*.txt"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    assert "NOT SENT" in content
    assert result.lifecycle_state == "CONTACT_APPROVED"


async def test_reject_writes_nothing_and_leaves_lifecycle_unchanged(monkeypatch):
    rel = make_relationship()
    _queue_input(monkeypatch, ["reject"])

    result = await supplier_approval.run_supplier_approval_gate(rel)

    assert not supplier_approval.OUTBOX_DIR.exists() or not list(supplier_approval.OUTBOX_DIR.glob("*.txt"))
    assert result.lifecycle_state == "RESEARCHED"


async def test_edit_updates_winning_draft_before_approval(monkeypatch):
    rel = make_relationship()
    new_subject = "Edited Subject Line"
    _queue_input(monkeypatch, ["edit", new_subject, "New body line", "END", "approve"])

    result = await supplier_approval.run_supplier_approval_gate(rel)

    winner = result.outreach_drafts[result.manager_decision.winning_strategy]
    assert winner.subject == new_subject
    assert "New body line" in winner.body


async def test_no_outreach_drafted_means_nothing_to_approve(monkeypatch):
    rel = make_relationship(with_outreach=False)
    # No input() should even be needed - the gate should short-circuit.
    monkeypatch.setattr("builtins.input", lambda *a, **k: (_ for _ in ()).throw(AssertionError("input() should not be called")))

    result = await supplier_approval.run_supplier_approval_gate(rel)
    assert result.outreach_drafts == {}


def test_module_never_references_a_send_capability():
    source = inspect.getsource(supplier_approval)
    for forbidden in ("smtp", "send_email", "messenger", "sendgrid"):
        assert forbidden not in source.lower()

"""
Human Approval Gate for supplier outreach - the supplier-side counterpart
to approval.py, kept as a separate small module rather than a generic
refactor of approval.py because the two record shapes differ enough
(BrandSupplierRelationship needs a supplier_id, a gated lifecycle-state
advance, and persistence back to supplier_data/) that sharing one function
would need as much branching as just having two clear ones.

Same no-send rule: only writes the approved subject/body to outbox/, never
sends anything. APPROVE also advances the relationship's lifecycle_state to
CONTACT_APPROVED - a real human decision ("R&T intends to contact this
supplier"), not an assumption about anything happening outside this system.
Nothing beyond CONTACT_APPROVED is ever set here; later states like
ACCOUNT_APPROVED or APPROVED_FOR_PURCHASE represent external events (a
reply was received, an account was approved) that only a human recording
those facts through a future tool should set - see
supplier_scoring.advance_lifecycle_state for the hard gates on states that
require verified evidence (never reachable from research alone).
"""

import re
from datetime import datetime
from pathlib import Path

import supplier_persistence
from models import BrandSupplierRelationship
from supplier_report import format_relationship_detail
from supplier_scoring import advance_lifecycle_state, LifecycleGateError
from supplier_workflow import regenerate_supplier_outreach

OUTBOX_DIR = Path(__file__).parent / "outbox"
VALID_CHOICES = {"approve", "edit", "regenerate", "reject"}


def _prompt_choice() -> str:
    while True:
        raw = input("\nYour decision [APPROVE / EDIT / REGENERATE / REJECT]: ").strip().lower()
        if raw in VALID_CHOICES:
            return raw
        print(f"Please type one of: APPROVE, EDIT, REGENERATE, REJECT (got: {raw!r})")


def _prompt_multiline(label: str) -> str:
    print(f"{label} (end with a single line containing only END; leave empty to keep current):")
    lines = []
    while True:
        line = input()
        if line.strip() == "END":
            break
        lines.append(line)
    return "\n".join(lines)


def _save_approved(rel: BrandSupplierRelationship) -> Path:
    OUTBOX_DIR.mkdir(exist_ok=True)
    safe_name = re.sub(
        r"[^A-Za-z0-9_-]+", "_", f"{rel.brand_name}_{rel.assessment.candidate.legal_business_name}"
    ).strip("_") or "supplier"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = OUTBOX_DIR / f"{safe_name}_{timestamp}.txt"
    winner = rel.outreach_drafts[rel.manager_decision.winning_strategy]

    content = (
        "STATUS: APPROVED BY HUMAN — NOT SENT (sending is not implemented)\n"
        f"Approved at: {datetime.now().isoformat(timespec='seconds')}\n"
        f"Brand: {rel.brand_name}\n"
        f"Supplier: {rel.assessment.candidate.legal_business_name} (id: {rel.supplier_id})\n"
        f"Strategy: {winner.strategy}\n\n"
        f"Subject: {winner.subject}\n\n"
        f"{winner.body}\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


async def run_supplier_approval_gate(rel: BrandSupplierRelationship) -> BrandSupplierRelationship:
    """
    Presents the report and drives the APPROVE / EDIT / REGENERATE / REJECT
    loop for one brand<->supplier relationship. Returns the (possibly
    updated) relationship; the caller is responsible for nothing further -
    APPROVE/REGENERATE already persist via supplier_persistence.
    """
    while True:
        print("\n" + format_relationship_detail(rel))

        if not rel.outreach_drafts or not rel.manager_decision:
            print(f"\nNo outreach was drafted for this candidate (recommendation: {rel.assessment.recommendation}).")
            print("Nothing to approve here - see the do-not-pursue / missing-information reports instead.")
            return rel

        choice = _prompt_choice()

        if choice == "approve":
            path = _save_approved(rel)
            print(f"\nApproved. Saved to: {path}")
            print("Reminder: this project does not send email automatically. Send this message yourself.")
            try:
                rel.lifecycle_state = advance_lifecycle_state(rel.assessment, "CONTACT_APPROVED")
            except LifecycleGateError as exc:
                print(f"(Lifecycle state left at {rel.lifecycle_state}: {exc})")
            supplier_persistence.save_relationship(rel)
            return rel

        if choice == "reject":
            print("\nRejected. Nothing was saved.")
            return rel

        if choice == "edit":
            winner = rel.outreach_drafts[rel.manager_decision.winning_strategy]
            print(f"\nCurrent subject: {winner.subject}")
            new_subject = input("New subject (leave blank to keep current): ").strip()
            new_body = _prompt_multiline("New body")
            if new_subject:
                winner.subject = new_subject
            if new_body.strip():
                winner.body = new_body
            continue  # re-present the (possibly edited) report

        if choice == "regenerate":
            print("\nRegenerating outreach drafts and manager evaluation...")
            drafts, manager_decision = await regenerate_supplier_outreach(rel.brand_name, rel.assessment)
            rel.outreach_drafts = drafts
            rel.manager_decision = manager_decision
            continue

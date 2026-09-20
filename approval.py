"""
Human Approval Gate.

This is the mandatory stop before anything could eventually be sent. No
function in this entire project calls messenger.send_email or any other
network-sending code. The only action APPROVE takes is writing the final
message to a local text file under outbox/, for a human to review and send
themselves. Wiring an actual send (with its own separate confirmation) is
listed as a future enhancement in the README, not implemented here.

The UI is intentionally a simple terminal prompt, per the course exercise's
own instruction not to overengineer this part.
"""

import re
from datetime import datetime
from pathlib import Path

from report import format_report
from workflow import WorkflowResult, regenerate_outreach

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


def _save_approved(result: WorkflowResult) -> Path:
    OUTBOX_DIR.mkdir(exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", result.prospect.company_name).strip("_") or "prospect"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = OUTBOX_DIR / f"{safe_name}_{timestamp}.txt"
    winner = result.winning_draft

    content = (
        "STATUS: APPROVED BY HUMAN — NOT SENT (sending is not implemented in Version 1)\n"
        f"Approved at: {datetime.now().isoformat(timespec='seconds')}\n"
        f"Prospect: {result.prospect.company_name}\n"
        f"Strategy: {winner.strategy}\n\n"
        f"Subject: {winner.subject}\n\n"
        f"{winner.body}\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


async def run_approval_gate(result: WorkflowResult) -> None:
    """
    Presents the report and drives the APPROVE / EDIT / REGENERATE / REJECT
    loop until the user approves or rejects. Returns without side effects
    on REJECT; writes a local file (never sends anything) on APPROVE.
    """
    while True:
        print("\n" + format_report(result))
        choice = _prompt_choice()

        if choice == "approve":
            path = _save_approved(result)
            print(f"\nApproved. Saved to: {path}")
            print(
                "Reminder: this project does not send email automatically. "
                "Send this message yourself, or wire up an actual send step in a future version."
            )
            return

        if choice == "reject":
            print("\nRejected. Nothing was saved.")
            return

        if choice == "edit":
            winner = result.winning_draft
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
            result = await regenerate_outreach(result)
            continue

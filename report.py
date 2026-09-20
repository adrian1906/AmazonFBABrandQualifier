"""
Formats a WorkflowResult into the human-readable report shown to the user
and used as the basis for the human-approval gate (see approval.py).

Kept separate from workflow.py (which only produces data) and approval.py
(which only handles the approve/edit/regenerate/reject interaction) so each
file has one job.
"""

from workflow import WorkflowResult

_STRATEGY_LABELS = {
    "relationship": "Relationship",
    "procurement": "Procurement",
    "partnership": "Partnership",
}


def format_report(result: WorkflowResult, status: str = "AWAITING HUMAN APPROVAL") -> str:
    p = result.prospect
    q = result.qualification
    m = result.manager_decision

    research_summary = p.company_description or "(no company description determined)"
    key_opportunities = "\n".join(f"- {r}" for r in q.key_reasons) or "(none noted)"
    risks_unknowns_lines = [f"- {r}" for r in q.risks] + [f"- Unknown: {u}" for u in q.missing_information]
    risks_unknowns = "\n".join(risks_unknowns_lines) or "(none noted)"

    draft_lines = []
    for i, (strategy, draft) in enumerate(result.drafts.items(), start=1):
        score_entry = next((d for d in m.draft_scores if d.strategy == strategy), None)
        score_text = f"{score_entry.score}/100" if score_entry else "(not scored)"
        label = _STRATEGY_LABELS.get(strategy, strategy.title())
        draft_lines.append(f"Draft {i} — {label}\nScore: {score_text}")
    outreach_candidates = "\n\n".join(draft_lines)

    winner = result.winning_draft
    winner_label = _STRATEGY_LABELS.get(m.winning_strategy, m.winning_strategy.title())

    return f"""
R&T BRAND ACQUISITION REPORT

Prospect:
{p.company_name}

Qualification Score:
{q.overall_score}/100

Recommendation:
{q.recommendation}

Research Summary:
{research_summary}

Key Opportunities:
{key_opportunities}

Risks / Unknowns:
{risks_unknowns}

Recommended Next Step:
{q.recommended_next_action}

OUTREACH CANDIDATES

{outreach_candidates}

WINNER:
{winner_label} Draft

Reason:
{m.winning_reason}

SELECTED EMAIL

Subject:
{winner.subject}

Body:
{winner.body}

STATUS:
{status}
""".strip()

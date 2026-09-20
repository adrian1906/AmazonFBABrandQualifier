"""
The R&T Brand Acquisition workflow: wires the individual agents together.

    Prospect
       |
    Research Agent
       |
    Qualification Agent
       |
    +-----------------------------+
    |              |              |
 Relationship   Procurement   Partnership     <- run independently via asyncio.gather
    |              |              |
    +--------------+--------------+
                   |
            Outreach Manager
                   |
             (returned to caller -
              see approval.py for the
              human approval gate)

This module does the orchestration in plain Python using Runner.run(),
rather than the SDK's handoff feature - see README.md for why. The whole
run is wrapped in a single `trace(...)` so it shows up as one named run in
the OpenAI traces dashboard (https://platform.openai.com/traces), the same
way 3_lab3.ipynb's `with trace("Sales Manager across different models"):`
does.

Nothing in this file sends anything externally.
"""

import asyncio
from dataclasses import dataclass

from agents import Runner, trace

from models import Prospect, ResearchFindings, QualificationResult, OutreachDraft, ManagerDecision
from research_agent import build_research_agent
from qualification_agent import qualification_agent
from outreach_agents import OUTREACH_AGENTS
from outreach_manager import outreach_manager_agent


@dataclass
class WorkflowResult:
    """Everything produced by one run of the workflow, bundled for the report/approval step."""
    prospect: Prospect
    research: ResearchFindings
    qualification: QualificationResult
    drafts: dict[str, OutreachDraft]  # keyed by strategy: "relationship" / "procurement" / "partnership"
    manager_decision: ManagerDecision

    @property
    def winning_draft(self) -> OutreachDraft:
        return self.drafts[self.manager_decision.winning_strategy]


# ---------------------------------------------------------------------------
# Prompt-rendering helpers - turn our Pydantic objects into plain text to
# feed as the next agent's input. Kept as small named functions so each
# hand-off point in the pipeline is easy to find and adjust independently.
# ---------------------------------------------------------------------------

def _render_research_input(prospect: Prospect, manual_notes: str) -> str:
    return f"""
Research this prospective brand/manufacturer/distributor for R&T Distribution Group LLC.

Company name (as provided by the user): {prospect.company_name}
Website (as provided by the user): {prospect.website or "not provided"}

Manually supplied notes from the user (treat these as a primary, trustworthy source):
{manual_notes or "(none provided)"}

Organize what you can determine into the structured research findings format.
""".strip()


def _render_qualification_input(research: ResearchFindings) -> str:
    return f"""
Here are the research findings for a prospective brand/manufacturer/distributor.
Evaluate this opportunity per your instructions.

{research.model_dump_json(indent=2)}
""".strip()


def _render_outreach_input(prospect: Prospect, research: ResearchFindings, qualification: QualificationResult) -> str:
    return f"""
Write an outreach email to this prospective brand/manufacturer/distributor,
following your assigned strategy.

Prospect: {prospect.company_name} ({prospect.website or "website unknown"})
Contact: {prospect.contact_name or "unknown"} ({prospect.contact_email or "email unknown"})

Research findings:
{research.model_dump_json(indent=2)}

Qualification summary:
- Recommendation: {qualification.recommendation}
- Overall score: {qualification.overall_score}/100
- Key reasons: {"; ".join(qualification.key_reasons) or "none noted"}
""".strip()


def _render_manager_input(drafts: dict[str, OutreachDraft]) -> str:
    sections = [
        f"--- Draft ({strategy}) ---\nSubject: {draft.subject}\n\n{draft.body}"
        for strategy, draft in drafts.items()
    ]
    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Outreach drafting - the three agents run concurrently against the exact
# same input, and never see each other's output, guaranteeing independence.
# ---------------------------------------------------------------------------

async def _generate_outreach_drafts(
    prospect: Prospect, research: ResearchFindings, qualification: QualificationResult
) -> dict[str, OutreachDraft]:
    input_text = _render_outreach_input(prospect, research, qualification)
    results = await asyncio.gather(
        *[Runner.run(agent, input_text) for agent, _strategy in OUTREACH_AGENTS]
    )
    drafts: dict[str, OutreachDraft] = {}
    for (_agent, strategy), result in zip(OUTREACH_AGENTS, results):
        draft: OutreachDraft = result.final_output
        draft.strategy = strategy
        drafts[strategy] = draft
    return drafts


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

async def run_brand_acquisition(
    prospect: Prospect,
    manual_research_notes: str = "",
    allow_web_search: bool = True,
) -> WorkflowResult:
    """
    Run the full R&T Brand Acquisition workflow for a single prospect:
    Research -> Qualification -> 3 independent Outreach drafts -> Manager evaluation.

    Set allow_web_search=False for fictional/demo prospects so the Research
    Agent never tries to search the web for a company that doesn't exist.

    Does not send anything, and never imports messenger.py. The caller is
    responsible for taking the WorkflowResult to the human-approval gate
    (see approval.py) before anything is saved.
    """
    with trace(f"R&T Brand Acquisition: {prospect.company_name}"):
        research_agent = build_research_agent(allow_web_search=allow_web_search)
        research_result = await Runner.run(
            research_agent, _render_research_input(prospect, manual_research_notes)
        )
        research: ResearchFindings = research_result.final_output

        qualification_result = await Runner.run(
            qualification_agent, _render_qualification_input(research)
        )
        qualification: QualificationResult = qualification_result.final_output

        drafts = await _generate_outreach_drafts(prospect, research, qualification)

        manager_result = await Runner.run(outreach_manager_agent, _render_manager_input(drafts))
        manager_decision: ManagerDecision = manager_result.final_output

    updated_prospect = prospect.model_copy(update={
        "company_description": research.company_description or prospect.company_description,
        "product_categories": research.product_categories or prospect.product_categories,
        "wholesale_program": research.wholesale_program or prospect.wholesale_program,
        "wholesale_application_url": research.wholesale_application_url or prospect.wholesale_application_url,
        "amazon_policy": research.amazon_marketplace_policy or prospect.amazon_policy,
        "map_policy": research.map_policy or prospect.map_policy,
        "minimum_order_quantity": research.minimum_order_quantity or prospect.minimum_order_quantity,
        "opening_order": research.opening_order_requirement or prospect.opening_order,
        "sources": research.sources or prospect.sources,
        "qualification_score": qualification.overall_score,
        "qualification_status": qualification.recommendation,
        "recommended_action": qualification.recommended_next_action,
    })

    return WorkflowResult(
        prospect=updated_prospect,
        research=research,
        qualification=qualification,
        drafts=drafts,
        manager_decision=manager_decision,
    )


async def regenerate_outreach(result: WorkflowResult) -> WorkflowResult:
    """
    Re-run just the outreach-drafting + manager-evaluation stage, reusing
    the existing research/qualification from a previous WorkflowResult.
    Used by the approval gate's REGENERATE option.
    """
    with trace(f"R&T Brand Acquisition (regenerate): {result.prospect.company_name}"):
        drafts = await _generate_outreach_drafts(result.prospect, result.research, result.qualification)
        manager_result = await Runner.run(outreach_manager_agent, _render_manager_input(drafts))
        manager_decision: ManagerDecision = manager_result.final_output

    return WorkflowResult(
        prospect=result.prospect,
        research=result.research,
        qualification=result.qualification,
        drafts=drafts,
        manager_decision=manager_decision,
    )

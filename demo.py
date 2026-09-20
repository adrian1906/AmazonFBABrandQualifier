"""
Entry point for the R&T Brand Acquisition System - Version 1.

Run with:
    python demo.py

Runs the full pipeline (Research -> Qualification -> 3 independent
Outreach drafts -> Outreach Manager) against the fictional demo prospect
in sample_prospects.py, prints the structured report, and then hands
control to the human-approval gate. Nothing is ever sent automatically -
see approval.py.

This is a plain Python script, not a notebook, per the project's
requirements - but it uses exactly the same OpenAI Agents SDK primitives
(Agent, Runner, trace, output_type) taught in the notebooks under
agents/2_openai/.
"""

import asyncio

from dotenv import load_dotenv

from approval import run_approval_gate
from sample_prospects import SAMPLE_PROSPECT, SAMPLE_MANUAL_NOTES
from workflow import run_brand_acquisition

load_dotenv(override=True)


async def main() -> None:
    print("Running R&T Brand Acquisition workflow for demo prospect...")
    print("(Fictional data - web search disabled for this run.)\n")

    result = await run_brand_acquisition(
        prospect=SAMPLE_PROSPECT,
        manual_research_notes=SAMPLE_MANUAL_NOTES,
        allow_web_search=False,
    )

    # run_approval_gate prints the formatted report before prompting for a decision.
    await run_approval_gate(result)


if __name__ == "__main__":
    asyncio.run(main())

"""
Outreach Manager / Evaluator Agent.

Receives all three independently-generated drafts (Relationship,
Procurement, Partnership) already produced by outreach_agents.py, and
scores each one against the weighted rubric defined in config.OUTREACH_RUBRIC.

Unlike the course's 3_lab3.ipynb sales-manager example (which lets the
manager agent call the drafting agents itself, as tools, and choose when to
generate them), this manager is only an evaluator: workflow.py generates
the three drafts up front via independent Runner.run() calls, and only
then hands all three to this agent. This guarantees true independence
between drafts and keeps the manager's job simple and auditable: score
what it's given, don't create anything itself.
"""

from agents import Agent

from config import MODEL_NAME, format_rubric
from models import ManagerDecision

_rubric_text = format_rubric()

INSTRUCTIONS = f"""
You are the Outreach Manager for R&T Distribution Group LLC.

You will be given three independently written draft outreach emails for
the same prospective brand/manufacturer/distributor - one written from a
relationship-building strategy, one from a procurement strategy, and one
from a strategic-partnership strategy. Each draft is labeled with its
strategy.

Score each draft using this weighted rubric (out of 100 total):

{_rubric_text}

For each draft, return a score (0-100) using the rubric above, a short
explanation of the score, and its strengths and weaknesses.

Then select the single winning draft - the one most likely to:
1. receive a response,
2. establish credibility,
3. begin a legitimate wholesale relationship,
4. avoid sounding like spam,
5. avoid making unsupported claims,
6. make the next step clear and easy for the recipient.

Explain why it won. If you believe the winning draft could be improved
before it is sent, include specific recommended_final_edits - otherwise
leave that field empty.

Before finalizing, verify for the winning draft that:
- all statements about R&T Distribution Group LLC are supported
  (nothing about authorization, sales history, or purchasing volume that
  wasn't actually established)
- the email does not imply authorization or marketplace permission R&T
  does not yet have
- the requested next step is clear

R&T values professionalism, honesty, relationship building, and long-term
supplier relationships over aggressive sales language.
"""

outreach_manager_agent = Agent(
    name="Outreach Manager",
    instructions=INSTRUCTIONS,
    model=MODEL_NAME,
    output_type=ManagerDecision,
)

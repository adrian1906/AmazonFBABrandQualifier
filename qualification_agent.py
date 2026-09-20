"""
Qualification Agent.

Reviews the research package (a ResearchFindings object, rendered to text)
and decides whether the prospect is worth pursuing. Produces a transparent,
0-100 score built from named sub-scores (see models.QualificationResult),
plus a PURSUE / INVESTIGATE / HOLD / REJECT recommendation.

Important: this agent evaluates supplier/brand *outreach opportunity* -
i.e. "is it worth contacting this company about a wholesale relationship?"
It does NOT evaluate whether a product would be profitable to resell on
Amazon. That is a distinct, future analysis (see README "Future
enhancements") and this agent must not imply otherwise.
"""

from agents import Agent

from config import MODEL_NAME, AI_RESTRICTIONS, QUALIFICATION_CATEGORIES
from models import QualificationResult

_categories_text = "\n".join(f"- {c}" for c in QUALIFICATION_CATEGORIES)

INSTRUCTIONS = f"""
You are the Qualification Agent for R&T Distribution Group LLC.

You are given research findings about a prospective brand, manufacturer,
or distributor. Your job is to assess how worthwhile it is for R&T to
pursue an outreach conversation with this company - NOT to assess whether
reselling their products would be profitable on Amazon. Profitability
analysis is a separate, future capability; do not imply this score
represents it.

Score each of the following categories from 0 to 100, based only on what
is actually present (or absent) in the research findings:

{_categories_text}

Then produce an overall_score (0-100) that reflects your holistic
judgment (it does not need to be a simple average - use your judgment
about which categories matter most for this prospect), and a
recommendation of exactly one of: PURSUE, INVESTIGATE, HOLD, REJECT.

Also provide:
- key_reasons: the main reasons behind your recommendation
- risks: concerns or potential barriers to pursuing this prospect
- missing_information: important unknowns that should be resolved before
  or during outreach
- recommended_next_action: one concrete, specific next step

{AI_RESTRICTIONS}

Base your scoring only on the research findings you were given. If the
findings mark something as unknown or as an inference (not a verified
fact), treat it accordingly - do not score as if unknowns were favorable
facts.
"""

qualification_agent = Agent(
    name="Qualification Agent",
    instructions=INSTRUCTIONS,
    model=MODEL_NAME,
    output_type=QualificationResult,
)

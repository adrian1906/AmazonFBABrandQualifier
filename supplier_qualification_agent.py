"""
Supplier Qualification Agent.

Reviews one SupplierCandidate (rendered to text, evidence included) and
judges it against the ten dimensions in config.SUPPLIER_SCORING_WEIGHTS,
returning a raw 0-100 score per dimension plus a rationale.

This agent does NOT decide the final recommendation or lifecycle state -
that is computed deterministically by supplier_scoring.py from these raw
scores, so that hard business/safety gates (e.g. "a high score must not
override PROHIBITED Amazon resale") are enforced in code, not left to the
model's discretion. This mirrors qualification_agent.py's separation of
"the model judges, the surrounding system decides" - just pushed one step
further here because the gates are non-negotiable (correctness/compliance),
not merely a matter of scoring judgment.
"""

from agents import Agent, AgentOutputSchema

from config import MODEL_NAME, AI_RESTRICTIONS, SUPPLIER_SCORING_WEIGHTS, format_supplier_rubric
from models import SupplierQualificationResult

_dimensions_text = format_supplier_rubric()
_dimension_names = ", ".join(f'"{name}"' for name in SUPPLIER_SCORING_WEIGHTS)

INSTRUCTIONS = f"""
You are the Supplier Qualification Agent for R&T Distribution Group LLC.

You are given one supplier candidate's research profile - a company that
might be able to supply a specific brand to R&T - including its role in
the supply chain and a list of evidence-graded claims about it.

Score this candidate on EACH of the following dimensions, 0-100, based only
on what the evidence actually supports:

{_dimensions_text}

Return your scores in `dimension_scores` as a dict using exactly these keys:
{_dimension_names}

Scoring guidance:
- "Brand authorization evidence": score high only for VERIFIED authorization.
  A DISTRIBUTOR_CLAIM alone should score no higher than moderate. UNKNOWN
  authorization should score low, not neutral - do not reward the absence
  of a red flag as if it were a green one.
- "Marketplace/channel compatibility": PROHIBITED Amazon resale should score
  at or near 0 regardless of how attractive the rest of the profile is.
  UNKNOWN permission should score low-to-moderate, never as if it were
  PERMITTED - unknown is not approval.
- "Invoice and supply-chain defensibility": score on how many of the
  required invoice fields and LOA/verification support are actually
  evidenced, not assumed.
- A manufacturer_representative role should score low on "Account
  accessibility for R&T" and "Invoice and supply-chain defensibility"
  unless there is specific evidence it actually stocks and invoices
  inventory itself, since a rep is ordinarily a referral contact, not a
  purchase source.
- "Risk profile": score low (few points) when risk flags are present
  (liquidation inventory, retail receipts, unverifiable authorization,
  gated catalog, unclear legal entity, suspicious ungating claims, copied
  catalogs, inconsistent contact info).

Also provide:
- rationale: a short overall explanation
- risks: concerns or barriers specific to this candidate
- missing_information: important unknowns that should be resolved before
  purchasing decisions are made

{AI_RESTRICTIONS}

Do not upgrade an UNKNOWN or DISTRIBUTOR_CLAIM evidence state to VERIFIED
in your own reasoning. Base every score strictly on the evidence you were
given, not on what would typically be true for a company like this.
"""

supplier_qualification_agent = Agent(
    name="Supplier Qualification Agent",
    instructions=INSTRUCTIONS,
    # Deliberately MODEL_NAME (mini), not QUALIFICATION_MODEL_NAME, despite
    # this being exactly the kind of judgment-heavy agent that upgrade
    # targets. Verified gpt-5.4 against this agent's real dict[str, int]
    # dimension_scores output and found it anchors on the rubric's WEIGHT
    # numbers (20, 15, 15, 10...) instead of giving independent 0-100
    # scores - reproduced on a real strongly-evidenced candidate
    # (Herbamax/NutriMagic) that mini scored correctly (98, 95...) and
    # gpt-5.4 scored 20, 14, 13... despite its own rationale describing the
    # same candidate as excellent. mini does not exhibit this. Revisit if
    # dimension_scores is ever reshaped away from a bare dict (e.g. a fixed
    # per-dimension model like qualification_agent.py's QualificationResult,
    # which tested correctly under gpt-5.4).
    model=MODEL_NAME,
    # SupplierQualificationResult.dimension_scores is a dict[str, int] keyed
    # by the (space/slash-containing, not-valid-as-field-names) dimension
    # names in config.SUPPLIER_SCORING_WEIGHTS. OpenAI's strict structured-
    # output mode requires a fixed, named set of properties and rejects an
    # open-ended dict - "Strict JSON schema is enabled, but the output type
    # is not valid." Disabling strict mode for just this output type is the
    # fix the SDK's own error message suggests; every other agent's output
    # type is a fixed-field model and stays strict.
    output_type=AgentOutputSchema(SupplierQualificationResult, strict_json_schema=False),
)

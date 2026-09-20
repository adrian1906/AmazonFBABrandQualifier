"""
Shared configuration for the R&T Brand Acquisition System.

This is the single source of truth for:
  - R&T's company profile (used by every outreach agent so the facts
    about the company are never duplicated or retyped in each prompt)
  - The hard restrictions on what the AI is allowed to claim
  - The scoring rubric weights used by the Qualification Agent and the
    Outreach Manager (kept here, not hardcoded in prompts, so a weight
    can be changed in one place and every agent that scores using it
    picks up the change automatically)
  - The default LLM model name, following the same env-var convention
    used elsewhere in the course (see deep_research/search_agent.py)

Nothing in this file makes network calls or sends anything - it is pure
configuration.
"""

import os
from dotenv import load_dotenv

load_dotenv(override=True)

# Same convention as agents/2_openai/deep_research/search_agent.py:
# allow overriding the model via an env var, default to a small/cheap model.
MODEL_NAME = os.getenv("DEFAULT_MODEL_NAME", "gpt-5.4-mini")


# ---------------------------------------------------------------------------
# R&T company profile
# ---------------------------------------------------------------------------
# Every agent that writes or reasons about outreach gets this text
# interpolated into its instructions, so R&T's facts are defined exactly
# once. If a fact isn't listed here, agents are told to treat it as UNKNOWN
# rather than invent it.

RT_PROFILE = {
    "company_name": "R&T Distribution Group LLC",
    "website": "https://rtdistributiongroup.com",
    "business_type": "Wholesale e-commerce retailer and distribution company",
    "business_objective": (
        "Establish legitimate wholesale purchasing relationships with brands, "
        "manufacturers, and distributors, and responsibly resell approved products."
    ),
    "primary_email": "info@rtdistributiongroup.com",
    "functional_emails": [
        "sales@rtdistributiongroup.com",
        "purchasing@rtdistributiongroup.com",
        "operations@rtdistributiongroup.com",
    ],
    # Used to sign outreach emails. If left as None, agents are instructed
    # to sign with the company name only rather than inventing a person's
    # name or leaving a "[Your Name]"-style placeholder.
    "signer_name": None,
    "business_principles": [
        "Professional supplier relationships",
        "Accurate product representation",
        "Compliance with supplier requirements",
        "Responsible marketplace participation",
        "Long-term purchasing relationships",
    ],
}


def format_rt_profile() -> str:
    """Render RT_PROFILE as readable text to embed in an agent's instructions."""
    emails = ", ".join(RT_PROFILE["functional_emails"])
    principles = "\n".join(f"- {p}" for p in RT_PROFILE["business_principles"])
    signer = RT_PROFILE["signer_name"]
    signature_instruction = (
        f'Sign emails as "{signer}, {RT_PROFILE["company_name"]}".' if signer
        else f'No individual signer name has been configured - sign emails with '
             f'just "{RT_PROFILE["company_name"]}" (do not invent a person\'s name, '
             f'and do not leave a placeholder like "[Your Name]").'
    )
    return f"""
Company Name: {RT_PROFILE['company_name']}
Website: {RT_PROFILE['website']}
Business Type: {RT_PROFILE['business_type']}
Business Objective: {RT_PROFILE['business_objective']}
Primary Business Email: {RT_PROFILE['primary_email']}
Functional Email Aliases: {emails}

Business Principles:
{principles}

Signature instruction: {signature_instruction}
""".strip()


# ---------------------------------------------------------------------------
# AI restrictions - what the system must NEVER claim unless the fact was
# explicitly supplied to it (via research findings or the prospect record).
# ---------------------------------------------------------------------------

AI_RESTRICTIONS = """
Never claim, imply, or assume any of the following unless it has been
explicitly supplied to you as a documented fact in the research findings
or prospect record:

- existing authorization from a brand
- historical sales volume
- purchasing volume
- years of wholesale experience
- exclusive relationships
- marketplace permission (e.g. Amazon authorization)
- certifications
- customer counts, revenue, or performance figures

If a piece of information is missing, treat it as UNKNOWN. Do not fill in
a plausible-sounding value. Do not use vague language designed to sound
like a claim without technically being one - if you don't know something,
say you don't know, or simply don't mention it.
""".strip()


# ---------------------------------------------------------------------------
# Outreach evaluation rubric (used by the Outreach Manager)
# ---------------------------------------------------------------------------
# Weights must sum to 100. Defined once here; the manager's instructions are
# built from this dict (see outreach_manager.py) so changing a weight here
# changes the actual prompt the manager agent sees - no need to hunt through
# free-text instructions to retune scoring.

OUTREACH_RUBRIC = {
    "Credibility": 20,
    "Probability of Response": 20,
    "Professionalism": 15,
    "Clarity": 10,
    "Conciseness": 10,
    "Personalization": 10,
    "Call to Action": 10,
    "Compliance / No Unsupported Claims": 5,
}

assert sum(OUTREACH_RUBRIC.values()) == 100, "OUTREACH_RUBRIC weights must sum to 100"


def format_rubric() -> str:
    """Render OUTREACH_RUBRIC as a readable weighted list for a prompt."""
    lines = [f"- {name}: {weight} points" for name, weight in OUTREACH_RUBRIC.items()]
    return "\n".join(lines) + f"\n\nTotal: {sum(OUTREACH_RUBRIC.values())} points"


# ---------------------------------------------------------------------------
# Qualification scoring categories (used by the Qualification Agent)
# ---------------------------------------------------------------------------
# Unlike the outreach rubric, these categories don't need fixed point
# weights up front - the qualification agent reasons about them holistically
# to produce the overall_score. Listing them here (rather than only in the
# agent's prompt string) keeps them visible and easy to extend later.

QUALIFICATION_CATEGORIES = [
    "Wholesale relationship availability",
    "Apparent product/business fit",
    "Accessibility of reseller program",
    "Availability of business contact information",
    "Marketplace compatibility",
    "Amazon resale clarity",
    "Evidence of professional wholesale operations",
    "Potential barriers or restrictions",
]


# ===========================================================================
# Supplier Qualifier configuration
#
# Everything specific to the Supplier/Distributor Discovery capability lives
# below, following the same principle as the rest of this file: weights and
# thresholds are defined once here (not scattered through prompts or code)
# so they can be tuned in one place.
# ===========================================================================

# ---------------------------------------------------------------------------
# R&T operating region / geographic fit
# ---------------------------------------------------------------------------

RT_OPERATING_STATE = "Maryland"
RT_REQUIRES_MARYLAND_SERVICE = True  # a candidate must ship to/serve Maryland to be viable at all

PREFERRED_SUPPLIER_GEOGRAPHY = [
    "Maryland", "Delaware", "Washington D.C.", "Northern Virginia",
]  # prioritized, but credible national suppliers are never excluded for being outside this list

# ---------------------------------------------------------------------------
# Supplier scoring weights (0-100 total). Mirrors the OUTREACH_RUBRIC pattern
# above: defined once, asserted to sum to 100, rendered into agent prompts by
# format_supplier_rubric() so a weight change here changes actual behavior.
# ---------------------------------------------------------------------------

SUPPLIER_SCORING_WEIGHTS = {
    "Brand authorization evidence": 20,
    "Marketplace/channel compatibility": 15,
    "Invoice and supply-chain defensibility": 15,
    "Account accessibility for R&T": 10,
    "Business legitimacy and contact quality": 10,
    "Catalog/data usability": 10,
    "Commercial terms and initial-order accessibility": 8,
    "Geographic/service fit": 5,
    "Operational fulfillment fit": 4,
    "Risk profile": 3,
}

assert sum(SUPPLIER_SCORING_WEIGHTS.values()) == 100, "SUPPLIER_SCORING_WEIGHTS weights must sum to 100"


def format_supplier_rubric() -> str:
    lines = [f"- {name}: {weight} points" for name, weight in SUPPLIER_SCORING_WEIGHTS.items()]
    return "\n".join(lines) + f"\n\nTotal: {sum(SUPPLIER_SCORING_WEIGHTS.values())} points"


# Recommendation thresholds, applied to the final (post-gate) 0-100 score.
# Gates in supplier_scoring.py can force DO_NOT_PURSUE regardless of score;
# these thresholds only apply when no hard gate has already decided the outcome.
SUPPLIER_RECOMMENDATION_THRESHOLDS = {
    "CONTACT_NOW": 70,          # final_score >= this -> CONTACT_NOW (if no gate blocks it)
    "INVESTIGATE_FURTHER": 40,  # final_score >= this (and < CONTACT_NOW) -> INVESTIGATE_FURTHER
    # anything below INVESTIGATE_FURTHER -> DO_NOT_PURSUE
}

# ---------------------------------------------------------------------------
# Research / escalation / caching
# ---------------------------------------------------------------------------

# Fields that trigger a second, more targeted research pass when left UNKNOWN
# or CONFLICTING after the first pass. No second provider is introduced (see
# README "Research provider" note) - escalation means a second, more targeted
# WebSearchTool-backed pass focused specifically on these fields.
SUPPLIER_ESCALATION_FIELDS = [
    "brand_authorization",
    "amazon_marketplace_permission",
    "legal_identity_or_location",
    "supplier_role",
    "invoice_suitability",
]

SUPPLIER_MAX_ESCALATIONS_PER_BRAND = 3  # cap on extra targeted research passes per brand, to bound cost
SUPPLIER_STALE_DATA_DAYS = 90  # a relationship's research is considered stale after this many days
# The cache lives under SUPPLIER_DATA_DIR_NAME/cache/ - see supplier_persistence.CACHE_DIR.

SUPPLIER_DEFAULT_CONCURRENCY = 5
SUPPLIER_DEFAULT_RETRIES = 2
SUPPLIER_DEFAULT_TIMEOUT_SECONDS = 120
# Cost is bounded via SUPPLIER_MAX_ESCALATIONS_PER_BRAND above, --concurrency,
# and --limit on supplier_batch_runner.py - there is no per-run USD spend
# metering API available to enforce a raw dollar cap against, so one isn't
# faked here.

# ---------------------------------------------------------------------------
# Output locations
# ---------------------------------------------------------------------------

SUPPLIER_DATA_DIR_NAME = "supplier_data"          # suppliers/, relationships/, runs/, cache/
SUPPLIER_REPORTS_DIR_NAME = "supplier_reports"

# ---------------------------------------------------------------------------
# Outreach
# ---------------------------------------------------------------------------

SUPPLIER_ENABLED_OUTREACH_STRATEGIES = ["relationship", "procurement", "partnership"]


def supplier_sender_email() -> str:
    """The email identity used for supplier-facing outreach - purchasing@
    if configured, falling back to R&T's primary email. Never hardcoded
    outside RT_PROFILE."""
    for candidate in RT_PROFILE["functional_emails"]:
        if candidate.startswith("purchasing@"):
            return candidate
    return RT_PROFILE["primary_email"]

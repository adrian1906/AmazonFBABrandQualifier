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
import sys
from dotenv import load_dotenv
from agents import set_tracing_disabled

load_dotenv(override=True)

# Research/distributor names routinely contain non-ASCII characters (e.g.
# "Hāmākua Macadamia Nut Company", "丸久小山園") that crash a plain print()
# on Windows' default cp1252 console encoding - UnicodeEncodeError, seen for
# real in supplier_report_cli.py and grow_distributor_master_list.py. Fixed
# once, centrally, here (config.py is imported by nearly every entry point)
# rather than patched into each CLI script individually. Guarded because
# pytest sometimes substitutes a stdout/stderr wrapper that doesn't support
# .reconfigure() - this must never break the test suite.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# Disabled at the user's request - the per-run trace at
# platform.openai.com/traces (every `with trace(...)` in workflow.py /
# supplier_workflow.py) wasn't considered useful enough to keep. This is a
# one-time global toggle: `trace(...)` calls everywhere else in the project
# become no-ops once this runs, so nothing else needed to change. Set once
# here (config.py is imported by every agent module) rather than repeated
# in each entry point. Tests disable it independently in conftest.py, for
# the same reason plus avoiding a real network call during mocked runs.
set_tracing_disabled(True)

# Same convention as agents/2_openai/deep_research/search_agent.py:
# allow overriding the model via an env var, default to a small/cheap model.
MODEL_NAME = os.getenv("DEFAULT_MODEL_NAME", "gpt-5.4-mini")

# Used only by qualification_agent.py and supplier_qualification_agent.py -
# the two agents whose raw per-dimension scores feed directly into the hard
# gates in supplier_scoring.py (and, on the brand side, the PURSUE/
# INVESTIGATE/HOLD/REJECT call that decides what even reaches Stage 2).
# They apply nuanced conditional rules ("a DISTRIBUTOR_CLAIM alone should
# score no higher than moderate", "UNKNOWN should score low, not neutral")
# where a stronger model's judgment has the most downstream consequence.
# Deliberately NOT applied everywhere - research, drafting, and the
# outreach manager stay on MODEL_NAME, since this model costs ~3.3x more
# per token and a blanket upgrade would undo the cost cut from gating
# outreach drafting to CONTACT_NOW only (see supplier_workflow.py).
QUALIFICATION_MODEL_NAME = os.getenv("QUALIFICATION_MODEL_NAME", "gpt-5.4")


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

# Mirrors SUPPLIER_STALE_DATA_DAYS below (same default) - a brand's saved
# score is considered stale after this many days, so batch_runner.py's
# skip-already-scored check treats it as needing a fresh re-score rather
# than skipping it forever. SmartScout's own numbers (revenue, seller
# count, etc.) drift over time, so "already scored" shouldn't mean
# "scored once, ever" indefinitely.
BRAND_STALE_DATA_DAYS = 90


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

# "New-business/startup accessibility" added for a brand-new R&T, which has
# no existing trading history or distributor relationships yet - whether a
# candidate will actually work with a buyer in that position is a distinct
# question from general wholesale quality (a candidate can be an excellent
# supplier in general and still require 2 years in business or trade
# references R&T can't yet provide). Funded by trimming points from
# dimensions that matter less at this very first stage (catalog/data
# integration and operational fulfillment fit matter more once R&T is
# already buying from someone, not before the first distributor exists).
SUPPLIER_SCORING_WEIGHTS = {
    "Brand authorization evidence": 20,
    "Marketplace/channel compatibility": 15,
    "Invoice and supply-chain defensibility": 15,
    "Account accessibility for R&T": 10,
    "New-business/startup accessibility": 10,
    "Commercial terms and initial-order accessibility": 8,
    "Business legitimacy and contact quality": 9,
    "Catalog/data usability": 6,
    "Geographic/service fit": 3,
    "Operational fulfillment fit": 2,
    "Risk profile": 2,
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


# ===========================================================================
# FBA Catalog Analyzer configuration
#
# Turns a distributor price list (catalogs/*.xlsx or .csv) into per-ASIN
# landed-cost ROI and a target supplier price/discount. See catalog_models.py,
# roi_engine.py, catalog_import.py, keepa_adapter.py, demand_engine.py, and
# catalog_scan.py. Every value below is a PROVISIONAL default - same
# principle as SUPPLIER_SCORING_WEIGHTS above: defined once here, not
# scattered through code, so it can be tuned without hunting for it, and
# never silently treated as a researched fact about R&T's actual costs.
# ===========================================================================

from decimal import Decimal as _Decimal  # local alias - this file has no other Decimal use above

CATALOG_MARKETPLACE = "Amazon US"
CATALOG_CURRENCY = "USD"

# Qualification thresholds (spec sections 5 and 9). ROI always means
# landed-cost ROI by default (CATALOG_ROI_CONVENTION) - profit / (COGS + B),
# never margin or an annualized return. Both conventions are always shown;
# this only decides which one gates qualification. Highlight is "ROI
# strictly greater than" this threshold; target is "ROI >= " that one -
# asserted below to keep the ordering sane.
CATALOG_TARGET_ROI = _Decimal("0.10")
CATALOG_HIGHLIGHT_ROI_THRESHOLD = _Decimal("0.08")
CATALOG_ROI_CONVENTION = "landed"  # "landed" or "merchandise"

assert CATALOG_HIGHLIGHT_ROI_THRESHOLD < CATALOG_TARGET_ROI,     "CATALOG_HIGHLIGHT_ROI_THRESHOLD must be strictly below CATALOG_TARGET_ROI"

# Historical price-window defaults (section 3). Seasonal products need the
# longer windows - there is no automatic seasonality detector, so these are
# chosen per-product by the user, never inferred.
CATALOG_DEFAULT_HISTORY_DAYS = 90
CATALOG_SEASONAL_HISTORY_DAYS = 365
CATALOG_SEASONAL_EXTENDED_HISTORY_DAYS = 730

# Cost model defaults (section 4). PROVISIONAL, not researched facts about
# R&T's actual prep center or Amazon's current fee schedule - every
# CostComponent built from these is tagged status="assumed", never
# "verified", and must be overridable per run/product.
CATALOG_PREP_COST_PER_UNIT = _Decimal("1.50")  # per finished Amazon sellable unit
CATALOG_REFERRAL_RATE_FALLBACK = _Decimal("0.15")  # used only when no product-specific fee is supplied

# Set with Dr. Hood 2026-10-08 - explicit answers to the FBA Catalog
# Analyzer spec's own blocking questions, not generic industry defaults:
CATALOG_HOLDING_PERIOD_DAYS = 60  # cash-exposure / storage-fee planning assumption
CATALOG_MIN_MONTHLY_SALES = 25  # demand floor - see demand_engine.py; overridable per category/product
CATALOG_MIN_PROFIT_PER_UNIT: _Decimal | None = None  # None = no minimum - ROI% alone qualifies (explicitly declined)
CATALOG_ORDER_BUDGET_CAP: _Decimal | None = None  # None = no cap - size to the catalog as given (explicitly declined)

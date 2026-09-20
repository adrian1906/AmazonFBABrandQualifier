"""
Structured data models for the R&T Brand Acquisition System.

Every agent in this system uses Pydantic's `output_type=` feature (see the
course's Structured Outputs section in 3_lab3.ipynb) instead of free text,
so the framework parses the LLM's JSON response straight into one of these
Python objects. This makes the pipeline between agents type-safe and easy
to extend later.
"""

from typing import Literal, Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Prospect - the central record for a company R&T is considering contacting.
# This gets filled in gradually as it moves through the pipeline: it starts
# with just what the user knows, then research/qualification add to it.
# ---------------------------------------------------------------------------

class Prospect(BaseModel):
    company_name: str
    website: Optional[str] = None
    contact_name: Optional[str] = None
    contact_role: Optional[str] = None
    contact_email: Optional[str] = None
    product_categories: list[str] = Field(default_factory=list)
    company_description: Optional[str] = None
    wholesale_program: Optional[str] = None
    wholesale_application_url: Optional[str] = None
    amazon_policy: Optional[str] = None
    map_policy: Optional[str] = None
    minimum_order_quantity: Optional[str] = None
    opening_order: Optional[str] = None
    payment_terms: Optional[str] = None
    research_notes: Optional[str] = None
    sources: list[str] = Field(default_factory=list)

    # Filled in after the Qualification Agent runs
    qualification_score: Optional[int] = None
    qualification_status: Optional[str] = None
    recommended_action: Optional[str] = None


# ---------------------------------------------------------------------------
# Research Agent output
# ---------------------------------------------------------------------------
# Facts, inferences, and unknowns are kept in separate lists on purpose -
# this is what lets us honor "never treat an inference as a verified fact."

class ResearchFindings(BaseModel):
    company_name: str = Field(description="The prospect's company name")
    website: Optional[str] = Field(default=None, description="The prospect's website, if known")
    company_description: Optional[str] = Field(default=None, description="Brief description of what the company does")
    product_categories: list[str] = Field(default_factory=list, description="Major product categories the company sells")

    wholesale_program: Optional[str] = Field(default=None, description="What is known about a wholesale/dealer program, if anything")
    distributor_info: Optional[str] = Field(default=None, description="What is known about the company's distributors, if any")
    wholesale_application_url: Optional[str] = Field(default=None, description="URL for a wholesale/reseller application, if known")
    amazon_marketplace_policy: Optional[str] = Field(default=None, description="Known policy on Amazon resale, if any")
    map_policy: Optional[str] = Field(default=None, description="Known Minimum Advertised Price policy, if any")
    opening_order_requirement: Optional[str] = Field(default=None, description="Known opening order requirement, if any")
    minimum_order_quantity: Optional[str] = Field(default=None, description="Known minimum order quantity (MOQ), if any")
    contact_info: Optional[str] = Field(default=None, description="Known wholesale/business contact info, if any")

    verified_facts: list[str] = Field(
        default_factory=list,
        description="Statements directly supported by a source (a webpage, or explicit user-supplied information). Only put things here you are confident are accurate.",
    )
    inferences: list[str] = Field(
        default_factory=list,
        description="Reasonable guesses or interpretations that are NOT directly confirmed by a source. Must be clearly speculative, never phrased as fact.",
    )
    unknown_fields: list[str] = Field(
        default_factory=list,
        description="Named fields (e.g. 'MOQ', 'MAP policy') that could not be determined and should be treated as unknown rather than guessed at.",
    )
    observations: list[str] = Field(default_factory=list, description="Other relevant observations that don't fit the fields above")
    sources: list[str] = Field(default_factory=list, description="URLs or descriptions of where information came from (e.g. 'user-supplied notes', a URL from a web search)")


# ---------------------------------------------------------------------------
# Qualification Agent output
# ---------------------------------------------------------------------------

QualificationRecommendation = Literal["PURSUE", "INVESTIGATE", "HOLD", "REJECT"]


class QualificationResult(BaseModel):
    overall_score: int = Field(description="Overall qualification score from 0 to 100", ge=0, le=100)
    recommendation: QualificationRecommendation = Field(description="One of PURSUE, INVESTIGATE, HOLD, REJECT")

    # One score per category in config.QUALIFICATION_CATEGORIES, kept as
    # explicit named fields (not a generic dict) so this stays transparent
    # and each score is individually visible in the output.
    wholesale_relationship_availability: int = Field(ge=0, le=100)
    product_business_fit: int = Field(ge=0, le=100)
    reseller_program_accessibility: int = Field(ge=0, le=100)
    contact_information_availability: int = Field(ge=0, le=100)
    marketplace_compatibility: int = Field(ge=0, le=100)
    amazon_resale_clarity: int = Field(ge=0, le=100)
    professional_operations_evidence: int = Field(ge=0, le=100)
    barriers_or_restrictions: int = Field(
        ge=0, le=100,
        description="Higher = fewer/weaker barriers observed; lower = significant barriers observed",
    )

    key_reasons: list[str] = Field(default_factory=list, description="Key reasons behind the recommendation")
    risks: list[str] = Field(default_factory=list, description="Risks or concerns about pursuing this prospect")
    missing_information: list[str] = Field(default_factory=list, description="Important information that is still unknown")
    recommended_next_action: str = Field(description="A concrete, specific next step")


# ---------------------------------------------------------------------------
# Outreach Agent output
# ---------------------------------------------------------------------------

OutreachStrategy = Literal["relationship", "procurement", "partnership"]


class OutreachDraft(BaseModel):
    subject: str = Field(description="The email subject line")
    body: str = Field(description="The full email body")
    # strategy is set by our own code (not requested from the model) so it's
    # always accurate to which agent actually produced the draft.
    strategy: Optional[OutreachStrategy] = None


# ---------------------------------------------------------------------------
# Outreach Manager output
# ---------------------------------------------------------------------------

class DraftScore(BaseModel):
    strategy: OutreachStrategy = Field(description="Which draft this score belongs to: relationship, procurement, or partnership")
    score: int = Field(description="Score out of 100 using the weighted rubric", ge=0, le=100)
    explanation: str = Field(description="Short explanation of the score")
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)


class ManagerDecision(BaseModel):
    draft_scores: list[DraftScore] = Field(description="One score entry per draft evaluated")
    winning_strategy: OutreachStrategy = Field(description="The strategy tag of the winning draft")
    winning_reason: str = Field(description="Explanation of why this draft won")
    recommended_final_edits: Optional[str] = Field(
        default=None,
        description="Any specific edits recommended before the winning draft is sent, if any",
    )


# ===========================================================================
# Supplier Qualifier models
#
# The Brand Qualifier above answers "is this brand worth pursuing?". The
# models below answer a different question about a brand that already
# looks promising: "who can legitimately supply it to R&T, and is that
# supply path usable for Amazon resale?"
#
# Same design principles as above: facts, inferences, and unknowns are kept
# separate (here, generalized into a per-claim EvidenceItem so different
# fields on the same supplier can carry different evidence quality - see
# EvidenceItem below), and nothing here implies authorization or marketplace
# permission that wasn't actually found.
# ===========================================================================

YesNoUnknown = Literal["yes", "no", "unknown"]

# ---------------------------------------------------------------------------
# Evidence grading - attached to individual claims, not whole companies.
# ---------------------------------------------------------------------------

EvidenceState = Literal["VERIFIED", "DISTRIBUTOR_CLAIM", "INFERRED", "UNKNOWN", "CONFLICTING"]

SourceType = Literal[
    "official_brand_manufacturer_site",
    "distributor_or_supplier_site",
    "third_party_directory",
    "marketplace_listing",
    "user_supplied",
    "other_web_source",
]


class EvidenceItem(BaseModel):
    """One graded, sourced claim about a supplier candidate.

    `claim_field` names which fact this evidence supports (e.g.
    "brand_authorization", "amazon_marketplace_permission", "physical_address",
    "supplier_role") so a report can group evidence by field and a company
    is never described with a single evidence label covering everything.
    """
    claim_field: str = Field(description="Which fact this evidence supports, e.g. 'brand_authorization'")
    claim_value: str = Field(description="Short statement of what is claimed or found")
    evidence_state: EvidenceState
    source_type: SourceType
    source_url: Optional[str] = Field(default=None, description="URL of the source, if it has one")
    source_title: Optional[str] = Field(default=None, description="Page title or source name")
    excerpt: Optional[str] = Field(default=None, description="Relevant excerpt or a concise paraphrase")
    retrieved_at: Optional[str] = Field(default=None, description="ISO timestamp this claim was retrieved/observed")


# ---------------------------------------------------------------------------
# Supplier role classification
# ---------------------------------------------------------------------------

SupplierRole = Literal[
    "manufacturer_direct",
    "authorized_distributor",
    "importer_master_distributor",
    "stocking_wholesaler",
    "manufacturer_representative",
    "retail_dealer",
    "marketplace_broker_liquidator",
    "unclear_intermediary",
]

MarketplaceName = Literal["amazon", "walmart", "ebay", "etsy", "other_marketplace"]
MarketplacePermission = Literal["PERMITTED", "PROHIBITED", "RESTRICTED", "UNKNOWN"]
MarketplacePermissionScope = Literal["general", "account_specific", "brand_specific", "sku_specific", "channel_specific", "unknown"]


class MarketplacePolicy(BaseModel):
    marketplace: MarketplaceName
    permission: MarketplacePermission = "UNKNOWN"
    scope: MarketplacePermissionScope = "unknown"
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# Supplier candidate - one company that might be able to supply a brand.
# Produced by the Supplier Research Agent; evidence-graded field by field.
# ---------------------------------------------------------------------------

class SupplierCandidate(BaseModel):
    legal_business_name: str
    also_known_as: list[str] = Field(default_factory=list, description="Other names/aliases this candidate is known by")
    website: Optional[str] = None
    physical_address: Optional[str] = None
    phone: Optional[str] = None
    contact_method: Optional[str] = Field(default=None, description="Contact form URL, email, or account-application URL")

    service_area: Optional[str] = None
    ships_to_maryland: YesNoUnknown = "unknown"

    role: SupplierRole = "unclear_intermediary"
    brands_carried: list[str] = Field(default_factory=list)

    accepts_online_only_retailers: YesNoUnknown = "unknown"
    marketplace_policies: list[MarketplacePolicy] = Field(
        default_factory=list,
        description="Never infer PERMITTED just because a supplier lists/sells the brand, or from phrases like "
                     "'Amazon-friendly' or 'FBA-ready'. Leave UNKNOWN unless permission is actually stated.",
    )

    provides_itemized_invoices: YesNoUnknown = "unknown"
    invoice_fields_supported: list[str] = Field(
        default_factory=list,
        description="Which of: supplier legal name/address/phone, R&T legal name/address, transaction date, "
                     "item descriptions/model numbers, quantities - the invoice appears able to show",
    )
    can_verify_or_provide_loa: YesNoUnknown = "unknown"

    opening_order: Optional[str] = None
    recurring_moq: Optional[str] = None
    case_pack_info: Optional[str] = None
    payment_terms: Optional[str] = None
    freight_threshold: Optional[str] = None
    credit_requirements: Optional[str] = None

    catalog_data_formats: list[str] = Field(
        default_factory=list, description="e.g. 'price list PDF', 'UPC/GTIN spreadsheet', 'API', 'EDI', 'product feed'"
    )
    prep_center_or_dropship_support: Optional[str] = None
    direct_to_fba_support: YesNoUnknown = "unknown"

    map_territory_restrictions: Optional[str] = None
    risk_flags: list[str] = Field(
        default_factory=list,
        description="e.g. liquidation inventory, retail receipts, unverifiable authorization, gated catalog, "
                     "unclear legal entity, suspicious ungating claims, inconsistent contact info",
    )

    evidence: list[EvidenceItem] = Field(default_factory=list, description="Graded, sourced claims about this candidate")
    sources: list[str] = Field(default_factory=list, description="Quick list of source URLs/descriptions, for convenience")


class SupplierResearchFindings(BaseModel):
    """Output of the Supplier Research Agent for a single brand."""
    brand_name: str
    brand_website_checked: Optional[str] = None
    direct_brand_program_found: YesNoUnknown = Field(
        default="unknown", description="Whether an official direct wholesale/dealer program was found on the brand's own site"
    )
    candidates: list[SupplierCandidate] = Field(default_factory=list)
    aliases_flagged_for_review: list[str] = Field(
        default_factory=list,
        description="Possible duplicate/alias company names noticed but NOT auto-merged - flagged for human review",
    )
    observations: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Supplier scoring - the LLM assigns per-dimension scores; supplier_scoring.py
# (plain Python) turns them into a weighted, gated final result. Hard gates
# are enforced in code, not trusted to the model alone - the same philosophy
# as AI_RESTRICTIONS, applied to business-critical gating decisions.
# ---------------------------------------------------------------------------

class SupplierQualificationResult(BaseModel):
    """Raw LLM judgment for one supplier candidate, before gates are applied."""
    supplier_legal_name: str = Field(description="Must match the SupplierCandidate.legal_business_name this assessment is for")
    dimension_scores: dict[str, int] = Field(
        description="One 0-100 score per dimension name in config.SUPPLIER_SCORING_WEIGHTS"
    )
    rationale: str = Field(description="Short explanation of the overall judgment")
    risks: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)


class SupplierScoreDimension(BaseModel):
    name: str
    raw_score: int
    weight: float
    weighted_points: float


class SupplierScoreBreakdown(BaseModel):
    dimensions: list[SupplierScoreDimension]
    raw_weighted_total: float
    final_score: int = Field(ge=0, le=100)
    gates_triggered: list[str] = Field(default_factory=list, description="Names of hard gates that fired, if any")


SupplierRecommendation = Literal["CONTACT_NOW", "INVESTIGATE_FURTHER", "DO_NOT_PURSUE"]

SupplierLifecycleState = Literal[
    "DISCOVERED",
    "RESEARCHED",
    "CONTACT_APPROVED",
    "CONTACTED",
    "RESPONSE_RECEIVED",
    "APPLICATION_SUBMITTED",
    "ACCOUNT_APPROVED",
    "CATALOG_RECEIVED",
    "APPROVED_FOR_ASIN_ANALYSIS",
    "APPROVED_FOR_PURCHASE",
    "DECLINED",
    "DISQUALIFIED",
]


class SupplierAssessment(BaseModel):
    """The fully gated, code-computed result for one supplier candidate."""
    candidate: SupplierCandidate
    qualification: SupplierQualificationResult
    score_breakdown: SupplierScoreBreakdown
    recommendation: SupplierRecommendation
    lifecycle_state: SupplierLifecycleState
    gate_notes: list[str] = Field(default_factory=list, description="Human-readable reasons any gate constrained the outcome")


# ---------------------------------------------------------------------------
# Brand <-> Supplier relationship - the persisted, many-to-many join record.
# One brand may have multiple suppliers; one supplier may carry multiple
# brands with a different role/authorization/assessment for each.
# ---------------------------------------------------------------------------

class BrandSupplierRelationship(BaseModel):
    brand_name: str
    supplier_id: str = Field(description="Stable entity id from entity_resolution.py - same supplier, same id, across runs/brands")
    assessment: SupplierAssessment
    outreach_drafts: dict[str, OutreachDraft] = Field(default_factory=dict, description="Keyed by strategy: relationship/procurement/partnership")
    manager_decision: Optional[ManagerDecision] = None
    lifecycle_state: SupplierLifecycleState = "DISCOVERED"

    origin_brand_batch_id: Optional[str] = Field(default=None, description="Traceable link back to the brand batch this run came from, if any")
    origin_brand_qualification_status: Optional[str] = Field(
        default=None, description="The originating brand's status (PURSUE/INVESTIGATE/HOLD/REJECT), or 'standalone' if not from a brand batch"
    )
    inclusion_reason: str = Field(
        default="default_pursue",
        description="'default_pursue', 'manual_include', or 'standalone' - why this brand was researched",
    )

    first_researched_at: str
    last_researched_at: str
    research_run_ids: list[str] = Field(default_factory=list, description="History of research run ids that have touched this relationship")

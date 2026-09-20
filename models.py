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

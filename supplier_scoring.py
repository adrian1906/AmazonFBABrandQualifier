"""
Deterministic supplier scoring and hard-gate enforcement.

supplier_qualification_agent.py produces a raw, per-dimension judgment
(SupplierQualificationResult). This module turns that into the final,
weighted SupplierScoreBreakdown and applies the hard business/safety gates
from the spec - in plain Python, not left to the model's discretion, so
"a high score must not override PROHIBITED Amazon resale" etc. are actual
guarantees rather than instructions the model might drift from.

No function here makes network calls or changes anything externally. The
lifecycle-advancement gate (advance_lifecycle_state) is meant to be called
only in response to an explicit human decision (e.g. from a review CLI) -
never automatically, since a lifecycle state represents a real external
fact (an application was submitted, an account was approved, etc.) that
this system must never assume.
"""

from models import (
    SupplierCandidate,
    SupplierQualificationResult,
    SupplierScoreBreakdown,
    SupplierScoreDimension,
    SupplierAssessment,
    SupplierRecommendation,
    SupplierLifecycleState,
    EvidenceState,
)
from config import SUPPLIER_SCORING_WEIGHTS, SUPPLIER_RECOMMENDATION_THRESHOLDS

_LIQUIDATION_RISK_KEYWORDS = (
    "liquidat", "retail receipt", "unverifiable authorization", "suspicious ungating",
    "copied catalog", "unclear legal entity",
)

_AUTHORIZATION_FIELD = "brand_authorization"
_INVOICE_FIELDS = ("invoice_suitability", "invoice_capability", "stocking_evidence")


class LifecycleGateError(Exception):
    """Raised when a manual lifecycle-state change is blocked by an unmet gate."""


def compute_score_breakdown(qualification: SupplierQualificationResult) -> SupplierScoreBreakdown:
    dimensions: list[SupplierScoreDimension] = []
    total = 0.0
    for name, weight in SUPPLIER_SCORING_WEIGHTS.items():
        raw_score = max(0, min(100, qualification.dimension_scores.get(name, 0)))
        weighted_points = (raw_score / 100) * weight
        dimensions.append(SupplierScoreDimension(name=name, raw_score=raw_score, weight=weight, weighted_points=round(weighted_points, 2)))
        total += weighted_points
    return SupplierScoreBreakdown(
        dimensions=dimensions,
        raw_weighted_total=round(total, 2),
        final_score=round(total),
        gates_triggered=[],
    )


def _amazon_permission(candidate: SupplierCandidate) -> str:
    for policy in candidate.marketplace_policies:
        if policy.marketplace == "amazon":
            return policy.permission
    return "UNKNOWN"


def brand_authorization_state(candidate: SupplierCandidate) -> EvidenceState:
    items = [e for e in candidate.evidence if e.claim_field == _AUTHORIZATION_FIELD]
    for state in ("VERIFIED", "CONFLICTING", "DISTRIBUTOR_CLAIM", "INFERRED"):
        if any(e.evidence_state == state for e in items):
            return state
    return "UNKNOWN"


def _has_liquidation_style_risk(candidate: SupplierCandidate) -> bool:
    if candidate.role == "marketplace_broker_liquidator":
        return True
    text = " ".join(candidate.risk_flags).lower()
    return any(keyword in text for keyword in _LIQUIDATION_RISK_KEYWORDS)


def _has_confirmed_stocking_evidence(candidate: SupplierCandidate) -> bool:
    """Whether a manufacturer_representative candidate has evidence it actually
    sells and invoices inventory itself, rather than only referring buyers on."""
    has_invoice_evidence = any(
        e.claim_field in _INVOICE_FIELDS and e.evidence_state in ("VERIFIED", "DISTRIBUTOR_CLAIM")
        for e in candidate.evidence
    )
    return has_invoice_evidence and candidate.provides_itemized_invoices == "yes"


def gate_and_recommend(
    candidate: SupplierCandidate, breakdown: SupplierScoreBreakdown
) -> tuple[SupplierRecommendation, SupplierLifecycleState, list[str]]:
    """Apply the hard gates on top of the numeric score. Returns
    (recommendation, initial_lifecycle_state, human_readable_gate_notes)."""
    triggered: list[str] = []
    notes: list[str] = []

    amazon_permission = _amazon_permission(candidate)

    if amazon_permission == "PROHIBITED":
        triggered.append("amazon_resale_prohibited")
        notes.append(
            "Amazon resale is explicitly PROHIBITED for this brand/supplier - "
            "recommendation forced to DO_NOT_PURSUE regardless of score."
        )
        recommendation: SupplierRecommendation = "DO_NOT_PURSUE"
    elif _has_liquidation_style_risk(candidate):
        triggered.append("liquidation_or_unverifiable_risk")
        notes.append(
            "Liquidation inventory, retail-receipt, unverifiable-authorization, or similar risk flag present - "
            "not recommended as a normal replenishable wholesale source."
        )
        recommendation = "DO_NOT_PURSUE"
    else:
        if breakdown.final_score >= SUPPLIER_RECOMMENDATION_THRESHOLDS["CONTACT_NOW"]:
            recommendation = "CONTACT_NOW"
        elif breakdown.final_score >= SUPPLIER_RECOMMENDATION_THRESHOLDS["INVESTIGATE_FURTHER"]:
            recommendation = "INVESTIGATE_FURTHER"
        else:
            recommendation = "DO_NOT_PURSUE"

    if candidate.role == "manufacturer_representative" and not _has_confirmed_stocking_evidence(candidate):
        triggered.append("rep_without_stocking_evidence")
        notes.append(
            "Manufacturer representative with no confirmed stocking/invoicing evidence - "
            "treat as a referral contact to the actual stocking distributor, not as the purchase source."
        )

    if amazon_permission == "UNKNOWN":
        triggered.append("amazon_permission_unknown")
        notes.append("Amazon marketplace permission is UNKNOWN - this remains unknown and is never treated as approval.")

    auth_state = brand_authorization_state(candidate)
    if auth_state != "VERIFIED":
        triggered.append("authorization_not_verified")
        notes.append(
            f"Brand authorization evidence state is {auth_state}, not VERIFIED - "
            "this blocks reaching APPROVED_FOR_PURCHASE until verified."
        )

    breakdown.gates_triggered = triggered
    # The automatic pipeline never assigns anything beyond RESEARCHED - every
    # later lifecycle state represents a real external event a human must record.
    initial_lifecycle_state: SupplierLifecycleState = "RESEARCHED"
    return recommendation, initial_lifecycle_state, notes


def assess_candidate(candidate: SupplierCandidate, qualification: SupplierQualificationResult) -> SupplierAssessment:
    breakdown = compute_score_breakdown(qualification)
    recommendation, lifecycle_state, notes = gate_and_recommend(candidate, breakdown)
    return SupplierAssessment(
        candidate=candidate,
        qualification=qualification,
        score_breakdown=breakdown,
        recommendation=recommendation,
        lifecycle_state=lifecycle_state,
        gate_notes=notes,
    )


_ASIN_OR_PURCHASE_STATES = {"APPROVED_FOR_ASIN_ANALYSIS", "APPROVED_FOR_PURCHASE"}


def advance_lifecycle_state(assessment: SupplierAssessment, new_state: SupplierLifecycleState) -> SupplierLifecycleState:
    """
    Validate (and return) a manual lifecycle-state change requested by a
    human reviewer. Raises LifecycleGateError if the change is not allowed
    yet. Never called automatically - see module docstring.
    """
    if new_state in _ASIN_OR_PURCHASE_STATES:
        candidate = assessment.candidate
        amazon_permission = _amazon_permission(candidate)
        if amazon_permission != "PERMITTED":
            raise LifecycleGateError(
                f"Cannot move to {new_state}: Amazon marketplace permission is {amazon_permission}, not PERMITTED."
            )
        if brand_authorization_state(candidate) != "VERIFIED":
            raise LifecycleGateError(
                f"Cannot move to {new_state}: brand authorization evidence is not VERIFIED."
            )
        if _has_liquidation_style_risk(candidate):
            raise LifecycleGateError(
                f"Cannot move to {new_state}: unresolved liquidation/unverifiable-authorization risk flag."
            )
    return new_state

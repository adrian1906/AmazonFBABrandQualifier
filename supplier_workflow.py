"""
The Supplier Qualifier workflow: wires the supplier-side agents together for
one brand at a time.

    Brand name (+ optional brand-qualifier context)
       |
    Supplier Research Agent          <- official site first, then broader search
       |
    (targeted escalation pass, per candidate, if key fields remain uncertain)
       |
    Entity resolution                <- stable supplier_id per real-world company
       |
    Supplier Qualification Agent     <- per candidate, run concurrently
       |
    supplier_scoring.assess_candidate  <- deterministic weights + hard gates (plain Python)
       |
    (CONTACT_NOW / INVESTIGATE_FURTHER only)
       |
    +-----------------------------+
    |              |              |
 Relationship   Procurement   Partnership     <- supplier_outreach_agents.py, run concurrently
    |              |              |
    +--------------+--------------+
                   |
       outreach_manager_agent (reused as-is)
                   |
         BrandSupplierRelationship            <- persisted; see approval.py for the human gate

Mirrors workflow.py: plain async/await Runner.run() calls inside a single
trace(...), no SDK handoffs. Nothing in this file sends anything externally
or advances a lifecycle state past RESEARCHED automatically - see
supplier_scoring.py.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime

from agents import Runner, trace

import supplier_persistence
from config import (
    SUPPLIER_MAX_ESCALATIONS_PER_BRAND,
    SUPPLIER_ENABLED_OUTREACH_STRATEGIES,
    SUPPLIER_DEFAULT_RETRIES,
    SUPPLIER_DEFAULT_TIMEOUT_SECONDS,
)
from entity_resolution import EntityIndex
from models import (
    SupplierResearchFindings,
    SupplierCandidate,
    SupplierQualificationResult,
    SupplierAssessment,
    BrandSupplierRelationship,
    OutreachDraft,
    ManagerDecision,
    EvidenceItem,
)
from outreach_manager import outreach_manager_agent
from supplier_outreach_agents import SUPPLIER_OUTREACH_AGENTS
from supplier_qualification_agent import supplier_qualification_agent
from supplier_research_agent import build_supplier_research_agent
from supplier_scoring import assess_candidate, brand_authorization_state


@dataclass
class SupplierWorkflowResult:
    """Everything produced by one supplier-research run for a single brand."""
    brand_name: str
    research: SupplierResearchFindings
    relationships: list[BrandSupplierRelationship]
    run_id: str
    supplier_batch_id: str | None = None
    origin_brand_batch_id: str | None = None
    origin_brand_qualification_status: str | None = None
    inclusion_reason: str = "standalone"
    cache_hit: bool = False
    escalated_candidates: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Prompt-rendering helpers
# ---------------------------------------------------------------------------

def _render_research_input(brand_name: str, context_notes: str) -> str:
    return f"""
Discover and profile candidate supply paths for this brand for R&T Distribution Group LLC.

Brand name: {brand_name}

Context already known about this brand (e.g. from R&T's Brand Qualifier, or user-supplied notes) - treat as trustworthy:
{context_notes or "(none provided)"}

Follow your search-order and evidence-grading instructions.
""".strip()


def _render_escalation_input(brand_name: str, candidate: SupplierCandidate, missing_fields: list[str]) -> str:
    return f"""
Follow-up, targeted research pass for ONE supplier candidate already found for brand "{brand_name}".
Focus specifically on resolving these currently unknown/uncertain fields - do not spend effort
re-confirming facts already established below.

Candidate already known:
{candidate.model_dump_json(indent=2)}

Fields that still need resolving: {", ".join(missing_fields)}

Return your findings in the same structured format, with a single candidate entry for this
same company reflecting anything new you found.
""".strip()


def _render_qualification_input(candidate: SupplierCandidate) -> str:
    brands = ", ".join(candidate.brands_carried) or "the target brand"
    return f"""
Here is one supplier candidate's research profile relevant to brand(s): {brands}.
Score it per your instructions.

{candidate.model_dump_json(indent=2)}
""".strip()


def _render_supplier_outreach_input(brand_name: str, assessment: SupplierAssessment) -> str:
    c = assessment.candidate
    return f"""
Write outreach to this supplier candidate regarding brand: {brand_name}

Candidate: {c.legal_business_name} ({c.website or "website unknown"})
Role: {c.role}
Contact method: {c.contact_method or "unknown"}

Full candidate profile (use this to know what's already known vs. still unknown - do not re-ask about known facts):
{c.model_dump_json(indent=2)}

Qualification summary:
- Recommendation: {assessment.recommendation}
- Final score: {assessment.score_breakdown.final_score}/100
- Gate notes: {"; ".join(assessment.gate_notes) or "none"}
""".strip()


async def _run_with_retry(agent, input_text: str):
    """Runner.run wrapped with config.SUPPLIER_DEFAULT_TIMEOUT_SECONDS and
    config.SUPPLIER_DEFAULT_RETRIES - the only place that policy lives.
    A transient failure (a slow response, a flaky tool call) gets retried
    here; if retries are exhausted the exception propagates up to the
    batch runner's own per-brand try/except, so one bad brand still can't
    take down a whole batch."""
    last_error: Exception | None = None
    for attempt in range(SUPPLIER_DEFAULT_RETRIES + 1):
        try:
            return await asyncio.wait_for(Runner.run(agent, input_text), timeout=SUPPLIER_DEFAULT_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001 - deliberately broad: retry any transient agent-call failure
            last_error = exc
            if attempt == SUPPLIER_DEFAULT_RETRIES:
                raise
    raise last_error  # pragma: no cover - unreachable, satisfies static analysis


def _render_manager_input(drafts: dict[str, OutreachDraft]) -> str:
    sections = [
        f"--- Draft ({strategy}) ---\nSubject: {draft.subject}\n\n{draft.body}"
        for strategy, draft in drafts.items()
    ]
    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Escalation - a second, targeted research pass for candidates whose
# high-impact fields are still uncertain after the first pass.
# ---------------------------------------------------------------------------

def _needs_escalation(candidate: SupplierCandidate) -> list[str]:
    missing: list[str] = []
    if brand_authorization_state(candidate) in ("UNKNOWN", "INFERRED"):
        missing.append("brand_authorization")
    amazon_policy = next((p for p in candidate.marketplace_policies if p.marketplace == "amazon"), None)
    if amazon_policy is None or amazon_policy.permission == "UNKNOWN":
        missing.append("amazon_marketplace_permission")
    if not candidate.physical_address or not candidate.website:
        missing.append("legal_identity_or_location")
    if candidate.role == "unclear_intermediary":
        missing.append("supplier_role")
    if candidate.provides_itemized_invoices == "unknown":
        missing.append("invoice_suitability")
    return missing


_MERGEABLE_SCALAR_FIELDS = (
    "physical_address", "phone", "contact_method", "service_area",
    "provides_itemized_invoices", "can_verify_or_provide_loa",
    "direct_to_fba_support", "accepts_online_only_retailers", "ships_to_maryland",
)
_MERGEABLE_LIST_FIELDS = (
    "catalog_data_formats", "risk_flags", "invoice_fields_supported",
    "brands_carried", "also_known_as", "sources",
)


def _merge_escalation(candidate: SupplierCandidate, escalation_findings: SupplierResearchFindings) -> None:
    """Merge a single-candidate escalation pass back into the original
    candidate. Never silently overwrites a known value with a different
    one - a genuine conflict becomes a CONFLICTING evidence item instead."""
    if not escalation_findings.candidates:
        return
    new_data = escalation_findings.candidates[0]

    seen = {(e.claim_field, e.claim_value, e.source_url) for e in candidate.evidence}
    for item in new_data.evidence:
        signature = (item.claim_field, item.claim_value, item.source_url)
        if signature not in seen:
            candidate.evidence.append(item)
            seen.add(signature)

    for field_name in _MERGEABLE_SCALAR_FIELDS:
        current = getattr(candidate, field_name)
        new_value = getattr(new_data, field_name)
        current_unknown = current is None or current == "unknown"
        new_known = new_value is not None and new_value != "unknown"
        if current_unknown and new_known:
            setattr(candidate, field_name, new_value)
        elif not current_unknown and new_known and new_value != current:
            candidate.evidence.append(EvidenceItem(
                claim_field=field_name,
                claim_value=f"Conflicting values found: original={current!r}, escalation pass={new_value!r}",
                evidence_state="CONFLICTING",
                source_type="other_web_source",
            ))

    if candidate.role == "unclear_intermediary" and new_data.role != "unclear_intermediary":
        candidate.role = new_data.role

    for policy in new_data.marketplace_policies:
        existing = next((p for p in candidate.marketplace_policies if p.marketplace == policy.marketplace), None)
        if existing is None:
            candidate.marketplace_policies.append(policy)
        elif existing.permission == "UNKNOWN" and policy.permission != "UNKNOWN":
            existing.permission = policy.permission
            existing.scope = policy.scope
            existing.notes = policy.notes

    for field_name in _MERGEABLE_LIST_FIELDS:
        current_list = getattr(candidate, field_name)
        for value in getattr(new_data, field_name):
            if value not in current_list:
                current_list.append(value)


# ---------------------------------------------------------------------------
# Outreach drafting
# ---------------------------------------------------------------------------

async def _generate_supplier_outreach_drafts(brand_name: str, assessment: SupplierAssessment) -> dict[str, OutreachDraft]:
    input_text = _render_supplier_outreach_input(brand_name, assessment)
    agents_to_run = [(a, s) for a, s in SUPPLIER_OUTREACH_AGENTS if s in SUPPLIER_ENABLED_OUTREACH_STRATEGIES]
    if not agents_to_run:
        return {}
    results = await asyncio.gather(*[_run_with_retry(agent, input_text) for agent, _strategy in agents_to_run])
    drafts: dict[str, OutreachDraft] = {}
    for (_agent, strategy), result in zip(agents_to_run, results):
        draft: OutreachDraft = result.final_output
        draft.strategy = strategy
        drafts[strategy] = draft
    return drafts


async def _evaluate_drafts(drafts: dict[str, OutreachDraft]) -> ManagerDecision:
    manager_result = await _run_with_retry(outreach_manager_agent, _render_manager_input(drafts))
    return manager_result.final_output


# ---------------------------------------------------------------------------
# Full per-brand pipeline
# ---------------------------------------------------------------------------

async def run_supplier_research_for_brand(
    brand_name: str,
    context_notes: str = "",
    allow_web_search: bool = True,
    supplier_batch_id: str | None = None,
    origin_brand_batch_id: str | None = None,
    origin_brand_qualification_status: str | None = None,
    inclusion_reason: str = "standalone",
    use_cache: bool = True,
) -> SupplierWorkflowResult:
    """
    Run the full Supplier Qualifier pipeline for one brand: Research
    (+ targeted escalation) -> entity resolution -> Qualification -> scoring
    + gates -> (for CONTACT_NOW / INVESTIGATE_FURTHER only) outreach drafts
    + manager evaluation. Persists results via supplier_persistence and
    returns a SupplierWorkflowResult for reporting/approval.
    """
    run_id = supplier_persistence.new_run_id()
    cache_hit = False
    escalated: list[str] = []

    with trace(f"R&T Supplier Qualifier: {brand_name}"):
        findings: SupplierResearchFindings | None = supplier_persistence.cache_get(brand_name) if use_cache else None
        cache_hit = findings is not None

        if findings is None:
            research_agent = build_supplier_research_agent(allow_web_search=allow_web_search)
            research_result = await _run_with_retry(research_agent, _render_research_input(brand_name, context_notes))
            findings = research_result.final_output
            supplier_persistence.cache_set(brand_name, findings)

        if allow_web_search and SUPPLIER_MAX_ESCALATIONS_PER_BRAND > 0:
            escalation_agent = build_supplier_research_agent(allow_web_search=True)
            budget = SUPPLIER_MAX_ESCALATIONS_PER_BRAND
            for candidate in findings.candidates:
                if budget <= 0:
                    break
                missing_fields = _needs_escalation(candidate)
                if not missing_fields:
                    continue
                escalation_result = await _run_with_retry(
                    escalation_agent, _render_escalation_input(brand_name, candidate, missing_fields)
                )
                _merge_escalation(candidate, escalation_result.final_output)
                escalated.append(candidate.legal_business_name)
                budget -= 1

        supplier_persistence.save_research_run(run_id, brand_name, findings, batch_id=supplier_batch_id)

        index: EntityIndex = supplier_persistence.load_entity_index()
        relationships: list[BrandSupplierRelationship] = []
        now = datetime.now().isoformat(timespec="seconds")

        qualification_runs = (
            await asyncio.gather(*[
                _run_with_retry(supplier_qualification_agent, _render_qualification_input(c))
                for c in findings.candidates
            ])
            if findings.candidates else []
        )

        for candidate, qual_run in zip(findings.candidates, qualification_runs):
            qualification: SupplierQualificationResult = qual_run.final_output
            assessment = assess_candidate(candidate, qualification)

            supplier_id = index.resolve(candidate)
            supplier_persistence.save_supplier_candidate(supplier_id, candidate)

            drafts: dict[str, OutreachDraft] = {}
            manager_decision: ManagerDecision | None = None
            if assessment.recommendation in ("CONTACT_NOW", "INVESTIGATE_FURTHER"):
                drafts = await _generate_supplier_outreach_drafts(brand_name, assessment)
                if drafts:
                    manager_decision = await _evaluate_drafts(drafts)

            relationship = BrandSupplierRelationship(
                brand_name=brand_name,
                supplier_id=supplier_id,
                assessment=assessment,
                outreach_drafts=drafts,
                manager_decision=manager_decision,
                lifecycle_state=assessment.lifecycle_state,
                origin_brand_batch_id=origin_brand_batch_id,
                origin_brand_qualification_status=origin_brand_qualification_status,
                inclusion_reason=inclusion_reason,
                first_researched_at=now,
                last_researched_at=now,
                research_run_ids=[run_id],
            )

            existing_path = supplier_persistence.relationship_path(brand_name, supplier_id)
            if existing_path.exists():
                existing = supplier_persistence.load_relationship(existing_path)
                relationship.first_researched_at = existing.first_researched_at
                relationship.research_run_ids = existing.research_run_ids + [run_id]
                # A human-recorded lifecycle state represents a real external
                # fact - a fresh research pass must never silently reset it.
                if existing.lifecycle_state not in ("DISCOVERED", "RESEARCHED"):
                    relationship.lifecycle_state = existing.lifecycle_state

            supplier_persistence.save_relationship(relationship)
            relationships.append(relationship)

        supplier_persistence.save_entity_index(index)

    return SupplierWorkflowResult(
        brand_name=brand_name,
        research=findings,
        relationships=relationships,
        run_id=run_id,
        supplier_batch_id=supplier_batch_id,
        origin_brand_batch_id=origin_brand_batch_id,
        origin_brand_qualification_status=origin_brand_qualification_status,
        inclusion_reason=inclusion_reason,
        cache_hit=cache_hit,
        escalated_candidates=escalated,
    )


async def regenerate_supplier_outreach(brand_name: str, assessment: SupplierAssessment) -> tuple[dict[str, OutreachDraft], ManagerDecision | None]:
    """Re-run just the outreach-drafting + manager-evaluation stage for one
    relationship, reusing its existing assessment. Used by the approval
    gate's REGENERATE option - no research/qualification re-run, no
    lifecycle-state change."""
    with trace(f"R&T Supplier Qualifier (regenerate): {brand_name} / {assessment.candidate.legal_business_name}"):
        drafts = await _generate_supplier_outreach_drafts(brand_name, assessment)
        manager_decision = await _evaluate_drafts(drafts) if drafts else None
    return drafts, manager_decision

"""
Formats BrandSupplierRelationship records into the human-readable reports
required by the spec: a ranked supplier qualification report, a
brand-to-supplier matrix, a missing-information/action queue, a
contact-now queue, and a do-not-pursue section with reasons - plus a
single-relationship detail view used by the approval gate (see approval.py
/ supplier_review_one.py), mirroring report.py's separation of concerns.
"""

from models import BrandSupplierRelationship

_ROLE_LABELS = {
    "manufacturer_direct": "Manufacturer/Brand Direct",
    "authorized_distributor": "Authorized Distributor",
    "importer_master_distributor": "Importer / Master Distributor",
    "stocking_wholesaler": "Stocking Wholesaler",
    "manufacturer_representative": "Manufacturer Representative (referral only, not a stocking supplier)",
    "retail_dealer": "Retail Dealer",
    "marketplace_broker_liquidator": "Marketplace/Broker/Liquidator",
    "unclear_intermediary": "Unclear Intermediary (unverified)",
}


def _role_label(role: str) -> str:
    return _ROLE_LABELS.get(role, role)


def _amazon_status(rel: BrandSupplierRelationship) -> str:
    for policy in rel.assessment.candidate.marketplace_policies:
        if policy.marketplace == "amazon":
            return f"{policy.permission} ({policy.scope})"
    return "UNKNOWN"


def _evidence_lines(rel: BrandSupplierRelationship) -> list[str]:
    lines = []
    for item in rel.assessment.candidate.evidence:
        source = item.source_url or item.source_title or "(no source url)"
        checked = item.retrieved_at or "(retrieval date unknown)"
        lines.append(f"    [{item.evidence_state}] {item.claim_field}: {item.claim_value} — {source} (checked: {checked})")
    return lines


def format_relationship_summary_line(rel: BrandSupplierRelationship) -> str:
    c = rel.assessment.candidate
    return (
        f"{rel.assessment.score_breakdown.final_score:>3}/100  {rel.assessment.recommendation:<20} "
        f"{c.legal_business_name}  [{_role_label(c.role)}]  <- {rel.brand_name}"
    )


def format_relationship_detail(rel: BrandSupplierRelationship, status: str = "AWAITING HUMAN APPROVAL") -> str:
    c = rel.assessment.candidate
    q = rel.assessment.qualification
    breakdown = rel.assessment.score_breakdown

    dimension_lines = "\n".join(
        f"  - {d.name}: {d.raw_score}/100 (weight {d.weight}, {d.weighted_points} pts)" for d in breakdown.dimensions
    )
    gate_lines = "\n".join(f"  - {note}" for note in rel.assessment.gate_notes) or "  (none triggered)"
    risk_lines = "\n".join(f"  - {r}" for r in q.risks) or "  (none noted)"
    missing_lines = "\n".join(f"  - {m}" for m in q.missing_information) or "  (none noted)"
    evidence_lines = "\n".join(_evidence_lines(rel)) or "  (no evidence recorded)"

    draft_lines = []
    for i, (strategy, draft) in enumerate(rel.outreach_drafts.items(), start=1):
        score_entry = None
        if rel.manager_decision:
            score_entry = next((d for d in rel.manager_decision.draft_scores if d.strategy == strategy), None)
        score_text = f"{score_entry.score}/100" if score_entry else "(not scored)"
        draft_lines.append(f"Draft {i} — {strategy.title()}\nScore: {score_text}")
    outreach_candidates = "\n\n".join(draft_lines) or "(no outreach drafted for this recommendation)"

    winner_section = "(no winning draft - recommendation did not warrant outreach)"
    if rel.manager_decision and rel.outreach_drafts:
        winner = rel.outreach_drafts[rel.manager_decision.winning_strategy]
        winner_section = (
            f"WINNER: {rel.manager_decision.winning_strategy.title()} Draft\n\n"
            f"Reason:\n{rel.manager_decision.winning_reason}\n\n"
            f"Subject:\n{winner.subject}\n\n"
            f"Body:\n{winner.body}"
        )

    return f"""
SUPPLIER QUALIFICATION REPORT

Brand: {rel.brand_name}
Supplier: {c.legal_business_name} (id: {rel.supplier_id})
Role: {_role_label(c.role)}
Website: {c.website or "unknown"}
Address: {c.physical_address or "unknown"}
Phone: {c.phone or "unknown"}
Contact method: {c.contact_method or "unknown"}
Serves Maryland: {c.ships_to_maryland}

Score: {breakdown.final_score}/100
Recommendation: {rel.assessment.recommendation}
Lifecycle state: {rel.lifecycle_state}

Score breakdown:
{dimension_lines}

Gates triggered:
{gate_lines}

Amazon marketplace status: {_amazon_status(rel)}
Invoice fields supported: {", ".join(c.invoice_fields_supported) or "unknown"}
Can verify / provide LOA: {c.can_verify_or_provide_loa}
Commercial terms: opening order={c.opening_order or "unknown"}, MOQ={c.recurring_moq or "unknown"}, payment={c.payment_terms or "unknown"}
Catalog/data availability: {", ".join(c.catalog_data_formats) or "unknown"}
MAP/territory restrictions: {c.map_territory_restrictions or "unknown"}
Risk flags: {", ".join(c.risk_flags) or "none noted"}

Risks:
{risk_lines}

Missing information:
{missing_lines}

Evidence:
{evidence_lines}

OUTREACH CANDIDATES

{outreach_candidates}

{winner_section}

Origin: brand_batch_id={rel.origin_brand_batch_id or "(standalone)"}, brand_status={rel.origin_brand_qualification_status or "n/a"}, inclusion_reason={rel.inclusion_reason}

STATUS:
{status}
""".strip()


# ---------------------------------------------------------------------------
# Batch-level reports
# ---------------------------------------------------------------------------

def format_ranked_report(relationships: list[BrandSupplierRelationship]) -> str:
    ranked = sorted(relationships, key=lambda r: r.assessment.score_breakdown.final_score, reverse=True)
    lines = ["RANKED SUPPLIER QUALIFICATION REPORT", ""]
    lines += [format_relationship_summary_line(r) for r in ranked] or ["(no suppliers researched)"]
    return "\n".join(lines)


def format_brand_supplier_matrix(relationships: list[BrandSupplierRelationship]) -> str:
    brands: dict[str, list[BrandSupplierRelationship]] = {}
    for rel in relationships:
        brands.setdefault(rel.brand_name, []).append(rel)

    lines = ["BRAND-TO-SUPPLIER MATRIX", ""]
    for brand, rels in brands.items():
        lines.append(f"{brand}:")
        for rel in sorted(rels, key=lambda r: r.assessment.score_breakdown.final_score, reverse=True):
            c = rel.assessment.candidate
            lines.append(
                f"  - {c.legal_business_name} [{_role_label(c.role)}] "
                f"-> {rel.assessment.recommendation} ({rel.assessment.score_breakdown.final_score}/100)"
            )
        lines.append("")
    return "\n".join(lines).strip()


def format_missing_information_queue(relationships: list[BrandSupplierRelationship]) -> str:
    lines = ["MISSING-INFORMATION / ACTION QUEUE", ""]
    any_items = False
    for rel in relationships:
        missing = rel.assessment.qualification.missing_information
        if not missing:
            continue
        any_items = True
        lines.append(f"{rel.assessment.candidate.legal_business_name} (brand: {rel.brand_name}):")
        lines += [f"  - {m}" for m in missing]
        lines.append("")
    if not any_items:
        lines.append("(nothing outstanding)")
    return "\n".join(lines).strip()


def format_contact_now_queue(relationships: list[BrandSupplierRelationship]) -> str:
    contact_now = [r for r in relationships if r.assessment.recommendation == "CONTACT_NOW"]
    contact_now.sort(key=lambda r: r.assessment.score_breakdown.final_score, reverse=True)
    lines = ["CONTACT-NOW QUEUE", ""]
    lines += [format_relationship_summary_line(r) for r in contact_now] or ["(nothing ready to contact yet)"]
    return "\n".join(lines)


def format_do_not_pursue_section(relationships: list[BrandSupplierRelationship]) -> str:
    do_not_pursue = [r for r in relationships if r.assessment.recommendation == "DO_NOT_PURSUE"]
    lines = ["DO-NOT-PURSUE", ""]
    if not do_not_pursue:
        lines.append("(none)")
        return "\n".join(lines)
    for rel in do_not_pursue:
        c = rel.assessment.candidate
        lines.append(f"{c.legal_business_name} (brand: {rel.brand_name}) [{_role_label(c.role)}]")
        for note in rel.assessment.gate_notes:
            lines.append(f"  - {note}")
        lines.append("")
    return "\n".join(lines).strip()


def format_full_supplier_report(relationships: list[BrandSupplierRelationship]) -> str:
    sections = [
        format_ranked_report(relationships),
        format_brand_supplier_matrix(relationships),
        format_missing_information_queue(relationships),
        format_contact_now_queue(relationships),
        format_do_not_pursue_section(relationships),
    ]
    return "\n\n" + ("\n\n" + "=" * 78 + "\n\n").join(sections)

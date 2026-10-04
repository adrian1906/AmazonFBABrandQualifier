"""
Render saved Distributor/Supplier Qualifier results (BrandSupplierRelationship
records under supplier_data/) into ONE printable Markdown file, grouped by
brand - the Stage 2 equivalent of batch_report_md.py (which does this for
Stage 1 Brand Qualifier results). Makes no API calls; only reads
already-saved data, like supplier_report_cli.py does.

Usage:
    python supplier_report_md.py --batch supbatch_20261002_060912
    python supplier_report_md.py --brand "ActiPatch"
    python supplier_report_md.py                        # every saved relationship
    python supplier_report_md.py --batch supbatch_... --all-drafts
    python supplier_report_md.py --batch supbatch_... --output my_review.md
"""

import argparse
from datetime import datetime
from pathlib import Path

import supplier_persistence
from config import RT_PROFILE
from models import BrandSupplierRelationship
from supplier_report import _role_label, _amazon_status

REPORTS_DIR = Path(__file__).parent / "batch_reports"
_PAGE_BREAK = '<div style="page-break-after: always;"></div>'
_COMPANY_NAME = RT_PROFILE["company_name"]


def _val(value) -> str:
    return str(value).strip() if value not in (None, "") else "—"


def _blockquote(text: str) -> str:
    return "\n".join(f"> {line}".rstrip() for line in text.strip().splitlines())


def _evidence_table(rel: BrandSupplierRelationship) -> list[str]:
    items = rel.assessment.candidate.evidence
    if not items:
        return ["_(no evidence recorded)_", ""]
    lines = ["| State | Claim | Value | Source | Checked |", "|---|---|---|---|---|"]
    for item in items:
        source = item.source_url or item.source_title or "—"
        checked = item.retrieved_at or "—"
        # Markdown table cells can't contain raw newlines or unescaped pipes.
        value = item.claim_value.replace("\n", " ").replace("|", "\\|")
        lines.append(f"| {item.evidence_state} | {item.claim_field} | {value} | {source} | {checked} |")
    lines.append("")
    return lines


def _email_section(rel: BrandSupplierRelationship, strategy: str, heading: str) -> str:
    draft = rel.outreach_drafts[strategy]
    score = None
    if rel.manager_decision:
        score = next((d.score for d in rel.manager_decision.draft_scores if d.strategy == strategy), None)
    score_text = f" (score {score}/100)" if score is not None else ""
    return f"#### {heading} — {strategy.title()}{score_text}\n\n**Subject:** {draft.subject}\n\n{_blockquote(draft.body)}"


def _distributor_section(rel: BrandSupplierRelationship) -> str:
    """One distributor candidate's full detail, as a sub-section under its brand."""
    c = rel.assessment.candidate
    q = rel.assessment.qualification
    breakdown = rel.assessment.score_breakdown

    lines = [
        f"### {c.legal_business_name} — {breakdown.final_score}/100 · {rel.assessment.recommendation}",
        f"_Role: {_role_label(c.role)}_",
        "",
    ]

    lines += [
        "**Key facts**", "",
        f"- **Website:** {_val(c.website)}",
        f"- **Address:** {_val(c.physical_address)}",
        f"- **Phone:** {_val(c.phone)}",
        f"- **Contact method:** {_val(c.contact_method)}",
        f"- **Serves Maryland:** {c.ships_to_maryland}",
        f"- **Lifecycle state:** {rel.lifecycle_state}",
        f"- **Amazon marketplace status:** {_amazon_status(rel)}",
        f"- **Invoice fields supported:** {', '.join(c.invoice_fields_supported) or '—'}",
        f"- **Can verify / provide LOA:** {c.can_verify_or_provide_loa}",
        f"- **Opening order / MOQ / payment terms:** {_val(c.opening_order)} / {_val(c.recurring_moq)} / {_val(c.payment_terms)}",
        f"- **Catalog/data formats:** {', '.join(c.catalog_data_formats) or '—'}",
        f"- **MAP/territory restrictions:** {_val(c.map_territory_restrictions)}",
        f"- **Risk flags:** {', '.join(c.risk_flags) or 'none noted'}",
        "",
    ]

    lines += ["**Score breakdown**", "", "| Dimension | Raw score | Weight | Weighted points |", "|---|---|---|---|"]
    lines += [f"| {d.name} | {d.raw_score}/100 | {d.weight} | {d.weighted_points} |" for d in breakdown.dimensions]
    lines += [f"| **Total** | | | **{breakdown.raw_weighted_total}** (final: {breakdown.final_score}) |", ""]

    gates = [f"- {g}" for g in rel.assessment.gate_notes] or ["- (none triggered)"]
    lines += ["**Gates triggered**", ""] + gates + [""]

    risks = [f"- {r}" for r in q.risks] or ["- (none noted)"]
    lines += ["**Risks**", ""] + risks + [""]

    missing = [f"- {m}" for m in q.missing_information] or ["- (none noted)"]
    lines += ["**Missing information**", ""] + missing + [""]

    lines += ["**Evidence**", ""] + _evidence_table(rel)

    if rel.manager_decision and rel.outreach_drafts:
        winner = rel.manager_decision.winning_strategy
        lines += [_email_section(rel, winner, "Selected email"), ""]
        lines += [f"**Why this draft won:** {rel.manager_decision.winning_reason}", ""]
        edits = rel.manager_decision.recommended_final_edits
        if edits:
            lines += [f"**Manager's suggested final edits:** {edits}", ""]
    else:
        lines += [
            f"_No outreach drafted - recommendation was {rel.assessment.recommendation}, "
            "which doesn't reach the outreach-drafting stage._", "",
        ]

    lines += [
        f"_Origin: brand batch `{rel.origin_brand_batch_id or '(standalone)'}`, "
        f"brand status `{rel.origin_brand_qualification_status or 'n/a'}`, "
        f"inclusion reason `{rel.inclusion_reason}`_", "",
        "**My decision:** ☐ Approve   ☐ Edit   ☐ Regenerate   ☐ Reject", "",
        "Notes:", "", "&nbsp;", "",
    ]
    return "\n".join(lines)


def _brand_section(rank: int, brand_name: str, rels: list[BrandSupplierRelationship]) -> str:
    ranked = sorted(rels, key=lambda r: r.assessment.score_breakdown.final_score, reverse=True)
    top = ranked[0]
    lines = [
        f"## {rank}. {brand_name}",
        f"_Best option: **{top.assessment.candidate.legal_business_name}** — "
        f"{top.assessment.score_breakdown.final_score}/100, {top.assessment.recommendation} · "
        f"{len(ranked)} distributor(s) found_",
        "",
    ]
    for rel in ranked:
        lines.append(_distributor_section(rel))
    return "\n".join(lines)


def build_markdown(relationships: list[BrandSupplierRelationship], label: str) -> str:
    by_brand: dict[str, list[BrandSupplierRelationship]] = {}
    for rel in relationships:
        by_brand.setdefault(rel.brand_name, []).append(rel)

    # Brands ranked by their best candidate's score, so the most promising
    # brands - the ones worth acting on first - appear at the top.
    brand_order = sorted(by_brand.items(), key=lambda kv: max(r.assessment.score_breakdown.final_score for r in kv[1]), reverse=True)

    header = [
        f"# {_COMPANY_NAME} — Distributor Candidates Review",
        "",
        f"Source: {label} · Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} · "
        f"{len(by_brand)} brand(s), {len(relationships)} distributor candidate(s) total",
        "",
        "| Rank | Brand | Best distributor | Score | Recommendation | # Found |",
        "|---|---|---|---|---|---|",
    ]
    for i, (brand_name, rels) in enumerate(brand_order, start=1):
        top = max(rels, key=lambda r: r.assessment.score_breakdown.final_score)
        header.append(
            f"| {i} | {brand_name} | {top.assessment.candidate.legal_business_name} | "
            f"{top.assessment.score_breakdown.final_score} | {top.assessment.recommendation} | {len(rels)} |"
        )

    sections = [_brand_section(i, brand_name, rels) for i, (brand_name, rels) in enumerate(brand_order, start=1)]
    return f"\n{_PAGE_BREAK}\n\n".join(["\n".join(header)] + sections)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render saved Distributor/Supplier Qualifier results into one printable Markdown file - no API calls."
    )
    parser.add_argument("--batch", help="Distributor/supplier batch id (printed by supplier_batch_runner.py)")
    parser.add_argument("--brand", help="Only relationships for brand names matching this fragment")
    parser.add_argument("--output", help="Output .md path (default: batch_reports/Distributor_candidates_<label>_<timestamp>.md)")
    args = parser.parse_args()

    if args.batch:
        relationships = supplier_persistence.relationships_for_batch(args.batch)
        label = f"batch {args.batch}"
        label_slug = args.batch
    elif args.brand:
        paths = supplier_persistence.find_relationships(brand_fragment=args.brand)
        relationships = [supplier_persistence.load_relationship(p) for p in paths]
        label = f"brand fragment '{args.brand}'"
        label_slug = "".join(c if c.isalnum() or c in "-_" else "_" for c in args.brand)
    else:
        relationships = supplier_persistence.all_relationships()
        label = "all saved distributor relationships"
        label_slug = "all"

    if not relationships:
        print(f"No saved relationships found for {label}.")
        return

    if args.output:
        path = Path(args.output)
    else:
        REPORTS_DIR.mkdir(exist_ok=True)
        path = REPORTS_DIR / f"Distributor_candidates_{label_slug}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    path.write_text(build_markdown(relationships, label), encoding="utf-8")

    print(f"Wrote {len(relationships)} distributor candidate(s) across "
          f"{len({r.brand_name for r in relationships})} brand(s) from {label} to {path}")


if __name__ == "__main__":
    main()

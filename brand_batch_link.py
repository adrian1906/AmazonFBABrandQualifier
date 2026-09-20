"""
Mode A (integrated) support: select brands from an existing Brand Qualifier
batch run and carry their context into the Supplier Qualifier.

No changes were needed to batch_runner.py or persistence.py - the summary
CSV that batch_runner.py already writes (batch_results/summary_<timestamp>.csv)
serves as the batch manifest, and its filename (without extension) is used
as the traceable batch_id stored on every resulting relationship
(see models.BrandSupplierRelationship.origin_brand_batch_id).
"""

import csv
from dataclasses import dataclass
from pathlib import Path

from persistence import RESULTS_DIR, find_results, load_result


@dataclass
class BrandBatchEntry:
    company_name: str
    qualification_status: str | None
    qualification_score: int | None
    research_notes: str  # rendered brand-qualifier context to seed supplier research with


def resolve_batch_summary_path(batch_or_report: str) -> Path:
    direct = Path(batch_or_report)
    if direct.exists() and direct.suffix == ".csv":
        return direct

    fragment = batch_or_report.lower()
    matches = sorted(RESULTS_DIR.glob("summary_*.csv")) if RESULTS_DIR.exists() else []
    matches = [p for p in matches if fragment in p.name.lower()]
    if not matches:
        raise FileNotFoundError(
            f"No brand batch summary matching {batch_or_report!r} found under {RESULTS_DIR}. "
            f"Run batch_runner.py first, or pass the exact path to a summary_*.csv file."
        )
    if len(matches) > 1:
        names = ", ".join(m.name for m in matches)
        raise ValueError(f"Multiple brand batch summaries match {batch_or_report!r}: {names}. Be more specific.")
    return matches[0]


def batch_id_for(summary_path: Path) -> str:
    return summary_path.stem  # e.g. "summary_20260828_140000"


def _render_context_notes(result) -> str:
    p, q, r = result.prospect, result.qualification, result.research
    lines = [
        f"From R&T's Brand Qualifier (status: {q.recommendation}, score: {q.overall_score}/100):",
        f"- Company description: {p.company_description or '(none)'}",
        f"- Product categories: {', '.join(p.product_categories) or '(none)'}",
        f"- Wholesale program (brand-level, previously researched): {p.wholesale_program or '(unknown)'}",
        f"- Amazon policy (brand-level, previously researched): {p.amazon_policy or '(unknown)'}",
        f"- Key reasons: {'; '.join(q.key_reasons) or '(none)'}",
    ]
    if r.verified_facts:
        lines.append("- Verified facts: " + "; ".join(r.verified_facts))
    return "\n".join(lines)


def load_brand_batch(
    batch_or_report: str,
    status: str = "PURSUE",
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> tuple[str, list[BrandBatchEntry]]:
    """
    Selects brands from a saved Brand Qualifier batch: by default, every
    brand whose qualification_status == `status` (PURSUE). `include` adds
    specific brands regardless of status (a manual override); `exclude`
    removes specific brands regardless of status. Never researches
    INVESTIGATE/HOLD/REJECT brands unless explicitly included.

    Returns (batch_id, entries).
    """
    summary_path = resolve_batch_summary_path(batch_or_report)
    batch_id = batch_id_for(summary_path)
    include_lower = {name.strip().lower() for name in (include or [])}
    exclude_lower = {name.strip().lower() for name in (exclude or [])}

    entries: list[BrandBatchEntry] = []
    with summary_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            company_name = row["company_name"]
            lower_name = company_name.strip().lower()
            row_status = row.get("recommendation")

            if lower_name in exclude_lower:
                continue
            if lower_name not in include_lower and row_status != status:
                continue

            matches = find_results(company_name)
            if not matches:
                entries.append(BrandBatchEntry(
                    company_name=company_name,
                    qualification_status=row_status,
                    qualification_score=int(row["qualification_score"]) if row.get("qualification_score") else None,
                    research_notes="(full brand-qualifier record not found on disk - researching from name only)",
                ))
                continue

            result = load_result(matches[-1])  # most recent saved result for this company
            entries.append(BrandBatchEntry(
                company_name=company_name,
                qualification_status=result.qualification.recommendation,
                qualification_score=result.qualification.overall_score,
                research_notes=_render_context_notes(result),
            ))

    return batch_id, entries

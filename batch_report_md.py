"""
Render every saved Brand Qualifier result in batch_results/ into ONE Markdown
file, ranked by qualification score - meant to be opened and printed for
paper review/markup. Makes no API calls (it only reads the saved JSON, like
review_one.py does), so it costs nothing to run or re-run.

If a company was run more than once (a test run plus the full batch, say),
only its newest saved result is included.

Defaults to labeling everything "Brand" (title, column header, print output)
since that's what this script is for - pass --label Distributor when
reviewing a distributor_master_list.csv-sourced batch instead, where the
companies are distributors, not product brands.

Usage:
    python batch_report_md.py                     # all results -> batch_reports/review_<timestamp>.md
    python batch_report_md.py --all-drafts        # also print the two non-winning email drafts
    python batch_report_md.py --since 20260920    # only result files saved on/after that date (YYYYMMDD)
    python batch_report_md.py --names-csv distributor_master_list.csv --label Distributor   # only these companies
    python batch_report_md.py --output my_review.md
"""

import argparse
import re
from datetime import datetime
from pathlib import Path

from config import RT_PROFILE
from persistence import RESULTS_DIR, load_result
from report import _STRATEGY_LABELS
from workflow import WorkflowResult

_COMPANY_NAME = RT_PROFILE["company_name"]

REPORTS_DIR = Path(__file__).parent / "batch_reports"
_PAGE_BREAK = '<div style="page-break-after: always;"></div>'
_TIMESTAMP_RE = re.compile(r"_(\d{8})_(\d{6})$")

_SCORE_LABELS = [
    ("wholesale_relationship_availability", "Wholesale relationship availability"),
    ("product_business_fit", "Product/business fit"),
    ("reseller_program_accessibility", "Reseller program accessibility"),
    ("contact_information_availability", "Contact information availability"),
    ("marketplace_compatibility", "Marketplace compatibility"),
    ("amazon_resale_clarity", "Amazon resale clarity"),
    ("professional_operations_evidence", "Professional operations evidence"),
    ("barriers_or_restrictions", "Barriers or restrictions"),
]


def _timestamp_of(path: Path) -> str:
    """The YYYYMMDD_HHMMSS tail of a saved result's filename ('' if it has none)."""
    m = _TIMESTAMP_RE.search(path.stem)
    return f"{m.group(1)}_{m.group(2)}" if m else ""


def _load_name_filter(names_csv: str) -> set[str]:
    """Normalized company names from a CSV (reuses smartscout_import's header
    detection, which already recognizes a 'Distributor' column) - used to
    scope the review to one specific list instead of everything ever saved
    under batch_results/."""
    from entity_resolution import normalize_company_name
    from smartscout_import import load_prospects_from_csv

    rows = load_prospects_from_csv(names_csv)
    return {normalize_company_name(p.company_name) for p, _notes in rows}


def _latest_results(directory: Path, since: str | None, names: set[str] | None) -> tuple[list[WorkflowResult], int]:
    """Newest saved result per company. Returns (results, number_of_older_duplicates_skipped)."""
    from entity_resolution import normalize_company_name

    latest: dict[str, tuple[str, WorkflowResult]] = {}
    total = 0
    for path in sorted(directory.glob("*.json")):
        stamp = _timestamp_of(path)
        if not stamp or (since and stamp[:8] < since):
            continue
        result = load_result(path)
        if names is not None and normalize_company_name(result.prospect.company_name) not in names:
            continue
        total += 1
        key = re.sub(r"[^a-z0-9]+", "", result.prospect.company_name.lower())
        if key not in latest or stamp > latest[key][0]:
            latest[key] = (stamp, result)
    results = sorted((r for _s, r in latest.values()), key=lambda r: r.qualification.overall_score, reverse=True)
    return results, total - len(results)


def _val(value) -> str:
    return str(value).strip() if value not in (None, "") else "—"


def _blockquote(text: str) -> str:
    return "\n".join(f"> {line}".rstrip() for line in text.strip().splitlines())


def _email_section(result: WorkflowResult, strategy: str, heading: str) -> str:
    draft = result.drafts[strategy]
    score = next((d.score for d in result.manager_decision.draft_scores if d.strategy == strategy), None)
    label = _STRATEGY_LABELS.get(strategy, strategy.title())
    score_text = f" (score {score}/100)" if score is not None else ""
    return f"### {heading} — {label}{score_text}\n\n**Subject:** {draft.subject}\n\n{_blockquote(draft.body)}"


def _company_section(rank: int, result: WorkflowResult, all_drafts: bool) -> str:
    p, q, m = result.prospect, result.qualification, result.manager_decision
    lines = [f"## {rank}. {p.company_name} — {q.overall_score}/100 · {q.recommendation}", ""]

    lines += ["### Research summary", "", _val(p.company_description), ""]

    lines += [
        "### Key facts", "",
        f"- **Website:** {_val(p.website)}",
        f"- **Categories:** {', '.join(p.product_categories) if p.product_categories else '—'}",
        f"- **Wholesale program:** {_val(p.wholesale_program)}",
        f"- **Application URL:** {_val(p.wholesale_application_url)}",
        f"- **Amazon policy:** {_val(p.amazon_policy)}",
        f"- **MAP policy:** {_val(p.map_policy)}",
        f"- **Minimum order quantity:** {_val(p.minimum_order_quantity)}",
        f"- **Opening order:** {_val(p.opening_order)}",
        f"- **Payment terms:** {_val(p.payment_terms)}",
        f"- **Contact:** {_val(p.contact_name)} / {_val(p.contact_role)} / {_val(p.contact_email)}",
        "",
    ]

    lines += ["### Score breakdown", "", "| Factor | Score |", "|---|---|"]
    lines += [f"| {label} | {getattr(q, field)} |" for field, label in _SCORE_LABELS]
    lines.append("")

    lines += ["### Key opportunities", ""] + ([f"- {r}" for r in q.key_reasons] or ["- (none noted)"]) + [""]
    risks = [f"- {r}" for r in q.risks] + [f"- Unknown: {u}" for u in q.missing_information]
    lines += ["### Risks / unknowns", ""] + (risks or ["- (none noted)"]) + [""]
    lines += ["### Recommended next step", "", q.recommended_next_action, ""]

    winner = m.winning_strategy
    lines += [_email_section(result, winner, "Selected email"), ""]
    lines += [f"**Why this draft won:** {m.winning_reason}", ""]
    edits = m.recommended_final_edits
    if edits:
        if isinstance(edits, str):
            lines += [f"**Manager's suggested final edits:** {edits}", ""]
        else:
            lines += ["**Manager's suggested final edits:**", ""] + [f"- {e}" for e in edits] + [""]

    if all_drafts:
        for strategy in result.drafts:
            if strategy != winner:
                lines += [_email_section(result, strategy, "Alternate email"), ""]

    if p.sources:
        lines += ["### Sources", ""] + [f"- {s}" for s in p.sources] + [""]

    lines += [
        "### My decision", "",
        "☐ Approve   ☐ Edit   ☐ Regenerate   ☐ Reject", "",
        "Notes:", "", "&nbsp;", "", "&nbsp;", "",
    ]
    return "\n".join(lines)


def build_markdown(results: list[WorkflowResult], all_drafts: bool, label: str = "Brand") -> str:
    header = [
        f"# {_COMPANY_NAME} — {label} Review",
        "",
        f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} · {len(results)} {label.lower()}(s), ranked by qualification score",
        "",
        f"| Rank | {label} | Score | Recommendation | Winning email style |",
        "|---|---|---|---|---|",
    ]
    for i, r in enumerate(results, start=1):
        style = _STRATEGY_LABELS.get(r.manager_decision.winning_strategy, r.manager_decision.winning_strategy)
        header.append(f"| {i} | {r.prospect.company_name} | {r.qualification.overall_score} | {r.qualification.recommendation} | {style} |")
    sections = [_company_section(i, r, all_drafts) for i, r in enumerate(results, start=1)]
    return f"\n{_PAGE_BREAK}\n\n".join(["\n".join(header)] + sections)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render all saved batch results into one printable Markdown file - no API calls.")
    parser.add_argument("--output", help="Output .md path (default: batch_reports/review_<timestamp>.md)")
    parser.add_argument("--all-drafts", action="store_true", help="Also print the two non-winning email drafts for each company")
    parser.add_argument("--since", help="Only include result files saved on/after this date (YYYYMMDD)")
    parser.add_argument("--names-csv", help="Only include companies listed in this CSV (e.g. distributor_master_list.csv) - "
                                             "use this to scope the review to one specific batch instead of everything ever saved")
    parser.add_argument("--dir", default=str(RESULTS_DIR), help="Folder of saved results (default: batch_results/)")
    parser.add_argument("--label", default="Brand", help="What these companies are, for the title/column header "
                                                           "(default: Brand - pass e.g. 'Distributor' when reviewing distributor_master_list.csv)")
    args = parser.parse_args()

    directory = Path(args.dir)
    if not directory.exists():
        print(f"No results folder at {directory}.")
        return

    names = _load_name_filter(args.names_csv) if args.names_csv else None
    results, skipped = _latest_results(directory, args.since, names)
    if not results:
        print("No saved results found - run batch_runner.py first.")
        return

    if args.output:
        path = Path(args.output)
    else:
        REPORTS_DIR.mkdir(exist_ok=True)
        path = REPORTS_DIR / f"review_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    path.write_text(build_markdown(results, args.all_drafts, args.label), encoding="utf-8")

    print(f"Wrote {len(results)} {args.label.lower()}(s) to {path}")
    if skipped:
        print(f"({skipped} older duplicate result file(s) skipped - only the newest per company is included.)")


if __name__ == "__main__":
    main()

"""
Builds exact, copy-pasteable CLI commands for the long-running / expensive
operations the GUI deliberately does not run itself (batch brand or
supplier research). Pure string-building logic - no Streamlit import, no
subprocess execution - so it's easy to test and impossible to accidentally
trigger a real run from here.

Each builder returns (command, explanation) so the GUI can show both the
command and a plain-language note about what it does and roughly what it
costs - the "guidance for the syntax you'll forget" the tool is for.
"""

import shlex


def _quote(value: str) -> str:
    return shlex.quote(value)


def brand_batch_command(csv_path: str, limit: int | None, concurrency: int, no_web_search: bool) -> tuple[str, str]:
    parts = ["python", "batch_runner.py", "--csv", _quote(csv_path)]
    if limit:
        parts += ["--limit", str(limit)]
    if concurrency and concurrency != 5:
        parts += ["--concurrency", str(concurrency)]
    if no_web_search:
        parts.append("--no-web-search")
    command = " ".join(parts)

    n = f"the first {limit} rows of " if limit else "every row in "
    explanation = (
        f"Runs the Brand Qualifier over {n}{csv_path}, {concurrency} at a time"
        f"{' with WebSearchTool disabled (offline/cache-only)' if no_web_search else ''}. "
        "Each brand costs several agent calls (research, qualification, 3 outreach drafts, manager) - "
        "for real numbers, run with --limit 5 first."
    )
    return command, explanation


def supplier_batch_command(
    mode: str,
    from_brand_batch: str = "",
    status: str = "PURSUE",
    include: str = "",
    exclude: str = "",
    brands: str = "",
    input_path: str = "",
    concurrency: int = 5,
    limit: int | None = None,
    dry_run: bool = False,
    no_web_search: bool = False,
) -> tuple[str, str]:
    parts = ["python", "supplier_batch_runner.py"]

    if mode == "integrated":
        parts += ["--from-brand-batch", _quote(from_brand_batch)]
        if status and status != "PURSUE":
            parts += ["--status", _quote(status)]
        if include.strip():
            parts += ["--include", _quote(include.strip())]
        if exclude.strip():
            parts += ["--exclude", _quote(exclude.strip())]
        source_desc = f"brands marked {status or 'PURSUE'} in {from_brand_batch or '<brand batch summary>'}"
        if include.strip():
            source_desc += f", plus manually included: {include}"
        if exclude.strip():
            source_desc += f" (excluding: {exclude})"
    elif mode == "standalone_list":
        parts += ["--brands", _quote(brands)]
        source_desc = f"the brand list: {brands}"
    else:  # standalone_file
        parts += ["--input", _quote(input_path)]
        source_desc = f"brands listed in {input_path or '<file>'}"

    if concurrency and concurrency != 5:
        parts += ["--concurrency", str(concurrency)]
    if limit:
        parts += ["--limit", str(limit)]
    if dry_run:
        parts.append("--dry-run")
    if no_web_search:
        parts.append("--no-web-search")

    command = " ".join(parts)
    explanation = (
        f"Runs the Supplier Qualifier for {source_desc}, {concurrency} brand(s) at a time"
        f"{', limited to the first ' + str(limit) + ' selected' if limit else ''}"
        f"{' - cache/offline only, no paid calls' if dry_run else ''}"
        f"{' - WebSearchTool disabled' if no_web_search and not dry_run else ''}. "
        "Prints a batch id at the end - save it for --resume or the report command below."
    )
    return command, explanation


def supplier_resume_command(batch_id: str, concurrency: int = 5) -> tuple[str, str]:
    parts = ["python", "supplier_batch_runner.py", "--resume", _quote(batch_id)]
    if concurrency and concurrency != 5:
        parts += ["--concurrency", str(concurrency)]
    command = " ".join(parts)
    explanation = (
        f"Re-researches only the brands in batch {batch_id!r} that previously failed, "
        "or whose cached research has gone stale - already-successful, still-fresh brands are skipped."
    )
    return command, explanation


def supplier_report_command(batch_id: str = "", brand_fragment: str = "", save: bool = False) -> tuple[str, str]:
    parts = ["python", "supplier_report_cli.py"]
    if batch_id:
        parts += ["--batch", _quote(batch_id)]
        target = f"supplier batch {batch_id}"
    else:
        parts += ["--brand", _quote(brand_fragment)]
        target = f"brands matching {brand_fragment!r}"
    if save:
        parts.append("--save")
    command = " ".join(parts)
    explanation = (
        f"Renders the ranked report / brand-supplier matrix / missing-info / contact-now / "
        f"do-not-pursue sections for {target}, from already-persisted data - no paid calls."
        + (" Also writes a copy under supplier_reports/." if save else "")
    )
    return command, explanation


def supplier_review_command(fragment: str) -> tuple[str, str]:
    command = f"python supplier_review_one.py {_quote(fragment)}"
    explanation = (
        f"Opens the interactive APPROVE / EDIT / REGENERATE / REJECT gate for the saved supplier "
        f"relationship matching {fragment!r} - equivalent to using the Review Queue tab in this app, "
        "just from the terminal."
    )
    return command, explanation


def brand_review_command(fragment: str) -> tuple[str, str]:
    command = f"python review_one.py {_quote(fragment)}"
    explanation = (
        f"Opens the interactive APPROVE / EDIT / REGENERATE / REJECT gate for the saved brand "
        f"result matching {fragment!r} - equivalent to using the Review Queue tab in this app."
    )
    return command, explanation

"""
CLI entry point for the Supplier Qualifier - both modes:

Integrated (Mode A) - default to PURSUE brands from an existing Brand
Qualifier batch, with optional manual include/exclude:
    python supplier_batch_runner.py --from-brand-batch batch_results/summary_20260828_140000.csv
    python supplier_batch_runner.py --from-brand-batch summary_20260828 --status PURSUE
    python supplier_batch_runner.py --from-brand-batch summary_20260828 --include "Brand A,Brand B" --exclude "Brand C"

Standalone (Mode B) - no Brand Qualifier record required:
    python supplier_batch_runner.py --brands "Brand A,Brand B"
    python supplier_batch_runner.py --input brands.csv
    python supplier_batch_runner.py --input brands.json
    python supplier_batch_runner.py --input brands.txt

Resume - re-research only brands that failed or whose cached research has
gone stale in a previous supplier batch:
    python supplier_batch_runner.py --resume supbatch_20260920_101500

Every run writes a batch manifest (see supplier_persistence.save_batch_manifest)
so it can be resumed, and every resulting relationship carries a traceable
link back to its brand batch (Mode A) or is marked standalone (Mode B) -
see models.BrandSupplierRelationship.

This does not show the interactive approval prompt per relationship - that
doesn't scale. Once you've picked a promising brand/supplier from the
report, review and approve it with:
    python supplier_review_one.py "company name fragment"
"""

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime

from dotenv import load_dotenv

import brand_batch_link
import input_loader
import supplier_persistence
from config import SUPPLIER_DEFAULT_CONCURRENCY
from supplier_workflow import run_supplier_research_for_brand

load_dotenv(override=True)


@dataclass
class BrandInput:
    name: str
    notes: str = ""
    qualification_status: str | None = None
    inclusion_reason: str = "standalone"


def _new_batch_id() -> str:
    return f"supbatch_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def _resolve_entries_from_source(source: dict, mode: str) -> tuple[str | None, list[BrandInput]]:
    """Rebuild (origin_brand_batch_id, entries) from a manifest's stored
    `source` dict - used both for a fresh run and for --resume, so resuming
    re-derives the same brand list rather than needing it duplicated in storage."""
    if mode == "integrated":
        origin_brand_batch_id, batch_entries = brand_batch_link.load_brand_batch(
            source["from_brand_batch"], status=source["status"], include=source["include"], exclude=source["exclude"]
        )
        include_lower = {n.lower() for n in source["include"]}
        entries = [
            BrandInput(
                e.company_name, e.research_notes, e.qualification_status,
                "manual_include" if e.company_name.lower() in include_lower else "default_pursue",
            )
            for e in batch_entries
        ]
        return origin_brand_batch_id, entries

    if "brands" in source:
        return None, [BrandInput(name, "", None, "standalone") for name in source["brands"]]

    names = input_loader.brands_from_file(source["input"])
    deduped, flagged = input_loader.dedupe_and_flag_aliases(names)
    for a, b in flagged:
        print(f"NOTICE: '{a}' and '{b}' look like possible aliases - kept as separate brands, please confirm.")
    return None, [BrandInput(name, "", None, "standalone") for name in deduped]


def _build_source_from_args(args: argparse.Namespace) -> tuple[str, dict]:
    provided = [bool(args.from_brand_batch), bool(args.brands), bool(args.input)]
    if sum(provided) != 1:
        raise SystemExit("Provide exactly one of --from-brand-batch, --brands, or --input.")

    if args.from_brand_batch:
        include = [s.strip() for s in args.include.split(",") if s.strip()]
        exclude = [s.strip() for s in args.exclude.split(",") if s.strip()]
        return "integrated", {
            "from_brand_batch": args.from_brand_batch, "status": args.status,
            "include": include, "exclude": exclude,
        }

    if args.brands:
        names = input_loader.brands_from_cli_list(args.brands)
        deduped, flagged = input_loader.dedupe_and_flag_aliases(names)
        for a, b in flagged:
            print(f"NOTICE: '{a}' and '{b}' look like possible aliases - kept as separate brands, please confirm.")
        return "standalone", {"brands": deduped}

    return "standalone", {"input": args.input}


async def _run_one(semaphore, supplier_batch_id, entry: BrandInput, origin_brand_batch_id, allow_web_search, use_cache, manifest, index, total):
    async with semaphore:
        print(f"[{index}/{total}] Researching suppliers for: {entry.name} ...")
        try:
            result = await run_supplier_research_for_brand(
                entry.name,
                context_notes=entry.notes,
                allow_web_search=allow_web_search,
                supplier_batch_id=supplier_batch_id,
                origin_brand_batch_id=origin_brand_batch_id,
                origin_brand_qualification_status=entry.qualification_status,
                inclusion_reason=entry.inclusion_reason,
                use_cache=use_cache,
            )
            tag = "cached" if result.cache_hit else ("escalated" if result.escalated_candidates else "fresh")
            print(
                f"[{index}/{total}] Done: {entry.name} -> {len(result.relationships)} candidate(s) "
                f"[{tag}]"
                + (f", escalated: {', '.join(result.escalated_candidates)}" if result.escalated_candidates else "")
            )
            manifest["brands"][entry.name] = {
                "status": "done",
                "relationships": len(result.relationships),
                "cache_hit": result.cache_hit,
                "escalated": result.escalated_candidates,
                "run_id": result.run_id,
            }
            return result
        except Exception as exc:  # one bad brand must not take down the whole batch
            print(f"[{index}/{total}] FAILED: {entry.name}: {exc}")
            manifest["brands"][entry.name] = {"status": "failed", "error": str(exc)}
            return None


async def run_supplier_batch(
    mode: str,
    source: dict,
    batch_id: str | None = None,
    concurrency: int = SUPPLIER_DEFAULT_CONCURRENCY,
    allow_web_search: bool = True,
    dry_run: bool = False,
    resume_entries: list[BrandInput] | None = None,
    origin_brand_batch_id_override: str | None = None,
    limit: int | None = None,
):
    if resume_entries is not None:
        entries = resume_entries
        origin_brand_batch_id = origin_brand_batch_id_override
    else:
        origin_brand_batch_id, entries = _resolve_entries_from_source(source, mode)

    if limit is not None:
        entries = entries[:limit]

    if not entries:
        print("No brands selected - nothing to do.")
        return batch_id, []

    batch_id = batch_id or _new_batch_id()
    manifest = {
        "batch_id": batch_id, "mode": mode, "source": source,
        "origin_brand_batch_id": origin_brand_batch_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "concurrency": concurrency, "allow_web_search": allow_web_search and not dry_run,
        "brands": {},
    }

    print(
        f"Supplier batch {batch_id} ({mode}): {len(entries)} brand(s). "
        f"Concurrency: {concurrency}. Web search: {'off (dry-run/cache-only)' if dry_run else ('on' if allow_web_search else 'off')}.\n"
    )

    semaphore = asyncio.Semaphore(concurrency)
    tasks = [
        _run_one(
            semaphore, batch_id, entry, origin_brand_batch_id,
            allow_web_search=(allow_web_search and not dry_run),
            use_cache=True,
            manifest=manifest, index=i, total=len(entries),
        )
        for i, entry in enumerate(entries, start=1)
    ]
    results = await asyncio.gather(*tasks)
    manifest["updated_at"] = datetime.now().isoformat(timespec="seconds")
    supplier_persistence.save_batch_manifest(batch_id, manifest)

    succeeded = [r for r in results if r is not None]
    failed = [name for name, info in manifest["brands"].items() if info["status"] == "failed"]
    print(f"\nBatch complete: {len(succeeded)} succeeded, {len(failed)} failed.")
    if failed:
        print("Failed brands:", ", ".join(failed))
    print(f"\nBatch id: {batch_id}")
    print(f"Next steps:\n  python supplier_report_cli.py --batch {batch_id}")
    print(f"  python supplier_review_one.py \"<company name or partial match>\"")
    return batch_id, succeeded


def _entries_needing_resume(manifest: dict) -> list[BrandInput]:
    entries: list[BrandInput] = []
    source = manifest["source"]
    _origin_brand_batch_id, all_entries = _resolve_entries_from_source(source, manifest["mode"])
    for entry in all_entries:
        info = manifest["brands"].get(entry.name)
        if info is None or info.get("status") == "failed" or not supplier_persistence.is_cache_fresh(entry.name):
            entries.append(entry)
    return entries


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the R&T Supplier Qualifier (integrated or standalone mode).")
    parser.add_argument("--from-brand-batch", help="Path or fragment matching a Brand Qualifier batch_results/summary_*.csv")
    parser.add_argument("--status", default="PURSUE", help="Brand qualification status to include by default (default: PURSUE)")
    parser.add_argument("--include", default="", help="Comma-separated brand names to include regardless of status")
    parser.add_argument("--exclude", default="", help="Comma-separated brand names to exclude regardless of status")
    parser.add_argument("--brands", help="Comma-separated brand names (standalone mode)")
    parser.add_argument("--input", help="Path to a .csv, .json, or .txt file of brand names (standalone mode)")
    parser.add_argument("--resume", help="Re-research only failed/stale brands from an existing supplier batch id")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N selected brands (useful for a cheap test run)")
    parser.add_argument("--concurrency", type=int, default=SUPPLIER_DEFAULT_CONCURRENCY)
    parser.add_argument("--no-web-search", action="store_true", help="Disable WebSearchTool; rely only on cache/manual notes")
    parser.add_argument("--dry-run", action="store_true", help="Use cached research only - no paid research calls")
    return parser.parse_args()


async def _main():
    args = _parse_args()

    if args.resume:
        manifest = supplier_persistence.load_batch_manifest(args.resume)
        if manifest is None:
            raise SystemExit(f"No supplier batch manifest found for {args.resume!r}.")
        entries = _entries_needing_resume(manifest)
        print(f"Resuming {args.resume}: {len(entries)} brand(s) need (re)research.")
        await run_supplier_batch(
            manifest["mode"], manifest["source"], batch_id=args.resume,
            concurrency=args.concurrency, allow_web_search=not args.no_web_search,
            dry_run=args.dry_run, resume_entries=entries, limit=args.limit,
            origin_brand_batch_id_override=manifest.get("origin_brand_batch_id"),
        )
        return

    mode, source = _build_source_from_args(args)
    await run_supplier_batch(
        mode, source, concurrency=args.concurrency, limit=args.limit,
        allow_web_search=not args.no_web_search, dry_run=args.dry_run,
    )


if __name__ == "__main__":
    asyncio.run(_main())

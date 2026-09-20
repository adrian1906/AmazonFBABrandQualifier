"""
Sync wrapper functions the GUI (app.py) calls - no Streamlit import here on
purpose, so these stay plain, testable Python. Every function here either:

  (a) reads already-persisted data (free, instant), or
  (b) performs one bounded, single-brand/single-relationship agent action
      (a few API calls, seconds to ~1-2 minutes) - the kind of thing safe
      to put behind a GUI button for a non-technical user, or
  (c) writes a local outbox file on an explicit Approve click - never sends
      anything, exactly like the CLI approval gates.

Multi-brand BATCH runs are deliberately NOT wrapped here - see
gui_command_builder.py. Those are long-running and cost real API money, so
the GUI only ever generates the exact CLI command for a human to run
themselves in a terminal, never triggers one directly.
"""

import asyncio

import approval
import persistence
import supplier_approval
import supplier_persistence
from models import BrandSupplierRelationship, Prospect
from supplier_scoring import LifecycleGateError, advance_lifecycle_state
from supplier_workflow import SupplierWorkflowResult, regenerate_supplier_outreach, run_supplier_research_for_brand
from workflow import WorkflowResult, regenerate_outreach, run_brand_acquisition


def run_async(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Brand Qualifier actions
# ---------------------------------------------------------------------------

def run_single_brand_lookup(company_name: str, website: str, manual_notes: str, allow_web_search: bool) -> WorkflowResult:
    prospect = Prospect(company_name=company_name.strip(), website=website.strip() or None)
    result = run_async(run_brand_acquisition(prospect, manual_research_notes=manual_notes, allow_web_search=allow_web_search))
    persistence.save_result(result)
    return result


def approve_brand_outreach(result: WorkflowResult):
    return approval.save_approved_outreach(result)


def regenerate_brand_outreach(result: WorkflowResult) -> WorkflowResult:
    result = run_async(regenerate_outreach(result))
    persistence.save_result(result)
    return result


def list_brand_results() -> list:
    """All saved Brand Qualifier results, newest first."""
    if not persistence.RESULTS_DIR.exists():
        return []
    paths = sorted(persistence.RESULTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [persistence.load_result(p) for p in paths]


def list_brand_batch_summaries() -> list:
    if not persistence.RESULTS_DIR.exists():
        return []
    return sorted(persistence.RESULTS_DIR.glob("summary_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)


# ---------------------------------------------------------------------------
# Supplier Qualifier actions
# ---------------------------------------------------------------------------

def run_single_supplier_lookup(brand_name: str, context_notes: str, allow_web_search: bool) -> SupplierWorkflowResult:
    return run_async(
        run_supplier_research_for_brand(brand_name.strip(), context_notes=context_notes, allow_web_search=allow_web_search)
    )


def approve_supplier_relationship(rel: BrandSupplierRelationship):
    path = supplier_approval.save_approved_outreach(rel)
    try:
        rel.lifecycle_state = advance_lifecycle_state(rel.assessment, "CONTACT_APPROVED")
    except LifecycleGateError:
        pass  # state stays as-is; gate failure here would be surprising for CONTACT_APPROVED specifically
    supplier_persistence.save_relationship(rel)
    return path


def regenerate_supplier_relationship(rel: BrandSupplierRelationship) -> BrandSupplierRelationship:
    drafts, manager_decision = run_async(regenerate_supplier_outreach(rel.brand_name, rel.assessment))
    rel.outreach_drafts = drafts
    rel.manager_decision = manager_decision
    supplier_persistence.save_relationship(rel)
    return rel


def list_supplier_relationships() -> list[BrandSupplierRelationship]:
    """All persisted brand<->supplier relationships, newest first."""
    rels = supplier_persistence.all_relationships()
    return sorted(rels, key=lambda r: r.last_researched_at, reverse=True)


def list_pending_supplier_relationships() -> list[BrandSupplierRelationship]:
    """Relationships that have a drafted outreach and haven't been contacted yet."""
    return [
        r for r in list_supplier_relationships()
        if r.outreach_drafts and r.lifecycle_state in ("DISCOVERED", "RESEARCHED")
    ]


def list_supplier_batch_ids() -> list[str]:
    if not supplier_persistence.BATCHES_DIR.exists():
        return []
    paths = sorted(supplier_persistence.BATCHES_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [p.stem for p in paths]

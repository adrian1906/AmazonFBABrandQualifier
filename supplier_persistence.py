"""
On-disk storage for the Supplier Qualifier: the entity index, canonical
supplier records, brand<->supplier relationship records, historical research
runs, a lightweight research cache, and batch manifests for --resume and for
tracing a supplier run back to its originating brand batch.

Mirrors persistence.py's plain-JSON-file approach (no database dependency),
extended with stable ids (see entity_resolution.py) so repeat research on
the same brand/supplier updates existing records instead of creating
duplicates, and historical runs are preserved rather than overwritten.

Layout, under supplier_data/ (see config.SUPPLIER_DATA_DIR_NAME):
    entity_index.json              - EntityIndex (match keys -> supplier_id)
    suppliers/<supplier_id>.json   - latest known SupplierCandidate for that entity
    relationships/<brand>__<supplier_id>.json  - BrandSupplierRelationship
    runs/<run_id>.json             - one historical SupplierResearchFindings run
    batches/<batch_id>.json        - batch manifest (input, brands, originating brand batch, timestamps)
    cache/<brand_slug>.json        - most recent raw research findings for a brand, for --resume / cache reuse
"""

import json
import re
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

from config import SUPPLIER_DATA_DIR_NAME, SUPPLIER_STALE_DATA_DAYS
from entity_resolution import EntityIndex
from models import SupplierCandidate, SupplierResearchFindings, BrandSupplierRelationship

SUPPLIER_DATA_DIR = Path(__file__).parent / SUPPLIER_DATA_DIR_NAME
SUPPLIERS_DIR = SUPPLIER_DATA_DIR / "suppliers"
RELATIONSHIPS_DIR = SUPPLIER_DATA_DIR / "relationships"
RUNS_DIR = SUPPLIER_DATA_DIR / "runs"
BATCHES_DIR = SUPPLIER_DATA_DIR / "batches"
CACHE_DIR = SUPPLIER_DATA_DIR / "cache"
ENTITY_INDEX_PATH = SUPPLIER_DATA_DIR / "entity_index.json"


def _slugify(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_") or "item"


def _ensure_dirs() -> None:
    for d in (SUPPLIERS_DIR, RELATIONSHIPS_DIR, RUNS_DIR, BATCHES_DIR, CACHE_DIR):
        d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Entity index
# ---------------------------------------------------------------------------

def load_entity_index() -> EntityIndex:
    if not ENTITY_INDEX_PATH.exists():
        return EntityIndex()
    return EntityIndex.from_dict(json.loads(ENTITY_INDEX_PATH.read_text(encoding="utf-8")))


def save_entity_index(index: EntityIndex) -> None:
    _ensure_dirs()
    ENTITY_INDEX_PATH.write_text(json.dumps(index.to_dict(), indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Canonical supplier records (one per stable supplier_id)
# ---------------------------------------------------------------------------

def save_supplier_candidate(supplier_id: str, candidate: SupplierCandidate) -> Path:
    """Overwrite the canonical record for this supplier_id with the latest
    known profile. Historical detail is preserved separately in runs/."""
    _ensure_dirs()
    path = SUPPLIERS_DIR / f"{supplier_id}.json"
    path.write_text(candidate.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_supplier_candidate(supplier_id: str) -> SupplierCandidate | None:
    path = SUPPLIERS_DIR / f"{supplier_id}.json"
    if not path.exists():
        return None
    return SupplierCandidate(**json.loads(path.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# Brand <-> Supplier relationships
# ---------------------------------------------------------------------------

def relationship_path(brand_name: str, supplier_id: str) -> Path:
    return RELATIONSHIPS_DIR / f"{_slugify(brand_name)}__{supplier_id}.json"


def save_relationship(relationship: BrandSupplierRelationship) -> Path:
    _ensure_dirs()
    path = relationship_path(relationship.brand_name, relationship.supplier_id)
    path.write_text(relationship.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_relationship(path: Path) -> BrandSupplierRelationship:
    return BrandSupplierRelationship(**json.loads(Path(path).read_text(encoding="utf-8")))


def find_relationships(brand_fragment: str | None = None, supplier_id: str | None = None) -> list[Path]:
    if not RELATIONSHIPS_DIR.exists():
        return []
    paths = sorted(RELATIONSHIPS_DIR.glob("*.json"))
    if brand_fragment:
        fragment = _slugify(brand_fragment).lower()
        paths = [p for p in paths if fragment in p.name.lower()]
    if supplier_id:
        paths = [p for p in paths if p.name.endswith(f"__{supplier_id}.json")]
    return paths


def all_relationships() -> list[BrandSupplierRelationship]:
    return [load_relationship(p) for p in find_relationships()]


def relationships_for_batch(batch_id: str) -> list[BrandSupplierRelationship]:
    """All relationships touched by a research run belonging to this
    *supplier* batch_id (see runs/<run_id>.json's own batch_id field) -
    not to be confused with a relationship's origin_brand_batch_id, which
    traces back to the Brand Qualifier batch instead (Mode A only)."""
    if not RUNS_DIR.exists():
        return []
    run_ids_in_batch: set[str] = set()
    for path in RUNS_DIR.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("batch_id") == batch_id:
            run_ids_in_batch.add(payload["run_id"])
    if not run_ids_in_batch:
        return []
    return [r for r in all_relationships() if run_ids_in_batch & set(r.research_run_ids)]


# ---------------------------------------------------------------------------
# Historical research runs (never overwritten - one file per run)
# ---------------------------------------------------------------------------

def new_run_id() -> str:
    return f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


def save_research_run(run_id: str, brand_name: str, findings: SupplierResearchFindings, batch_id: str | None = None) -> Path:
    _ensure_dirs()
    path = RUNS_DIR / f"{run_id}.json"
    payload = {
        "run_id": run_id,
        "brand_name": brand_name,
        "batch_id": batch_id,
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
        "findings": findings.model_dump(),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Batch manifests - Mode A traceability + --resume
# ---------------------------------------------------------------------------

def save_batch_manifest(batch_id: str, meta: dict) -> Path:
    _ensure_dirs()
    path = BATCHES_DIR / f"{batch_id}.json"
    path.write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    return path


def load_batch_manifest(batch_id: str) -> dict | None:
    path = BATCHES_DIR / f"{batch_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Research cache - avoid paying to repeat identical searches. Keyed on brand
# name only (case-insensitive); respects config.SUPPLIER_STALE_DATA_DAYS.
# ---------------------------------------------------------------------------

def cache_get(brand_name: str) -> SupplierResearchFindings | None:
    path = CACHE_DIR / f"{_slugify(brand_name).lower()}.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    cached_at = datetime.fromisoformat(payload["cached_at"])
    if datetime.now() - cached_at > timedelta(days=SUPPLIER_STALE_DATA_DAYS):
        return None
    return SupplierResearchFindings(**payload["findings"])


def cache_set(brand_name: str, findings: SupplierResearchFindings) -> Path:
    _ensure_dirs()
    path = CACHE_DIR / f"{_slugify(brand_name).lower()}.json"
    payload = {"cached_at": datetime.now().isoformat(timespec="seconds"), "findings": findings.model_dump()}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def is_cache_fresh(brand_name: str) -> bool:
    return cache_get(brand_name) is not None

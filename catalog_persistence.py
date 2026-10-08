"""
Save/load CatalogScanResult and CatalogScanRun records to/from JSON on
disk - mirrors persistence.py (Brand Qualifier) and supplier_persistence.py
(Distributor Qualifier)'s pattern, so a batch scan can be resumed/reviewed
later without re-running anything, and so later prices don't silently
rewrite a prior decision (spec section 7: "Save run snapshots").
"""

import json
from datetime import datetime
from pathlib import Path

from catalog_models import CatalogScanResult, CatalogScanRun

CATALOG_DATA_DIR = Path(__file__).parent / "catalog_data"
RUNS_DIR = CATALOG_DATA_DIR / "runs"
MAPPING_PROFILES_DIR = CATALOG_DATA_DIR / "mapping_profiles"


def _slugify(name: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_") or "catalog"


def save_run(run: CatalogScanRun, directory: Path | None = None) -> Path:
    directory = directory or RUNS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{run.run_id}.json"
    path.write_text(run.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_run(path: Path) -> CatalogScanRun:
    return CatalogScanRun.model_validate_json(Path(path).read_text(encoding="utf-8"))


def find_runs(directory: Path | None = None) -> list[Path]:
    directory = directory or RUNS_DIR
    if not directory.exists():
        return []
    return sorted(directory.glob("*.json"))


def new_run_id(supplier_name: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"catscan_{_slugify(supplier_name)}_{timestamp}"


def save_mapping_profile(mapping, directory: Path | None = None) -> Path:
    """Persist a ColumnMapping keyed by supplier name, so a repeat catalog
    from the same source doesn't need remapping (spec section 1)."""
    directory = directory or MAPPING_PROFILES_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{_slugify(mapping.supplier_name)}.json"
    path.write_text(mapping.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_mapping_profile(supplier_name: str, directory: Path | None = None):
    from catalog_models import ColumnMapping

    directory = directory or MAPPING_PROFILES_DIR
    path = directory / f"{_slugify(supplier_name)}.json"
    if not path.exists():
        return None
    return ColumnMapping.model_validate_json(path.read_text(encoding="utf-8"))

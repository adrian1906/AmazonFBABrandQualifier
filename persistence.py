"""
Save/load a WorkflowResult to/from a JSON file on disk.

This lets a batch run (batch_runner.py) persist each brand's full result
without holding everything in memory, and lets review_one.py reload a
specific result later to run the interactive approval gate on it - without
re-running any agents (and therefore without spending more API budget)
unless the user explicitly chooses REGENERATE.
"""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from entity_resolution import normalize_company_name
from models import Prospect, ResearchFindings, QualificationResult, OutreachDraft, ManagerDecision
from workflow import WorkflowResult

RESULTS_DIR = Path(__file__).parent / "batch_results"

# Matches the "<slug>_<YYYYMMDD>_<HHMMSS>" filename save_result() writes -
# same convention batch_report_md.py's _timestamp_of() already parses.
_TIMESTAMP_RE = re.compile(r"_(\d{8})_(\d{6})$")


def _slug_core(company_name: str) -> str:
    """The slug without the "prospect" fallback below - an empty result
    means the name has no ASCII-representable characters at all (e.g. a
    name written only in Japanese/Chinese/etc. script)."""
    return re.sub(r"[^A-Za-z0-9_-]+", "_", company_name).strip("_")


def _slugify(company_name: str) -> str:
    return _slug_core(company_name) or "prospect"


def has_usable_name(company_name: str) -> bool:
    """False when company_name has no ASCII-representable characters at
    all, so _slugify() would silently collapse it to the generic
    "prospect" stem - every such company would be indistinguishable from
    every other one in a file listing. batch_runner.py uses this to skip
    a row BEFORE spending any API money on it, rather than research it and
    then have nowhere distinguishable to save the result."""
    return bool(_slug_core(company_name))


def result_to_dict(result: WorkflowResult) -> dict:
    return {
        "prospect": result.prospect.model_dump(),
        "research": result.research.model_dump(),
        "qualification": result.qualification.model_dump(),
        "drafts": {strategy: draft.model_dump() for strategy, draft in result.drafts.items()},
        "manager_decision": result.manager_decision.model_dump(),
    }


def result_from_dict(data: dict) -> WorkflowResult:
    return WorkflowResult(
        prospect=Prospect(**data["prospect"]),
        research=ResearchFindings(**data["research"]),
        qualification=QualificationResult(**data["qualification"]),
        drafts={strategy: OutreachDraft(**d) for strategy, d in data["drafts"].items()},
        manager_decision=ManagerDecision(**data["manager_decision"]),
    )


def save_result(result: WorkflowResult, directory: Path | None = None) -> Path:
    """Save one WorkflowResult as a JSON file, named after the company. Returns the file path."""
    directory = directory or RESULTS_DIR
    directory.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = directory / f"{_slugify(result.prospect.company_name)}_{timestamp}.json"
    path.write_text(json.dumps(result_to_dict(result), indent=2), encoding="utf-8")
    return path


def load_result(path: Path) -> WorkflowResult:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return result_from_dict(data)


def _timestamp_of(path: Path) -> datetime | None:
    m = _TIMESTAMP_RE.search(path.stem)
    if not m:
        return None
    return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")


def scored_company_names(directory: Path | None = None, max_age_days: float | None = None) -> set[str]:
    """Normalized names (entity_resolution.normalize_company_name, the same
    normalization used throughout this project) of every company whose
    MOST RECENT saved result is fresh enough to count as "already scored".
    Used by batch_runner.py to skip brands that would otherwise be silently
    re-scored - and re-paid for - for no reason, e.g. after merging a new
    SmartScout export with an old one.

    max_age_days: if given, a company whose newest saved result is older
    than this is treated as NOT scored (needs a refresh) - mirrors
    config.SUPPLIER_STALE_DATA_DAYS's reasoning on the Stage 2 side:
    SmartScout's own numbers (revenue, seller count, etc.) drift over
    time, so "already scored" shouldn't mean "scored once, ever,
    indefinitely." A company scored more than once keeps its newest
    timestamp - one fresh result is enough to count as scored even if an
    older one for the same company would, on its own, be stale.
    If None (the default), any existing result counts, regardless of age.

    Reads just the company_name field from each file directly, rather than
    the full load_result() (which also reconstructs qualification/drafts),
    since that's the only thing needed here and this may run over hundreds
    of files.
    """
    directory = directory or RESULTS_DIR
    if not directory.exists():
        return set()

    newest_by_name: dict[str, datetime] = {}
    for path in directory.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            name = data["prospect"]["company_name"]
        except (json.JSONDecodeError, KeyError, TypeError):
            continue  # not a WorkflowResult file (or unexpectedly shaped) - skip rather than fail the batch
        if not name:
            continue
        key = normalize_company_name(name)
        # A file whose name doesn't match the expected timestamp pattern
        # (unexpected/hand-placed file) is treated as always-fresh (datetime.max)
        # rather than silently excluded - erring toward "don't re-pay for it"
        # since its absence of a parseable date isn't evidence it's stale.
        saved_at = _timestamp_of(path) or datetime.max
        if key not in newest_by_name or saved_at > newest_by_name[key]:
            newest_by_name[key] = saved_at

    if max_age_days is None:
        return set(newest_by_name)

    cutoff = datetime.now() - timedelta(days=max_age_days)
    return {name for name, saved_at in newest_by_name.items() if saved_at >= cutoff}


def find_results(name_fragment: str, directory: Path | None = None) -> list[Path]:
    """Find saved result files whose filename contains name_fragment (case-insensitive).

    Matches against the slugified form of the fragment, not the raw text -
    saved filenames replace spaces/punctuation with underscores (see
    _slugify above), so a raw multi-word fragment like "Northwind Outdoor"
    would otherwise never match "Northwind_Outdoor_..." at all.
    """
    directory = directory or RESULTS_DIR
    if not directory.exists():
        return []
    fragment = _slugify(name_fragment).lower()
    return sorted(p for p in directory.glob("*.json") if fragment in p.name.lower())

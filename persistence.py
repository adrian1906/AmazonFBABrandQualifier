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
from datetime import datetime
from pathlib import Path

from models import Prospect, ResearchFindings, QualificationResult, OutreachDraft, ManagerDecision
from workflow import WorkflowResult

RESULTS_DIR = Path(__file__).parent / "batch_results"


def _slugify(company_name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", company_name).strip("_") or "prospect"


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


def save_result(result: WorkflowResult, directory: Path = RESULTS_DIR) -> Path:
    """Save one WorkflowResult as a JSON file, named after the company. Returns the file path."""
    directory.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = directory / f"{_slugify(result.prospect.company_name)}_{timestamp}.json"
    path.write_text(json.dumps(result_to_dict(result), indent=2), encoding="utf-8")
    return path


def load_result(path: Path) -> WorkflowResult:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return result_from_dict(data)


def find_results(name_fragment: str, directory: Path = RESULTS_DIR) -> list[Path]:
    """Find saved result files whose filename contains name_fragment (case-insensitive).

    Matches against the slugified form of the fragment, not the raw text -
    saved filenames replace spaces/punctuation with underscores (see
    _slugify above), so a raw multi-word fragment like "Northwind Outdoor"
    would otherwise never match "Northwind_Outdoor_..." at all.
    """
    if not directory.exists():
        return []
    fragment = _slugify(name_fragment).lower()
    return sorted(p for p in directory.glob("*.json") if fragment in p.name.lower())

"""
Load one saved batch result and run the normal interactive human-approval
gate (APPROVE / EDIT / REGENERATE / REJECT) on it.

This is the second half of the batch workflow: batch_runner.py processes
many brands unattended and saves each result; this script is how you
actually review and approve one of them afterward, without re-running
Research/Qualification/Outreach/Manager (i.e. without spending more API
budget) unless you choose REGENERATE.

Usage:
    python review_one.py "Trailhead"          # matches by filename fragment
    python review_one.py batch_results/Fictional_Trailhead_Gear_Co_20260828_140000.json
"""

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

from approval import run_approval_gate
from persistence import RESULTS_DIR, find_results, load_result

load_dotenv(override=True)


def _resolve_path(query: str) -> Path:
    direct = Path(query)
    if direct.exists():
        return direct

    matches = find_results(query)
    if not matches:
        print(f"No saved result found matching {query!r} under {RESULTS_DIR}.")
        print("Run batch_runner.py first, or check the company name spelling.")
        sys.exit(1)
    if len(matches) > 1:
        print(f"Multiple saved results match {query!r} - please be more specific:")
        for m in matches:
            print(f"  - {m.name}")
        sys.exit(1)
    return matches[0]


async def main(query: str) -> None:
    path = _resolve_path(query)
    print(f"Loaded: {path.name}\n")
    result = load_result(path)
    await run_approval_gate(result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Review and approve one saved batch result.")
    parser.add_argument("query", help="Company name fragment, or a direct path to a saved .json result")
    args = parser.parse_args()
    asyncio.run(main(args.query))

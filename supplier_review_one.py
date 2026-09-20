"""
Load one saved brand<->supplier relationship and run the interactive
human-approval gate (APPROVE / EDIT / REGENERATE / REJECT) on it - the
supplier-side counterpart to review_one.py. No further agent calls unless
you choose REGENERATE (i.e. no more research/qualification spend).

Usage:
    python supplier_review_one.py "Trailhead"          # matches by filename fragment (brand, supplier, or both)
    python supplier_review_one.py supplier_data/relationships/Fictional_Trailhead_Gear_Co__sup_abc123def456.json
"""

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

import supplier_persistence
from supplier_approval import run_supplier_approval_gate

load_dotenv(override=True)


def _resolve_path(query: str) -> Path:
    direct = Path(query)
    if direct.exists():
        return direct

    matches = supplier_persistence.find_relationships(brand_fragment=query)
    if not matches:
        print(f"No saved relationship found matching {query!r} under {supplier_persistence.RELATIONSHIPS_DIR}.")
        print("Run supplier_batch_runner.py first, or check the brand/supplier name spelling.")
        sys.exit(1)
    if len(matches) > 1:
        print(f"Multiple saved relationships match {query!r} - please be more specific:")
        for m in matches:
            print(f"  - {m.name}")
        sys.exit(1)
    return matches[0]


async def main(query: str) -> None:
    path = _resolve_path(query)
    print(f"Loaded: {path.name}\n")
    relationship = supplier_persistence.load_relationship(path)
    await run_supplier_approval_gate(relationship)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Review and approve one saved brand<->supplier relationship.")
    parser.add_argument("query", help="Brand/supplier name fragment, or a direct path to a saved relationship .json")
    args = parser.parse_args()
    asyncio.run(main(args.query))

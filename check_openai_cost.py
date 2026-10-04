"""Print OpenAI API spend for this project, using the Costs API.

Needs an Admin API key - not the regular OPENAI_API_KEY this project uses
for agent calls, which can't read billing data. Create one at
platform.openai.com under Settings -> Organization -> Admin keys (needs the
api.usage.read scope, org owner/admin role required), then add it to .env
as OPENAI_ADMIN_KEY.

An Admin key sees the whole org, not just this project - if other apps
share the same OpenAI org (billed on the same card, which is exactly how a
day can jump to $18+ without it being obvious which app did it), set
OPENAI_PROJECT_ID in .env (the "proj_..." id this project's own
OPENAI_API_KEY belongs to - already set in this project's .env) to scope
results to just this project's spend. Without it, this reports org-wide
spend across every app, which is misleading once more than one app shares
the org.

OpenAI's API reports spend, not your remaining prepaid balance - compare
the total this prints against what you've deposited to know when to top up.

Usage:
    python check_openai_cost.py                # this month so far, this project
    python check_openai_cost.py --since 2026-09-01
    python check_openai_cost.py --project proj_abc123  # override OPENAI_PROJECT_ID
    python check_openai_cost.py --org-wide      # ignore project scoping entirely - see every app on the org
"""

import argparse
import os
import sys
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv(override=True)

ADMIN_KEY = os.getenv("OPENAI_ADMIN_KEY")
DEFAULT_PROJECT_ID = os.getenv("OPENAI_PROJECT_ID")
COSTS_URL = "https://api.openai.com/v1/organization/costs"


def month_start_unix() -> int:
    start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp())


def date_to_unix(date_str: str) -> int:
    return int(datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def fetch_cost_buckets(start_time: int, project_id: str | None) -> list[dict]:
    headers = {"Authorization": f"Bearer {ADMIN_KEY}"}
    buckets = []
    page = None
    while True:
        params = {"start_time": start_time, "bucket_width": "1d", "limit": 180}
        if project_id:
            params["project_ids"] = [project_id]
        if page:
            params["page"] = page
        response = requests.get(COSTS_URL, headers=headers, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        buckets.extend(data.get("data", []))
        if data.get("has_more") and data.get("next_page"):
            page = data["next_page"]
        else:
            return buckets


def main():
    if not ADMIN_KEY:
        print(
            "Set OPENAI_ADMIN_KEY in .env first - create one at platform.openai.com "
            "under Settings > Organization > Admin keys (needs api.usage.read). "
            "The regular OPENAI_API_KEY this project uses for agent calls can't read billing data."
        )
        sys.exit(1)

    parser = argparse.ArgumentParser()
    parser.add_argument("--since", help="YYYY-MM-DD, defaults to the start of this month")
    parser.add_argument("--project", help="Project id (proj_...) to scope to; overrides OPENAI_PROJECT_ID")
    parser.add_argument("--org-wide", action="store_true", help="Report org-wide spend, ignoring project scoping")
    args = parser.parse_args()

    project_id = None if args.org_wide else (args.project or DEFAULT_PROJECT_ID)

    start_time = date_to_unix(args.since) if args.since else month_start_unix()
    buckets = fetch_cost_buckets(start_time, project_id)

    scope = f"project {project_id}" if project_id else "entire org"
    print(f"Scope: {scope}\n")
    total = 0.0
    print(f"{'Date':<12} {'Cost (USD)':>10}")
    for bucket in buckets:
        day = datetime.fromtimestamp(bucket["start_time"], tz=timezone.utc).strftime("%Y-%m-%d")
        day_total = sum(result["amount"]["value"] for result in bucket.get("results", []))
        if day_total:
            print(f"{day:<12} {day_total:>10.4f}")
        total += day_total

    print(f"\nTotal spend since {args.since or 'start of month'}: ${total:.4f}")


if __name__ == "__main__":
    main()

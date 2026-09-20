# R&T Brand Acquisition System — Version 1

A prototype multi-agent system that helps **R&T Distribution Group LLC**
research, qualify, and draft outreach to prospective wholesale suppliers
(brands, manufacturers, distributors). Built on top of the OpenAI Agents
SDK, adapted from the Week 2 sales-agent exercise in
[`3_lab3.ipynb`](../3_lab3.ipynb) of Ed Donner's Agentic AI course.

**This is a business-development research/drafting tool, not an autosender.
No code path in this project sends an email.** Every run ends at a human
approval gate.

## What it does

Given a prospective brand/manufacturer/distributor, the system:

1. Gathers and organizes what's known about them (Research Agent)
2. Scores whether it's worth R&T's time to reach out (Qualification Agent)
3. Independently drafts three different outreach emails, each with a
   different strategy (Relationship / Procurement / Partnership)
4. Has a Manager agent score all three against a weighted rubric and pick
   a winner
5. Presents the winning draft to a human for **APPROVE / EDIT / REGENERATE
   / REJECT** — nothing leaves this system without that step

The system is designed to never fabricate facts about R&T (existing
authorizations, sales history, certifications, etc.) — see
[`config.py`](config.py)'s `AI_RESTRICTIONS`.

## Agent architecture

```
Prospect
   |
Research Agent            <- WebSearchTool (optional) + manually supplied notes
   |
Qualification Agent       <- scores 0-100, PURSUE/INVESTIGATE/HOLD/REJECT
   |
   +-----------------------------+
   |              |              |
Relationship   Procurement   Partnership     <- run independently, concurrently
   |              |              |
   +--------------+--------------+
                  |
           Outreach Manager       <- weighted rubric, picks a winner
                  |
            Human Review          <- APPROVE / EDIT / REGENERATE / REJECT
                  |
            Approved Draft        <- saved to outbox/, never auto-sent
```

| Agent | File | Output type |
|---|---|---|
| Brand Research Agent | [`research_agent.py`](research_agent.py) | `ResearchFindings` |
| Qualification Agent | [`qualification_agent.py`](qualification_agent.py) | `QualificationResult` |
| Relationship / Procurement / Partnership Outreach Agents | [`outreach_agents.py`](outreach_agents.py) | `OutreachDraft` |
| Outreach Manager | [`outreach_manager.py`](outreach_manager.py) | `ManagerDecision` |

Supporting (non-agent) modules: [`smartscout_import.py`](smartscout_import.py)
(CSV → `Prospect` list), [`batch_runner.py`](batch_runner.py) (run many
prospects unattended), [`persistence.py`](persistence.py) (save/load a full
result as JSON), [`review_one.py`](review_one.py) (reload one saved result
into the approval gate).

All Pydantic models are in [`models.py`](models.py). The shared R&T
company profile, AI restrictions, and scoring rubric weights live in
[`config.py`](config.py) so they're defined exactly once and reused by
every agent's instructions.

## Data flow

`workflow.py`'s `run_brand_acquisition()` orchestrates the pipeline with
plain `async`/`await` calls to `Runner.run()` — **not** the SDK's handoff
feature. This mirrors the course's own official
[`deep_research/research_manager.py`](../deep_research/research_manager.py)
pattern (planner → search agents → writer, all called explicitly), and
matches the course author's stated preference in `2_lab2.ipynb` ("I am not
a fan of handoffs... unreliable"). The three outreach agents run
concurrently via `asyncio.gather()` against identical input, which is what
guarantees they're truly independent — none of them can see another's
draft.

The whole run is wrapped in `with trace(...)`, so each run shows up as a
named trace at <https://platform.openai.com/traces>, exactly like the
existing notebooks. You can see which agent ran, its input/output, any
tool calls (e.g. `WebSearchTool`), and how long each step took. No API
keys are ever printed to the console or included in trace output by this
code.

## How to run

From this directory, using the same conda environment the notebooks use:

```
python demo.py
```

This runs the fictional demo prospect (`Northwind Outdoor Gear Co.` in
[`sample_prospects.py`](sample_prospects.py) — clearly labeled as
fictional, **not** a real company) through the whole pipeline with web
search disabled, prints the structured report, and drops you into the
approval prompt.

To run against a real prospect, write a small script that builds a real
`Prospect` (see [`models.py`](models.py)) and calls
`workflow.run_brand_acquisition(prospect, manual_research_notes=..., allow_web_search=True)`
— see `demo.py` for the pattern. With `allow_web_search=True`, the
Research Agent may use the SDK's built-in `WebSearchTool` to look things
up itself, in addition to whatever manual notes you supply.

## Batch mode: importing a SmartScout export

For processing many candidate brands at once (e.g. the ~100 that already
passed your SmartScout filters — seller count, margin ratio, Amazon
competition, ungating), use the batch pipeline instead of `demo.py`:

```
python batch_runner.py --csv your_smartscout_export.csv --limit 5   # cheap test first
python batch_runner.py --csv your_smartscout_export.csv             # full run
```

This runs every row through Research → Qualification → Outreach → Manager
(concurrency-limited, one bad row won't kill the batch), saves each full
result to `batch_results/<company>_<timestamp>.json`, and writes a single
ranked `batch_results/summary_<timestamp>.csv` you can scan in Excel/Sheets.
It does **not** show the interactive approval prompt per company — that
doesn't scale to 100 brands.

Once you've picked a promising company from the summary, review and
approve it individually — with no further API calls unless you choose
REGENERATE:

```
python review_one.py "company name fragment"
```

**On SmartScout's column names**: [`smartscout_import.py`](smartscout_import.py)'s
`COLUMN_ALIASES` dict is a best-effort guess at SmartScout's export headers
— this project has no access to SmartScout itself to verify them against a
real file. Once you have a real export, compare its header row to
`COLUMN_ALIASES` and add any exact header text that isn't matching yet; no
other code needs to change. [`sample_smartscout_export.csv`](sample_smartscout_export.csv)
is a small fictional file you can use to test the importer today. Whatever
SmartScout told you about a brand (seller count, Amazon-as-seller, margin
ratio, etc.) is treated as a verified, user-supplied fact and passed
straight to the Research Agent — never re-derived or guessed at.

## Required environment variables

Same `.env` already used by the rest of the course repo:

- `OPENAI_API_KEY` — required. Used for all agent calls and for
  `WebSearchTool` (no separate search API key needed).
- `DEFAULT_MODEL_NAME` — optional, defaults to `gpt-5.4-mini` (same
  convention as `deep_research/search_agent.py`).

## Human approval requirement

`approval.py` implements the mandatory gate. **APPROVE** does not send
anything — it writes the approved subject/body to a local file under
`outbox/`, clearly marked `NOT SENT`, for a human to send manually.
**EDIT** lets you retype the subject/body before re-approving. **REGENERATE**
re-runs only the outreach-drafting + manager stages (research/qualification
are reused). **REJECT** exits without saving anything. `messenger.py` (the
course's SMTP/Pushover sender) is never imported anywhere in this project.

## Current limitations (Version 1)

- Research relies on `WebSearchTool` and/or manually supplied notes — no
  custom scraping, no Amazon-specific data sources.
- Qualification scoring evaluates *outreach opportunity*, not Amazon
  product-level profitability — those are different questions.
- No persistence between runs (no CRM/pipeline) — each run is standalone.
- No actual email sending, follow-up scheduling, or reply handling.
- Human approval loop is a plain terminal prompt.

## Planned future enhancements

Not implemented now, but the architecture (separate modules, structured
Pydantic outputs, a single orchestrating `workflow.py`) is meant to make
these additive rather than requiring a rewrite:

- Deeper automated web research (multi-query planning, like `deep_research/planner_agent.py`)
- SmartScout / Keepa / SellerAmp data as additional research inputs
- Amazon profitability analysis (a distinct agent from Qualification)
- Wholesale contact discovery
- Gmail integration for the actual send step (with its own confirmation)
- Automatic follow-up scheduling
- A CRM / supplier pipeline with persistence (e.g. `SQLiteSession`-style storage)
- Response classification (parsing replies)
- Application-form assistance
- Distributor research
- Product catalog ingestion, SKU-level opportunity analysis
- MAP policy tracking over time
- Supplier-document storage, relationship history
- Analytics based on response rate, A/B testing outreach strategies
- Learning evaluation weights from real outcomes

## Relationship to the original tutorial

[`3_lab3.ipynb`](../3_lab3.ipynb) (the course notebook this was adapted
from) is untouched and preserved for reference — this project lives
alongside it in its own folder, not inside it.

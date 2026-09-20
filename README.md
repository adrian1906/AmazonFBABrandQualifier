# R&T Brand/Supplier Acquisition System — Version 2

A prototype multi-agent system that helps **R&T Distribution Group LLC**
research, qualify, and draft outreach — first to prospective **brands**
(Stage 1, Version 1), and then to the **suppliers** who can actually get
those brands into R&T's hands for resale (Stage 2, added in Version 2).
Built on top of the OpenAI Agents SDK. The Brand Qualifier stage started
life as an adaptation of the Week 2 sales-agent exercise (`3_lab3.ipynb`)
from Ed Donner's Agentic AI course, and later graduated into this
standalone repository as its own product.

**Two-stage workflow:**

1. **Brand Qualifier** ("is this brand worth pursuing?") - the original
   Version 1 system, unchanged and still fully functional on its own. See
   [Brand Qualifier](#brand-qualifier-stage-1) below.
2. **Supplier Qualifier** ("who can legitimately supply this brand to R&T,
   and is that supply path usable for Amazon resale?") - the Version 2
   addition. See [Supplier Qualifier](#supplier-qualifier-stage-2) below.

Both stages share the same architecture, conventions, and hard rule:
**public web research is not the same thing as approval to purchase or
resell on Amazon.** Nothing in either stage assumes brand authorization or
marketplace permission that wasn't actually found and evidenced - see
`AI_RESTRICTIONS` in [`config.py`](config.py) and the evidence-grading and
hard-gate sections below.

**This is a business-development research/drafting tool, not an autosender.
No code path in this project sends an email.** Every run ends at a human
approval gate.

## Brand Qualifier (Stage 1)

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
feature (deliberately: handoffs hand control *between* agents, which makes
it harder to guarantee independence between the three outreach drafts
below). The three outreach agents run concurrently via `asyncio.gather()`
against identical input, which is what guarantees they're truly
independent — none of them can see another's draft.

The whole run is wrapped in `with trace(...)`, so each run shows up as a
named trace at <https://platform.openai.com/traces>. You can see which
agent ran, its input/output, any tool calls (e.g. `WebSearchTool`), and how
long each step took. No API keys are ever printed to the console or
included in trace output by this code.

## Setup

```
uv sync
cp .env.example .env   # then fill in OPENAI_API_KEY
```

## How to run

From this directory, using the project's virtual environment (`uv run ...`,
or activate `.venv` directly):

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

Copy [`.env.example`](.env.example) to `.env` and fill it in:

- `OPENAI_API_KEY` — required. Used for all agent calls and for
  `WebSearchTool` (no separate search API key needed).
- `DEFAULT_MODEL_NAME` — optional, defaults to `gpt-5.4-mini`.

The Supplier Qualifier (below) needed no new secrets.

## Human approval requirement

`approval.py` implements the mandatory gate. **APPROVE** does not send
anything — it writes the approved subject/body to a local file under
`outbox/`, clearly marked `NOT SENT`, for a human to send manually.
**EDIT** lets you retype the subject/body before re-approving. **REGENERATE**
re-runs only the outreach-drafting + manager stages (research/qualification
are reused). **REJECT** exits without saving anything. No email-sending
library or capability is imported anywhere in this project.

## Current limitations (Brand Qualifier)

- Research relies on `WebSearchTool` and/or manually supplied notes — no
  custom scraping, no Amazon-specific data sources.
- Qualification scoring evaluates *outreach opportunity*, not Amazon
  product-level profitability — those are different questions.
- No persistence between runs (no CRM/pipeline) — each run is standalone.
- No actual email sending, follow-up scheduling, or reply handling.
- Human approval loop is a plain terminal prompt.

## Planned future enhancements (Brand Qualifier)

Not implemented now, but the architecture (separate modules, structured
Pydantic outputs, a single orchestrating `workflow.py`) is meant to make
these additive rather than requiring a rewrite. ~~Struck through~~ items
are now implemented - by the Supplier Qualifier (see below), not by
rewriting the Brand Qualifier itself:

- Deeper automated web research (multi-query planning across several search passes)
- SmartScout / Keepa / SellerAmp data as additional research inputs
- Amazon profitability analysis (a distinct agent from Qualification)
- ~~Wholesale contact discovery~~ → see Supplier Qualifier
- Gmail integration for the actual send step (with its own confirmation)
- Automatic follow-up scheduling
- ~~A CRM / supplier pipeline with persistence~~ → see `supplier_persistence.py` below
- Response classification (parsing replies)
- Application-form assistance
- ~~Distributor research~~ → see Supplier Qualifier
- Product catalog ingestion, SKU-level opportunity analysis
- MAP policy tracking over time
- ~~Supplier-document storage, relationship history~~ → see `supplier_persistence.py` below
- Analytics based on response rate, A/B testing outreach strategies
- Learning evaluation weights from real outcomes

---

## Supplier Qualifier (Stage 2)

Given a brand that already looks promising, the Supplier Qualifier answers
a different question: **who can legitimately supply it to R&T, and is that
supply path operationally and evidentially suitable for resale on Amazon?**

It reuses the Brand Qualifier's architecture end to end - same OpenAI
Agents SDK primitives, same `config.py`/`models.py` single-source-of-truth
pattern, same explicit `Runner.run()` orchestration inside `trace(...)`,
same three-strategy outreach + manager evaluation, same human approval gate
philosophy. Nothing about the Brand Qualifier changed to make room for it
(other than one bug fix - see "A note on a bug fix" below) - the two stages
run independently or together.

### Two modes

**Mode A - Integrated** (`brand_batch_link.py`): consumes an existing Brand
Qualifier batch run. By default it researches suppliers only for brands
whose `qualification_status == PURSUE` - INVESTIGATE/HOLD/REJECT brands are
never auto-researched. A human can override this with `--include`/
`--exclude` regardless of status. Every resulting relationship carries a
traceable `origin_brand_batch_id` back to the batch it came from (the
existing `batch_results/summary_*.csv` that `batch_runner.py` already
writes serves as that batch's manifest - nothing needed to change there).

**Mode B - Standalone** (`input_loader.py`): runs from a manually supplied
brand list - no Brand Qualifier record required. Accepts a CLI
comma-separated list, or a `.csv`, `.json`, or `.txt` file. Both modes feed
the exact same research → evidence → scoring → report → outreach pipeline.

### Core pipeline (`supplier_workflow.py`)

```
Brand name (+ optional brand-qualifier context)
   |
Supplier Research Agent          <- official brand/manufacturer site FIRST,
   |                                then broader distributor/wholesaler search
(targeted escalation pass, per candidate,
 if key fields are still uncertain)
   |
Entity resolution                <- stable supplier_id per real-world company,
   |                                so the same supplier is recognized across
   |                                brands and across runs, not duplicated
Supplier Qualification Agent     <- per candidate, concurrent
   |
supplier_scoring.py              <- deterministic weights + HARD GATES,
   |                                enforced in plain Python, not left to the model
   |
(CONTACT_NOW / INVESTIGATE_FURTHER only)
   |
Relationship / Procurement / Partnership outreach agents  <- concurrent, role-aware
   |
Outreach Manager (reused as-is from outreach_manager.py)
   |
BrandSupplierRelationship  <- persisted; stops at the human approval gate
```

### Evidence grading

Every material claim about a supplier candidate carries its own
`EvidenceItem` (source URL, page title, excerpt, retrieval date, source
type, and an evidence state) - never one label for a whole company. States:

- `VERIFIED` - directly supported by an official brand/manufacturer source.
- `DISTRIBUTOR_CLAIM` - asserted by the supplier itself, not independently confirmed.
- `INFERRED` - reasonably suggested but not expressly stated.
- `UNKNOWN` - not found or not established. Never guessed at.
- `CONFLICTING` - credible sources materially disagree.

A distributor simply **listing** or selling a brand is a `DISTRIBUTOR_CLAIM`
at most, never `VERIFIED` authorization. Phrases like "Amazon-friendly" or
"we supply Amazon sellers" are never interpreted as brand permission to
resell on Amazon - marketplace permission is `UNKNOWN` unless explicitly stated.

### Scoring and hard gates

`supplier_scoring.py` turns the Qualification Agent's raw per-dimension
judgment into a weighted 0-100 score (weights: `config.SUPPLIER_SCORING_WEIGHTS`,
sums to 100, same pattern as `OUTREACH_RUBRIC`) and then applies hard gates
**in plain Python**, so these are guarantees, not instructions an LLM might
drift from:

- A high score never overrides `PROHIBITED` Amazon resale - forced to `DO_NOT_PURSUE`.
- `UNKNOWN` Amazon permission stays unknown - never treated as approval.
- Unverified brand authorization blocks the `APPROVED_FOR_PURCHASE` lifecycle
  state (see `advance_lifecycle_state`) even if everything else looks great.
- A manufacturer representative without confirmed stocking/invoicing evidence
  is flagged as a referral contact, not the purchase source.
- Liquidation inventory, retail-receipt, or other listed risk flags force
  `DO_NOT_PURSUE` regardless of score.

Recommendations: `CONTACT_NOW` / `INVESTIGATE_FURTHER` / `DO_NOT_PURSUE`.
Lifecycle states: `DISCOVERED` → `RESEARCHED` → `CONTACT_APPROVED` →
`CONTACTED` → `RESPONSE_RECEIVED` → `APPLICATION_SUBMITTED` →
`ACCOUNT_APPROVED` → `CATALOG_RECEIVED` → `APPROVED_FOR_ASIN_ANALYSIS` /
`APPROVED_FOR_PURCHASE`, or `DECLINED` / `DISQUALIFIED`. **The automatic
pipeline never assigns anything beyond `RESEARCHED`** - every later state
represents a real external event (a reply, an approved account...) and can
only be set by an explicit human action, never assumed.

### Reports (`supplier_report.py` / `supplier_report_cli.py`)

Five views, all rendered from already-persisted data (no paid calls):
a ranked supplier report, a brand-to-supplier matrix, a
missing-information/action queue, a contact-now queue, and a do-not-pursue
section with reasons. Every evidence line shows its source URL and the date
it was checked.

```
python supplier_report_cli.py --batch supbatch_20260920_101500
python supplier_report_cli.py --brand "Lemax"
python supplier_report_cli.py --batch supbatch_20260920_101500 --save   # also writes to supplier_reports/
```

### Human approval workflow

Same no-send rule as the Brand Qualifier: `supplier_approval.py` /
`supplier_review_one.py` present the report and drive
APPROVE / EDIT / REGENERATE / REJECT. APPROVE writes the message to
`outbox/` (clearly marked `NOT SENT`) and advances the relationship's
lifecycle state to `CONTACT_APPROVED` - the one lifecycle transition this
system will make automatically, because it's recording the human's own
decision, not assuming anything happened externally.

```
python supplier_review_one.py "Lemax"
```

### Running it

```
# Integrated: default to PURSUE brands from an existing Brand Qualifier batch
python supplier_batch_runner.py --from-brand-batch batch_results/summary_20260920_101500.csv

# Integrated with manual override
python supplier_batch_runner.py --from-brand-batch summary_20260920 --include "Brand A,Brand B" --exclude "Brand C"

# Standalone
python supplier_batch_runner.py --brands "Lemax,Pacific Giftware,BRK,HEM,Super Snouts,Diamine,Zebra,Kalita,New Age Imports,Midori,Sortkwik"
python supplier_batch_runner.py --input brands.csv    # or .json / .txt

# Cheap test run, and resuming a stalled/failed batch
python supplier_batch_runner.py --brands "Lemax,Diamine" --limit 2
python supplier_batch_runner.py --resume supbatch_20260920_101500
```

Every run writes a batch manifest (`supplier_data/batches/<id>.json`) so it
can be `--resume`d later - only brands that failed or whose cached research
has gone stale (`config.SUPPLIER_STALE_DATA_DAYS`, default 90 days) are
re-researched. `--dry-run` renders from cache only, with no paid calls.
`--no-web-search` disables `WebSearchTool` entirely. One bad brand never
kills the batch - failures are caught, logged, and reported at the end.

### Caching and escalation

Each brand's research is cached (`supplier_data/cache/`) so repeat runs
don't pay to re-ask the same question. After the first research pass, any
candidate still missing a high-impact fact (brand authorization, Amazon
permission, legal identity/location, role, invoice suitability) gets one
additional, narrowly-targeted research pass (capped by
`config.SUPPLIER_MAX_ESCALATIONS_PER_BRAND`, default 3 per brand, to bound
cost) - see `supplier_workflow._needs_escalation` /`_merge_escalation`.

**On the research provider**: research uses the same `WebSearchTool` the
Brand Qualifier already uses - no Tavily, no OpenRouter. Introducing a
second paid search provider or routing model calls through OpenRouter were
both explicitly declined for this version (new secrets + new external cost
with no functional gain, since `WebSearchTool` requires OpenAI-direct model
calls to work at all). "Escalation" here means a second, more targeted
`WebSearchTool` pass, not a second provider.

### Entity resolution and persistence (`entity_resolution.py` / `supplier_persistence.py`)

Suppliers are deduplicated by normalized domain, phone, and legal name into
a stable `supplier_id`, so the same real-world company is recognized across
brands and across research runs instead of being duplicated. A brand may
have multiple suppliers; a supplier may carry multiple brands with a
different role/authorization per brand - each `BrandSupplierRelationship`
is its own record. All of it is plain JSON on disk under `supplier_data/`
(no database dependency, same philosophy as `persistence.py`):

```
supplier_data/
  entity_index.json              # match keys -> supplier_id
  suppliers/<supplier_id>.json   # latest known profile for that entity
  relationships/<brand>__<supplier_id>.json
  runs/<run_id>.json             # every historical research run, never overwritten
  batches/<batch_id>.json        # batch manifests, for --resume and traceability
  cache/<brand>.json             # most recent research findings per brand
```

### Configuration (`config.py`)

Everything supplier-specific is grouped in one section of `config.py`:
`RT_OPERATING_STATE` / `RT_REQUIRES_MARYLAND_SERVICE` / `PREFERRED_SUPPLIER_GEOGRAPHY`,
`SUPPLIER_SCORING_WEIGHTS`, `SUPPLIER_RECOMMENDATION_THRESHOLDS`,
`SUPPLIER_ESCALATION_FIELDS`, `SUPPLIER_STALE_DATA_DAYS`,
`SUPPLIER_MAX_ESCALATIONS_PER_BRAND`, `SUPPLIER_DEFAULT_CONCURRENCY`,
`SUPPLIER_ENABLED_OUTREACH_STRATEGIES`, and `supplier_sender_email()`
(defaults to `purchasing@rtdistributiongroup.com` from the existing
`RT_PROFILE`, never a separately hardcoded address). No new secrets were
needed - see [`.env.example`](.env.example).

### Known limitations (Supplier Qualifier)

- A supplier candidate does not need a Maryland warehouse, only a
  verifiable physical address and confirmation it ships to/serves Maryland
  - `RT_REQUIRES_MARYLAND_SERVICE` documents the requirement but isn't yet
    enforced as a hard filter (it feeds the "Geographic/service fit"
    scoring dimension instead, since a strong national supplier shouldn't
    be discarded outright).
  - There is no live cost-metering integration, so `--concurrency`/
    `--limit`/`SUPPLIER_MAX_ESCALATIONS_PER_BRAND` are the actual cost
    controls, not a dollar budget.
  - As with the Brand Qualifier, there is no automated email send, form
    submission, account creation, or catalog/UPC-to-ASIN ingestion in this
    version - see the outreach prompt's implementation spec for the full
    non-goals list. Extension points (stable `supplier_id`s, evidence
    items, lifecycle states) are deliberately in place for that later work.
- The evidence-grading and role-classification rules are enforced by
  hard-coded Python gates (`supplier_scoring.py`) wherever the spec called
  them non-negotiable; everything else (which candidates to search for, how
  to phrase outreach) is still LLM judgment, so quality depends on the
  underlying model and what `WebSearchTool` can actually find.

### A note on a bug fix

While wiring `brand_batch_link.py` to the Brand Qualifier's saved results,
testing turned up a pre-existing bug in `persistence.find_results()`: it
compared a raw (space-containing) name fragment against slugified
(underscore-containing) filenames, so a multi-word company name - the
common case, and the exact usage documented in `review_one.py`'s own
docstring - never matched its own saved file. Fixed by slugifying the
search fragment the same way filenames are slugified; see
`tests/test_persistence.py` for the regression test. This was the one
change made to an existing Brand Qualifier file.

---

## Tests

```
uv run pytest
```

No test in this project makes a network or paid API call: every agent call
goes through `agents.Runner.run`, and tests replace it with a fake that
returns frozen, hand-written responses (see `tests/helpers.py`'s
`FakeRunner`). `tests/test_supplier_workflow_fixtures.py` covers the
required deterministic scenarios (direct reseller program, officially named
distributor, manufacturer rep needing referral, unverified-authorization
wholesaler, unknown Amazon permission, prohibited Amazon resale, one
supplier tied to multiple brands) end to end through the real pipeline with
mocked agents; `tests/test_supplier_scoring.py` covers the same hard gates
directly. `isolated_supplier_data` (in `conftest.py`) redirects all
`supplier_data/`/`outbox/` paths to a temp directory automatically, so
tests never touch real project data. The Brand Qualifier itself had no test
suite before this change; `tests/test_persistence.py` and
`tests/test_brand_batch_link.py` are its first tests, added because
`brand_batch_link.py` depends on `persistence.py`'s behavior directly.

## Origin

This project started as an exercise adapted from Ed Donner's Agentic AI
course (`3_lab3.ipynb`'s sales-agent lab) and has since grown into its own
standalone system, developed in this repository going forward.

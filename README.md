# R&T Brand/Supplier Acquisition System — Version 2

A prototype multi-agent system that helps **R&T Distribution Group LLC**
research, qualify, and draft outreach — first to prospective **brands**
(Stage 1, Version 1), and then to the **suppliers** who can actually get
those brands into R&T's hands for resale (Stage 2, added in Version 2).
Built on top of the OpenAI Agents SDK. The Brand Qualifier stage started
life as an adaptation of the Week 2 sales-agent exercise (`3_lab3.ipynb`)
from Ed Donner's Agentic AI course, and later graduated into this
standalone repository as its own product.

**Three-stage workflow:**

1. **Brand Qualifier** ("is this brand worth pursuing?") - the original
   Version 1 system, unchanged and still fully functional on its own. See
   [Brand Qualifier](#brand-qualifier-stage-1) below.
2. **Supplier Qualifier** ("who can legitimately supply this brand to R&T,
   and is that supply path usable for Amazon resale?") - the Version 2
   addition. See [Supplier Qualifier](#supplier-qualifier-stage-2) below.
3. **FBA Catalog Analyzer** ("now that I have a distributor, which specific
   products in their price list are actually profitable to buy and resell
   on Amazon, and at what price would an unprofitable one become
   profitable?") - the Version 3 addition. See
   [FBA Catalog Analyzer](#fba-catalog-analyzer-stage-3) below.

Around those two stages sits a small toolkit for running this repeatedly
without re-deriving the process each time or accidentally re-paying for
work already done: SmartScout import/merge/filter helpers, a self-growing
distributor reference list, printable review documents, and OpenAI cost
tracking - each covered where it's used below, or all together under
[Supporting tooling](#supporting-tooling).

Both stages share the same architecture, conventions, and hard rule:
**public web research is not the same thing as approval to purchase or
resell on Amazon.** Nothing in either stage assumes brand authorization or
marketplace permission that wasn't actually found and evidenced - see
`AI_RESTRICTIONS` in [`config.py`](config.py) and the evidence-grading and
hard-gate sections below.

**This is a business-development research/drafting tool, not an autosender.
No code path in this project sends an email.** Every run ends at a human
approval gate.

**New here, or don't have a first distributor yet?** See
[WORKFLOW.md](WORKFLOW.md) for a complete, copy-pasteable walkthrough -
SmartScout export in, printable call list out - rather than piecing it
together from the sections below.

## GUI (recommended if you're not comfortable on a command line)

```
uv sync
cp .env.example .env   # fill in OPENAI_API_KEY
uv run streamlit run app.py
```

This opens a local web page (`http://localhost:8501`) covering both
stages, with four tabs:

- **Reports** - read-only. Browse Brand Qualifier results and Supplier
  Qualifier reports (ranked list, brand-to-supplier matrix, missing-info
  queue, contact-now queue, do-not-pursue-with-reasons). No API calls.
- **Review Queue** - the human approval gate as buttons instead of typed
  commands: pick a brand or a brand↔supplier relationship, edit the
  subject/body if you want, then **Approve** (writes to `outbox/`, marked
  NOT SENT - nothing is ever emailed automatically), **Regenerate**, or
  **Reject**.
- **Single Lookup** - research one brand, or one brand's suppliers, right
  from the page. Bounded cost (a handful of API calls, not a batch) - safe
  to hand to someone who isn't going to accidentally run up a bill.
- **Command Builder** - fill in a form (which CSV, which brands, how many
  at once, etc.) and it prints the *exact* `batch_runner.py` /
  `supplier_batch_runner.py` command to paste into a terminal, with a
  plain-language note on what it does and roughly what it costs. Multi-brand
  batch runs are deliberately **never** started from the GUI itself - they
  cost real money and can run for a while, so a terminal command with a
  human in front of it stays the only way to kick one off. This tab exists
  so you (or anyone else running this) don't have to memorize the flags.

Everything the GUI does calls the exact same underlying code as the CLI
tools below (`gui_actions.py` is a thin wrapper, no separate logic) - the
GUI and the CLI can be used interchangeably on the same data.

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

| Agent | File | Output type | Model |
|---|---|---|---|
| Brand Research Agent | [`research_agent.py`](research_agent.py) | `ResearchFindings` | `config.MODEL_NAME` (cheap/fast) |
| Qualification Agent | [`qualification_agent.py`](qualification_agent.py) | `QualificationResult` | `config.QUALIFICATION_MODEL_NAME` (stronger) |
| Relationship / Procurement / Partnership Outreach Agents | [`outreach_agents.py`](outreach_agents.py) | `OutreachDraft` | `config.MODEL_NAME` |
| Outreach Manager | [`outreach_manager.py`](outreach_manager.py) | `ManagerDecision` | `config.MODEL_NAME` |

The Qualification Agent alone runs on a separate, stronger model
(`QUALIFICATION_MODEL_NAME`, defaults to `gpt-5.4` vs. `MODEL_NAME`'s
`gpt-5.4-mini`) - a deliberate, narrow upgrade: its raw score is what
decides `PURSUE`/`INVESTIGATE`/`HOLD`/`REJECT`, so a wrong judgment there
has the most downstream consequence, while research and drafting don't
need the stronger (pricier) model. The Supplier Qualification Agent
(Stage 2) was tested against the same upgrade and deliberately **not**
given it - see [Known limitations (Supplier Qualifier)](#known-limitations-supplier-qualifier)
below for why.

Supporting (non-agent) modules: [`smartscout_import.py`](smartscout_import.py)
(CSV → `Prospect` list), [`batch_runner.py`](batch_runner.py) (run many
prospects unattended, skipping brands already scored - see
[Batch mode](#batch-mode-importing-a-smartscout-export) below),
[`persistence.py`](persistence.py) (save/load a full result as JSON,
including the already-scored lookup), [`review_one.py`](review_one.py)
(reload one saved result into the approval gate).

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

The whole run is wrapped in `with trace(...)`, which would otherwise make
each run show up as a named trace at <https://platform.openai.com/traces>
(which agent ran, its input/output, any tool calls, how long each step
took). **Tracing is currently disabled** (`set_tracing_disabled(True)` in
`config.py`) - at the user's request, since it wasn't considered useful
enough to keep and it also removed a flaky network call that occasionally
surfaced as a `[non-fatal] Tracing: request failed` warning during batch
runs. The `with trace(...)` calls are harmless no-ops as a result; no code
needed to change to turn this back on, just remove that one line. No API
keys are ever printed to the console or included in trace output by this
code, whether tracing is on or off.

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

**Already-scored brands are skipped by default.** `persistence.scored_company_names()`
checks every saved result before spending anything, so re-running this
against a CSV that overlaps a previous run — e.g. after merging a new
SmartScout export with an old one — doesn't silently re-pay to re-score
brands already on file. A brand only counts as "already scored" if its
newest saved result is within `config.BRAND_STALE_DATA_DAYS` (default 90
days) — older than that, SmartScout's own numbers have likely drifted, so
it's processed again rather than skipped forever. Pass `--rescore` to
process every row regardless of age.

Once you've picked a promising company from the summary, review and
approve it individually — with no further API calls unless you choose
REGENERATE:

```
python review_one.py "company name fragment"
```

### Preparing the input CSV: merging and narrowing SmartScout exports

Two small, free (no API calls), local tools for building that input CSV
from scratch or from multiple pulls over time:

```
# Combine multiple SmartScout exports (same column schema) into one
# deduplicated file - the newest file wins when the same brand appears twice
python merge_smartscout_exports.py --csv export_jan.csv --csv export_mar.csv -o merged.csv

# Narrow a brand export down to replenishable product families BEFORE
# paying for Stage 1 - see WORKFLOW.md for the full reasoning and the
# ranked category list this implements
python filter_smartscout_categories.py --list-categories
python filter_smartscout_categories.py --csv merged.csv --category office_products
python filter_smartscout_categories.py --csv merged.csv --category office_products --append-distributors distributor_master_list.csv
```

`--append-distributors` adds rows from a `distributor_master_list.csv`-shaped
file (see [Supplier Qualifier](#supplier-qualifier-stage-2) below) onto the
*end* of the filtered output, after the keyword filter runs rather than
through it — a hand-curated distributor list is a different kind of
candidate (already vetted, carries many categories) than an
undifferentiated product-brand export, so it shouldn't be subject to the
same product-keyword narrowing.

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
- `DEFAULT_MODEL_NAME` — optional, defaults to `gpt-5.4-mini`. Sets
  `config.MODEL_NAME`, used by every agent except the Qualification Agent.
- `QUALIFICATION_MODEL_NAME` — optional, defaults to `gpt-5.4`. Used only
  by `qualification_agent.py` - see the Agent architecture table above.
- `OPENAI_PROJECT_ID` — optional, but recommended once more than one app
  shares your OpenAI org. Scopes `check_openai_cost.py`'s spend report to
  just this project instead of every app on the org.
- `OPENAI_ADMIN_KEY` — optional, only needed to run `check_openai_cost.py`.
  An Admin API key (**not** the regular `OPENAI_API_KEY`, which can't read
  billing data) - create one at platform.openai.com → Settings →
  Organization → Admin keys (needs the `api.usage.read` scope).

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
- ~~No persistence between runs (no CRM/pipeline) — each run is
  standalone.~~ → `batch_results/` now persists across runs and
  `batch_runner.py` checks it (see "Already-scored brands are skipped by
  default" above) - still no CRM-style pipeline/lifecycle tracking on the
  Brand Qualifier side, though (that exists on the Supplier Qualifier side
  via `lifecycle_state`).
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

### New-business accessibility

Added for a brand-new R&T, which starts with no trading history and no
existing distributor relationships: `SupplierCandidate` also captures
`new_business_accessible` (yes/no/unknown), `requires_minimum_years_in_business`,
`requires_trade_references`, and `requires_credit_application` - evidence-graded
the same way as everything else above (only set from an explicit source
like a wholesale FAQ or application page, never inferred from a small
opening order or from silence). This feeds its own weighted scoring
dimension (below) and a dedicated "Startup-Friendly Call List" in both
reports - the actionable output for someone with no distributors yet. See
[WORKFLOW.md](WORKFLOW.md) for the full worked example.

### Scoring and hard gates

`supplier_scoring.py` turns the Qualification Agent's raw per-dimension
judgment into a weighted 0-100 score (weights: `config.SUPPLIER_SCORING_WEIGHTS`,
11 dimensions summing to 100, including "New-business/startup accessibility"
(10 pts) - same pattern as `OUTREACH_RUBRIC`) and then applies hard gates
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

**Outreach drafting only auto-fires for `CONTACT_NOW`**, not
`INVESTIGATE_FURTHER` - a cost decision, not a scoring one. Most
`INVESTIGATE_FURTHER` candidates (missing info, unresolved questions) are
never actually pursued, so drafting 3 emails + a manager evaluation for
every one of them mostly went to waste. A human can still draft outreach
for a specific `INVESTIGATE_FURTHER` candidate once they've decided it's
worth pursuing - "Draft outreach now" in the GUI's Review Queue, or
`REGENERATE` at the `supplier_review_one.py` prompt.

### Reports (`supplier_report.py` / `supplier_report_cli.py` / `supplier_report_md.py`)

Six views, all rendered from already-persisted data (no paid calls):
a ranked supplier report, a **Startup-Friendly Call List** (phone, contact
method, and opening order for every candidate confirmed to work with a
brand-new business, sorted by score - the actionable output
[WORKFLOW.md](WORKFLOW.md) is built around), a brand-to-supplier matrix, a
missing-information/action queue, a contact-now queue, and a do-not-pursue
section with reasons. Every evidence line shows its source URL and the date
it was checked.

```
python supplier_report_cli.py --batch supbatch_20260920_101500
python supplier_report_cli.py --brand "Lemax"
python supplier_report_cli.py --batch supbatch_20260920_101500 --save   # also writes to supplier_reports/
```

For a printable version grouped by brand (one page per candidate, score
breakdown table, the winning email, a decision checklist) instead of
terminal text:

```
python supplier_report_md.py --batch supbatch_20260920_101500
```
→ `batch_reports/Distributor_candidates_<batch id>_<timestamp>.md`. The
Brand Qualifier side has the equivalent for Stage 1 results - see
[Supporting tooling](#supporting-tooling) below.

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

### Growing distributor_master_list.csv (`grow_distributor_master_list.py`)

Closes the loop: SmartScout brands → vet brands → discover distributors →
vet distributors → **grow `distributor_master_list.csv`**. Every
distributor Stage 2 discovers and qualifies as not `DO_NOT_PURSUE` is a
real candidate worth keeping on record, so the master list - originally a
one-time import from a PDF (see [Supplier Qualifier](#supplier-qualifier-stage-2)
intro) - grows automatically at the end of every `supplier_batch_runner.py`
run instead of staying a frozen snapshot:

```
python grow_distributor_master_list.py             # standalone backfill from every saved relationship
python grow_distributor_master_list.py --dry-run    # preview without writing
```

**Append-only** - an existing row is never modified or removed, and a
distributor already in the list (matched by normalized name) is never
duplicated. The rule for what gets added is deliberately permissive
(anything not `DO_NOT_PURSUE`), so the growing list still needs the same
kind of human eyeballing as everything else here - it's a candidate pool,
not a pre-vetted shortlist. `distributor_master_list.csv` itself is
real business data and is **not tracked by git** (see `.gitignore`) - back
it up some other way, since it's now a compounding asset, not just a
one-time import.

### Configuration (`config.py`)

Everything supplier-specific is grouped in one section of `config.py`:
`RT_OPERATING_STATE` / `RT_REQUIRES_MARYLAND_SERVICE` / `PREFERRED_SUPPLIER_GEOGRAPHY`,
`SUPPLIER_SCORING_WEIGHTS` (now 11 dimensions - see "New-business
accessibility" above), `SUPPLIER_RECOMMENDATION_THRESHOLDS`,
`SUPPLIER_ESCALATION_FIELDS`, `SUPPLIER_STALE_DATA_DAYS`,
`SUPPLIER_MAX_ESCALATIONS_PER_BRAND`, `SUPPLIER_DEFAULT_CONCURRENCY`,
`SUPPLIER_ENABLED_OUTREACH_STRATEGIES`, and `supplier_sender_email()`
(defaults to `purchasing@rtdistributiongroup.com` from the existing
`RT_PROFILE`, never a separately hardcoded address). No new secrets were
needed - see [`.env.example`](.env.example). The Brand Qualifier side has
its own equivalent staleness/model settings now too:
`BRAND_STALE_DATA_DAYS` and `QUALIFICATION_MODEL_NAME` (see
[Required environment variables](#required-environment-variables) above).

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
- The Supplier Qualification Agent deliberately runs on `MODEL_NAME`
  (mini), not the stronger `QUALIFICATION_MODEL_NAME` the Brand Qualifier's
  Qualification Agent uses - tested and found that the stronger model
  miscalibrates this agent's `dimension_scores` (a `dict[str, int]`): it
  anchors on the rubric's *weight* numbers instead of giving independent
  0-100 scores, reproduced on a real strongly-evidenced candidate that
  mini scored correctly. Revisit if `dimension_scores` is ever reshaped
  away from a bare dict (see `supplier_qualification_agent.py`'s
  `model=` comment for the full reproduction).

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

### A note on a second bug fix: the strict-schema crash

Every `Runner.run()` against the Supplier Qualification Agent used to
crash with a pydantic `ValidationError` before this was fixed -
`SupplierQualificationResult.dimension_scores` (an open-ended
`dict[str, int]`) is rejected by OpenAI's strict structured-output mode,
which requires a fixed, named set of properties. Fixed with
`AgentOutputSchema(SupplierQualificationResult, strict_json_schema=False)`
in `supplier_qualification_agent.py` - the fix the SDK's own error message
suggests. This had gone uncaught because the test suite mocks
`Runner.run()` entirely, so the real schema is never built in tests; it
only surfaced the first time the Supplier Qualifier ran against the real
API.

---

## FBA Catalog Analyzer (Stage 3)

Once a distributor exists (Stage 2 found one, or you already had one),
this turns their price list into a per-product answer to "is this worth
buying, and at what price would it be?" - landed-cost ROI, a target
supplier price for anything below target, and a minimum-monthly-sales
demand check, all computed in plain deterministic Python (`roi_engine.py`,
`decimal.Decimal`, never an LLM doing arithmetic).

### What it does and doesn't do today

**Works today, against a real catalog, for real:** CSV/XLSX import with
column-mapping detection and a preview before anything is committed
(`catalog_import.py`); UPC/EAN checksum validation, including detecting
and flagging the single most common real-world data defect - a UPC that
lost a leading zero somewhere upstream (confirmed on the sample catalog:
~32% of its UPCs are exactly one digit short of a checksum-valid UPC-A);
the full landed-cost/merchandise-cost ROI math and the "what supplier
price would reach my target ROI" solver, rounded down and
re-verified (`roi_engine.py`); the minimum-monthly-sales demand gate,
including the "100+"-lower-bound and parent/child-ambiguity cases
(`demand_engine.py`); and the orchestration that ties a row to a cost
ledger, an ROI tier, and a qualify/don't-qualify decision with a reason
(`catalog_scan.py`).

**Live as of 2026-10-08, against R&T's real accounts:** Both Keepa
(`keepa_adapter.py`, needs `KEEPA_API_KEY` - a real Keepa **API key**,
**not** your Keepa login password) and Amazon's own SP-API Catalog Items
(`sp_api_adapter.py`, needs `AMAZONSOLUTIONCLIENTID` /
`AMAZONSOLUTIONSECRET` / `AMAZONSOLUTIONREFRESHTOKEN`) do real ASIN
resolution. `catalog_batch.py` prefers Keepa when it's configured, since
it's the only one of the two that also does price **history** - SP-API's
equivalent (Product Pricing API) isn't built yet, so `catalog_batch.py`
falls back to SP-API-only (resolution, no history) when Keepa isn't set,
then to fixture/demo mode when neither is.

Live-verified end to end, both ways: a real UPC from the sample catalog
resolved to the same real ASIN (`B0CLNWM532`, "1057 Extra Mature Scottish
Cheddar, 7 OZ") via both Keepa and SP-API independently; `get_fees_estimate()`
returned a real referral fee ($2.40 on a $15.99 price, ~15.0% - matching
this project's own fallback rate almost exactly for this category), and
correctly *failed* an `IsAmazonFulfilled=True` (FBA) estimate for that
same item because it's refrigerated and isn't FBA-eligible - a real
constraint surfaced correctly, not a bug; and `get_pricing_snapshot()`
returned a real 365-day historical low ($84.79, time-weighted mean
$91.73, 219/365 days with coverage) for a different real catalog item,
which fed straight into `roi_engine` to produce a real `TARGET_MET`
result (`landed_cost_roi` ≈ 11.4%) with zero manually-entered price - the
full pipeline, for real, start to end. `catalog_import.py`'s leading-zero-
UPC correction (above) is wired INTO resolution too: `catalog_scan.py`
retries a no-match UPC against its checksum-corrected form before giving
up, which is how most of the sample catalog's truncated UPCs actually
resolve.

`get_pricing_snapshot(asin, window_days)` takes window_days as a plain
integer - 30, 60, 90, 365, 730, or any custom number - passed straight
through to Keepa's own server-side min/max/mean computation over exactly
that window (not just its fixed 30/90/180/365 buckets), so a seasonal
product's 365- or 730-day lookback (`config.CATALOG_SEASONAL_HISTORY_DAYS`
/ `_EXTENDED_HISTORY_DAYS`) works the same way a 30-day one does.
`catalog_batch.py --history-days N` exposes this on the CLI. The
**planning price is always the LOWER of the current price and that
window's historical low** (never the average, never the current price
alone) - the conservative "profit even at the lowest point" basis the
spec calls for - and it prefers the shipping-inclusive Buy Box series,
falling back through lowest-new-FBA, lowest-new-any-fulfillment, and
Amazon's own retail price when Buy Box has no data, with every fallback
visibly flagged (`requires_review=True`, `planning_price_basis` names
which series was actually used) rather than silently substituted. A
*lack* of data in a short window is common and correct, not a bug -
confirmed live on two real low-traffic catalog items that had zero
tracked history in their most recent 30 days despite having real history
further back; `raw_minimum` stays `None` rather than reading that as "no
sales" or "$0."

Keepa's own token-bucket rate limit (reported in every response, even
error ones) is respected automatically: `KeepaRateLimiter`
(`keepa_adapter.py`) paces requests to the account's real `refillRate`
rather than guessing, and retries a real 429 ("out of tokens") using
Keepa's own reported `refillIn`, up to a bounded number of attempts and a
capped maximum single wait - so a very slow plan (e.g. 1 token/minute)
works correctly for a small batch, just slower, instead of erroring out.

And critically: **even a real resolved ASIN never auto-qualifies a row**
- both adapters return `match_status="needs_review"`, because resolving a
UPC to an ASIN doesn't by itself confirm the retail pack count. A human
(via `override_asin_candidate`) still has to confirm that before
cost-ledger math runs - real ASIN candidates and real prices make that
review faster, but don't skip it.

### Running it

```bash
# 1. Preview - fast (~1s even on a 98,000-row catalog), no commitment.
python catalog_batch.py --preview "catalogs/my_price_list.xlsx" --supplier "Acme Distribution"

# 2. Same, but also run the full duplicate-UPC scan (slower on a big file).
python catalog_batch.py --preview "catalogs/my_price_list.xlsx" --supplier "Acme Distribution" --thorough

# 3. Full scan - cheap test run first.
python catalog_batch.py --run "catalogs/my_price_list.xlsx" --supplier "Acme Distribution" --limit 10

# 4. The real thing, once the mapping/price-basis above look right.
python catalog_batch.py --run "catalogs/my_price_list.xlsx" --supplier "Acme Distribution"
```

`--price-basis case` (default `each`) tells the importer the WHOLESALE-style
price column is per-case/inner-pack rather than per-single-unit - this
can't be detected from the file itself (see `catalog_import.py`'s module
docstring for why), so it's always an explicit, overridable assumption,
never a silent guess baked into a result. A full run writes a ranked
summary CSV to `batch_reports/` and the complete per-row result (cost
ledger, evidence, every intermediate number) to `catalog_data/runs/`.

### Resolution and pricing use different providers, on purpose

Keepa's real rate limit on this account is 1 token/minute - far too slow
to resolve tens of thousands of UPCs against directly. Amazon's own
SP-API has no comparably tight limit for this project's usage, so
`catalog_batch.py` splits the work: **SP-API resolves ASINs (when
configured), Keepa only prices them (by ASIN, once resolved)** - Keepa is
the only one of the two with price history at all, so that half can't be
avoided, but resolution doesn't have to pay Keepa's slow rate at all.

That resolution result is then cached PERSISTENTLY, independent of any
one catalog file (`asin_cache.py`, `catalog_data/asin_cache.json`,
gitignored - it accumulates real data, not project source). A UPC-to-ASIN
mapping is a fact about the product, not about this month's price list,
so once resolved it's free on every future scan - re-running this
catalog, a newer price list from the same supplier, or even a different
supplier selling the same product. Live-verified: resolving 5 real rows
cost 5 real SP-API calls the first time and exactly 0 the second time.
The cache expires after `asin_cache.ASIN_CACHE_STALE_DAYS` (180, far
longer than price data's own staleness window) and stores a genuine
"no match" too, so a known dead-end UPC isn't re-queried every scan
either - pass `scan_row(..., use_asin_cache=False)` to force a fresh
lookup regardless.

`catalog_scan.scan_row` takes `resolution_provider=`/`pricing_provider=`
separately for this reason (`provider=` still works as a shared default
for both, for backward compatibility and simpler one-off calls).
`catalog_batch.py`'s `_select_resolution_provider()`/`_select_pricing_provider()`
make the actual choice: SP-API first for resolution (falling back to
Keepa, then fixture/demo), always Keepa for pricing (falling back to
fixture/demo - SP-API has no price history yet, see above).

### The ROI math

Two conventions are always computed and shown; `config.CATALOG_ROI_CONVENTION`
("landed", the default) decides which one gates qualification:

- **Landed-cost ROI** = Profit / (normalized COGS + landed pre-sale costs)
- **Merchandise-cost ROI** = Profit / normalized COGS
- **Margin** = Profit / selling price (never confused with ROI)

A case/inner-pack purchase price is normalized to the Amazon listing's own
pack count before any of this runs - `roi_engine.normalize_cogs()`'s own
worked example: a $48 case of 12 identical items, 2 of which make up one
Amazon listing, is a $8 listing COGS, never a $48 one. For anything below
the target ROI, `roi_engine.solve_target_supplier_price()` solves for the
supplier price that would reach it, rounds the result DOWN to the
supplier's quote precision, and recomputes the ROI at that rounded price
to confirm it actually clears the target - all pinned to the spec's own
worked numbers in `tests/test_roi_engine.py`.

| Config | Default | Meaning |
|---|---|---|
| `CATALOG_TARGET_ROI` | 10% | "Target met" - qualifies on ROI alone |
| `CATALOG_HIGHLIGHT_ROI_THRESHOLD` | 8% | "Negotiation candidate" floor - strictly above this, below target |
| `CATALOG_MIN_MONTHLY_SALES` | 25 units/mo | Demand floor - set with Dr. Hood 2026-10-08 |
| `CATALOG_PREP_COST_PER_UNIT` | $1.50 | PROVISIONAL prep-center cost per finished Amazon sellable unit |
| `CATALOG_REFERRAL_RATE_FALLBACK` | 15% | PROVISIONAL - used only when no product-specific Amazon fee is known |
| `CATALOG_HOLDING_PERIOD_DAYS` | 60 | Cash-exposure/storage planning assumption |
| `CATALOG_MIN_PROFIT_PER_UNIT` | none | Explicitly declined - ROI% alone qualifies |
| `CATALOG_ORDER_BUDGET_CAP` | none | Explicitly declined - no per-run cash cap |

Every number above is provisional/configurable, defined once in
`config.py` - never hardcoded into the math itself.

### Why every row needs a cost ledger, not just a formula

A row never gets a profit number from guessed or defaulted costs.
`catalog_scan.py` only fills in two cost-ledger lines by default -
`prep_center_processing` (the $1.50 provisional default) and
`referral_fee` (the 15% fallback, scaled to the selling price) - both
tagged `status="assumed"`, never `"verified"`. Everything else mandatory
(supplier freight to prep, prep-to-Amazon shipping, Amazon inbound
placement, FBA fulfillment fee, expected storage, returns allowance)
starts `status="unknown"` and stays that way until supplied explicitly
via `known_costs=`. **Any one unknown mandatory cost makes the whole
result `INCOMPLETE`** - it is never silently treated as zero, and an
`INCOMPLETE` row is never counted as qualifying, no matter how good its
partial numbers look. The same rule applies to a mixed bundle with no
complete bill-of-materials cost allocation, and to a row whose ASIN match
was never confirmed (`match_status != "verified"`) - which, in today's
fixture/demo mode, is every row, by design.

### Demand gating

A product qualifies only when it clears BOTH the ROI threshold and
`CATALOG_MIN_MONTHLY_SALES` - meeting one without the other shows up as
an explicit note (`"ROI met - sales minimum not met."` or the reverse),
never a silent pass or fail. Missing sales data is `UNKNOWN_NEEDS_REVIEW`,
not zero. A reported lower bound (e.g. a SmartScout "100+") passes only
when the bound itself clears the minimum; otherwise it's inconclusive. A
number that applies to a parent/combined-variations listing rather than
the confirmed child ASIN is always flagged for review regardless of its
size. See `demand_engine.py` and `tests/test_demand_engine.py`.

### Files

| File | Role |
|---|---|
| `catalog_models.py` | Every typed record (CatalogRow, AsinCandidate, CostComponent, RoiResult, ...) |
| `catalog_import.py` | CSV/XLSX intake, column-mapping, UPC validation/leading-zero detection |
| `roi_engine.py` | Pure deterministic cost/ROI math - no I/O, no catalog/Keepa knowledge |
| `demand_engine.py` | Minimum-monthly-sales classification |
| `keepa_adapter.py` | ASIN resolution AND real price history via Keepa - **live** (incl. rate limiting), see above |
| `sp_api_adapter.py` | ASIN resolution/fee estimates via Amazon's own SP-API - **live**, see above |
| `asin_cache.py` | Persistent UPC -> ASIN cache, independent of any one catalog file - see "Resolution and pricing use different providers" above |
| `catalog_scan.py` | Orchestrates one row: match -> cost ledger -> ROI -> demand -> qualify |
| `catalog_persistence.py` | Save/load scan runs and column-mapping profiles |
| `catalog_batch.py` | CLI entry point - `--preview` and `--run` (this section's examples); SP-API resolves, Keepa prices - see above |

### Known limitations (FBA Catalog Analyzer)

- SmartScout API/MCP integration isn't wired in. SP-API's Product Pricing
  API (its equivalent of Keepa's historical tracking) isn't built either,
  so Keepa is the only source of price history today. Every scan is
  still a `needs_review` screening pass until a human confirms the pack
  match (`override_asin_candidate`) - real ASIN/price data makes that
  review faster and better-informed, never automatic.
- SP-API's `get_fees_estimate()`/`extract_fee_components()` (real
  referral/FBA fees) aren't wired into `catalog_scan.py`'s cost ledger
  automatically yet - still manual via `known_costs=` today.
- `KeepaRateLimiter` paces Keepa calls correctly but is Keepa-specific;
  SP-API has no equivalent throttling yet (its own rate limits are far
  more generous, so this hasn't been needed so far).
- PDF catalogs aren't parsed - `catalog_import.load_catalog_rows` raises a
  clear error pointing at a manual CSV/XLSX conversion route rather than
  guessing at an unstructured table.
- No Streamlit GUI tab yet - `catalog_batch.py` is CLI-only for now,
  consistent with how this project's other expensive/batch operations
  (`batch_runner.py`, `supplier_batch_runner.py`) are run from a terminal
  rather than a button click.
- Eligibility checks (listing approval, brand authorization, hazmat/IP
  flags) are a typed placeholder (`EligibilityCheck`) with no live data
  source behind them yet - every field defaults to `"unknown"`, which
  never blocks `qualifies` on its own, matching the spec's "a product can
  meet 10% ROI while still needing approval" distinction.

---

## Supporting tooling

A few standalone, free (no API calls) tools that sit around both stages -
see [WORKFLOW.md](WORKFLOW.md) for how they fit together end to end.

### Printable batch review (`batch_report_md.py`)

The Brand Qualifier equivalent of `supplier_report_md.py` above: renders
every saved `batch_results/` result into one printable Markdown file,
ranked by score - meant to be opened and printed for paper review/markup
(a score breakdown, risks, the winning email, and a decision checklist per
company). Only the newest result per company is included if a brand was
scored more than once.

```
python batch_report_md.py                                    # every saved result
python batch_report_md.py --since 20261004                   # only results saved on/after that date
python batch_report_md.py --names-csv distributor_master_list.csv --label Distributor   # scope to one list
```

`--label` controls the title/column header (default `Brand`) - pass
`--label Distributor` when reviewing a `distributor_master_list.csv`-sourced
batch, where the companies are distributors, not product brands.

### OpenAI cost tracking (`check_openai_cost.py`)

Reports this project's actual OpenAI spend via the Costs API, scoped to
`OPENAI_PROJECT_ID` so it isn't muddied by other apps sharing the same
OpenAI org (needs an Admin API key - see
[Required environment variables](#required-environment-variables) above,
not the regular `OPENAI_API_KEY`, which can't read billing data):

```
python check_openai_cost.py                    # this month so far, this project
python check_openai_cost.py --since 2026-10-01
python check_openai_cost.py --org-wide          # ignore project scoping - see every app on the org
```

Reports *spend*, not remaining prepaid balance - compare the total against
what's been deposited to know when to top up.

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
`supplier_data/`/`batch_results/`/`outbox/` paths to a temp directory
automatically and disables SDK tracing (`agents.set_tracing_disabled`),
since `trace(...)` otherwise tries to phone home to OpenAI independently of
whether `Runner.run` is mocked - so tests never touch real project data or
make a real network call, full stop. `tests/test_gui_actions.py` and
`tests/test_gui_command_builder.py` cover the GUI's wrapper/command-string
logic the same way (no Streamlit process is started in tests - `app.py`
itself was verified by hand with `streamlit run`). The Brand Qualifier
itself had no test suite before this change; `tests/test_persistence.py`
and `tests/test_brand_batch_link.py` are its first tests, added because
`brand_batch_link.py` depends on `persistence.py`'s behavior directly.

`tests/test_batch_runner.py` covers the already-scored skip/staleness
behavior; `tests/test_filter_smartscout_categories.py` covers the
SmartScout category-filter matching (including a regression test for a
real false-positive bug - plain substring matching once matched "tea"
inside "steak"); `tests/test_grow_distributor_master_list.py` covers the
append-only/dedup/`DO_NOT_PURSUE`-exclusion guarantees for
`distributor_master_list.csv`'s growth. All of the above were also
verified by hand against the real OpenAI API and the user's real data at
least once each, beyond what the mocked test suite covers - notably the
new-business-accessibility scoring calibration (see
[Known limitations](#known-limitations-supplier-qualifier) above) and the
strict-schema crash fix, neither of which the mocked tests alone would
have caught.

`tests/test_roi_engine.py` pins the FBA Catalog Analyzer's deterministic
math to the spec's own worked acceptance-check numbers (the $48/12/2 ->
$8 COGS example, the P=20/C=8/B=2/S=7 -> 30%/37.5%/15% example, the
9.818181.../$58.90-rounded-down-and-reverified example, and the "exactly
8% is not highlighted, exactly 10% meets target" boundary), plus zero/
negative-denominator and infeasible-discount cases. `tests/test_demand_engine.py`
covers its required edge cases directly (exactly at the minimum, a "100+"
lower bound above vs. below the threshold, missing data, parent/combined-
variation scope). `tests/test_catalog_import.py` and
`tests/test_catalog_scan.py` run against the real sample catalog in
`catalogs/` (skipped automatically if that file isn't present) in addition
to synthetic CSVs - including the real leading-zero-UPC data defect this
project found in that file (see
[FBA Catalog Analyzer](#fba-catalog-analyzer-stage-3) above).
`tests/test_sp_api_adapter.py` and `tests/test_keepa_adapter.py` mock
every `requests`/`time.sleep` call (same no-real-network-calls, no-slow-
tests rules as the rest of this suite) using the ACTUAL response shapes
and field semantics (Keepa Time Minutes conversion, the -1 sentinel,
token-bucket fields) captured live against R&T's real accounts on
2026-10-08, not guessed-at schemas. `tests/test_asin_cache.py` and the
cache-specific tests in `tests/test_catalog_scan.py` cover the
persistent UPC->ASIN cache (round trip, staleness, a corrupt file
degrading to empty rather than crashing, a negative "no match" result
being cached too) and that `resolution_provider`/`pricing_provider` are
genuinely used independently - `conftest.py`'s `isolated_asin_cache`
fixture (autouse) redirects the cache file to a tmp_path for every test,
the same way `isolated_supplier_data` already did for the other stages.

## Origin

This project started as an exercise adapted from Ed Donner's Agentic AI
course (`3_lab3.ipynb`'s sales-agent lab) and has since grown into its own
standalone system, developed in this repository going forward.

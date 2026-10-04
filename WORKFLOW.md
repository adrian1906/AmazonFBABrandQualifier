# Example Workflow: Finding Your First Distributor

This walks through one real, complete pass through the system: from a raw
SmartScout export to a printable call list, for someone who doesn't have a
first distributor yet. It's written around R&T's actual current situation
and sourcing advice, using **Office Products** as the worked example -
repeat the same steps for Home & Kitchen, then Pet Supplies, in that order.

No step in this workflow requires coming back to ask "what do I do next" -
every command below is the literal thing to type. If a step's output looks
wrong, the troubleshooting note under that step should cover it; if not,
that's worth a real question, not a guess.

## The pipeline, at a glance

```
SmartScout export (raw, many brands)
   |
filter_smartscout_categories.py   <- narrow to replenishable product families (free, local, no API cost)
   |
batch_runner.py                   <- Stage 1: is this BRAND worth pursuing? (paid)
   |
supplier_batch_runner.py          <- Stage 2: WHO can supply it, and will they work with a new business? (paid)
   |
supplier_report_md.py / supplier_report_cli.py   <- your call list (free, reads saved data)
```

Two things are worth understanding before you start:

- **"Replenishable" isn't just consumables.** A durable product with
  steady sales and dependable wholesale supply qualifies too - the point
  is "can I keep buying and reselling the same SKU," not "does it get used
  up."
- **"New-business friendly" is a confirmed fact, not a guess.** The system
  only marks a distributor as friendly to a brand-new buyer when it found
  explicit evidence (a wholesale FAQ, an application page) saying so. Most
  candidates on your first few runs will come back `unknown` here, not
  `yes` or `no` - that's expected, not a bug. `unknown` means "worth
  asking directly," not "skip it."

## Step 1 - Export brands from SmartScout

Pull a brand export for **Office Products** from your SmartScout account
(Category/Brand research view). You need at minimum a brand-name column
and a subcategory column; `smartscout_import.py` already recognizes common
header variants:

| What's needed | Accepted header names |
|---|---|
| Brand name | `Brand`, `Brand Name`, `Company`, `Company Name`, `Seller Name` |
| Category/subcategory | `Category`, `Subcategory`, `Amazon Category`, `Primary Categories`, or SmartScout's own `Main Category` / `Primary Subcategory` columns |

If your export uses different column names than these, open
`smartscout_import.py` and `filter_smartscout_categories.py` and add your
exact header text to the alias lists - no other code needs to change.

Save it somewhere findable, e.g. `smartscout_office_products.csv`.

## Step 2 - Narrow to replenishable product families (free)

This is the cost-control step - narrowing the list *before* spending API
budget on it, per R&T's own sourcing advice. See the full category list
and keyword presets:

```
python filter_smartscout_categories.py --list-categories
```

Then filter your export:

```
python filter_smartscout_categories.py --csv smartscout_office_products.csv --category office_products
```

This writes `smartscout_office_products_office_products.csv` and prints a
breakdown of how many rows were dropped and why. **Open the filtered file
and eyeball it** - this is a blunt keyword filter, not a judgment call;
it exists to cut an expensive batch down to a reasonable size, not to be
perfectly precise. Delete any row that obviously doesn't belong.

*Troubleshooting:* if it keeps far too few rows, your export's subcategory
text may use different wording than the preset's keywords - open
`filter_smartscout_categories.py` and add a few keywords to
`PRODUCT_FAMILY_FILTERS["office_products"]`. If it keeps far too many
(or the wrong category entirely), check that your Main Category column
actually says "Office Products" and not something SmartScout labels
slightly differently in your account.

## Step 3 - Qualify the brands (Stage 1, paid)

Run a cheap test first - this is real money, so confirm it's working
before committing to the full file:

```
python batch_runner.py --csv smartscout_office_products_office_products.csv --limit 5
```

Check the output (and `check_openai_cost.py`, see Step 7) look sane, then
run the rest:

```
python batch_runner.py --csv smartscout_office_products_office_products.csv
```

This writes `batch_results/summary_<timestamp>.csv` - a ranked list of
every brand, each with a `PURSUE`/`INVESTIGATE`/`HOLD`/`REJECT` status.
Only `PURSUE` brands move on to Stage 2 by default.

## Step 4 - Find distributors for the PURSUE brands (Stage 2, paid)

```
python supplier_batch_runner.py --from-brand-batch batch_results/summary_<timestamp>.csv --limit 3
```

(swap in the real summary filename printed by Step 3). Again, check the
small run before committing to the full batch:

```
python supplier_batch_runner.py --from-brand-batch batch_results/summary_<timestamp>.csv
```

This prints a **distributor batch id** (`supbatch_...`) at the end - save
it, you need it for the next step. For each `PURSUE` brand, this researches
candidate distributors/manufacturers, scores each one (including the new
"will they work with a brand-new business" dimension), and auto-drafts an
outreach email only for the strongest (`CONTACT_NOW`) candidates - not
every candidate, to keep cost down.

## Step 5 - Get your call list (free, reads saved data)

Printable version (recommended - open it, print it, mark it up):

```
python supplier_report_md.py --batch supbatch_<id>
```

Writes `batch_reports/Distributor_candidates_supbatch_<id>_<timestamp>.md`.
The very first section, **"Startup-Friendly Call List,"** is the actionable
output this whole workflow exists to produce: company name, phone, contact
method, and opening order, for every candidate explicitly confirmed to
work with a business that has no trading history yet.

Terminal version, if you just want to read it without opening a file:

```
python supplier_report_cli.py --batch supbatch_<id>
```

## Step 6 - What to actually do with the output

- **Startup-Friendly Call List is non-empty:** call them, in score order.
  This is your actual next action.
- **It's empty (common on a first run):** check the main ranked list and
  the missing-information queue in the same report. Most candidates will
  be `unknown` on new-business accessibility rather than confirmed `no` -
  the fastest next step is calling a few top-ranked `CONTACT_NOW`/
  `INVESTIGATE_FURTHER` candidates yourself and asking directly whether
  they'll work with a new account. Whatever you learn, feed it back in:
  open `supplier_review_one.py "<company name fragment>"` or the GUI's
  Review Queue and you (a human) can update that candidate's record.
- **A candidate is `DO_NOT_PURSUE`:** skip it, or hand it to someone else
  later - it's in the do-not-pursue section with the reason, so nothing
  gets lost, it just isn't worth action right now.
- **Found a promising `INVESTIGATE_FURTHER` candidate with no drafted
  email yet:** that's deliberate (outreach only auto-drafts for
  `CONTACT_NOW`, to control cost) - click "Draft outreach now" in the GUI's
  Review Queue, or choose `REGENERATE` at the `supplier_review_one.py`
  prompt, once you've decided it's worth pursuing.

Nothing in this system sends anything automatically - `APPROVE` only
writes a file to `outbox/`, clearly marked `NOT SENT`. You send it.

## Step 7 - Check what it cost

```
python check_openai_cost.py
```

Scoped to this project's `OPENAI_PROJECT_ID` automatically, so it won't be
muddied by any other app sharing your OpenAI org. Run this after Step 3
and Step 4's test batches (`--limit`) before committing to the full run,
so a bad estimate doesn't turn into a bad bill.

## Repeat for the next category

Per R&T's sourcing advice, go in this order - stop once a category is
producing enough qualified leads:

1. `office_products` (done above)
2. `home_kitchen`
3. `pet_supplies`
4. `industrial_scientific`
5. `tools_home_improvement`

Same four commands each time, just a fresh SmartScout export and a
different `--category` value in Step 2. Categories 6-8
(`grocery_gourmet_food`, `health_household`, `beauty_personal_care`) are
lower priority and need more manual screening - the filter presets for
those are intentionally narrower; expect to trim the output more by hand.

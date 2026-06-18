# Fuzzy CSV Reconciler

A command-line tool that matches rows between two CSV files when there's
**no clean shared ID** between them — for example, reconciling your
invoices against your bank statement.

## The problem

If you've ever tried to check that every invoice you sent actually got
paid, you've probably run into this: your accounting system says
`"Jonathan Smith Consulting"` paid `$271.30` on `2026-05-04`, and your bank
statement says `"J SMITH CONSULTING LLC"` deposited `$271.30` on
`2026-05-05`. Same transaction — but an exact match (`VLOOKUP`, SQL `JOIN`,
etc.) will never find it, because:

- **Names are formatted differently** between systems
- **Dates are off by a day or more** (banks take time to clear payments)
- **Amounts differ slightly** (processing fees, currency rounding)

Generic reconciliation tools handle exact matches well and dump everything
else into a "manual review" pile. This tool instead **scores every possible
pair** of rows on three signals — name similarity, date closeness, amount
closeness — and automatically sorts them into confident matches, borderline
matches that need a human's eyes, and true unmatched rows on each side.

## How it works

1. Every row in file A is compared against every row in file B.
2. Each pair gets a similarity score (0.0–1.0) on:
   - **Name similarity** — fuzzy string matching (handles abbreviations,
     casing, punctuation, LLC/Inc suffixes)
   - **Date similarity** — 1.0 if identical, decaying to 0.0 as the gap
     approaches `--date-tolerance-days`
   - **Amount similarity** — 1.0 if identical, decaying to 0.0 as the gap
     approaches `--amount-tolerance`
3. The three scores are averaged (equal weight by default) into one
   combined score.
4. Pairs are greedily matched starting from the highest score, so the
   strongest matches "claim" their rows first and a single bank transaction
   can't be double-booked against two invoices.
5. Every pair lands in one of four buckets:
   - **`matched.csv`** — score ≥ `--match-threshold` (confident match)
   - **`review.csv`** — score between `--review-threshold` and
     `--match-threshold` (probably a match, but flagged for a human)
   - **`unmatched_left.csv`** — rows from file A with nothing close enough
     in file B
   - **`unmatched_right.csv`** — rows from file B with nothing close enough
     in file A

No external libraries are required — the fuzzy string matching uses
Python's built-in `difflib`, so it runs anywhere stock Python 3 runs.

## Quick start

```bash
# 1. Clone the repo
git clone https://github.com/YOUR_USERNAME/fuzzy-csv-reconciler.git
cd fuzzy-csv-reconciler

# 2. (Optional) Generate realistic fake sample data to try it out on
python3 generate_sample_data.py

# 3. Run the reconciler on the sample data
python3 reconcile.py --left sample_data/invoices.csv --right sample_data/bank_statement.csv

# 4. Check the results
cat output/matched.csv
cat output/review.csv
```

Requires Python 3.7+. No `pip install` needed.

## Using it on your own data

```bash
python3 reconcile.py \
  --left your_invoices.csv         --left-name-col customer    --left-date-col date_sent    --left-amount-col total \
  --right your_bank_export.csv     --right-name-col memo       --right-date-col posted_date --right-amount-col amount \
  --date-tolerance-days 5 \
  --amount-tolerance 10 \
  --match-threshold 0.75 \
  --review-threshold 0.55
```

Run `python3 reconcile.py --help` for the full list of options.

### Tuning tips
- **Tighten `--amount-tolerance`** if your data shouldn't ever have fees —
  any gap then becomes more suspicious.
- **Loosen `--date-tolerance-days`** if your bank/payment processor is slow
  to clear funds (some take a week).
- **Lower `--match-threshold`** cautiously — it trades fewer "needs review"
  rows for a higher chance of an incorrect auto-match.

## Example output

Running on the included sample data:

```
Left file rows:              17
Right file rows:             20
Confident matches:           13   -> output/matched.csv
Needs human review:          3    -> output/review.csv
Unmatched (left, no match):  1    -> output/unmatched_left.csv
Unmatched (right, no match): 4    -> output/unmatched_right.csv
```

The 3 "needs review" rows are genuinely ambiguous (e.g. `"C Mendes Audio"`
vs `"Carlos Mendes Audio"` — a heavily abbreviated name) — exactly the kind
of judgment call that should go to a human rather than being silently
auto-matched or silently dropped.

## Project structure

```
.
├── reconcile.py              # main CLI tool
├── generate_sample_data.py   # creates realistic messy sample CSVs
├── sample_data/
│   ├── invoices.csv
│   └── bank_statement.csv
├── output/                   # created when you run reconcile.py
└── README.md
```

## Why this approach (not just a generic "diff two CSVs" tool)

Generic CSV-diff tools assume a clean, shared key. The moment your two data
sources come from different systems with no agreed-upon ID, you need
**custom fuzzy logic with tunable tolerances** — exactly the kind of
problem that's a quick, precise fit for a small script, but awkward to
expose through a one-size-fits-all UI.

## License

MIT — see [LICENSE](LICENSE).

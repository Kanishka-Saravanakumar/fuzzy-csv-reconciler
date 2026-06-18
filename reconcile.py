"""
reconcile.py

A fuzzy CSV reconciliation tool.

PROBLEM IT SOLVES
------------------
You have two records of the same financial activity (e.g. your invoices vs
your bank statement) and you need to know which rows in file A correspond
to which rows in file B - even when:
  - Names are formatted/spelled differently between the two systems
  - Dates differ by a day or two (bank clearing delays)
  - Amounts differ slightly (fees, currency rounding)

Exact-match tools (a plain SQL JOIN, Excel VLOOKUP, etc.) fail on this
because there is no shared, clean key between the two files. This script
scores every possible pair of rows on three signals - name similarity,
date closeness, amount closeness - and decides whether they're the same
underlying transaction.

USAGE
-----
    python3 reconcile.py --left sample_data/invoices.csv --right sample_data/bank_statement.csv

    python3 reconcile.py \
        --left invoices.csv --left-name-col client_name --left-date-col invoice_date --left-amount-col amount \
        --right bank_statement.csv --right-name-col description --right-date-col transaction_date --right-amount-col amount \
        --date-tolerance-days 3 --amount-tolerance 5.00 --match-threshold 0.75

OUTPUT
------
Three CSVs are written to the --output-dir (default: ./output):
  - matched.csv          confident matches (score >= match-threshold)
  - review.csv           borderline matches, flagged for a human to check
  - unmatched_left.csv   rows from the left file with no acceptable match
  - unmatched_right.csv  rows from the right file with no acceptable match

A plain-English summary is also printed to the terminal.
"""

import argparse
import csv
import sys
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path


# ----------------------------------------------------------------------
# Scoring functions - each returns a value from 0.0 (no match) to 1.0 (perfect)
# ----------------------------------------------------------------------

def normalize_name(name: str) -> str:
    """Strip punctuation/casing/common suffixes so names compare fairly."""
    name = name.lower()
    for ch in "._-,&":
        name = name.replace(ch, " ")
    for suffix in [" llc", " inc", " co", " ltd", " and "]:
        name = name.replace(suffix, " ")
    return " ".join(name.split())  # collapse repeated whitespace


def name_similarity(name_a: str, name_b: str) -> float:
    """Fuzzy string similarity using Python's built-in difflib (0.0-1.0).
    No external dependency required."""
    a, b = normalize_name(name_a), normalize_name(name_b)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def date_similarity(date_a: datetime, date_b: datetime, tolerance_days: int) -> float:
    """1.0 if dates are identical, decaying linearly to 0.0 at tolerance_days apart."""
    diff = abs((date_a - date_b).days)
    if diff > tolerance_days:
        return 0.0
    if tolerance_days == 0:
        return 1.0 if diff == 0 else 0.0
    return 1.0 - (diff / tolerance_days)


def amount_similarity(amount_a: float, amount_b: float, tolerance: float) -> float:
    """1.0 if amounts are identical, decaying linearly to 0.0 at `tolerance` apart."""
    diff = abs(amount_a - amount_b)
    if diff > tolerance:
        return 0.0
    if tolerance == 0:
        return 1.0 if diff == 0 else 0.0
    return 1.0 - (diff / tolerance)


def overall_score(name_sim: float, date_sim: float, amount_sim: float,
                   weights=(1.0, 1.0, 1.0)) -> float:
    """Equal-weighted average of the three signals by default."""
    w_name, w_date, w_amount = weights
    total_weight = w_name + w_date + w_amount
    return (name_sim * w_name + date_sim * w_date + amount_sim * w_amount) / total_weight


# ----------------------------------------------------------------------
# CSV loading helpers
# ----------------------------------------------------------------------

def load_rows(path, name_col, date_col, amount_col):
    if not Path(path).exists():
        print(f"ERROR: file not found: {path}")
        sys.exit(1)

    rows = []
    with open(path, mode='r', encoding='latin-1') as f:

        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            print(f"ERROR: {path} appears to be empty.")
            sys.exit(1)
        for required_col in (name_col, date_col, amount_col):
            if required_col not in reader.fieldnames:
                print(f"ERROR: column '{required_col}' not found in {path}.")
                print(f"  Available columns: {', '.join(reader.fieldnames)}")
                print(f"  Did you mean to pass e.g. --left-name-col or --right-date-col with a different value?")
                sys.exit(1)
        for i, raw in enumerate(reader):
            try:
                date_val = datetime.strptime(raw[date_col].strip(), "%Y-%m-%d")
            except ValueError:
                print(f"  WARNING: row {i} in {path} has unparseable date "
                      f"'{raw[date_col]}' (expected YYYY-MM-DD) - skipping row.")
                continue
            try:
                amount_val = float(raw[amount_col].replace(",", "").replace("$", "").strip())
            except ValueError:
                print(f"  WARNING: row {i} in {path} has unparseable amount "
                      f"'{raw[amount_col]}' - skipping row.")
                continue

            rows.append({
                "_row_index": i,
                "_name": raw[name_col].strip(),
                "_date": date_val,
                "_amount": amount_val,
                "_raw": raw,
            })
    return rows


# ----------------------------------------------------------------------
# Core matching logic
# ----------------------------------------------------------------------

def check_initials_match(name1, name2):
    """Returns True if one name is an abbreviation/initial of the other"""
    n1 = name1.lower().replace('.', '').split()
    n2 = name2.lower().replace('.', '').split()
    if not n1 or not n2:
        return False
    shortest = n1 if len(n1) < len(n2) else n2
    longest = n2 if len(n1) < len(n2) else n1
    if all(len(word) == 1 for word in shortest if word.isalpha()):
        initials = "".join(shortest)
        long_initials = "".join([word for word in longest if word])
        if initials in long_initials:
            return True
    return False

def adjust_score_for_business_rules(base_score, l_row, r_row):
    """Bumps up the match score if it meets safe business exception rules"""
    final_score = base_score
    if check_initials_match(l_row.get("_name", ""), r_row.get("_name", "")):
        final_score += 0.15  # 15% boost for initials like Y. T. Design
    try:
        amt1 = float(l_row.get("_amount", 0))
        amt2 = float(r_row.get("_amount", 0))
        if abs(amt1 - amt2) <= 1.00:
            final_score += 0.10  # 10% boost for $1 errors
    except (ValueError, TypeError):
        pass
    return min(final_score, 1.0)


def reconcile(left_rows, right_rows, date_tolerance_days, amount_tolerance,
              match_threshold, review_threshold):
    """
    Greedy best-match algorithm:
      1. Score every (left, right) pair.
      2. Sort all pairs by score, descending.
      3. Walk down the sorted list, claiming the highest-scoring pair first;
         once a left or right row is claimed, it can't be matched again.
    This avoids double-booking a single bank transaction against two
    different invoices, while still prioritizing the strongest matches.
    """
    candidates = []
    for l in left_rows:
        for r in right_rows:
            n_sim = name_similarity(l["_name"], r["_name"])
            d_sim = date_similarity(l["_date"], r["_date"], date_tolerance_days)
            a_sim = amount_similarity(l["_amount"], r["_amount"], amount_tolerance)
            score = overall_score(n_sim, d_sim, a_sim)
            score = adjust_score_for_business_rules(score, l, r)

            if score > 0:  # no point keeping zero-score pairs
                candidates.append((score, l, r, n_sim, d_sim, a_sim))

    candidates.sort(key=lambda c: c[0], reverse=True)

    matched_left_idx = set()
    matched_right_idx = set()
    matched, review = [], []

    for score, l, r, n_sim, d_sim, a_sim in candidates:
        if l["_row_index"] in matched_left_idx or r["_row_index"] in matched_right_idx:
            continue  # one or both rows already claimed by a better match
        if score < review_threshold:
            continue  # too weak to even flag for review

        record = {
            "score": round(score, 3),
            "name_similarity": round(n_sim, 3),
            "date_similarity": round(d_sim, 3),
            "amount_similarity": round(a_sim, 3),
            "left_row": l["_raw"],
            "right_row": r["_raw"],
        }
        if score >= match_threshold:
            matched.append(record)
        else:
            review.append(record)

        matched_left_idx.add(l["_row_index"])
        matched_right_idx.add(r["_row_index"])

    unmatched_left = [l["_raw"] for l in left_rows if l["_row_index"] not in matched_left_idx]
    unmatched_right = [r["_raw"] for r in right_rows if r["_row_index"] not in matched_right_idx]

    return matched, review, unmatched_left, unmatched_right


# ----------------------------------------------------------------------
# Output writing
# ----------------------------------------------------------------------

def write_match_csv(path, records, left_cols, right_cols):
    fieldnames = (["score", "name_similarity", "date_similarity", "amount_similarity"]
                  + [f"left_{c}" for c in left_cols]
                  + [f"right_{c}" for c in right_cols])
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for rec in records:
            row = {
                "score": rec["score"],
                "name_similarity": rec["name_similarity"],
                "date_similarity": rec["date_similarity"],
                "amount_similarity": rec["amount_similarity"],
            }
            for c in left_cols:
                row[f"left_{c}"] = rec["left_row"].get(c, "")
            for c in right_cols:
                row[f"right_{c}"] = rec["right_row"].get(c, "")
            writer.writerow(row)


def write_plain_csv(path, rows, columns):
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Fuzzy-match two CSVs (e.g. invoices vs bank statement) that lack a shared clean key.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--left", required=True, help="Path to the first CSV (e.g. invoices.csv)")
    parser.add_argument("--right", required=True, help="Path to the second CSV (e.g. bank_statement.csv)")

    parser.add_argument("--left-name-col", default="client_name")
    parser.add_argument("--left-date-col", default="invoice_date")
    parser.add_argument("--left-amount-col", default="amount")

    parser.add_argument("--right-name-col", default="description")
    parser.add_argument("--right-date-col", default="transaction_date")
    parser.add_argument("--right-amount-col", default="amount")

    parser.add_argument("--date-tolerance-days", type=int, default=5,
                         help="Max number of days apart two dates can be and still be considered a possible match")
    parser.add_argument("--amount-tolerance", type=float, default=10.00,
                         help="Max absolute amount difference (in currency units) allowed for a possible match")
    parser.add_argument("--match-threshold", type=float, default=0.75,
                         help="Combined score (0-1) at or above which a pair is a CONFIDENT match")
    parser.add_argument("--review-threshold", type=float, default=0.55,
                         help="Combined score (0-1) at or above which a pair is flagged for human REVIEW (below match-threshold)")

    parser.add_argument("--output-dir", default="output", help="Folder to write result CSVs into")

    args = parser.parse_args()

    if args.review_threshold > args.match_threshold:
        print("ERROR: --review-threshold cannot be greater than --match-threshold.")
        sys.exit(1)

    print(f"Loading left file:  {args.left}")
    left_rows = load_rows(args.left, args.left_name_col, args.left_date_col, args.left_amount_col)
    print(f"  -> {len(left_rows)} usable rows")

    print(f"Loading right file: {args.right}")
    right_rows = load_rows(args.right, args.right_name_col, args.right_date_col, args.right_amount_col)
    print(f"  -> {len(right_rows)} usable rows")

    if not left_rows or not right_rows:
        print("ERROR: One of the input files has no usable rows. Check column names with --left-name-col etc.")
        sys.exit(1)

    print("\nMatching rows (this compares every row in --left against every row in --right)...")
    matched, review, unmatched_left, unmatched_right = reconcile(
        left_rows, right_rows,
        date_tolerance_days=args.date_tolerance_days,
        amount_tolerance=args.amount_tolerance,
        match_threshold=args.match_threshold,
        review_threshold=args.review_threshold,
    )

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    left_cols = list(left_rows[0]["_raw"].keys())
    right_cols = list(right_rows[0]["_raw"].keys())

    write_match_csv(out_dir / "matched.csv", matched, left_cols, right_cols)
    write_match_csv(out_dir / "review.csv", review, left_cols, right_cols)
    write_plain_csv(out_dir / "unmatched_left.csv", unmatched_left, left_cols)
    write_plain_csv(out_dir / "unmatched_right.csv", unmatched_right, right_cols)

    print("\n" + "=" * 60)
    print("RECONCILIATION SUMMARY")
    print("=" * 60)
    print(f"Left file rows:              {len(left_rows)}")
    print(f"Right file rows:             {len(right_rows)}")
    print(f"Confident matches:           {len(matched)}   -> {out_dir / 'matched.csv'}")
    print(f"Needs human review:          {len(review)}   -> {out_dir / 'review.csv'}")
    print(f"Unmatched (left, no match):  {len(unmatched_left)}   -> {out_dir / 'unmatched_left.csv'}")
    print(f"Unmatched (right, no match): {len(unmatched_right)}   -> {out_dir / 'unmatched_right.csv'}")
    print("=" * 60)


if __name__ == "__main__":
    main()

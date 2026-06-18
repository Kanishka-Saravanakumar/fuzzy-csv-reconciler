"""
generate_sample_data.py

Generates two CSVs that simulate a real-world reconciliation scenario:
  - invoices.csv          -> what your accounting/invoicing system says
  - bank_statement.csv    -> what your bank statement actually shows

The two files describe mostly the SAME underlying transactions, but on
purpose introduce the kind of real-world messiness that breaks naive
exact-match reconciliation:
  - Slightly different name formatting ("Jonathan Smith" vs "J. SMITH")
  - Dates off by 1-3 days (bank clearing delay)
  - Amounts off by a small amount (bank fees / currency rounding)
  - A few invoices with NO matching bank transaction (payment never arrived)
  - A few bank transactions with NO matching invoice (unexpected/unknown deposits)

Run:
    python3 generate_sample_data.py
"""

import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

random.seed(42)  # reproducible output every time this is run

CLIENTS = [
    "Jonathan Smith Consulting",
    "Maria Garcia Designs",
    "Acme Robotics LLC",
    "Priya Patel Studio",
    "Chen & Associates",
    "Bluewave Marketing Inc",
    "David Okafor Freelance",
    "Summit Legal Partners",
    "Lena Müller Photography",
    "Riverstone Builders Co",
    "Yuki Tanaka Design",
    "Carlos Mendes Audio",
]

def messy_name(name):
    """Return a deliberately differently-formatted version of a name,
    the way a bank's system might mangle it."""
    variants = [
        name.upper(),
        name.replace(" ", "_").upper(),
        " ".join([p[0] + "." if i < len(name.split()) - 1 else p
                   for i, p in enumerate(name.split())]),
        name.split()[0][0] + " " + " ".join(name.split()[1:]),
        name.replace("&", "AND").upper(),
        name + " LLC" if "LLC" not in name and "Inc" not in name else name,
    ]
    return random.choice(variants)


def random_date(start, end):
    delta = end - start
    return start + timedelta(days=random.randint(0, delta.days))


def main():
    start_date = datetime(2026, 5, 1)
    end_date = datetime(2026, 5, 31)

    invoices = []
    bank_rows = []

    invoice_id = 1000

    for client in CLIENTS:
        # Most clients get 1-2 invoices in the month
        for _ in range(random.choice([1, 1, 2])):
            invoice_id += 1
            invoice_date = random_date(start_date, end_date)
            amount = round(random.uniform(150, 5000), 2)

            invoices.append({
                "invoice_id": f"INV-{invoice_id}",
                "client_name": client,
                "invoice_date": invoice_date.strftime("%Y-%m-%d"),
                "amount": f"{amount:.2f}",
            })

            # Decide what happens to this invoice in the bank data
            roll = random.random()
            if roll < 0.08:
                # 8% chance: payment never arrived - no bank row at all
                continue

            # Otherwise create a corresponding (messy) bank transaction
            bank_date = invoice_date + timedelta(days=random.randint(0, 3))
            fee = round(random.choice([0, 0, 0, 0.50, 1.00, 2.50, amount * 0.029]), 2)
            bank_amount = round(amount - fee, 2)

            bank_rows.append({
                "transaction_date": bank_date.strftime("%Y-%m-%d"),
                "description": messy_name(client),
                "amount": f"{bank_amount:.2f}",
            })

    # Add a few unexplained bank deposits with no matching invoice at all
    for _ in range(3):
        bank_rows.append({
            "transaction_date": random_date(start_date, end_date).strftime("%Y-%m-%d"),
            "description": messy_name(random.choice([
                "Unknown Wire Transfer", "Refund Adjustment Co", "Partner Revenue Share"
            ])),
            "amount": f"{round(random.uniform(50, 800), 2):.2f}",
        })

    random.shuffle(bank_rows)
    random.shuffle(invoices)

    Path("sample_data").mkdir(parents=True, exist_ok=True)

    with open("sample_data/invoices.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["invoice_id", "client_name", "invoice_date", "amount"])
        writer.writeheader()
        writer.writerows(invoices)

    with open("sample_data/bank_statement.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["transaction_date", "description", "amount"])
        writer.writeheader()
        writer.writerows(bank_rows)

    print(f"Generated {len(invoices)} invoices -> sample_data/invoices.csv")
    print(f"Generated {len(bank_rows)} bank transactions -> sample_data/bank_statement.csv")


if __name__ == "__main__":
    main()

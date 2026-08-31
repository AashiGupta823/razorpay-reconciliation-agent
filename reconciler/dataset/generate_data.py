"""
generate_data.py — produces two synthetic, intentionally-mismatched sources
to reconcile:
  - dataset/bank_statement.csv   (what the payment processor/bank reports)
  - dataset/internal_ledger.csv  (what our own system recorded)

Deterministic (fixed seed) so results are reproducible for the judge.

Injected exception types (documented, not hidden):
  1. missing_in_ledger   — bank has it, our system never recorded it
  2. missing_in_bank     — our system has it, bank never settled it
  3. amount_mismatch     — same transaction, amount differs (fee/rounding)
  4. date_shift          — same transaction, settlement date differs by 2-5 days
  5. duplicate_charge    — bank double-charged, ledger has it once
  6. narration_drift     — same transaction but description text differs
                           (needs semantic matching, not exact match)
  7. coincidental_no_match — same merchant, similar amount/date by pure
                           coincidence, but genuinely different transactions
                           (tests that the AI verifier says "no" correctly)
"""
import csv
import random
from pathlib import Path

random.seed(42)

OUT = Path(__file__).parent
N_CLEAN = 42          # clean, perfectly matching transactions
N_EXCEPTIONS_EACH = 3  # per exception type (6 types) -> 18 exception txns
MERCHANTS = ["Zylo Mart", "Kettle & Co", "Urban Threads", "Fresh Cart",
             "Nimbus Books", "Trailhead Gear", "Corner Bakery", "Wavelength Audio"]
NARRATION_VARIANTS = {
    "clean": "{merchant} order settlement",
}

def rand_amount():
    return round(random.uniform(199, 24999), 2)

def rand_date(base_day):
    return f"2026-08-{base_day:02d}"

def make_txn(i, day):
    return {
        "txn_id": f"TXN{10000+i}",
        "date": rand_date(day),
        "amount": rand_amount(),
        "merchant": random.choice(MERCHANTS),
    }

def main():
    bank_rows = []
    ledger_rows = []
    i = 0
    day_cycle = list(range(1, 29))

    # --- clean matches ---
    for _ in range(N_CLEAN):
        i += 1
        day = random.choice(day_cycle)
        t = make_txn(i, day)
        narration = f"{t['merchant']} order settlement"
        bank_rows.append({**t, "narration": narration})
        ledger_rows.append({**t, "narration": narration})

    # --- 1. missing_in_ledger ---
    for _ in range(N_EXCEPTIONS_EACH):
        i += 1
        day = random.choice(day_cycle)
        t = make_txn(i, day)
        bank_rows.append({**t, "narration": f"{t['merchant']} order settlement"})
        # not added to ledger_rows at all

    # --- 2. missing_in_bank ---
    for _ in range(N_EXCEPTIONS_EACH):
        i += 1
        day = random.choice(day_cycle)
        t = make_txn(i, day)
        ledger_rows.append({**t, "narration": f"{t['merchant']} order settlement"})
        # not added to bank_rows at all

    # --- 3. amount_mismatch (bank deducted a processing fee; ledger uses its
    #        own order ID, not the bank's reference — exact match won't work,
    #        forcing the fuzzy pass to earn its keep) ---
    for _ in range(N_EXCEPTIONS_EACH):
        i += 1
        day = random.choice(day_cycle)
        t = make_txn(i, day)
        narration = f"{t['merchant']} order settlement"
        bank_amt = round(t["amount"] - random.uniform(5, 35), 2)  # fee deducted
        bank_rows.append({**t, "amount": bank_amt, "narration": narration})
        ledger_rows.append({**t, "txn_id": f"ORD-{2000+i}", "narration": narration})

    # --- 4. date_shift (settlement lag; again a different ledger-side ID) ---
    for _ in range(N_EXCEPTIONS_EACH):
        i += 1
        day = random.choice(day_cycle[:24])
        t = make_txn(i, day)
        narration = f"{t['merchant']} order settlement"
        shifted_day = min(day + random.randint(2, 5), 28)
        bank_rows.append({**t, "date": rand_date(shifted_day), "narration": narration})
        ledger_rows.append({**t, "txn_id": f"ORD-{2000+i}", "narration": narration})

    # --- 5. duplicate_charge (bank charged twice) ---
    for _ in range(N_EXCEPTIONS_EACH):
        i += 1
        day = random.choice(day_cycle)
        t = make_txn(i, day)
        narration = f"{t['merchant']} order settlement"
        bank_rows.append({**t, "narration": narration})
        bank_rows.append({**t, "txn_id": t["txn_id"] + "-DUP", "narration": narration})
        ledger_rows.append({**t, "narration": narration})

    # --- 6. narration_drift (different ledger-side ID AND a larger fee, so
    #        neither exact nor strict-tolerance fuzzy match fires — only the
    #        AI verifier's wider candidate search + narration reasoning
    #        resolves these) ---
    drift_templates = [
        "POS purchase - {merchant}",
        "{merchant} - payment received",
        "Card txn {merchant} settlement ref",
    ]
    for _ in range(N_EXCEPTIONS_EACH):
        i += 1
        day = random.choice(day_cycle)
        t = make_txn(i, day)
        bank_narration = random.choice(drift_templates).format(merchant=t["merchant"])
        ledger_narration = f"{t['merchant']} order settlement"
        bank_amt = round(t["amount"] - random.uniform(60, 140), 2)  # bigger convenience fee
        bank_rows.append({**t, "amount": bank_amt, "narration": bank_narration})
        ledger_rows.append({**t, "txn_id": f"ORD-{3000+i}", "narration": ledger_narration})

    # --- 7. coincidental_no_match — same merchant, plausible amount/date
    #        proximity by pure coincidence, but genuinely different
    #        transactions. Tests whether the AI verifier correctly says
    #        "no" instead of rubber-stamping every candidate. ---
    for _ in range(N_EXCEPTIONS_EACH):
        i += 1
        day = random.choice(day_cycle)
        merchant = random.choice(MERCHANTS)
        bank_t = make_txn(i, day)
        bank_t["merchant"] = merchant
        i += 1
        ledger_t = make_txn(i, day)
        ledger_t["merchant"] = merchant
        ledger_t["amount"] = round(bank_t["amount"] + random.uniform(55, 95), 2)
        bank_rows.append({**bank_t, "narration": f"{merchant} refund adjustment"})
        ledger_rows.append({**ledger_t, "txn_id": f"ORD-{4000+i}",
                             "narration": f"{merchant} subscription renewal"})


    random.shuffle(bank_rows)
    random.shuffle(ledger_rows)

    for name, rows in [("bank_statement.csv", bank_rows), ("internal_ledger.csv", ledger_rows)]:
        with open(OUT / name, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["txn_id", "date", "amount", "merchant", "narration"])
            w.writeheader()
            w.writerows(rows)

    print(f"bank_statement.csv: {len(bank_rows)} rows")
    print(f"internal_ledger.csv: {len(ledger_rows)} rows")


if __name__ == "__main__":
    main()

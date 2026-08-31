"""
matcher.py — deterministic reconciliation core.

Two passes, cheapest/most-certain first:
  Pass 1 (exact):  match on txn_id directly present in both sources.
  Pass 2 (fuzzy):  for the leftovers, match on (merchant, amount within
                    tolerance, date within a day window) — catches fee
                    deductions and settlement-date lag without needing AI.

Anything still unmatched after both passes is handed to the AI verifier
(ai_verifier.py) for a semantic judgment call, or reported as a hard
exception if no verifier is available.

Every match records *why* it matched (reason code) — nothing is a black box.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Optional


AMOUNT_TOLERANCE = 40.0     # rupees — covers small processor fees only
DATE_WINDOW_DAYS = 6        # settlement lag tolerance


@dataclass
class Txn:
    txn_id: str
    date: date
    amount: float
    merchant: str
    narration: str
    source: str  # "bank" or "ledger"


@dataclass
class MatchResult:
    bank_txn: Optional[Txn]
    ledger_txn: Optional[Txn]
    status: str          # "matched" | "exception"
    reason: str           # human-readable reason code
    confidence: float     # 0-1
    pass_name: str         # "exact" | "fuzzy" | "ai_verifier" | "unresolved"


def load_csv(path: Path, source: str) -> list[Txn]:
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            y, m, d = map(int, r["date"].split("-"))
            rows.append(Txn(
                txn_id=r["txn_id"], date=date(y, m, d),
                amount=float(r["amount"]), merchant=r["merchant"],
                narration=r["narration"], source=source,
            ))
    return rows


def _fuzzy_candidate(b: Txn, ledger_pool: list[Txn]) -> Optional[Txn]:
    best, best_score = None, -1.0
    for l in ledger_pool:
        if l.merchant != b.merchant:
            continue
        if abs((l.date - b.date).days) > DATE_WINDOW_DAYS:
            continue
        if abs(l.amount - b.amount) > AMOUNT_TOLERANCE:
            continue
        # score: closer amount + closer date = better
        score = 1.0 - (abs(l.amount - b.amount) / (AMOUNT_TOLERANCE + 1)) * 0.5 \
                     - (abs((l.date - b.date).days) / (DATE_WINDOW_DAYS + 1)) * 0.5
        if score > best_score:
            best, best_score = l, score
    return best


def reconcile(bank: list[Txn], ledger: list[Txn]) -> list[MatchResult]:
    results: list[MatchResult] = []
    ledger_by_id: dict[str, list[Txn]] = {}
    for l in ledger:
        ledger_by_id.setdefault(l.txn_id, []).append(l)

    unmatched_bank: list[Txn] = []
    used_ledger_ids: set[int] = set()

    # --- Pass 1: exact txn_id match ---
    for b in bank:
        candidates = [l for l in ledger_by_id.get(b.txn_id, []) if id(l) not in used_ledger_ids]
        if candidates:
            l = candidates[0]
            used_ledger_ids.add(id(l))
            results.append(MatchResult(b, l, "matched", "exact txn_id match", 1.0, "exact"))
        else:
            unmatched_bank.append(b)

    ledger_remaining = [l for l in ledger if id(l) not in used_ledger_ids]

    # --- Pass 2: fuzzy (merchant + amount tolerance + date window) ---
    still_unmatched_bank: list[Txn] = []
    for b in unmatched_bank:
        pool = [l for l in ledger_remaining if id(l) not in used_ledger_ids]
        match = _fuzzy_candidate(b, pool)
        if match:
            used_ledger_ids.add(id(match))
            amt_diff = round(b.amount - match.amount, 2)
            reason = f"fuzzy match (merchant+date window, amount diff \u20b9{amt_diff})" if amt_diff \
                else "fuzzy match (merchant+date window)"
            results.append(MatchResult(b, match, "matched", reason, 0.8, "fuzzy"))
        else:
            still_unmatched_bank.append(b)

    unresolved_ledger = [l for l in ledger if id(l) not in used_ledger_ids]

    return results, still_unmatched_bank, unresolved_ledger

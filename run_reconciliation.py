"""
run_reconciliation.py — orchestrates the full loop and produces the report
the Buildathon brief asks for: match rate + an honest exception list.

Pipeline:
  1. Load both sources.
  2. Deterministic pass (exact txn_id, then fuzzy merchant+amount+date) —
     handles the large majority with zero AI cost and full certainty.
  3. Whatever's still unmatched goes through the AI verifier, but ONLY
     against candidates that already share merchant + are within a loose
     amount/date band — the AI breaks narration-text ties, it doesn't
     search blind.
  4. Anything the AI verifier itself isn't confident about (or that has
     no plausible candidate at all) is reported as a genuine exception —
     the "honest exception list" the brief explicitly asks for.

Run: python run_reconciliation.py
Output: reconciliation_report.json + a printed summary table.
"""
from __future__ import annotations

import json
from pathlib import Path

from reconciler.matcher import load_csv, reconcile, AMOUNT_TOLERANCE, DATE_WINDOW_DAYS
from reconciler.ai_verifier import verify

DATA_DIR = Path(__file__).parent / "dataset"


def find_narration_candidates(b, ledger_pool, wide_amount_tol=200, wide_date_window=8):
    """Looser pre-filter than matcher.py's fuzzy pass, used only to hand the
    AI verifier a *short, plausible* candidate list — never the full ledger."""
    out = []
    for l in ledger_pool:
        if l.merchant != b.merchant:
            continue
        if abs((l.date - b.date).days) > wide_date_window:
            continue
        if abs(l.amount - b.amount) > wide_amount_tol:
            continue
        out.append(l)
    return out


def main():
    bank = load_csv(DATA_DIR / "bank_statement.csv", "bank")
    ledger = load_csv(DATA_DIR / "internal_ledger.csv", "ledger")

    results, unmatched_bank, unresolved_ledger = reconcile(bank, ledger)

    ai_calls = 0
    exceptions = []

    # --- Pass 3: AI verifier on remaining unmatched bank txns ---
    still_unmatched = []
    for b in unmatched_bank:
        candidates = find_narration_candidates(b, unresolved_ledger)
        if not candidates:
            exceptions.append({
                "txn_id": b.txn_id, "source": "bank", "amount": b.amount,
                "merchant": b.merchant, "date": str(b.date), "narration": b.narration,
                "reason": "no plausible counterpart in internal ledger",
                "type": "missing_in_ledger",
            })
            continue

        matched = False
        for cand in candidates:
            ai_calls += 1
            v = verify(b.narration, cand.narration, b.merchant, b.amount)
            if v.is_match and v.confidence >= 0.7:
                results.append(type(results[0])(  # reuse MatchResult class via existing instance
                    bank_txn=b, ledger_txn=cand, status="matched",
                    reason=f"AI-verified narration match ({v.method}): {v.rationale}",
                    confidence=v.confidence, pass_name="ai_verifier",
                ))
                unresolved_ledger.remove(cand)
                matched = True
                break

        if not matched:
            exceptions.append({
                "txn_id": b.txn_id, "source": "bank", "amount": b.amount,
                "merchant": b.merchant, "date": str(b.date), "narration": b.narration,
                "reason": "candidate(s) found but AI verifier confidence below threshold",
                "type": "unresolved_ambiguous",
            })

    # --- Anything left in the ledger with no bank counterpart at all ---
    for l in unresolved_ledger:
        exceptions.append({
            "txn_id": l.txn_id, "source": "ledger", "amount": l.amount,
            "merchant": l.merchant, "date": str(l.date), "narration": l.narration,
            "reason": "no counterpart found in bank statement",
            "type": "missing_in_bank",
        })

    total_bank_txns = len(bank)
    matched_count = sum(1 for r in results if r.status == "matched")
    match_rate = round(matched_count / total_bank_txns * 100, 1)

    report = {
        "summary": {
            "total_bank_transactions": total_bank_txns,
            "total_ledger_transactions": len(ledger),
            "matched": matched_count,
            "match_rate_pct": match_rate,
            "exceptions": len(exceptions),
            "breakdown_by_pass": {
                p: sum(1 for r in results if r.pass_name == p)
                for p in ("exact", "fuzzy", "ai_verifier")
            },
            "ai_verifier_calls_made": ai_calls,
        },
        "exceptions": exceptions,
    }

    out_path = Path(__file__).parent / "reconciliation_report.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("=" * 60)
    print("RECONCILIATION REPORT")
    print("=" * 60)
    print(f"Bank transactions:     {total_bank_txns}")
    print(f"Ledger transactions:   {len(ledger)}")
    print(f"Matched:               {matched_count}  ({match_rate}%)")
    print(f"Exceptions:            {len(exceptions)}")
    print(f"  - exact match:       {report['summary']['breakdown_by_pass']['exact']}")
    print(f"  - fuzzy match:       {report['summary']['breakdown_by_pass']['fuzzy']}")
    print(f"  - AI-verified match: {report['summary']['breakdown_by_pass']['ai_verifier']}")
    print(f"AI verifier calls:     {ai_calls}")
    print()
    print("Exception breakdown by type:")
    by_type = {}
    for e in exceptions:
        by_type[e["type"]] = by_type.get(e["type"], 0) + 1
    for t, c in sorted(by_type.items()):
        print(f"  - {t}: {c}")
    print()
    print(f"Full report written to {out_path}")


if __name__ == "__main__":
    main()

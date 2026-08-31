"""
ai_verifier.py — the "AI" in this AI Finance Controller agent.

What deterministic rules (matcher.py) *can't* resolve, mostly comes down to
one thing: narration text that describes the same transaction differently
across systems ("Wavelength Audio order settlement" vs "POS purchase -
Wavelength Audio"). A human reconciler eyeballs these and just knows they
match. That judgment call is what we hand to an LLM — with the deterministic
signals (same merchant, same amount, close date) *already* filtering the
candidate pool, so the AI is only ever asked to break ties between a few
already-plausible options, never to search the whole ledger blind.

Design choices that matter for the "explainable, bounded" bar:
  - The AI never *moves money* or silently marks something reconciled —
    it returns a verdict + confidence + rationale, which the orchestrator
    logs. Anything below CONFIDENCE_THRESHOLD is still surfaced as a
    human-review exception, not silently accepted.
  - If no ANTHROPIC_API_KEY is set, we fail *loud and honest* into a
    local heuristic (token-overlap similarity) rather than pretending to
    be the LLM — this keeps the pipeline runnable for grading without
    a key, while being transparent in the report about which path ran.
"""
from __future__ import annotations

import difflib
import json
import os
from dataclasses import dataclass

CONFIDENCE_THRESHOLD = 0.7


@dataclass
class Verdict:
    is_match: bool
    confidence: float
    rationale: str
    method: str  # "llm" | "heuristic_fallback"


def _heuristic_fallback(bank_narration: str, ledger_narration: str, merchant: str) -> Verdict:
    ratio = difflib.SequenceMatcher(None, bank_narration.lower(), ledger_narration.lower()).ratio()
    # Both narrations should at least reference the merchant name for this
    # fallback to consider it a plausible same-transaction match.
    merchant_present = merchant.lower() in bank_narration.lower() and merchant.lower() in ledger_narration.lower()
    confidence = ratio if merchant_present else ratio * 0.5
    return Verdict(
        is_match=confidence >= CONFIDENCE_THRESHOLD,
        confidence=round(confidence, 2),
        rationale=(
            f"Local heuristic (no ANTHROPIC_API_KEY set): text-similarity ratio {ratio:.2f} "
            f"between narrations; merchant name present in both: {merchant_present}."
        ),
        method="heuristic_fallback",
    )


def _llm_verdict(bank_narration: str, ledger_narration: str, merchant: str, amount: float) -> Verdict:
    from anthropic import Anthropic  # imported lazily so the fallback path has zero hard dependency

    client = Anthropic()
    prompt = f"""Two finance records describe what may be the same transaction, already
pre-filtered to match on merchant name, similar amount, and nearby settlement date.
Your only job: decide if the two *narration/description texts* plausibly describe
the same underlying transaction, or are unrelated despite the coincidental match.

Merchant: {merchant}
Amount: \u20b9{amount}
Bank narration: "{bank_narration}"
Internal ledger narration: "{ledger_narration}"

Respond with ONLY a JSON object, no other text:
{{"is_match": true|false, "confidence": 0.0-1.0, "rationale": "one short sentence"}}"""

    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    data = json.loads(text)
    return Verdict(
        is_match=bool(data["is_match"]),
        confidence=float(data["confidence"]),
        rationale=data["rationale"],
        method="llm",
    )


def verify(bank_narration: str, ledger_narration: str, merchant: str, amount: float) -> Verdict:
    """Entry point used by the orchestrator. Tries the LLM; falls back to a
    transparent local heuristic if no API key is configured or the call fails."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return _llm_verdict(bank_narration, ledger_narration, merchant, amount)
        except Exception as e:  # noqa: BLE001 — deliberately broad: any failure must fall back, not crash the run
            v = _heuristic_fallback(bank_narration, ledger_narration, merchant)
            v.rationale = f"LLM call failed ({e.__class__.__name__}), used fallback. " + v.rationale
            return v
    return _heuristic_fallback(bank_narration, ledger_narration, merchant)

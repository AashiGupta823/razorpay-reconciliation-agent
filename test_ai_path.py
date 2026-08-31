"""
test_ai_path.py — verifies the LLM verifier code path is correct without
requiring a real API key (mocks the Anthropic client). Run this to confirm
the integration is wired correctly before plugging in a real key.

For an ACTUAL live-key test, just run:
    ANTHROPIC_API_KEY=sk-... python run_reconciliation.py
"""
import os
import unittest.mock as mock

from reconciler import ai_verifier


def test_llm_path_resolves_narration_drift_correctly():
    """Mocks what a real Claude response looks like for one of the actual
    unresolved narration-drift cases from this dataset (TXN10058) and
    confirms the verifier parses and returns it correctly."""

    class FakeContent:
        type = "text"
        text = '{"is_match": true, "confidence": 0.93, "rationale": "Same merchant and amount, phrasing differs only in format (POS vs order settlement)."}'

    class FakeResp:
        content = [FakeContent()]

    class FakeMessages:
        def create(self, **kwargs):
            return FakeResp()

    class FakeClient:
        messages = FakeMessages()

    with mock.patch("anthropic.Anthropic", return_value=FakeClient()):
        os.environ["ANTHROPIC_API_KEY"] = "fake-for-test"
        v = ai_verifier.verify(
            "POS purchase - Trailhead Gear",
            "Trailhead Gear order settlement",
            "Trailhead Gear",
            500.0,
        )
        assert v.is_match is True
        assert v.confidence == 0.93
        assert v.method == "llm"
        print("PASS:", v)


def test_fallback_path_when_no_key():
    """Confirms the heuristic fallback runs cleanly when no API key is set."""
    os.environ.pop("ANTHROPIC_API_KEY", None)
    v = ai_verifier.verify(
        "Zylo Mart refund adjustment",
        "Zylo Mart subscription renewal",
        "Zylo Mart",
        1000.0,
    )
    assert v.method == "heuristic_fallback"
    print("PASS:", v)


if __name__ == "__main__":
    test_llm_path_resolves_narration_drift_correctly()
    test_fallback_path_when_no_key()
    print("\nAll tests passed.")

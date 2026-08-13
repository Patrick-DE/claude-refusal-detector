"""Integration tests verifying full round-trip detection and live API calls."""

import os
import pytest

from refusal_detector.adapters import FakeOracleAdapter
from refusal_detector.config import Config
from refusal_detector.service import RefusalDetector


def test_known_trigger_detect_unblock_roundtrip():
    """End-to-end pipeline test: prompt with known trigger -> detect() -> isolates exact segment -> removal unblocks prompt."""
    prompt = (
        "Line 1: Safe context about project design.\n"
        "Line 2: DANGEROUS_TRIGGER_STRING_PAYLOAD\n"
        "Line 3: Additional safe documentation text."
    )

    fake_oracle = FakeOracleAdapter(triggers=["DANGEROUS_TRIGGER_STRING_PAYLOAD"])
    detector = RefusalDetector(oracle=fake_oracle)

    # 1. Detect minimal trigger
    report = detector.detect(prompt)

    # 2. Assert exact segment is isolated
    assert len(report.trigger_segments) == 1
    assert report.trigger_segments[0].index == 1
    assert "DANGEROUS_TRIGGER_STRING_PAYLOAD" in report.trigger_text

    # 3. Assert removing trigger unblocks request
    assert report.is_necessary is True

    # 4. Assert report output includes diff, trigger, and reason class
    rendered = detector.render_report(report)
    assert "Status: Trigger Isolated" in rendered
    assert "DANGEROUS_TRIGGER_STRING_PAYLOAD" in rendered
    assert "- Line 2: DANGEROUS_TRIGGER_STRING_PAYLOAD" in rendered


@pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY environment variable not set",
)
def test_anthropic_api_real_roundtrip():
    # Model comes from config, not hardcoded: pinning a specific id here made this test
    # 404 the moment that model was retired, and the failure only surfaced once an API key
    # existed to un-skip the test.
    config = Config.from_env(provider="anthropic")
    detector = RefusalDetector(config=config)

    prompt = "Hello Claude! Can you confirm this safe text is received?"
    verdict = detector.check(prompt)
    assert verdict.blocked is False


@pytest.mark.skipif(
    not os.getenv("DEEPSEEK_API_KEY"),
    reason="DEEPSEEK_API_KEY environment variable not set",
)
def test_deepseek_api_real_roundtrip():
    config = Config.from_env(provider="deepseek", model="deepseek-chat")
    detector = RefusalDetector(config=config)

    prompt = "Hello DeepSeek! Can you confirm this safe text is received?"
    verdict = detector.check(prompt)
    assert verdict.blocked is False

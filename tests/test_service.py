"""Unit tests for RefusalDetector service."""

from refusal_detector.adapters import FakeOracleAdapter
from refusal_detector.config import Config
from refusal_detector.ports import ReasonClass
from refusal_detector.service import RefusalDetector


def test_service_detect_with_fake_oracle():
    prompt = "Line 1: Safe\nLine 2: BAD_WORD payload\nLine 3: Safe"
    fake_oracle = FakeOracleAdapter(triggers=["BAD_WORD"])

    config = Config(split_mode="lines")
    detector = RefusalDetector(oracle=fake_oracle, config=config)

    report = detector.detect(prompt)

    assert len(report.trigger_segments) == 1
    assert "BAD_WORD" in report.trigger_text
    assert report.reason_class == ReasonClass.STRUCTURED_REFUSAL
    assert report.is_necessary is True
    assert report.total_calls >= 1

    rendered = detector.render_report(report)
    assert "Claude Refusal Detector" in rendered
    assert "Minimal Trigger Content" in rendered
    assert "BAD_WORD" in rendered


def test_service_check_with_fake_oracle():
    fake_oracle = FakeOracleAdapter(triggers=["REFUSE_THIS"])
    detector = RefusalDetector(oracle=fake_oracle)

    verdict_blocked = detector.check("Contains REFUSE_THIS in prompt")
    assert verdict_blocked.blocked is True
    assert verdict_blocked.reason_class == ReasonClass.STRUCTURED_REFUSAL

    verdict_clean = detector.check("Completely clean prompt")
    assert verdict_clean.blocked is False
    assert verdict_clean.reason_class == ReasonClass.NOT_BLOCKED

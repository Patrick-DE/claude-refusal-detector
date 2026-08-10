"""Unit tests for Claude plugin refusal hook."""

import json
from unittest.mock import patch

from refusal_detector.hooks.refusal_hook import process_hook_payload


def test_process_hook_payload_refusal_auto_trigger():
    payload = {
        "userPrompt": "Line 1: Safe\nLine 2: DANGEROUS_WORD payload",
        "lastResponse": "I'm sorry, but I cannot assist with that request.",
        "force_detect": True,
    }

    with patch("refusal_detector.hooks.refusal_hook.RefusalDetector") as mock_detector_cls:
        instance = mock_detector_cls.return_value
        instance.detect.return_value = "MockReport"
        instance.render_report.return_value = "# Auto-Trigger Diagnostic Report"

        result = process_hook_payload(payload)

        assert "injectSteps" in result
        assert len(result["injectSteps"]) == 1
        assert "# Auto-Trigger Diagnostic Report" in result["injectSteps"][0]["ephemeralMessage"]


def test_process_hook_payload_unblocked_pass_through():
    payload = {
        "userPrompt": "What is the capital of France?",
        "lastResponse": "The capital of France is Paris.",
    }

    result = process_hook_payload(payload)
    assert result == {}

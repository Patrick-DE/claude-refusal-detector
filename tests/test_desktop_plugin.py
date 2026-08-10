"""Unit tests for Desktop MCP Plugin."""

from unittest.mock import patch

from refusal_detector.desktop_plugin import detect_refusal_trigger


def test_detect_refusal_trigger_mcp_tool():
    with patch("refusal_detector.desktop_plugin.RefusalDetector") as mock_service_cls:
        instance = mock_service_cls.return_value
        instance.detect.return_value = "MockReport"
        instance.render_report.return_value = "# MCP Diagnostic Report"

        result = detect_refusal_trigger("Test blocked prompt text")
        assert "# MCP Diagnostic Report" in result

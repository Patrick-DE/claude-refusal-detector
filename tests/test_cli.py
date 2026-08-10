"""Unit tests for CLI interface."""

from unittest.mock import patch

from refusal_detector import cli
from refusal_detector.adapters import FakeOracleAdapter
from refusal_detector.ports import ReasonClass, Verdict


def test_cli_check_command(capsys):
    fake_oracle = FakeOracleAdapter(triggers=["DANGER"])
    with patch("refusal_detector.cli.RefusalDetector") as mock_service_cls:
        instance = mock_service_cls.return_value
        instance.check.return_value = Verdict(blocked=True, reason_class=ReasonClass.STRUCTURED_REFUSAL, details="Trigger found")

        exit_code = cli.main(["check", "Test prompt with DANGER"])
        assert exit_code == 1

        captured = capsys.readouterr()
        assert "Blocked: True" in captured.out
        assert "Reason Class: structured_refusal" in captured.out


def test_cli_detect_command(capsys, tmp_path):
    out_file = tmp_path / "report.md"
    with patch("refusal_detector.cli.RefusalDetector") as mock_service_cls:
        instance = mock_service_cls.return_value
        instance.detect.return_value = "MockReport"
        instance.render_report.return_value = "# Mock Report Output"

        exit_code = cli.main(["detect", "Test prompt", "--out", str(out_file)])
        assert exit_code == 0
        assert out_file.read_text(encoding="utf-8") == "# Mock Report Output"


def test_cli_exit_codes():
    """Exit code semantics: 0 = success/not blocked, 1 = blocked (check), 2 = error."""
    with patch("refusal_detector.cli.RefusalDetector") as mock_service_cls:
        instance = mock_service_cls.return_value
        instance.check.return_value = Verdict(blocked=False, reason_class=ReasonClass.NOT_BLOCKED)
        assert cli.main(["check", "Safe prompt"]) == 0

        instance.check.return_value = Verdict(blocked=True, reason_class=ReasonClass.STRUCTURED_REFUSAL)
        assert cli.main(["check", "Blocked prompt"]) == 1

        instance.check.side_effect = RuntimeError("boom")
        assert cli.main(["check", "Anything"]) == 2

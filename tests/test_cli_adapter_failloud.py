"""A failed CLI call must never be reported as 'not blocked'."""

from unittest.mock import MagicMock, patch

import pytest

from refusal_detector.adapters import ClaudeCodeCLIAdapter


def _run_with(returncode: int, stdout: str = "", stderr: str = ""):
    result = MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)
    return patch("refusal_detector.adapters.subprocess.run", return_value=result)


def test_not_logged_in_raises_instead_of_reporting_not_blocked():
    """The exact real-world output that silently produced false negatives."""
    adapter = ClaudeCodeCLIAdapter(timeout=5)
    with _run_with(1, stdout="Not logged in 00b7 Please run /login\n"):
        with pytest.raises(RuntimeError) as excinfo:
            adapter.test("anything")
    assert "not logged in" in str(excinfo.value).lower()


def test_generic_nonzero_exit_raises():
    adapter = ClaudeCodeCLIAdapter(timeout=5)
    with _run_with(2, stderr="some unexpected failure"):
        with pytest.raises(RuntimeError):
            adapter.test("anything")


def test_moderation_block_on_nonzero_exit_is_still_a_verdict():
    """A genuine block must stay a Verdict, not become an exception."""
    adapter = ClaudeCodeCLIAdapter(timeout=5)
    with _run_with(1, stdout="Request was blocked by content filters"):
        verdict = adapter.test("anything")
    assert verdict.blocked is True


def test_successful_call_still_classifies_normally():
    adapter = ClaudeCodeCLIAdapter(timeout=5)
    with _run_with(0, stdout="Here is a perfectly ordinary answer."):
        verdict = adapter.test("anything")
    assert verdict.blocked is False


def test_failure_message_is_not_double_wrapped():
    """The catch-all handler must not re-wrap a deliberately raised RuntimeError."""
    adapter = ClaudeCodeCLIAdapter(timeout=5)
    with _run_with(1, stdout="Not logged in 00b7 Please run /login\n"):
        with pytest.raises(RuntimeError) as excinfo:
            adapter.test("anything")
    message = str(excinfo.value)
    assert message.count("Claude CLI") == 1, f"message was wrapped more than once: {message}"
    assert "execution failed:" not in message

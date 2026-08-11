"""Preflight: refuse to probe when the CLI cannot authenticate."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from refusal_detector.cli_auth import CliNotAuthenticatedError, check_cli_auth


def _status(stdout: str, returncode: int = 0):
    return patch(
        "refusal_detector.cli_auth.subprocess.run",
        return_value=MagicMock(returncode=returncode, stdout=stdout, stderr=""),
    )


def test_logged_out_raises_with_setup_instructions():
    """The exact shape `claude auth status` really returns when logged out."""
    with _status('{"loggedIn": false, "authMethod": "none", "apiProvider": "firstParty"}'):
        with pytest.raises(CliNotAuthenticatedError) as excinfo:
            check_cli_auth()
    message = str(excinfo.value)
    assert "claude setup-token" in message, "must tell the user how to fix it"


def test_logged_in_passes_silently():
    with _status('{"loggedIn": true, "authMethod": "oauth", "apiProvider": "firstParty"}'):
        assert check_cli_auth() is None


def test_unparseable_status_raises_rather_than_assuming_success():
    with _status("not json at all"):
        with pytest.raises(CliNotAuthenticatedError):
            check_cli_auth()


def test_status_command_failure_raises_with_setup_instructions():
    """The branch a real logged-out CLI actually takes: exit 1, plus logged-out JSON."""
    with _status('{"loggedIn": false, "authMethod": "none"}', returncode=1):
        with pytest.raises(CliNotAuthenticatedError) as excinfo:
            check_cli_auth()
    assert "claude setup-token" in str(excinfo.value)


def test_noise_before_the_json_is_tolerated():
    """The CLI prefixes unrelated permission warnings on some machines."""
    noisy = 'Permission deny rule (...): ignore me\n{"loggedIn": true, "authMethod": "oauth"}'
    with _status(noisy):
        assert check_cli_auth() is None


def test_missing_binary_names_the_fix():
    with patch("refusal_detector.cli_auth.subprocess.run", side_effect=FileNotFoundError()):
        with pytest.raises(CliNotAuthenticatedError) as excinfo:
            check_cli_auth()
    assert "claude setup-token" in str(excinfo.value)


def test_timeout_names_the_fix():
    timeout_error = subprocess.TimeoutExpired(cmd="claude auth status", timeout=30)
    with patch("refusal_detector.cli_auth.subprocess.run", side_effect=timeout_error):
        with pytest.raises(CliNotAuthenticatedError) as excinfo:
            check_cli_auth()
    assert "claude setup-token" in str(excinfo.value)


def test_unexpected_oserror_still_becomes_the_documented_error():
    """Task 5 catches CliNotAuthenticatedError; a raw OSError would escape it."""
    with patch("refusal_detector.cli_auth.subprocess.run", side_effect=PermissionError("denied")):
        with pytest.raises(CliNotAuthenticatedError):
            check_cli_auth()

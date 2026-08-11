"""Preflight: refuse to probe when the CLI cannot authenticate."""

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


def test_status_command_failure_raises():
    with _status("", returncode=1):
        with pytest.raises(CliNotAuthenticatedError):
            check_cli_auth()


def test_noise_before_the_json_is_tolerated():
    """The CLI prefixes unrelated permission warnings on some machines."""
    noisy = 'Permission deny rule (...): ignore me\n{"loggedIn": true, "authMethod": "oauth"}'
    with _status(noisy):
        assert check_cli_auth() is None

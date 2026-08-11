"""Preflight check that the `claude` CLI can actually authenticate.

Probing while logged out is worse than not probing: the CLI exits non-zero, and any
diagnosis built on that is a false negative. This turns an invisible failure into a
message that names the fix.
"""

import json
import subprocess

from refusal_detector.logger import get_logger

logger = get_logger("cli_auth")

_SETUP_INSTRUCTIONS = (
    "The `claude` CLI is not authenticated, so refusal probes cannot run.\n"
    "Run `claude setup-token` (a long-lived token, which is what non-interactive "
    "hooks need), then set CLAUDE_CODE_OAUTH_TOKEN in the environment and restart "
    "Claude Code so the new value is inherited."
)


class CliNotAuthenticatedError(RuntimeError):
    """Raised when `claude auth status` does not confirm an authenticated session."""


def _extract_json(text: str) -> dict:
    """Parse the status JSON, tolerating unrelated warning lines printed before it."""
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object in output")
    return json.loads(text[start:])


def check_cli_auth(timeout: float = 30.0) -> None:
    """Raise CliNotAuthenticatedError unless the CLI reports a logged-in session."""
    try:
        result = subprocess.run(
            ["claude", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
    except FileNotFoundError as e:
        raise CliNotAuthenticatedError(
            f"Claude CLI binary 'claude' not found in PATH.\n{_SETUP_INSTRUCTIONS}"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise CliNotAuthenticatedError(
            f"`claude auth status` timed out after {timeout}s\n{_SETUP_INSTRUCTIONS}"
        ) from e
    except Exception as e:
        raise CliNotAuthenticatedError(
            f"Could not run `claude auth status`: {e}\n{_SETUP_INSTRUCTIONS}"
        ) from e

    if result.returncode != 0:
        raise CliNotAuthenticatedError(
            f"`claude auth status` exited {result.returncode}.\n{_SETUP_INSTRUCTIONS}"
        )

    try:
        status = _extract_json(result.stdout or "")
    except (ValueError, json.JSONDecodeError) as e:
        raise CliNotAuthenticatedError(
            f"Could not read `claude auth status` output.\n{_SETUP_INSTRUCTIONS}"
        ) from e

    if not status.get("loggedIn"):
        raise CliNotAuthenticatedError(
            f"Not logged in (authMethod={status.get('authMethod')!r}).\n{_SETUP_INSTRUCTIONS}"
        )

    logger.info("Claude CLI authenticated (authMethod=%s).", status.get("authMethod"))

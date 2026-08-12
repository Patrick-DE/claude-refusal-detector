"""The CLI call must hold ambient context fixed and inject pre-prompt material properly."""

from unittest.mock import MagicMock, patch

import pytest

from refusal_detector.system_prompt_adapter import SystemPromptCLIAdapter


def _run_with(returncode: int = 0, stdout: str = "ok", stderr: str = ""):
    return patch(
        "refusal_detector.system_prompt_adapter.subprocess.run",
        return_value=MagicMock(returncode=returncode, stdout=stdout, stderr=stderr),
    )


def _adapter(**kwargs):
    adapter = SystemPromptCLIAdapter(timeout=30, model="claude-fable-5", **kwargs)
    adapter._auth_checked = True  # preflight is covered by its own tests
    return adapter


def test_ambient_context_is_held_fixed():
    """Without these flags the probe silently varies with the working directory."""
    with _run_with() as run:
        _adapter().test_with_system("hello")

    cmd = run.call_args.args[0]
    assert "--exclude-dynamic-system-prompt-sections" in cmd
    assert "--model" in cmd and cmd[cmd.index("--model") + 1] == "claude-fable-5"


def test_system_prompt_is_passed_by_file_not_argv():
    """Pre-prompt material can be huge; argv limits would truncate it silently."""
    with _run_with() as run:
        _adapter().test_with_system("hello", system_prompt="a rule\nanother rule")

    cmd = run.call_args.args[0]
    assert "--append-system-prompt-file" in cmd
    path = cmd[cmd.index("--append-system-prompt-file") + 1]
    assert path, "a file path must follow the flag"


def test_no_system_prompt_flag_when_there_is_none():
    with _run_with() as run:
        _adapter().test_with_system("hello", system_prompt="")

    assert "--append-system-prompt-file" not in run.call_args.args[0]


def test_prompt_goes_via_stdin_not_argv():
    """Shell-injection safety: the content under test is untrusted input."""
    with _run_with() as run:
        _adapter().test_with_system("untrusted & content | here")

    assert run.call_args.kwargs["input"] == "untrusted & content | here"
    assert run.call_args.kwargs["shell"] is False


def test_failed_call_raises_rather_than_reporting_not_blocked():
    with _run_with(returncode=1, stdout="Not logged in \u00b7 Please run /login"):
        with pytest.raises(RuntimeError):
            _adapter().test_with_system("hello")


def test_temp_system_prompt_file_is_cleaned_up():
    import glob
    import tempfile

    before = set(glob.glob(f"{tempfile.gettempdir()}/refusal_sysprompt_*"))
    with _run_with():
        _adapter().test_with_system("hello", system_prompt="a rule")
    after = set(glob.glob(f"{tempfile.gettempdir()}/refusal_sysprompt_*"))

    assert after == before, "temp system-prompt files must not accumulate"

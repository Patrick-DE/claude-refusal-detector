"""Unit tests for the Claude plugin Stop-event refusal hook."""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from refusal_detector.hooks.refusal_hook import (
    _REENTRANCY_GUARD_ENV_VAR,
    _extract_last_exchange,
    process_hook_payload,
    read_hook_payload,
)

HOOK_SCRIPT = Path(__file__).resolve().parents[1] / "src" / "refusal_detector" / "hooks" / "refusal_hook.py"


@pytest.fixture(autouse=True)
def _clear_reentrancy_guard():
    os.environ.pop(_REENTRANCY_GUARD_ENV_VAR, None)
    yield
    os.environ.pop(_REENTRANCY_GUARD_ENV_VAR, None)


def _write_transcript(tmp_path, records: list[dict]) -> str:
    transcript_path = tmp_path / "transcript.jsonl"
    with open(transcript_path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")
    return str(transcript_path)


def _simple_transcript(tmp_path, user_text: str, assistant_text: str) -> str:
    return _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": user_text}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "internal reasoning", "signature": "sig"},
                        {"type": "text", "text": assistant_text},
                    ],
                },
            },
        ],
    )


def test_process_hook_payload_refusal_auto_trigger(tmp_path):
    transcript_path = _simple_transcript(
        tmp_path,
        user_text="Line 1: Safe\nLine 2: DANGEROUS_WORD payload",
        assistant_text="I'm sorry, but I cannot assist with that request.",
    )
    payload = {
        "session_id": "abc123",
        "transcript_path": transcript_path,
        "cwd": str(tmp_path),
        "hook_event_name": "Stop",
        "reason": "end_turn",
    }

    with patch("refusal_detector.hooks.refusal_hook.RefusalDetector") as mock_detector_cls:
        instance = mock_detector_cls.return_value
        instance.detect.return_value = "MockReport"
        instance.render_report.return_value = "# Auto-Trigger Diagnostic Report"

        result = process_hook_payload(payload)

        assert "# Auto-Trigger Diagnostic Report" in result["systemMessage"]
        assert "pattern match" in result["systemMessage"], "report should say how the refusal was detected"
        instance.detect.assert_called_once_with("Line 1: Safe\nLine 2: DANGEROUS_WORD payload")


def test_process_hook_payload_unblocked_pass_through(tmp_path):
    transcript_path = _simple_transcript(
        tmp_path,
        user_text="What is the capital of France?",
        assistant_text="The capital of France is Paris.",
    )
    payload = {
        "session_id": "abc123",
        "transcript_path": transcript_path,
        "cwd": str(tmp_path),
        "hook_event_name": "Stop",
        "reason": "end_turn",
    }

    assert process_hook_payload(payload) == {}


def test_process_hook_payload_missing_transcript_path_is_a_noop():
    assert process_hook_payload({"session_id": "abc123", "hook_event_name": "Stop"}) == {}


def test_process_hook_payload_nonexistent_transcript_file_is_a_noop(tmp_path):
    payload = {"transcript_path": str(tmp_path / "does-not-exist.jsonl")}
    assert process_hook_payload(payload) == {}


def test_extract_last_exchange_skips_tool_result_user_records(tmp_path):
    transcript_path = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "Real prompt text"}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "file contents"}],
                },
            },
            {
                "type": "assistant",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "Final reply text."}]},
            },
        ],
    )

    user_prompt, assistant_reply = _extract_last_exchange(transcript_path)
    assert user_prompt == "Real prompt text"
    assert assistant_reply == "Final reply text."


class _NeverEndingStream:
    """Stands in for a stdin pipe that is never closed: read() blocks until told to stop."""

    def __init__(self) -> None:
        self.released = threading.Event()

    def isatty(self) -> bool:
        return False

    def read(self) -> str:
        self.released.wait(30)
        return "never delivered"


def test_read_hook_payload_gives_up_when_stdin_never_reaches_eof():
    stream = _NeverEndingStream()
    started = time.monotonic()

    result = read_hook_payload(stream, timeout=0.5)

    elapsed = time.monotonic() - started
    stream.released.set()
    assert result == ""
    assert elapsed < 5, f"read_hook_payload blocked for {elapsed:.1f}s instead of timing out"


def test_read_hook_payload_returns_piped_input():
    import io

    assert read_hook_payload(io.StringIO('{"hook_event_name": "Stop"}'), timeout=5) == '{"hook_event_name": "Stop"}'


def test_hook_process_exits_when_stdin_is_never_closed():
    """Regression: the deployed hook leaked one blocked process per Stop event.

    `proc.wait()` is used rather than `proc.communicate()` on purpose — communicate()
    closes stdin, which hands the child the EOF this test exists to withhold, and so
    passes whether or not the timeout is present.
    """
    proc = subprocess.Popen(
        [sys.executable, str(HOOK_SCRIPT)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        proc.wait(timeout=45)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        pytest.fail("hook process did not exit while stdin was held open")
    finally:
        if proc.poll() is None:
            proc.kill()

    assert proc.returncode == 0
    assert proc.stdout.read().strip() == "{}"
    proc.stdout.close()
    proc.stderr.close()
    proc.stdin.close()


def _refusal_fallback_record(category: str = "cyber") -> dict:
    """Mirrors Claude Code's real system record for an API-level refusal."""
    return {
        "type": "system",
        "subtype": "model_refusal_fallback",
        "level": "warning",
        "apiRefusalCategory": category,
        "apiRefusalExplanation": "This request triggered restrictions on violative content.",
        "originalModel": "claude-fable-5",
        "fallbackModel": "claude-opus-4-8",
        "content": "Safeguards flagged this message. Switched to Opus 4.8",
    }


def test_structured_api_refusal_is_detected_without_any_assistant_text(tmp_path):
    """The real-world case: a refused turn has no assistant prose at all, only a system record."""
    transcript_path = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "Audit this parser for overflows."}},
            {
                "type": "assistant",
                "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": "x", "signature": "s"}]},
            },
            {
                "type": "assistant",
                "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}]},
            },
            _refusal_fallback_record(),
        ],
    )

    # No assistant text exists, so the pattern-matching path cannot fire.
    assert _extract_last_exchange(transcript_path)[1] is None

    with patch("refusal_detector.hooks.refusal_hook.RefusalDetector") as mock_detector_cls:
        instance = mock_detector_cls.return_value
        instance.render_report.return_value = "# Report"

        result = process_hook_payload({"transcript_path": transcript_path})

        instance.detect.assert_called_once_with("Audit this parser for overflows.")
        assert "cyber" in result["systemMessage"]
        assert "# Report" in result["systemMessage"]


def test_structured_refusal_reads_prompt_that_carries_attachments(tmp_path):
    """A prompt sent with attachments has list content; it is still the prompt to diagnose."""
    transcript_path = _write_transcript(
        tmp_path,
        [
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Review the attached exploit writeup."},
                        {"type": "image", "source": {"type": "base64", "data": "iVBOR"}},
                    ],
                },
            },
            _refusal_fallback_record("cyber"),
        ],
    )

    with patch("refusal_detector.hooks.refusal_hook.RefusalDetector") as mock_detector_cls:
        mock_detector_cls.return_value.render_report.return_value = "# Report"

        process_hook_payload({"transcript_path": transcript_path})

        mock_detector_cls.return_value.detect.assert_called_once_with("Review the attached exploit writeup.")


def test_transcript_without_any_refusal_signal_stays_a_noop(tmp_path):
    transcript_path = _simple_transcript(tmp_path, "hello", "Here is the answer.")
    assert process_hook_payload({"transcript_path": transcript_path}) == {}


def test_structured_refusal_banner_surfaces_every_provider_field(tmp_path):
    """The provider's own fields are the most trustworthy output; none may be dropped."""
    transcript_path = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "Audit this parser."}},
            _refusal_fallback_record("cyber"),
        ],
    )

    with patch("refusal_detector.hooks.refusal_hook.RefusalDetector") as mock_detector_cls:
        mock_detector_cls.return_value.render_report.return_value = "# Report"

        message = process_hook_payload({"transcript_path": transcript_path})["systemMessage"]

    assert "model_refusal_fallback" in message, "subtype missing"
    assert "warning" in message, "level missing"
    assert "cyber" in message, "apiRefusalCategory missing"
    assert "restrictions on violative content" in message, "apiRefusalExplanation missing"
    assert "claude-fable-5" in message and "claude-opus-4-8" in message, "model fallback missing"
    assert "# Report" in message, "diagnostic report missing"


def test_structured_refusal_banner_tolerates_absent_optional_fields(tmp_path):
    """A record missing the optional fields must still report, not crash or print 'None'."""
    bare = {"type": "system", "subtype": "model_refusal_fallback"}
    transcript_path = _write_transcript(
        tmp_path,
        [{"type": "user", "message": {"role": "user", "content": "Audit this."}}, bare],
    )

    with patch("refusal_detector.hooks.refusal_hook.RefusalDetector") as mock_detector_cls:
        mock_detector_cls.return_value.render_report.return_value = "# Report"

        message = process_hook_payload({"transcript_path": transcript_path})["systemMessage"]

    assert "unspecified" in message
    assert "None" not in message, "absent fields must not leak Python None into the report"


def _turn(prompt_id: str, text: str) -> dict:
    return {"type": "user", "promptId": prompt_id, "message": {"role": "user", "content": text}}


def _interrupted(prompt_id: str) -> dict:
    return {
        "type": "user",
        "promptId": prompt_id,
        "message": {"role": "user", "content": [{"type": "text", "text": "[Request interrupted by user]"}]},
    }


def test_retry_diagnoses_the_prompt_that_first_triggered_the_refusal(tmp_path):
    """Clicking retry creates a turn whose only content is the retry click itself."""
    transcript_path = _write_transcript(
        tmp_path,
        [
            _turn("turn-1", "Audit this parser for buffer overflows and write it up."),
            _refusal_fallback_record("cyber"),
            _interrupted("turn-1"),
            _turn("turn-2", "Erneut versuchen"),
            _refusal_fallback_record("cyber"),
            _interrupted("turn-2"),
        ],
    )

    with patch("refusal_detector.hooks.refusal_hook.RefusalDetector") as mock_detector_cls:
        mock_detector_cls.return_value.render_report.return_value = "# Report"

        process_hook_payload({"transcript_path": transcript_path})

        mock_detector_cls.return_value.detect.assert_called_once_with(
            "Audit this parser for buffer overflows and write it up."
        )


def test_a_new_refusal_after_successful_turns_uses_its_own_prompt(tmp_path):
    """An earlier, unrelated refusal must not hijack a fresh one."""
    transcript_path = _write_transcript(
        tmp_path,
        [
            _turn("turn-1", "An old request that was refused long ago."),
            _refusal_fallback_record("cyber"),
            _turn("turn-2", "Something entirely benign."),
            {
                "type": "assistant",
                "promptId": "turn-2",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "Sure, done."}]},
            },
            _turn("turn-3", "A brand new request that got refused."),
            _refusal_fallback_record("cyber"),
        ],
    )

    with patch("refusal_detector.hooks.refusal_hook.RefusalDetector") as mock_detector_cls:
        mock_detector_cls.return_value.render_report.return_value = "# Report"

        process_hook_payload({"transcript_path": transcript_path})

        mock_detector_cls.return_value.detect.assert_called_once_with("A brand new request that got refused.")


def test_generated_interruption_marker_is_never_diagnosed(tmp_path):
    transcript_path = _write_transcript(
        tmp_path,
        [
            _turn("turn-1", "Real content worth diagnosing."),
            _interrupted("turn-1"),
            _refusal_fallback_record("cyber"),
        ],
    )

    with patch("refusal_detector.hooks.refusal_hook.RefusalDetector") as mock_detector_cls:
        mock_detector_cls.return_value.render_report.return_value = "# Report"

        process_hook_payload({"transcript_path": transcript_path})

        mock_detector_cls.return_value.detect.assert_called_once_with("Real content worth diagnosing.")


def test_wall_clock_budget_can_actually_contain_the_work_it_bounds():
    """Regression: the budget was once shorter than the work, killing every run mid-flight.

    A measured `claude -p` probe takes up to ~24.5s (3 real probes, 2026-08-13,
    post-authentication). If the budget is under _HOOK_MAX_CALLS * that, a
    correctly-detected refusal is terminated before it can render a report -- the
    failure is silent, since the hook still exits 0 printing {}.
    """
    from refusal_detector.hooks.refusal_hook import (
        _HOOK_MAX_CALLS,
        _HOOK_WALL_CLOCK_BUDGET_SECONDS,
    )

    measured_probe_seconds = 24.5
    worst_case = _HOOK_MAX_CALLS * measured_probe_seconds

    assert _HOOK_WALL_CLOCK_BUDGET_SECONDS > worst_case, (
        f"budget {_HOOK_WALL_CLOCK_BUDGET_SECONDS}s cannot contain "
        f"{_HOOK_MAX_CALLS} probes x {measured_probe_seconds}s = {worst_case}s"
    )


def test_watchdog_budget_stays_under_the_configured_hook_timeout():
    """The process must self-terminate before Claude Code stops waiting, never after."""
    import json as _json

    from refusal_detector.hooks.refusal_hook import _HOOK_WALL_CLOCK_BUDGET_SECONDS

    hooks_config = _json.loads(
        (Path(__file__).resolve().parents[1] / "hooks" / "hooks.json").read_text(encoding="utf-8")
    )
    configured_timeout = hooks_config["hooks"]["Stop"][0]["hooks"][0]["timeout"]

    assert _HOOK_WALL_CLOCK_BUDGET_SECONDS < configured_timeout, (
        f"watchdog {_HOOK_WALL_CLOCK_BUDGET_SECONDS}s must fire before the "
        f"{configured_timeout}s hook timeout"
    )


def test_oracle_probes_the_model_that_actually_refused(tmp_path):
    """Guardrails are model-specific: probing another model finds no trigger at all."""
    transcript_path = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "Audit this parser."}},
            _refusal_fallback_record("cyber"),
        ],
    )

    with patch("refusal_detector.hooks.refusal_hook.RefusalDetector") as mock_detector_cls:
        mock_detector_cls.return_value.render_report.return_value = "# Report"

        process_hook_payload({"transcript_path": transcript_path})

        config = mock_detector_cls.call_args.kwargs["config"]
        assert config.cli_model == "claude-fable-5", (
            f"oracle must probe the refusing model, not {config.cli_model!r}"
        )


def test_claude_cli_adapter_passes_the_model_flag():
    from unittest.mock import MagicMock

    from refusal_detector.adapters import ClaudeCodeCLIAdapter

    adapter = ClaudeCodeCLIAdapter(timeout=5, model="claude-fable-5")
    with patch("refusal_detector.adapters.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout="fine", stderr="")
        adapter.test("hello")

    cmd = run.call_args.args[0]
    assert "--model" in cmd and cmd[cmd.index("--model") + 1] == "claude-fable-5"


def test_claude_cli_adapter_omits_model_flag_when_unset():
    from unittest.mock import MagicMock

    from refusal_detector.adapters import ClaudeCodeCLIAdapter

    adapter = ClaudeCodeCLIAdapter(timeout=5)
    with patch("refusal_detector.adapters.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout="fine", stderr="")
        adapter.test("hello")

    assert "--model" not in run.call_args.args[0]


def test_hook_reports_which_source_the_trigger_came_from(tmp_path):
    """"Line 340 of your project CLAUDE.md" is actionable; "line 340" is not."""
    from refusal_detector.context import ContextSegment, SegmentOrigin
    from refusal_detector.hooks.refusal_hook import render_origin_summary

    trigger = [
        ContextSegment(
            index=7,
            text="a rule that tripped a classifier",
            start_char=0,
            end_char=32,
            start_line=340,
            end_line=340,
            origin=SegmentOrigin.PROJECT_CLAUDE_MD,
            source_label="project CLAUDE.md",
        )
    ]

    summary = render_origin_summary(trigger)

    assert "project CLAUDE.md" in summary
    assert "340" in summary


def test_origin_summary_groups_multiple_sources(tmp_path):
    from refusal_detector.context import ContextSegment, SegmentOrigin
    from refusal_detector.hooks.refusal_hook import render_origin_summary

    trigger = [
        ContextSegment(
            index=1, text="from the prompt", start_char=0, end_char=15,
            start_line=2, end_line=2,
            origin=SegmentOrigin.PROMPT, source_label="prompt",
        ),
        ContextSegment(
            index=9, text="from a claude md", start_char=0, end_char=16,
            start_line=88, end_line=88,
            origin=SegmentOrigin.PROJECT_CLAUDE_MD, source_label="project CLAUDE.md",
        ),
    ]

    summary = render_origin_summary(trigger)

    assert "prompt" in summary
    assert "project CLAUDE.md" in summary


def test_origin_summary_is_empty_when_nothing_triggered():
    from refusal_detector.hooks.refusal_hook import render_origin_summary

    assert render_origin_summary([]) == ""

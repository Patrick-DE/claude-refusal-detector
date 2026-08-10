"""Unit tests for the Claude plugin Stop-event refusal hook."""

import json
from unittest.mock import patch

from refusal_detector.hooks.refusal_hook import _extract_last_exchange, process_hook_payload


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

        assert result == {"systemMessage": "# Auto-Trigger Diagnostic Report"}
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

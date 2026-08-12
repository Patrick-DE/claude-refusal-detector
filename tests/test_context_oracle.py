"""Pre-prompt segments must be replayed through the system-prompt channel."""

from unittest.mock import MagicMock

import pytest

from refusal_detector.context import ContextSegment, SegmentOrigin
from refusal_detector.context_oracle import ContextOracle
from refusal_detector.ports import ReasonClass, Verdict


def _segment(index: int, text: str, origin: SegmentOrigin) -> ContextSegment:
    return ContextSegment(
        index=index,
        text=text,
        start_char=0,
        end_char=len(text),
        start_line=1,
        end_line=1,
        origin=origin,
        source_label=origin.value,
    )


def _oracle(segments, adapter=None):
    return ContextOracle(segments=segments, model="claude-fable-5", adapter=adapter)


def test_pre_prompt_segments_go_to_the_system_channel():
    segments = [
        _segment(0, "a claude md rule", SegmentOrigin.PROJECT_CLAUDE_MD),
        _segment(1, "the user prompt", SegmentOrigin.PROMPT),
    ]

    system_text, conversation_text = _oracle(segments).build_channels(segments)

    assert "a claude md rule" in system_text
    assert "a claude md rule" not in conversation_text
    assert "the user prompt" in conversation_text
    assert "the user prompt" not in system_text


def test_a_subset_without_pre_prompt_segments_yields_empty_system_text():
    segments = [_segment(0, "just a prompt", SegmentOrigin.PROMPT)]

    system_text, conversation_text = _oracle(segments).build_channels(segments)

    assert system_text == ""
    assert "just a prompt" in conversation_text


def test_test_passes_the_system_prompt_through_to_the_adapter():
    segments = [
        _segment(0, "claude md line", SegmentOrigin.PROJECT_CLAUDE_MD),
        _segment(1, "prompt line", SegmentOrigin.PROMPT),
    ]
    adapter = MagicMock()
    adapter.test_with_system.return_value = Verdict(blocked=True, reason_class=ReasonClass.TEXT_PATTERN)

    verdict = _oracle(segments, adapter=adapter).test("claude md line\nprompt line")

    assert verdict.blocked is True
    kwargs = adapter.test_with_system.call_args.kwargs
    assert "claude md line" in kwargs["system_prompt"]
    assert "prompt line" in kwargs["prompt"]


def test_auth_failure_propagates_rather_than_becoming_not_blocked():
    from refusal_detector.cli_auth import CliNotAuthenticatedError

    segments = [_segment(0, "prompt line", SegmentOrigin.PROMPT)]
    adapter = MagicMock()
    adapter.test_with_system.side_effect = CliNotAuthenticatedError("not logged in")

    with pytest.raises(CliNotAuthenticatedError):
        _oracle(segments, adapter=adapter).test("prompt line")

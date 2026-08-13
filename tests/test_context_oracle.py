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


def test_resolve_adapter_builds_the_real_system_prompt_adapter():
    """Prove the seam: Task 5's lazy import must resolve Task 6's real module.

    Every other test here injects a mock, so without this the join is unproven -
    a rename or signature change would break wiring with the suite still green.
    Constructing the adapter makes no CLI call, so this is safe while logged out.
    """
    from refusal_detector.system_prompt_adapter import SystemPromptCLIAdapter

    oracle = ContextOracle(segments=[], model="claude-fable-5", timeout=99.0)
    adapter = oracle._resolve_adapter()

    assert isinstance(adapter, SystemPromptCLIAdapter)
    assert adapter.model == "claude-fable-5", "model must reach the adapter"
    assert adapter.timeout == 99.0, "timeout must reach the adapter"


def test_build_channels_does_not_double_newlines():
    """Segment text carries its own trailing newline, so channels must concatenate.

    Regression: segments originally had their newline stripped, so build_channels joined
    on "\n" to put it back. Once segments started carrying it (to match
    minimizer._join_segments, which uses ""), that join silently doubled every line break
    and every live probe tested text the caller never assembled.
    """
    from refusal_detector.context import ContextSegment, SegmentOrigin

    def _seg(index: int, text: str, origin: SegmentOrigin) -> ContextSegment:
        return ContextSegment(
            index=index, text=text, start_char=0, end_char=len(text),
            start_line=index + 1, end_line=index + 1, origin=origin, source_label=origin.value,
        )

    subset = [
        _seg(0, "rule one\n", SegmentOrigin.PROJECT_CLAUDE_MD),
        _seg(1, "rule two", SegmentOrigin.PROJECT_CLAUDE_MD),
        _seg(2, "alpha\n", SegmentOrigin.PROMPT),
        _seg(3, "beta", SegmentOrigin.PROMPT),
    ]

    system_text, conversation_text = ContextOracle(
        segments=subset, model="claude-fable-5", adapter=object()
    ).build_channels(subset)

    assert system_text == "rule one\nrule two"
    assert conversation_text == "alpha\nbeta"
    assert "\n\n" not in system_text and "\n\n" not in conversation_text


def test_channels_match_what_the_minimizer_would_join():
    """The oracle must probe exactly the text the minimizer asked about."""
    from refusal_detector.context import ContextSegment, SegmentOrigin
    from refusal_detector.minimizer import _join_segments

    subset = [
        ContextSegment(index=i, text=t, start_char=0, end_char=len(t), start_line=i + 1,
                       end_line=i + 1, origin=SegmentOrigin.PROMPT, source_label="prompt")
        for i, t in enumerate(["alpha\n", "beta\n", "gamma"])
    ]

    _, conversation_text = ContextOracle(
        segments=subset, model="claude-fable-5", adapter=object()
    ).build_channels(subset)

    assert conversation_text == _join_segments(subset)

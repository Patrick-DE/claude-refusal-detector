"""Origin-tagged segments: the report must name which source holds the trigger."""

from refusal_detector.context import (
    ContextSegment,
    SegmentOrigin,
    is_pre_prompt,
)
from refusal_detector.ports import Segment


def test_context_segment_is_usable_wherever_a_segment_is():
    """The minimizer consumes Segments; ContextSegment must not break that contract."""
    segment = ContextSegment(
        index=0,
        text="hello",
        start_char=0,
        end_char=5,
        start_line=1,
        end_line=1,
        origin=SegmentOrigin.PROMPT,
        source_label="user prompt",
    )
    assert isinstance(segment, Segment)
    assert segment.text == "hello"
    assert segment.origin is SegmentOrigin.PROMPT
    assert segment.source_label == "user prompt"


def test_claude_md_origins_are_pre_prompt_and_others_are_not():
    assert is_pre_prompt(SegmentOrigin.PROJECT_CLAUDE_MD) is True
    assert is_pre_prompt(SegmentOrigin.GLOBAL_CLAUDE_MD) is True
    assert is_pre_prompt(SegmentOrigin.PROMPT) is False
    assert is_pre_prompt(SegmentOrigin.TOOL_RESULT) is False
    assert is_pre_prompt(SegmentOrigin.PRIOR_TURN) is False


def test_real_minimizer_accepts_context_segments_and_preserves_origin():
    """The minimizer is untouched by this work; it must swallow the subclass unchanged."""
    from refusal_detector.adapters import FakeOracleAdapter
    from refusal_detector.minimizer import Minimizer

    segments = [
        ContextSegment(
            index=i, text=text, start_char=0, end_char=len(text), start_line=i + 1,
            end_line=i + 1, origin=origin, source_label=origin.value,
        )
        for i, (text, origin) in enumerate(
            [
                ("benign opening line", SegmentOrigin.PROMPT),
                ("DANGEROUS_TRIGGER_LINE", SegmentOrigin.PROJECT_CLAUDE_MD),
                ("another benign line", SegmentOrigin.PROMPT),
            ]
        )
    ]

    trigger, _ = Minimizer(FakeOracleAdapter(triggers=["DANGEROUS_TRIGGER_LINE"])).minimize(segments)

    assert [s.text for s in trigger] == ["DANGEROUS_TRIGGER_LINE"]
    assert trigger[0].origin is SegmentOrigin.PROJECT_CLAUDE_MD, "origin must survive minimization"

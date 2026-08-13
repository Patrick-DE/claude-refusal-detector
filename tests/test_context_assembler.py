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


from refusal_detector.context import assemble_context


def _user(text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def _tool_result(text: str) -> dict:
    return {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "tool_result", "content": text}]},
    }


def _refusal() -> dict:
    return {"type": "system", "subtype": "model_refusal_fallback", "apiRefusalCategory": "cyber"}


def test_content_after_the_refusal_is_excluded():
    """The defect that motivated this redesign: later tool results were never in the
    refused request, so including them would minimize content the model never saw."""
    records = [
        _user("the prompt that was refused"),
        _refusal(),
        _tool_result("file content that arrived AFTER the refusal"),
    ]

    segments = assemble_context(records, refusal_index=1)

    joined = " ".join(s.text for s in segments)
    assert "the prompt that was refused" in joined
    assert "AFTER the refusal" not in joined


def test_tool_results_before_the_refusal_are_included_and_tagged():
    records = [
        _user("please audit this"),
        _tool_result("contents of a scanned file"),
        _refusal(),
    ]

    segments = assemble_context(records, refusal_index=2)
    origins = {s.origin for s in segments}

    assert SegmentOrigin.TOOL_RESULT in origins
    assert any("scanned file" in s.text for s in segments)


def test_claude_md_segments_come_first_and_are_tagged():
    records = [_user("the prompt"), _refusal()]
    claude_md = [(SegmentOrigin.PROJECT_CLAUDE_MD, "project CLAUDE.md", "line one\nline two")]

    segments = assemble_context(records, refusal_index=1, claude_md_files=claude_md)

    assert segments[0].origin is SegmentOrigin.PROJECT_CLAUDE_MD
    assert segments[0].source_label == "project CLAUDE.md"
    assert segments[0].text == "line one\n"
    assert any(s.origin is SegmentOrigin.PROMPT for s in segments)


def test_indices_are_sequential_across_all_sources():
    records = [_user("a\nb"), _refusal()]
    claude_md = [(SegmentOrigin.GLOBAL_CLAUDE_MD, "global", "x\ny")]

    segments = assemble_context(records, refusal_index=1, claude_md_files=claude_md)

    assert [s.index for s in segments] == list(range(len(segments)))


def test_empty_lines_are_not_emitted_as_segments():
    records = [_user("first\n\n\nsecond"), _refusal()]

    segments = assemble_context(records, refusal_index=1)

    assert all(s.text.strip() for s in segments)


def test_segments_rejoin_into_the_original_text():
    """The minimizer reconstructs candidates with "".join, so segments must carry their
    newline - exactly as LineSegmenter already does. Without it every probe tests text
    with all lines run together."""
    from refusal_detector.minimizer import _join_segments

    records = [
        {"type": "user", "message": {"role": "user", "content": "alpha\nbeta\ngamma"}},
        {"type": "system", "subtype": "model_refusal_fallback"},
    ]

    segments = assemble_context(records, refusal_index=1)

    assert _join_segments(segments) == "alpha\nbeta\ngamma"


def test_a_subset_rejoins_without_running_lines_together():
    records = [
        {"type": "user", "message": {"role": "user", "content": "alpha\nbeta\ngamma"}},
        {"type": "system", "subtype": "model_refusal_fallback"},
    ]
    from refusal_detector.minimizer import _join_segments

    segments = assemble_context(records, refusal_index=1)
    joined = _join_segments([segments[0], segments[2]])

    assert "alphagamma" not in joined, "dropping a middle line must not fuse its neighbours"
    assert joined == "alpha\ngamma"

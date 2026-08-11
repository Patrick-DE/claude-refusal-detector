"""Origin-tagged segments spanning every reconstructable part of a refused request.

A refusal can be triggered by the prompt, by a prior turn, by a tool result, or by a
CLAUDE.md loaded ahead of the conversation. Reporting *which* source holds the trigger
is what makes the result actionable: "line 340 of your project CLAUDE.md" is a fix,
"line 340" is not.
"""

from dataclasses import dataclass
from enum import Enum

from refusal_detector.ports import Segment


class SegmentOrigin(str, Enum):
    """Which part of the assembled request a segment came from."""

    PROMPT = "prompt"
    TOOL_RESULT = "tool_result"
    PRIOR_TURN = "prior_turn"
    PROJECT_CLAUDE_MD = "project_claude_md"
    GLOBAL_CLAUDE_MD = "global_claude_md"


_PRE_PROMPT_ORIGINS = frozenset({SegmentOrigin.PROJECT_CLAUDE_MD, SegmentOrigin.GLOBAL_CLAUDE_MD})


def is_pre_prompt(origin: SegmentOrigin) -> bool:
    """True when the segment belongs ahead of the conversation, not inside it.

    Pre-prompt material must be replayed through the system-prompt channel so it sits
    where it sat in the real request; feeding it as conversation text would test a
    different arrangement than the one that was refused.
    """
    return origin in _PRE_PROMPT_ORIGINS


@dataclass(frozen=True)
class ContextSegment(Segment):
    """A Segment that remembers where it came from."""

    origin: SegmentOrigin = SegmentOrigin.PROMPT
    source_label: str = ""


def _segment_lines(
    text: str,
    origin: SegmentOrigin,
    source_label: str,
    start_index: int,
) -> list[ContextSegment]:
    """Split one source into line segments, skipping blank lines."""
    segments: list[ContextSegment] = []
    char_cursor = 0
    index = start_index

    for line_number, line in enumerate(text.split("\n"), start=1):
        line_length = len(line)
        if line.strip():
            segments.append(
                ContextSegment(
                    index=index,
                    text=line,
                    start_char=char_cursor,
                    end_char=char_cursor + line_length,
                    start_line=line_number,
                    end_line=line_number,
                    origin=origin,
                    source_label=source_label,
                )
            )
            index += 1
        char_cursor += line_length + 1  # +1 for the newline consumed by split

    return segments


def _conversation_text(record: dict) -> tuple[str, SegmentOrigin] | None:
    """Return (text, origin) for a record that contributed content to the request."""
    message = record.get("message")
    if not isinstance(message, dict):
        return None

    content = message.get("content")
    record_type = record.get("type")

    if isinstance(content, str):
        if not content.strip():
            return None
        origin = SegmentOrigin.PROMPT if record_type == "user" else SegmentOrigin.PRIOR_TURN
        return content, origin

    if isinstance(content, list):
        collected = []
        origin = SegmentOrigin.PRIOR_TURN
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                collected.append(str(block.get("content", "")))
                origin = SegmentOrigin.TOOL_RESULT
            elif block.get("type") == "text":
                collected.append(str(block.get("text", "")))
        joined = "\n".join(part for part in collected if part.strip())
        if joined.strip():
            return joined, origin

    return None


def assemble_context(
    records: list[dict],
    refusal_index: int,
    claude_md_files: list[tuple[SegmentOrigin, str, str]] | None = None,
) -> list[ContextSegment]:
    """Rebuild the refused request as origin-tagged segments.

    Everything at or after `refusal_index` is excluded: it had not been sent when the
    refusal fired, so it cannot be part of what was refused.
    """
    segments: list[ContextSegment] = []

    for origin, label, content in claude_md_files or []:
        segments.extend(_segment_lines(content, origin, label, len(segments)))

    for record in records[:refusal_index]:
        extracted = _conversation_text(record)
        if extracted is None:
            continue
        text, origin = extracted
        label = origin.value
        segments.extend(_segment_lines(text, origin, label, len(segments)))

    return segments

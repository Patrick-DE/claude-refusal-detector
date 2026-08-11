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

"""Protocols and core data structures for Claude Refusal Detector."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class ReasonClass(str, Enum):
    """Classification of guardrail refusal reason."""

    STRUCTURED_REFUSAL = "structured_refusal"
    CONTENT_FILTER = "content_filter"
    MODERATION_ERROR = "moderation_error"
    TEXT_PATTERN = "text_pattern"
    UNKNOWN = "unknown"
    NOT_BLOCKED = "not_blocked"


@dataclass(frozen=True)
class Segment:
    """An atomic segment of prompt text with original offset tracking."""

    index: int
    text: str
    start_char: int
    end_char: int
    start_line: int
    end_line: int


@dataclass(frozen=True)
class Verdict:
    """Outcome of testing a prompt string against an Oracle."""

    blocked: bool
    reason_class: ReasonClass = ReasonClass.NOT_BLOCKED
    details: str = ""


@dataclass
class DetectionReport:
    """Complete diagnostic result of trigger minimization."""

    original_prompt: str
    segments: list[Segment]
    trigger_segments: list[Segment]
    trigger_text: str
    diff_text: str
    reason_class: ReasonClass
    total_calls: int
    cache_hits: int
    is_necessary: bool
    core_segments: list[Segment] = field(default_factory=list)
    repro_payload: dict[str, Any] = field(default_factory=dict)



@runtime_checkable
class Oracle(Protocol):
    """Interface for evaluating whether a prompt triggers a refusal."""

    def test(self, prompt: str) -> Verdict:
        """Test prompt string and return Verdict."""
        ...


@runtime_checkable
class Cache(Protocol):
    """Interface for per-session prompt evaluation cache."""

    def get(self, prompt_hash: str) -> Verdict | None:
        """Retrieve cached verdict if present."""
        ...

    def set(self, prompt_hash: str, verdict: Verdict) -> None:
        """Cache verdict by prompt hash."""
        ...


@runtime_checkable
class Segmenter(Protocol):
    """Interface for splitting prompt text into segments."""

    def split(self, text: str) -> list[Segment]:
        """Split prompt text into ordered segments with offsets."""
        ...


@runtime_checkable
class Reporter(Protocol):
    """Interface for rendering a DetectionReport into human-readable text."""

    def render(self, report: DetectionReport) -> str:
        """Render DetectionReport to string output."""
        ...

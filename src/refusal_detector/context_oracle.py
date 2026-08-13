"""Replay candidate context subsets through the channel each segment really occupied.

A CLAUDE.md line is not equivalent to the same words typed into a prompt: the model
sees it ahead of the conversation, in the system channel. Replaying it as conversation
text would test an arrangement that never happened, so origin decides the channel.
"""

from refusal_detector.context import ContextSegment, is_pre_prompt
from refusal_detector.logger import get_logger
from refusal_detector.ports import Verdict

logger = get_logger("context_oracle")


class ContextOracle:
    """An Oracle that routes each segment to the request channel it came from."""

    def __init__(
        self,
        segments: list[ContextSegment],
        model: str | None = None,
        timeout: float = 120.0,
        adapter=None,
    ) -> None:
        self._segments = segments
        self._model = model
        self._timeout = timeout
        self._adapter = adapter

    def _resolve_adapter(self):
        """Construct the default adapter on first actual use, not at construction time.

        build_channels() is a pure function of `subset` and never touches the adapter,
        so callers that only assemble channels must not pay for (or require) Task 6's
        SystemPromptCLIAdapter. The import stays lazy relative to test(), the method
        that actually needs it.
        """
        if self._adapter is None:
            from refusal_detector.system_prompt_adapter import SystemPromptCLIAdapter

            self._adapter = SystemPromptCLIAdapter(timeout=self._timeout, model=self._model)
        return self._adapter

    def build_channels(self, subset: list[ContextSegment]) -> tuple[str, str]:
        """Split a candidate subset into (system prompt text, conversation text).

        Concatenated without a separator, matching `minimizer._join_segments`: segment text
        already carries its own trailing newline. Joining on "\\n" here would double every
        line break, so the text probed would not be the text under test.
        """
        system_text = "".join(s.text for s in subset if is_pre_prompt(s.origin))
        conversation_text = "".join(s.text for s in subset if not is_pre_prompt(s.origin))
        return system_text, conversation_text

    def _subset_for(self, prompt: str) -> list[ContextSegment]:
        """Recover which segments a joined prompt represents.

        The Oracle port hands us a joined string, so membership is resolved by walking
        the known segments in order and keeping those whose text appears in it. Order is
        preserved, which is what the channel split depends on.
        """
        return [s for s in self._segments if s.text and s.text in prompt]

    def test(self, prompt: str) -> Verdict:
        """Oracle port: probe one candidate subset."""
        subset = self._subset_for(prompt)
        system_prompt, conversation = self.build_channels(subset)
        logger.info(
            "Probing %d segments (%d pre-prompt, %d conversation).",
            len(subset),
            sum(1 for s in subset if is_pre_prompt(s.origin)),
            sum(1 for s in subset if not is_pre_prompt(s.origin)),
        )
        adapter = self._resolve_adapter()
        return adapter.test_with_system(prompt=conversation, system_prompt=system_prompt)

"""Pure refusal classification logic."""

import re
from typing import Any

from refusal_detector.logger import get_logger
from refusal_detector.ports import ReasonClass, Verdict

logger = get_logger("classifier")

DEFAULT_REFUSAL_PATTERNS = [
    r"i (?:cannot|can't|am unable to|must decline to) (?:help|assist|fulfill|provide|generate|process|answer|respond)",
    r"i'm (?:sorry|unable),? (?:but )?i (?:cannot|can't|must decline)",
    r"as an ai (?:language model|assistant),? i (?:cannot|can't|do not|must not)",
    r"sorry, (?:but )?i can't (?:help|do that|fulfill)",
    r"i am not able to (?:assist|fulfill|comply)",
    r"against my safety policies?",
    r"violates? (?:our|the) (?:content|safety|usage) policy",
]


class RefusalClassifier:
    """Classifies provider response structures and texts into standard Verdicts."""

    def __init__(self, custom_patterns: list[str] | None = None) -> None:
        patterns = custom_patterns if custom_patterns is not None else DEFAULT_REFUSAL_PATTERNS
        self._regexes = [re.compile(p, re.IGNORECASE) for p in patterns]

    def classify_anthropic_response(self, response_data: dict[str, Any]) -> Verdict:
        """Classify Anthropic API response dictionary."""
        stop_reason = response_data.get("stop_reason")
        if stop_reason == "refusal":
            logger.info("Classified Anthropic response: STRUCTURED_REFUSAL (stop_reason=refusal)")
            return Verdict(blocked=True, reason_class=ReasonClass.STRUCTURED_REFUSAL, details="Anthropic stop_reason='refusal'")

        content = response_data.get("content", [])
        for block in content:
            if isinstance(block, dict) and block.get("type") == "refusal":
                logger.info("Classified Anthropic response: STRUCTURED_REFUSAL (content block type=refusal)")
                return Verdict(blocked=True, reason_class=ReasonClass.STRUCTURED_REFUSAL, details="Anthropic refusal content block")

        # Extract text content and test patterns
        full_text = self._extract_text(content)
        return self.classify_text(full_text)

    def classify_openai_response(self, response_data: dict[str, Any]) -> Verdict:
        """Classify OpenAI / DeepSeek / OpenRouter response dictionary."""
        choices = response_data.get("choices", [])
        if choices and isinstance(choices[0], dict):
            choice0 = choices[0]
            finish_reason = choice0.get("finish_reason")
            if finish_reason == "content_filter":
                logger.info("Classified OpenAI response: CONTENT_FILTER (finish_reason=content_filter)")
                return Verdict(blocked=True, reason_class=ReasonClass.CONTENT_FILTER, details="OpenAI finish_reason='content_filter'")

            msg = choice0.get("message", {})
            if isinstance(msg, dict):
                refusal_msg = msg.get("refusal")
                if refusal_msg:
                    logger.info("Classified OpenAI response: STRUCTURED_REFUSAL (refusal field present)")
                    return Verdict(
                        blocked=True,
                        reason_class=ReasonClass.STRUCTURED_REFUSAL,
                        details=f"OpenAI refusal field: {refusal_msg[:100]}",
                    )
                content = msg.get("content", "") or ""
                return self.classify_text(content)

        return Verdict(blocked=False, reason_class=ReasonClass.NOT_BLOCKED)

    def classify_moderation_error(self, error_message: str) -> Verdict:
        """Classify endpoint moderation or safety rejection error."""
        logger.info("Classified API error as MODERATION_ERROR: %s", error_message)
        return Verdict(
            blocked=True,
            reason_class=ReasonClass.MODERATION_ERROR,
            details=f"Moderation error: {error_message}",
        )

    def classify_text(self, text: str) -> Verdict:
        """Classify plain text output using regex patterns."""
        if not text:
            return Verdict(blocked=False, reason_class=ReasonClass.NOT_BLOCKED)

        for regex in self._regexes:
            match = regex.search(text)
            if match:
                matched_snippet = match.group(0)
                logger.info("Classified text output: TEXT_PATTERN (matched: '%s')", matched_snippet)
                return Verdict(
                    blocked=True,
                    reason_class=ReasonClass.TEXT_PATTERN,
                    details=f"Pattern match: '{matched_snippet}'",
                )

        return Verdict(blocked=False, reason_class=ReasonClass.NOT_BLOCKED)

    def _extract_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif isinstance(item, str):
                    parts.append(item)
            return "\n".join(parts)
        return ""

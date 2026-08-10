"""Prompt loader and Segmenter implementations."""

import re
from refusal_detector.logger import get_logger
from refusal_detector.ports import Segment, Segmenter

logger = get_logger("input_loader")


def load_text_from_file_or_string(file_or_string: str) -> str:
    """Load text from a file path if exists, otherwise treat as literal prompt text."""
    try:
        with open(file_or_string, "r", encoding="utf-8") as f:
            content = f.read()
            logger.debug("Loaded %d chars from file '%s'", len(content), file_or_string)
            return content
    except (OSError, ValueError):
        logger.debug("Treating input as literal prompt text (%d chars)", len(file_or_string))
        return file_or_string


def _get_line_number(text: str, char_idx: int) -> int:
    """Calculate 1-indexed line number for a character index."""
    return text.count("\n", 0, char_idx) + 1


class LineSegmenter(Segmenter):
    """Splits prompt text into line segments."""

    def split(self, text: str) -> list[Segment]:
        if not text:
            return []

        segments = []
        pattern = re.compile(r".*?(?:\r?\n|$)")
        idx = 0
        seg_idx = 0

        for match in pattern.finditer(text):
            seg_text = match.group(0)
            if not seg_text and match.start() == len(text):
                break
            start_char = match.start()
            end_char = match.end()
            if start_char == end_char:
                continue

            start_line = _get_line_number(text, start_char)
            end_line = _get_line_number(text, max(start_char, end_char - 1))

            segments.append(
                Segment(
                    index=seg_idx,
                    text=seg_text,
                    start_char=start_char,
                    end_char=end_char,
                    start_line=start_line,
                    end_line=end_line,
                )
            )
            seg_idx += 1

        logger.debug("LineSegmenter produced %d segments", len(segments))
        return segments


class ParagraphSegmenter(Segmenter):
    """Splits prompt text into paragraph segments separated by blank lines."""

    def split(self, text: str) -> list[Segment]:
        if not text:
            return []

        segments = []
        # Split by empty lines (\n\n+)
        pattern = re.compile(r"(?:[^\n]|\n(?![\r\n]))+")
        seg_idx = 0

        for match in pattern.finditer(text):
            seg_text = match.group(0)
            if not seg_text.strip():
                continue
            start_char = match.start()
            end_char = match.end()

            start_line = _get_line_number(text, start_char)
            end_line = _get_line_number(text, max(start_char, end_char - 1))

            segments.append(
                Segment(
                    index=seg_idx,
                    text=seg_text,
                    start_char=start_char,
                    end_char=end_char,
                    start_line=start_line,
                    end_line=end_line,
                )
            )
            seg_idx += 1

        if not segments and text:
            # Fallback for single line/paragraph
            return [
                Segment(
                    index=0,
                    text=text,
                    start_char=0,
                    end_char=len(text),
                    start_line=1,
                    end_line=_get_line_number(text, len(text)),
                )
            ]

        logger.debug("ParagraphSegmenter produced %d segments", len(segments))
        return segments


class SentenceSegmenter(Segmenter):
    """Splits prompt text into sentence segments."""

    def split(self, text: str) -> list[Segment]:
        if not text:
            return []

        segments = []
        # Match sentences ending with . ! ? followed by whitespace or end of string
        pattern = re.compile(r".*?[.!?]+(?=\s+|$)|.+$", re.DOTALL)
        seg_idx = 0

        for match in pattern.finditer(text):
            seg_text = match.group(0)
            if not seg_text:
                continue
            start_char = match.start()
            end_char = match.end()

            start_line = _get_line_number(text, start_char)
            end_line = _get_line_number(text, max(start_char, end_char - 1))

            segments.append(
                Segment(
                    index=seg_idx,
                    text=seg_text,
                    start_char=start_char,
                    end_char=end_char,
                    start_line=start_line,
                    end_line=end_line,
                )
            )
            seg_idx += 1

        logger.debug("SentenceSegmenter produced %d segments", len(segments))
        return segments


class TokenSegmenter(Segmenter):
    """Splits prompt text into whitespace-delimited tokens."""

    def split(self, text: str) -> list[Segment]:
        if not text:
            return []

        segments = []
        pattern = re.compile(r"\S+\s*")
        seg_idx = 0

        for match in pattern.finditer(text):
            seg_text = match.group(0)
            start_char = match.start()
            end_char = match.end()

            start_line = _get_line_number(text, start_char)
            end_line = _get_line_number(text, max(start_char, end_char - 1))

            segments.append(
                Segment(
                    index=seg_idx,
                    text=seg_text,
                    start_char=start_char,
                    end_char=end_char,
                    start_line=start_line,
                    end_line=end_line,
                )
            )
            seg_idx += 1

        logger.debug("TokenSegmenter produced %d segments", len(segments))
        return segments

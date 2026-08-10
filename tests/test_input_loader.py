"""Unit tests for input loader and segmenter implementations."""

import pytest
from refusal_detector.input_loader import (
    LineSegmenter,
    ParagraphSegmenter,
    SentenceSegmenter,
    TokenSegmenter,
    load_text_from_file_or_string,
)


def test_load_text_from_file_or_string(tmp_path):
    # Literal text fallback
    assert load_text_from_file_or_string("Hello world") == "Hello world"

    # File loading
    file_path = tmp_path / "test.txt"
    file_path.write_text("File content line 1\nLine 2", encoding="utf-8")
    assert load_text_from_file_or_string(str(file_path)) == "File content line 1\nLine 2"


def test_line_segmenter_offset_round_trip():
    text = "Line 1: Hello\nLine 2: World\nLine 3: Goodbye"
    segmenter = LineSegmenter()
    segments = segmenter.split(text)

    assert len(segments) == 3
    reconstructed = "".join(s.text for s in segments)
    assert reconstructed == text

    for seg in segments:
        assert text[seg.start_char : seg.end_char] == seg.text

    assert segments[0].start_line == 1
    assert segments[0].end_line == 1
    assert segments[1].start_line == 2
    assert segments[2].start_line == 3


def test_paragraph_segmenter_offsets():
    text = "Paragraph 1 line 1\nParagraph 1 line 2\n\nParagraph 2 line 1"
    segmenter = ParagraphSegmenter()
    segments = segmenter.split(text)

    assert len(segments) == 2
    assert "Paragraph 1" in segments[0].text
    assert "Paragraph 2" in segments[1].text

    for seg in segments:
        assert text[seg.start_char : seg.end_char] == seg.text


def test_sentence_segmenter_offsets():
    text = "First sentence here. Second sentence starts now! Is this third?"
    segmenter = SentenceSegmenter()
    segments = segmenter.split(text)

    assert len(segments) == 3
    assert segments[0].text.strip() == "First sentence here."
    assert segments[1].text.strip() == "Second sentence starts now!"
    assert segments[2].text.strip() == "Is this third?"

    for seg in segments:
        assert text[seg.start_char : seg.end_char] == seg.text


def test_token_segmenter_offsets():
    text = "alpha beta gamma delta"
    segmenter = TokenSegmenter()
    segments = segmenter.split(text)

    assert len(segments) == 4
    assert segments[0].text.strip() == "alpha"
    assert segments[3].text.strip() == "delta"

    for seg in segments:
        assert text[seg.start_char : seg.end_char] == seg.text

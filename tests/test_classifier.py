"""Unit tests for refusal response classification."""

from refusal_detector.classifier import RefusalClassifier
from refusal_detector.ports import ReasonClass


def test_anthropic_stop_reason_refusal():
    classifier = RefusalClassifier()
    data = {"stop_reason": "refusal", "content": [{"type": "text", "text": "I can't help."}]}
    verdict = classifier.classify_anthropic_response(data)
    assert verdict.blocked is True
    assert verdict.reason_class == ReasonClass.STRUCTURED_REFUSAL


def test_anthropic_refusal_content_block():
    classifier = RefusalClassifier()
    data = {"stop_reason": "end_turn", "content": [{"type": "refusal", "text": "Blocked by policy"}]}
    verdict = classifier.classify_anthropic_response(data)
    assert verdict.blocked is True
    assert verdict.reason_class == ReasonClass.STRUCTURED_REFUSAL


def test_openai_finish_reason_content_filter():
    classifier = RefusalClassifier()
    data = {"choices": [{"finish_reason": "content_filter", "message": {"content": ""}}]}
    verdict = classifier.classify_openai_response(data)
    assert verdict.blocked is True
    assert verdict.reason_class == ReasonClass.CONTENT_FILTER


def test_openai_refusal_field():
    classifier = RefusalClassifier()
    data = {"choices": [{"finish_reason": "stop", "message": {"refusal": "Safety violation"}}]}
    verdict = classifier.classify_openai_response(data)
    assert verdict.blocked is True
    assert verdict.reason_class == ReasonClass.STRUCTURED_REFUSAL


def test_moderation_error_classification():
    classifier = RefusalClassifier()
    verdict = classifier.classify_moderation_error("Prompt violates safety guidelines")
    assert verdict.blocked is True
    assert verdict.reason_class == ReasonClass.MODERATION_ERROR


def test_text_pattern_classification():
    classifier = RefusalClassifier()
    text = "I apologize, but I cannot assist with hacking commands."
    verdict = classifier.classify_text(text)
    assert verdict.blocked is True
    assert verdict.reason_class == ReasonClass.TEXT_PATTERN

    unblocked_text = "Here is the code to calculate Fibonacci numbers."
    verdict_unblocked = classifier.classify_text(unblocked_text)
    assert verdict_unblocked.blocked is False
    assert verdict_unblocked.reason_class == ReasonClass.NOT_BLOCKED

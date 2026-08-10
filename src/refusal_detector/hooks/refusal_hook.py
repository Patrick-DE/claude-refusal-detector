"""PostInvocation lifecycle hook for automatic refusal detection."""

import json
import os
import sys
from typing import Any

from refusal_detector.classifier import RefusalClassifier
from refusal_detector.logger import get_logger
from refusal_detector.service import RefusalDetector

logger = get_logger("refusal_hook")


def process_hook_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Process incoming hook stdin payload and return injectSteps if refusal detected."""
    # Check direct user message or transcript file if provided
    prompt_text = payload.get("userPrompt") or payload.get("prompt")
    transcript_path = payload.get("transcriptPath")

    if not prompt_text and transcript_path and os.path.exists(transcript_path):
        prompt_text = _extract_last_user_prompt(transcript_path)

    if not prompt_text:
        return {}

    # Check if last response indicates a refusal
    last_response = payload.get("lastResponse") or payload.get("output", "")
    classifier = RefusalClassifier()
    verdict = classifier.classify_text(last_response)

    # Auto-trigger detection if refused or if explicitly flagged
    if verdict.blocked or payload.get("force_detect"):
        logger.info("Refusal auto-detected in hook execution. Running RefusalDetector...")
        detector = RefusalDetector()
        report = detector.detect(prompt_text)
        rendered = detector.render_report(report)

        return {
            "injectSteps": [
                {
                    "ephemeralMessage": rendered
                }
            ]
        }

    return {}


def _extract_last_user_prompt(transcript_path: str) -> str | None:
    """Extract last user prompt string from transcript JSONL file."""
    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if data.get("type") == "USER_INPUT":
                        content = data.get("content")
                        if isinstance(content, str):
                            return content
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        logger.warning("Could not read transcript file %s: %s", transcript_path, e)
    return None


def main() -> None:
    """Main CLI entry point for refusal hook execution."""
    try:
        input_data = sys.stdin.read()
        if not input_data.strip():
            print("{}")
            return

        payload = json.loads(input_data)
        result = process_hook_payload(payload)
        print(json.dumps(result))
    except Exception as e:
        logger.error("Error running refusal hook: %s", e)
        print("{}")


if __name__ == "__main__":
    main()

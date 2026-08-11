"""Stop-event hook: auto-detects a refusal in Claude's last reply and reports the minimal trigger."""

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

_SRC_ROOT = Path(__file__).resolve().parents[2]
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from refusal_detector.classifier import RefusalClassifier
from refusal_detector.config import Config
from refusal_detector.logger import configure_logging, get_logger
from refusal_detector.service import RefusalDetector

logger = get_logger("refusal_hook")

_REENTRANCY_GUARD_ENV_VAR = "REFUSAL_DETECTOR_HOOK_ACTIVE"
_HOOK_MAX_CALLS = 10
"""Deliberately smaller than Config's default (50): the auto-trigger runs inside a
bounded hook timeout, so it trades completeness for a bounded wall-clock budget."""

_STDIN_READ_TIMEOUT_SECONDS = 10.0
"""A hook whose stdin never reaches EOF must not outlive its invocation: Claude Code
stops waiting at the configured hook timeout, but the process itself would block in
`sys.stdin.read()` forever and leak. Observed in the wild as orphaned hook processes
accumulating, one per Stop event, each holding its interpreter open indefinitely."""

_HOOK_WALL_CLOCK_BUDGET_SECONDS = 110.0
"""Slightly under the 120s timeout in hooks/hooks.json. Once Claude Code stops waiting,
any work still running is unobservable but still consuming API calls, so the process
terminates itself rather than continuing detached."""


def process_hook_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Process a Stop-event hook payload; return a systemMessage if the last reply was a refusal."""
    if os.environ.get(_REENTRANCY_GUARD_ENV_VAR):
        return {}

    transcript_path = payload.get("transcript_path")
    if not transcript_path:
        return {}

    user_prompt, assistant_reply = _extract_last_exchange(transcript_path)
    if not user_prompt or not assistant_reply:
        return {}

    classifier = RefusalClassifier()
    verdict = classifier.classify_text(assistant_reply)
    if not verdict.blocked:
        return {}

    logger.info("Refusal auto-detected in Stop hook. Running RefusalDetector...")
    os.environ[_REENTRANCY_GUARD_ENV_VAR] = "1"
    config = Config.from_env(max_calls=_HOOK_MAX_CALLS)
    detector = RefusalDetector(config=config)
    report = detector.detect(user_prompt)
    rendered = detector.render_report(report)

    return {"systemMessage": rendered}


def _extract_last_exchange(transcript_path: str) -> tuple[str | None, str | None]:
    """Return (last real user prompt, last assistant reply text) from a transcript JSONL file."""
    user_prompt: str | None = None
    assistant_reply: str | None = None

    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError as e:
        logger.warning("Could not read transcript file %s: %s", transcript_path, e)
        return None, None

    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue

        message = record.get("message")
        if not isinstance(message, dict):
            continue
        record_type = record.get("type")

        if assistant_reply is None and record_type == "assistant":
            content = message.get("content")
            if isinstance(content, list):
                text_blocks = [
                    block.get("text", "")
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                if text_blocks:
                    assistant_reply = "".join(text_blocks)

        elif user_prompt is None and record_type == "user":
            content = message.get("content")
            if isinstance(content, str):
                user_prompt = content

        if user_prompt is not None and assistant_reply is not None:
            break

    return user_prompt, assistant_reply


def read_hook_payload(stream: Any = None, timeout: float = _STDIN_READ_TIMEOUT_SECONDS) -> str:
    """Read the hook payload, returning "" rather than blocking forever without EOF.

    The read runs on a daemon thread so a stream that never closes cannot keep the
    interpreter alive past `timeout`.
    """
    stream = sys.stdin if stream is None else stream
    if stream is None:
        return ""
    try:
        if stream.isatty():
            return ""
    except (AttributeError, ValueError):
        pass

    captured: list[str] = []
    reader = threading.Thread(target=lambda: captured.append(stream.read()), daemon=True)
    reader.start()
    reader.join(timeout)

    if not captured:
        logger.warning("No hook payload on stdin after %.0fs; exiting without work.", timeout)
        return ""
    return captured[0]


def _start_wall_clock_watchdog(budget: float = _HOOK_WALL_CLOCK_BUDGET_SECONDS) -> threading.Timer:
    """Hard-stop the process once Claude Code has stopped waiting for this hook."""

    def _expire() -> None:
        logger.error("Hook exceeded its %.0fs budget; terminating instead of running detached.", budget)
        sys.stderr.flush()
        os._exit(0)

    watchdog = threading.Timer(budget, _expire)
    watchdog.daemon = True
    watchdog.start()
    return watchdog


def main() -> None:
    """CLI entry point: read the hook payload from stdin, write the hook output JSON to stdout."""
    configure_logging()
    watchdog = _start_wall_clock_watchdog()
    try:
        input_data = read_hook_payload()
        if not input_data.strip():
            print("{}")
            return

        payload = json.loads(input_data)
        result = process_hook_payload(payload)
        print(json.dumps(result))
    except Exception as e:
        logger.error("Error running refusal hook: %s", e)
        print("{}")
    finally:
        watchdog.cancel()


if __name__ == "__main__":
    main()

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

_STRUCTURED_REFUSAL_SUBTYPE = "model_refusal_fallback"
"""Claude Code's own record of an API-level refusal. It carries an explicit category and
explanation, so it is authoritative where the text patterns are only a heuristic."""

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
    """Process a Stop-event hook payload; return a systemMessage if the turn was refused."""
    if os.environ.get(_REENTRANCY_GUARD_ENV_VAR):
        return {}

    transcript_path = payload.get("transcript_path")
    if not transcript_path:
        return {}

    records = _read_records(transcript_path)
    if not records:
        return {}

    prompt, banner = _refused_prompt(records)
    if not prompt:
        return {}

    logger.info("Refusal auto-detected in Stop hook. Running RefusalDetector...")
    os.environ[_REENTRANCY_GUARD_ENV_VAR] = "1"
    config = Config.from_env(max_calls=_HOOK_MAX_CALLS)
    detector = RefusalDetector(config=config)
    report = detector.detect(prompt)

    return {"systemMessage": banner + detector.render_report(report)}


def _refused_prompt(records: list[dict[str, Any]]) -> tuple[str | None, str]:
    """Return the prompt to diagnose plus a banner describing how the refusal was detected.

    The structured API refusal is authoritative and checked first; pattern matching on
    the assistant's prose is the lower-confidence fallback for turns that carry no
    structured signal.
    """
    structured = _find_structured_refusal(records)
    if structured:
        prompt = _last_user_prompt_before(records, structured["index"])
        if not prompt:
            return None, ""
        category = structured.get("category") or "unspecified"
        logger.info("Structured API refusal detected (category=%s).", category)
        return prompt, f"> Detected via Claude Code's API refusal signal (category: `{category}`).\n\n"

    user_prompt, assistant_reply = _extract_last_exchange_from(records)
    if not user_prompt or not assistant_reply:
        return None, ""
    if not RefusalClassifier().classify_text(assistant_reply).blocked:
        return None, ""
    return user_prompt, "> Detected via reply text pattern match (lower confidence).\n\n"


def _read_records(transcript_path: str) -> list[dict[str, Any]]:
    """Parse a transcript JSONL file into records, skipping unparseable lines."""
    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError as e:
        logger.warning("Could not read transcript file %s: %s", transcript_path, e)
        return []

    records: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _message_text(content: Any) -> str | None:
    """Extract human/assistant prose from a message body, ignoring non-text blocks.

    Content is a plain string for simple turns and a block list when the turn carries
    attachments, thinking, or tool traffic. Only `text` blocks are prose: `tool_result`,
    `tool_use`, and `thinking` blocks are deliberately excluded.
    """
    if isinstance(content, str):
        return content or None
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        joined = "".join(parts)
        return joined or None
    return None


def _find_structured_refusal(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the last API-level refusal record, the highest-confidence signal available.

    Claude Code records a real refusal as a `system` record carrying an explicit
    category and explanation — not as assistant prose — so this beats pattern matching
    on the reply text and is checked first.
    """
    for index in range(len(records) - 1, -1, -1):
        record = records[index]
        if record.get("type") == "system" and record.get("subtype") == _STRUCTURED_REFUSAL_SUBTYPE:
            return {
                "index": index,
                "category": record.get("apiRefusalCategory"),
                "explanation": record.get("apiRefusalExplanation") or record.get("content"),
            }
    return None


def _last_user_prompt_before(records: list[dict[str, Any]], index: int) -> str | None:
    """Return the prose of the last user turn occurring before `index`."""
    for record in reversed(records[:index]):
        if record.get("type") != "user":
            continue
        if record.get("isMeta"):
            continue
        message = record.get("message")
        if not isinstance(message, dict):
            continue
        text = _message_text(message.get("content"))
        if text:
            return text
    return None


def _extract_last_exchange(transcript_path: str) -> tuple[str | None, str | None]:
    """Return (last real user prompt, last assistant reply text) from a transcript JSONL file."""
    return _extract_last_exchange_from(_read_records(transcript_path))


def _extract_last_exchange_from(records: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    """Return (last real user prompt, last assistant reply text) from parsed records."""
    user_prompt = _last_user_prompt_before(records, len(records))

    assistant_reply: str | None = None
    for record in reversed(records):
        if record.get("type") != "assistant":
            continue
        message = record.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, list):
            text = _message_text(content)
            if text:
                assistant_reply = text
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

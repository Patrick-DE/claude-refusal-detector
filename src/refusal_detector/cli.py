"""Command line interface for Claude Refusal Detector."""

import argparse
import sys
from typing import Sequence

from refusal_detector.config import Config
from refusal_detector.logger import configure_logging, get_logger
from refusal_detector.service import RefusalDetector

logger = get_logger("cli")


def main(args: Sequence[str] | None = None) -> int:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="refusal-detector",
        description="Pinpoint minimal prompt triggers causing Claude or LLM refusals.",
        epilog=(
            "Exit codes:\n"
            "  0  success (check: prompt not blocked; detect: report produced)\n"
            "  1  check: prompt is blocked\n"
            "  2  execution error"
        ),
    )
    parser.add_argument("--debug", "-v", action="store_true", help="Enable verbose debug logging")
    parser.add_argument("--provider", help="Provider adapter: claude_cli, anthropic, deepseek, openrouter")
    parser.add_argument("--model", help="Model name (e.g. claude-sonnet-5, claude-fable-5, deepseek-chat)")
    parser.add_argument("--split", choices=["lines", "sentences", "paragraphs", "tokens"], default="lines", help="Segmentation mode")
    parser.add_argument("--max-calls", type=int, default=50, help="Maximum API call budget")
    parser.add_argument("--cache-file", help="Path to per-session JSON cache file")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # detect command
    detect_parser = subparsers.add_parser("detect", help="Pinpoint minimal refusal trigger in prompt or file")
    detect_parser.add_argument("input", help="File path or prompt text string")
    detect_parser.add_argument("--out", "-o", help="Output Markdown report file path")

    # check command
    check_parser = subparsers.add_parser("check", help="Check if prompt or file is currently refused (no minimization)")
    check_parser.add_argument("input", help="File path or prompt text string")

    parsed = parser.parse_args(args)

    if parsed.debug:
        configure_logging("DEBUG")
    else:
        configure_logging("INFO")

    config = Config.from_env(
        provider=parsed.provider,
        model=parsed.model,
        split_mode=parsed.split,
        max_calls=parsed.max_calls,
        cache_file_path=parsed.cache_file,
    )

    detector = RefusalDetector(config=config)

    try:
        if parsed.command == "check":
            verdict = detector.check(parsed.input)
            print(f"Blocked: {verdict.blocked}")
            print(f"Reason Class: {verdict.reason_class.value}")
            if verdict.details:
                print(f"Details: {verdict.details}")
            return 1 if verdict.blocked else 0

        elif parsed.command == "detect":
            report = detector.detect(parsed.input)
            rendered = detector.render_report(report)

            if parsed.out:
                with open(parsed.out, "w", encoding="utf-8") as f:
                    f.write(rendered)
                print(f"Report saved to {parsed.out}")
            else:
                print(rendered)

            return 0

    except Exception as e:
        logger.error("Error executing refusal detector: %s", e)
        print(f"Error: {e}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())

"""MCP Server plugin for Claude Desktop integration."""

from mcp.server.fastmcp import FastMCP

from refusal_detector.config import Config
from refusal_detector.logger import get_logger
from refusal_detector.service import RefusalDetector

logger = get_logger("desktop_plugin")

mcp = FastMCP("Claude Refusal Detector")


@mcp.tool()
def detect_refusal_trigger(
    prompt_or_path: str,
    split_mode: str = "lines",
    provider: str = "claude_cli",
    model: str = "claude-3-5-sonnet-20241022",
) -> str:
    """Analyze a prompt string or file path to pinpoint the minimal subset of text triggering a refusal.

    Args:
        prompt_or_path: The text prompt string or file path containing content that was refused.
        split_mode: Segmentation granularity: 'lines' (default), 'sentences', 'paragraphs', or 'tokens'.
        provider: Model oracle provider: 'claude_cli' (default keyless adapter), 'anthropic', 'deepseek', or 'openrouter'.
        model: Target model name.

    Returns:
        Rendered Markdown diagnostic report with minimal trigger, positions, diff, and reason class.
    """
    logger.info("MCP detect_refusal_trigger called (%s, split=%s)", prompt_or_path[:40], split_mode)
    config = Config.from_env(
        provider=provider,
        model=model,
        split_mode=split_mode,
    )
    detector = RefusalDetector(config=config)
    report = detector.detect(prompt_or_path)
    return detector.render_report(report)



def main() -> None:
    """Run MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()

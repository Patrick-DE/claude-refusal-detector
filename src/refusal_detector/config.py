"""Configuration dataclass and factory functions."""

import os
from dataclasses import dataclass
from typing import Any

from refusal_detector.logger import configure_logging, get_logger
from refusal_detector.ports import Cache, Oracle, Reporter, Segmenter

logger = get_logger("config")

MODEL_BASE_URL_MAP = {
    "deepseek": "https://api.deepseek.com",
    "openrouter": "https://openrouter.ai/api/v1",
    "anthropic": "https://api.anthropic.com",
}


@dataclass
class Config:
    """Configuration options for refusal detector execution."""

    provider: str = "claude_cli"
    model: str = "claude-3-5-sonnet-20241022"
    anthropic_api_key: str | None = None
    deepseek_api_key: str | None = None
    openrouter_api_key: str | None = None
    max_calls: int = 50
    timeout_seconds: float = 30.0
    cache_file_path: str | None = None
    cli_model: str | None = None
    """Model alias/id for the `claude -p` oracle. None means the CLI's own default."""
    split_mode: str = "lines"
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, **overrides: Any) -> "Config":
        """Load configuration from environment variables with optional overrides."""
        # Optionally load .env file
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            pass

        config = cls(
            provider=os.getenv("DEFAULT_PROVIDER", "claude_cli"),
            model=os.getenv("DEFAULT_MODEL", "claude-3-5-sonnet-20241022"),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
            deepseek_api_key=os.getenv("DEEPSEEK_API_KEY"),
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY"),
            max_calls=int(os.getenv("MAX_CALLS", "50")),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )

        for key, val in overrides.items():
            if val is not None and hasattr(config, key):
                setattr(config, key, val)

        configure_logging(config.log_level)
        logger.debug("Loaded config: provider=%s, model=%s", config.provider, config.model)
        return config


def build_segmenter(split_mode: str = "lines") -> Segmenter:
    """Factory creating segmenter by split mode name."""
    from refusal_detector.input_loader import (
        LineSegmenter,
        ParagraphSegmenter,
        SentenceSegmenter,
        TokenSegmenter,
    )

    mode = split_mode.lower()
    if mode == "lines":
        return LineSegmenter()
    elif mode == "sentences":
        return SentenceSegmenter()
    elif mode == "paragraphs":
        return ParagraphSegmenter()
    elif mode == "tokens":
        return TokenSegmenter()
    else:
        raise ValueError(f"Unknown split mode '{split_mode}'. Choice of: lines, sentences, paragraphs, tokens.")


def build_cache(cache_file_path: str | None = None) -> Cache:
    """Factory creating session cache adapter."""
    from refusal_detector.adapters import InMemoryCache, JsonFileCache

    if cache_file_path:
        return JsonFileCache(cache_file_path)
    return InMemoryCache()


def build_reporter() -> Reporter:
    """Factory creating Markdown reporter adapter."""
    from refusal_detector.adapters import MarkdownReporter

    return MarkdownReporter()


def build_oracle(config: Config) -> Oracle:
    """Factory creating base model Oracle adapter (unwrapped by runner)."""
    from refusal_detector.adapters import (
        AnthropicAPIAdapter,
        ClaudeCodeCLIAdapter,
        OpenAIAdapter,
    )

    provider = config.provider.lower()
    if provider == "claude_cli":
        return ClaudeCodeCLIAdapter(timeout=config.timeout_seconds, model=config.cli_model)
    elif provider == "anthropic":
        if not config.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is required for Anthropic provider.")
        return AnthropicAPIAdapter(
            api_key=config.anthropic_api_key,
            model=config.model,
            timeout=config.timeout_seconds,
        )
    elif provider == "deepseek":
        if not config.deepseek_api_key:
            raise ValueError("DEEPSEEK_API_KEY is required for DeepSeek provider.")
        return OpenAIAdapter(
            api_key=config.deepseek_api_key,
            base_url=MODEL_BASE_URL_MAP["deepseek"],
            model=config.model or "deepseek-chat",
            timeout=config.timeout_seconds,
        )
    elif provider == "openrouter":
        if not config.openrouter_api_key:
            raise ValueError("OPENROUTER_API_KEY is required for OpenRouter provider.")
        return OpenAIAdapter(
            api_key=config.openrouter_api_key,
            base_url=MODEL_BASE_URL_MAP["openrouter"],
            model=config.model,
            timeout=config.timeout_seconds,
        )
    else:
        raise ValueError(f"Unknown provider '{config.provider}'. Choice of: claude_cli, anthropic, deepseek, openrouter.")

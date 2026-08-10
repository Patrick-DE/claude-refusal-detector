"""Logging setup for refusal detector."""

import logging
import os
import sys

_LOGGER_NAME = "refusal_detector"


def get_logger(name: str | None = None) -> logging.Logger:
    """Get a configured logger instance for the refusal detector."""
    logger_name = f"{_LOGGER_NAME}.{name}" if name else _LOGGER_NAME
    return logging.getLogger(logger_name)


def configure_logging(level: str | int | None = None) -> None:
    """Configure logging format and level."""
    if level is None:
        level_str = os.getenv("LOG_LEVEL", "INFO").upper()
        level = getattr(logging, level_str, logging.INFO)
    elif isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    root_logger = logging.getLogger(_LOGGER_NAME)
    root_logger.setLevel(level)

    # Avoid duplicate handlers if re-configured
    if not root_logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        formatter = logging.Formatter(
            "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)

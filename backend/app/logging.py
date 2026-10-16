"""Centralized baseline logging configuration."""

import sys
from loguru import logger

_configured = False


def configure_logging() -> None:
    """Configure stderr-only application logging once per process."""
    global _configured
    if _configured:
        return
    logger.remove()
    logger.add(sys.stderr, level="INFO", backtrace=False, diagnose=False)
    _configured = True

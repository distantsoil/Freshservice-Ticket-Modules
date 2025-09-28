"""Logging configuration for Freshservice scripts."""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict

from .config import resolve_path

try:  # pragma: no cover - optional dependency
    from rich.logging import RichHandler
    _RICH_AVAILABLE = True
except Exception:  # pragma: no cover - fall back to standard logging
    RichHandler = None  # type: ignore
    _RICH_AVAILABLE = False


def configure_logging(config: Dict[str, Any], *, base_dir: Path | None = None) -> None:
    """Configure logging sinks based on YAML configuration."""
    logging.captureWarnings(True)
    logging.getLogger().handlers.clear()
    logging.getLogger().setLevel(logging.DEBUG)

    logging_config = config.get("logging", {})
    console_cfg = logging_config.get("console", {})
    file_cfg = logging_config.get("file", {})

    if console_cfg.get("enabled", True):
        level = console_cfg.get("level", "INFO")
        if console_cfg.get("rich_format", False) and _RICH_AVAILABLE:
            handler = RichHandler(level=level, rich_tracebacks=True)
            formatter = logging.Formatter("%(message)s")
        else:
            handler = logging.StreamHandler(sys.stdout)
            handler.setLevel(level)
            formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        handler.setFormatter(formatter)
        logging.getLogger().addHandler(handler)

    if file_cfg.get("enabled", True):
        file_path = resolve_path(file_cfg.get("path", "logs/freshservice.log"), base=base_dir)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(file_path, mode="a", encoding="utf-8")
        handler.setLevel(file_cfg.get("level", "DEBUG"))
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logging.getLogger().addHandler(handler)

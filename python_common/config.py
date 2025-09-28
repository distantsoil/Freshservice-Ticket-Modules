"""Configuration helpers for Freshservice ticket analysis tools."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import yaml


class ConfigError(RuntimeError):
    """Raised when configuration cannot be loaded."""


PACKAGE_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_CONFIG_LOCATIONS = (
    PACKAGE_ROOT / "config" / "config.yaml",
    PACKAGE_ROOT / "config" / "config.yml",
    PACKAGE_ROOT / "config" / "config.json",
    Path("./config/config.yaml"),
    Path("./config/config.yml"),
    Path("./config/config.json"),
    Path.home() / ".freshservice" / "config.yaml",
)


def resolve_path(path_str: str | None, *, base: Path | None = None) -> Path:
    """Resolve a path string that may be relative to an optional base directory."""
    base_path = base or Path.cwd()
    if not path_str:
        return base_path
    path = Path(path_str)
    if not path.is_absolute():
        path = base_path / path
    return path


def load_config(path: str | os.PathLike[str] | None = None) -> Dict[str, Any]:
    """Load configuration from YAML.

    Parameters
    ----------
    path: Optional path to a configuration file. If not provided, default
        locations will be searched.
    """
    if path:
        candidate_paths = [Path(path)]
    else:
        candidate_paths = list(DEFAULT_CONFIG_LOCATIONS)

    for candidate in candidate_paths:
        if candidate.exists():
            with candidate.open("r", encoding="utf-8") as handle:
                try:
                    data = yaml.safe_load(handle) or {}
                    return data
                except yaml.YAMLError as exc:  # pragma: no cover - defensive
                    raise ConfigError(f"Unable to parse configuration file {candidate}") from exc
    raise ConfigError(
        "No configuration file could be located. Provide --config or create "
        "freshservice_ticket_insights/config/config.yaml (or config/config.yaml)."
    )

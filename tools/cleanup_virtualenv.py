#!/usr/bin/env python3
"""Helper to remove a local Python virtual environment and optional pip caches."""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_VENV_PATH = BASE_DIR / ".venv"

LOGGER = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Remove a Python virtual environment (defaults to .venv in the project root)"
            " and optionally purge pip caches."
        )
    )
    parser.add_argument(
        "--venv-path",
        default=str(DEFAULT_VENV_PATH),
        help=(
            "Path to the virtual environment directory to delete. "
            "Relative paths are resolved from the project root "
            "(freshservice_ticket_insights/..)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only log what would be removed without deleting anything.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Proceed even if the target virtual environment appears to be active.",
    )
    parser.add_argument(
        "--purge-pip-cache",
        action="store_true",
        help="Also purge the pip download/cache directory using the current Python interpreter.",
    )
    return parser


def _resolve_path(path_str: str) -> Path:
    path = Path(path_str).expanduser()
    if not path.is_absolute():
        path = (BASE_DIR.parent / path).resolve()
    return path


def _warn_if_active(venv_path: Path, *, force: bool) -> None:
    active = os.environ.get("VIRTUAL_ENV")
    if active and Path(active).resolve() == venv_path and not force:
        LOGGER.error(
            "The virtual environment at %s is currently active. "
            "Run 'deactivate' or re-run with --force to continue.",
            venv_path,
        )
        raise SystemExit(2)


def _remove_directory(target: Path, *, dry_run: bool) -> None:
    if not target.exists():
        LOGGER.info("No virtual environment found at %s", target)
        return

    if dry_run:
        LOGGER.info("[dry-run] Would remove virtual environment at %s", target)
        return

    LOGGER.info("Removing virtual environment at %s", target)
    shutil.rmtree(target)
    LOGGER.info("Virtual environment removed.")


def _purge_pip_cache(*, dry_run: bool) -> None:
    if dry_run:
        LOGGER.info("[dry-run] Would purge pip cache via 'pip cache purge'")
        return

    try:
        LOGGER.info("Purging pip cache using interpreter %s", sys.executable)
        subprocess.run(
            [sys.executable, "-m", "pip", "cache", "purge"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        LOGGER.info("Pip cache purge completed.")
    except FileNotFoundError:  # pragma: no cover - defensive guard
        LOGGER.warning("Unable to locate pip executable for cache purge.")
    except subprocess.CalledProcessError as exc:
        LOGGER.warning("Pip cache purge exited with error: %s", exc)


def run(
    venv_path: str,
    *,
    dry_run: bool = False,
    force: bool = False,
    purge_pip_cache: bool = False,
) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
    resolved_path = _resolve_path(venv_path)
    LOGGER.debug("Resolved virtual environment path: %s", resolved_path)

    _warn_if_active(resolved_path, force=force)
    _remove_directory(resolved_path, dry_run=dry_run)

    if purge_pip_cache:
        _purge_pip_cache(dry_run=dry_run)


def main(argv: Iterable[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    run(
        args.venv_path,
        dry_run=args.dry_run,
        force=args.force,
        purge_pip_cache=args.purge_pip_cache,
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Quickly summarize Freshservice tickets by category in the console."""
from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable, List, Tuple

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = BASE_DIR / "config" / "config.yaml"
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from python_common.config import load_config  # type: ignore  # pylint: disable=import-error
from python_common.logging_setup import configure_logging  # type: ignore  # pylint: disable=import-error
from python_common.workflow import _create_client  # type: ignore  # pylint: disable=import-error

LOGGER = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Print a table summarising ticket counts per category.",
    )
    parser.add_argument(
        "--config",
        help=(
            "Path to configuration YAML file. Defaults to "
            f"{DEFAULT_CONFIG_PATH} or config/config.yaml if present."
        ),
    )
    parser.add_argument(
        "--updated-since",
        help="Optional ISO8601 timestamp to limit tickets to those updated since the provided value.",
    )
    return parser


def _summarise(client, *, updated_since: str | None) -> Tuple[Counter[str], int, int]:
    """Return category counts, uncategorised total, and grand total."""

    counts: Counter[str] = Counter()
    uncategorised = 0
    total = 0

    for ticket in client.iter_tickets(updated_since=updated_since):
        total += 1
        category = ticket.get("category")
        if category:
            counts[str(category)] += 1
        else:
            uncategorised += 1

    return counts, uncategorised, total


def _render_table(counts: Counter[str], uncategorised: int, total: int) -> List[str]:
    """Format summary counts into a simple text table."""

    rows: List[Tuple[str, int]] = []
    for category, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        rows.append((category, count))

    rows.append(("Uncategorised", uncategorised))
    rows.append(("Total", total))

    label_width = max(len(label) for label, _ in rows + [("Category", 0)])
    header = f"{'Category'.ljust(label_width)}  Tickets"
    separator = f"{'-' * label_width}  -------"

    lines = [header, separator]
    for label, count in rows:
        lines.append(f"{label.ljust(label_width)}  {count:>7}")
    return lines


def run(config_path: str | None, *, updated_since: str | None) -> List[str]:
    config = load_config(config_path)
    configure_logging(config, base_dir=BASE_DIR)

    try:
        client = _create_client(config)
        counts, uncategorised, total = _summarise(client, updated_since=updated_since)
    except Exception as exc:  # pragma: no cover - defensive logging
        LOGGER.exception("Failed to summarise ticket categories: %s", exc)
        raise SystemExit(1) from exc

    lines = _render_table(counts, uncategorised, total)
    for line in lines:
        print(line)
    return lines


def main(argv: Iterable[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    run(args.config, updated_since=args.updated_since)


if __name__ == "__main__":
    main()

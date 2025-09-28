#!/usr/bin/env python3
"""Utility for printing the Freshservice taxonomy tree."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = BASE_DIR / "config" / "config.yaml"
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from python_common.config import load_config  # type: ignore  # pylint: disable=import-error
from python_common.logging_setup import configure_logging  # type: ignore  # pylint: disable=import-error
from python_common.workflow import (  # type: ignore  # pylint: disable=import-error
    _create_client,
    _extract_taxonomy,
)

LOGGER = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Print the Freshservice category/subcategory/item-category hierarchy."
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help=(
            "Path to configuration YAML file (defaults to "
            "freshservice_ticket_insights/config/config.yaml)."
        ),
    )
    return parser


def _render_taxonomy(
    categories: List[str],
    subcategories: Dict[str | None, List[str]],
    item_categories: Dict[Tuple[str | None, str | None], List[str]],
) -> List[str]:
    """Convert taxonomy collections into dashed hierarchy lines."""

    lines: List[str] = []
    seen_sub_keys: set[str | None] = set()
    seen_item_keys: set[Tuple[str | None, str | None]] = set()

    def emit(label: str, depth: int) -> None:
        prefix = "-" * (depth + 1)
        lines.append(f"{prefix} {label}")

    for category in categories:
        emit(category, 0)
        children = subcategories.get(category, [])
        seen_sub_keys.add(category)
        for sub in children:
            emit(sub, 1)
            seen_item_keys.add((category, sub))
            for item in item_categories.get((category, sub), []):
                emit(item, 2)

    # Handle orphaned subcategories or items whose parents were absent from the metadata list.
    for parent, subs in subcategories.items():
        if parent in seen_sub_keys:
            continue
        if parent is not None:
            emit(parent, 0)
        for sub in subs:
            emit(sub, 1 if parent is not None else 0)
            seen_item_keys.add((parent, sub))
            for item in item_categories.get((parent, sub), []):
                depth = 2 if parent is not None else 1
                emit(item, depth)

    for key, items in item_categories.items():
        if key in seen_item_keys:
            continue
        category, sub = key
        depth = 0
        if category:
            emit(category, depth)
            depth = 1
        if sub:
            emit(sub, depth)
            depth += 1
        for item in items:
            emit(item, depth)

    return lines


def run(config_path: str | None) -> List[str]:
    config = load_config(config_path)
    configure_logging(config, base_dir=BASE_DIR)

    try:
        client = _create_client(config)
        ticket_fields = list(client.iter_ticket_fields())
    except Exception as exc:  # pragma: no cover - defensive logging
        LOGGER.exception("Failed to retrieve taxonomy from Freshservice: %s", exc)
        raise SystemExit(1) from exc

    categories, subcategories, item_categories = _extract_taxonomy(ticket_fields)
    lines = _render_taxonomy(categories, subcategories, item_categories)
    for line in lines:
        print(line)
    return lines


def main(argv: Iterable[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    run(args.config)


if __name__ == "__main__":
    main()

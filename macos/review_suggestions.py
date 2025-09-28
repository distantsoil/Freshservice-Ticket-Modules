#!/usr/bin/env python3
"""macOS helper to summarise and filter review decisions."""
from __future__ import annotations

import argparse
import csv
import logging
import sys
from collections import Counter
from pathlib import Path
from typing import List

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = BASE_DIR / "config" / "config.yaml"
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from python_common.config import ConfigError, load_config
from python_common.logging_setup import configure_logging
from python_common.workflow import ReviewOptions, review_rows

LOGGER = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Summarise manager decisions from the review CSV.")
    parser.add_argument("review_csv", help="Path to the *_review.csv file produced by the analysis step.")
    parser.add_argument(
        "--config",
        help=(
            "Optional configuration file to control logging behaviour. Defaults to "
            f"{DEFAULT_CONFIG_PATH} or config/config.yaml if present."
        ),
    )
    parser.add_argument(
        "--decision",
        action="append",
        choices=["approve", "decline", "skip", "pending"],
        help="Filter to specific decision states. Can be supplied multiple times.",
    )
    parser.add_argument(
        "--export",
        help="Optional path to export the filtered results for offline collaboration.",
    )
    return parser


def setup_logging(config_path: str | None, base_dir: Path) -> None:
    try:
        config = load_config(config_path)
    except ConfigError:
        config = {
            "logging": {
                "console": {"enabled": True, "level": "INFO", "rich_format": False},
                "file": {
                    "enabled": True,
                    "level": "DEBUG",
                    "path": str(base_dir / "logs" / "review_helper.log"),
                },
            }
        }
    configure_logging(config, base_dir=base_dir)


def export_rows(rows: List[dict], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="") as handle:
        if not rows:
            handle.write("No rows matched the provided filter.\n")
            return
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    base_dir = BASE_DIR
    setup_logging(args.config, base_dir)

    options = ReviewOptions(review_csv=args.review_csv, decision_filter=args.decision)
    rows = review_rows(options)
    LOGGER.info("Review rows loaded: %s", len(rows))

    counter = Counter(row.manager_decision for row in rows)
    for decision, count in counter.items():
        LOGGER.info("%s: %s", decision, count)

    if args.export:
        worksheet_rows = []
        with Path(args.review_csv).open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                decision = (row.get("manager_decision") or "").strip().lower()
                if args.decision and decision not in {d.lower() for d in args.decision}:
                    continue
                worksheet_rows.append(row)
        export_path = Path(args.export)
        export_rows(worksheet_rows, export_path)
        LOGGER.info("Exported %s rows to %s", len(worksheet_rows), export_path)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Windows entry point to apply approved category updates."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = BASE_DIR / "config" / "config.yaml"
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from python_common.workflow import ApplyUpdatesOptions, apply_updates


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Apply Freshservice ticket category updates (Windows edition)."
    )
    parser.add_argument(
        "--config",
        help=(
            "Path to configuration YAML file. Defaults to "
            f"{DEFAULT_CONFIG_PATH} or config/config.yaml if present."
        ),
    )
    parser.add_argument("--review-csv", help="Path to the reviewed CSV containing manager decisions.")
    parser.add_argument(
        "--ticket-id",
        action="append",
        type=int,
        help="Apply a targeted update to the specified ticket id. Can be used multiple times.",
    )
    parser.add_argument("--category", help="Override category when using --ticket-id.")
    parser.add_argument("--sub-category", help="Override sub-category when using --ticket-id.")
    parser.add_argument("--item-category", help="Override item category when using --ticket-id.")
    parser.add_argument(
        "--disable-console-log",
        action="store_true",
        help="Disable console logging output (deprecated; logging is hidden unless --show-console-log is provided).",
    )
    parser.add_argument(
        "--show-console-log",
        action="store_true",
        help="Show detailed log output instead of the default progress display.",
    )
    parser.add_argument(
        "--simple-console",
        action="store_true",
        help="Use a simple console log format instead of Rich formatting.",
    )
    parser.add_argument(
        "--console-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Override the console logging level.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview updates without sending changes to Freshservice.",
    )
    parser.add_argument(
        "--skip-log",
        help="Path to the skip-tracking file that records successfully updated tickets.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore the skip-tracking file and attempt updates for all approved tickets.",
    )
    parser.add_argument(
        "--force-ticket",
        action="append",
        type=int,
        help="Force updates for specific ticket IDs even if they appear in the skip log.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    base_dir = BASE_DIR
    options = ApplyUpdatesOptions(
        config_path=args.config,
        review_csv=args.review_csv,
        ticket_ids=args.ticket_id,
        category=args.category,
        sub_category=args.sub_category,
        item_category=args.item_category,
        disable_console=args.disable_console_log or not args.show_console_log,
        simple_console=args.simple_console,
        console_level=args.console_level,
        dry_run=args.dry_run,
        skip_log_path=args.skip_log,
        force_all=args.force,
        force_ticket_ids=args.force_ticket,
        show_console_log=args.show_console_log,
    )
    apply_updates(options, base_dir=base_dir)


if __name__ == "__main__":
    main()

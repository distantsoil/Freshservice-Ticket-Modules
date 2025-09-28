#!/usr/bin/env python3
"""macOS entry point for Freshservice ticket fetching and analysis."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = BASE_DIR / "config" / "config.yaml"
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from python_common.workflow import FetchAnalyzeOptions, fetch_and_analyze


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch all Freshservice tickets and produce a categorized analysis report (macOS edition)."
    )
    parser.add_argument(
        "--config",
        help=(
            "Path to configuration YAML file. Defaults to "
            f"{DEFAULT_CONFIG_PATH} or config/config.yaml if present."
        ),
    )
    parser.add_argument(
        "--output-directory",
        help="Directory where the analysis CSV should be written. Overrides reporting.output_directory.",
    )
    parser.add_argument("--report-name", help="Filename to use for the analysis CSV.")
    parser.add_argument(
        "--updated-since",
        help="Optional ISO8601 timestamp to limit tickets to those updated since the given value.",
    )
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
        "--skip-review-template",
        action="store_true",
        help="Do not generate the manager review template CSV automatically.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    base_dir = BASE_DIR
    options = FetchAnalyzeOptions(
        config_path=args.config,
        output_directory=args.output_directory,
        report_name=args.report_name,
        updated_since=args.updated_since,
        disable_console=args.disable_console_log or not args.show_console_log,
        simple_console=args.simple_console,
        console_level=args.console_level,
        create_review_template=not args.skip_review_template,
        show_console_log=args.show_console_log,
    )
    fetch_and_analyze(options, base_dir=base_dir)


if __name__ == "__main__":
    main()

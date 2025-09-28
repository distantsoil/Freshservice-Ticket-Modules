#!/usr/bin/env python3
"""macOS entry point for Freshservice advanced reporting."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = BASE_DIR / "config" / "config.yaml"
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from python_common.workflow import ReportOptions, generate_reports


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate HTML/PDF/image reports for Freshservice tickets (macOS edition)."
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
        help="Directory where the report bundle should be written. Overrides configuration defaults.",
    )
    parser.add_argument("--start-date", help="Optional ISO8601 start date filter (UTC assumed if no timezone).")
    parser.add_argument("--end-date", help="Optional ISO8601 end date filter (UTC assumed if no timezone).")
    parser.add_argument(
        "--category",
        action="append",
        help="Filter tickets by category. Can be supplied multiple times.",
    )
    parser.add_argument(
        "--sub-category",
        action="append",
        help="Filter tickets by subcategory. Can be supplied multiple times.",
    )
    parser.add_argument(
        "--format",
        action="append",
        choices=["html", "pdf", "images", "json"],
        help="Report output formats to generate (default: html, pdf, images, json).",
    )
    parser.add_argument(
        "--disable-console-log",
        action="store_true",
        help="Disable console logging output (deprecated; logs hidden unless --show-console-log is provided).",
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
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    base_dir = BASE_DIR
    options = ReportOptions(
        config_path=args.config,
        output_directory=args.output_directory,
        start_date=args.start_date,
        end_date=args.end_date,
        categories=args.category,
        sub_categories=args.sub_category,
        formats=args.format,
        disable_console=args.disable_console_log or not args.show_console_log,
        simple_console=args.simple_console,
        console_level=args.console_level,
        show_console_log=args.show_console_log,
    )
    report_dir = generate_reports(options, base_dir=base_dir)
    print(f"Report bundle available at {report_dir}")


if __name__ == "__main__":
    main()

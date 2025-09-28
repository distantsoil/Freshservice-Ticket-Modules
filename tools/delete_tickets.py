#!/usr/bin/env python3
"""Utility for deleting Freshservice tickets by ID or CSV list."""

from __future__ import annotations

import argparse
import csv
import re
import logging
import sys
from pathlib import Path
from typing import Iterable, List, Sequence, Set

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
        description=(
            "Delete Freshservice tickets by ID. Supply one or more --ticket-id flags "
            "for ad-hoc deletions or point --csv at a file containing a ticket_id column."
        )
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help=(
            "Path to configuration YAML file (defaults to "
            "freshservice_ticket_insights/config/config.yaml)."
        ),
    )
    parser.add_argument(
        "--ticket-id",
        dest="ticket_ids",
        action="append",
        type=int,
        help=(
            "Ticket ID to delete. Provide multiple --ticket-id flags to remove more than one "
            "ticket without preparing a CSV."
        ),
    )
    parser.add_argument(
        "--csv",
        help=(
            "Path to a CSV file containing a 'ticket_id' (or 'id') column listing tickets to delete."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log which tickets would be deleted without calling the API.",
    )
    return parser


_HEADER_CANDIDATES = ("ticket_id", "ticketid", "id")


def _normalize_header(name: str) -> str:
    """Return a canonical form for matching CSV headers."""

    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower())


def _parse_csv(path: Path) -> List[int]:
    ids: List[int] = []
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise ValueError("CSV is missing a header row")
            column_name = None
            normalized_fields = {
                _normalize_header(field): field
                for field in reader.fieldnames
                if field is not None
            }
            for candidate in _HEADER_CANDIDATES:
                normalized = _normalize_header(candidate)
                if normalized in normalized_fields:
                    column_name = normalized_fields[normalized]
                    break
            if column_name is None:
                raise ValueError(
                    "CSV must include a 'ticket_id' column (aliases: 'id') for deletion"
                )
            for row in reader:
                raw_value = (row.get(column_name) or "").strip()
                if not raw_value:
                    continue
                try:
                    ids.append(int(raw_value))
                except ValueError as exc:  # pragma: no cover - defensive logging
                    LOGGER.warning("Skipping non-integer ticket_id '%s': %s", raw_value, exc)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"CSV file not found: {path}") from exc
    return ids


def _collect_ticket_ids(ticket_ids: Sequence[int] | None, csv_path: str | None) -> List[int]:
    collected: Set[int] = set()
    if ticket_ids:
        collected.update(ticket_ids)
    if csv_path:
        csv_ids = _parse_csv(Path(csv_path))
        collected.update(csv_ids)
    ordered = sorted(collected)
    return ordered


def run(
    *,
    config_path: str | None,
    ticket_ids: Sequence[int] | None,
    csv_path: str | None,
    dry_run: bool = False,
) -> int:
    ids = _collect_ticket_ids(ticket_ids, csv_path)
    if not ids:
        LOGGER.error("No ticket IDs provided. Use --ticket-id and/or --csv.")
        return 1

    config = load_config(config_path)
    configure_logging(config, base_dir=BASE_DIR)
    client = _create_client(config)

    failures = 0
    for ticket_id in ids:
        if dry_run:
            LOGGER.info("[dry-run] Would delete ticket %s", ticket_id)
            continue
        try:
            client.delete_ticket(ticket_id)
            LOGGER.info("Deleted ticket %s", ticket_id)
        except Exception as exc:  # pragma: no cover - defensive logging
            LOGGER.exception("Failed to delete ticket %s: %s", ticket_id, exc)
            failures += 1

    if failures:
        LOGGER.error("Failed to delete %s ticket(s)", failures)
        return 1

    LOGGER.info("Processed %s ticket(s)", len(ids))
    return 0


def main(argv: Iterable[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    exit_code = run(
        config_path=args.config,
        ticket_ids=args.ticket_ids,
        csv_path=args.csv,
        dry_run=args.dry_run,
    )
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()

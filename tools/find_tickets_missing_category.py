#!/usr/bin/env python3
"""List Freshservice tickets created in the last N days with no category.

Uses repo config (config/config.yaml) and the existing API client. Handy for
quick audits such as "tickets in the last 7 or 14 days without a category".
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from dateutil import parser as date_parser

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = BASE_DIR / "config" / "config.yaml"
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from python_common.config import load_config  # type: ignore  # pylint: disable=import-error
from python_common.logging_setup import configure_logging  # type: ignore  # pylint: disable=import-error
from python_common.workflow import _create_client  # type: ignore  # pylint: disable=import-error

LOGGER = logging.getLogger(__name__)


def _parse_iso_dt(value: str | None) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = date_parser.isoparse(value)
    except Exception:  # pragma: no cover - defensive
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "List tickets created in the last N days that do not have a category.\n"
            "Examples:\n"
            "  find_tickets_missing_category.py --days 7 --output table\n"
            "  find_tickets_missing_category.py --days 14 --output csv > missing.csv\n"
        ),
    )
    p.add_argument(
        "--config",
        help=f"Path to configuration YAML (default {DEFAULT_CONFIG_PATH})",
    )
    p.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of days back from now to include (created_at >= now-N). Default: 7",
    )
    p.add_argument(
        "--output",
        choices=["table", "csv", "json"],
        default="table",
        help="Output format. Default: table",
    )
    p.add_argument(
        "--updated-since",
        help=(
            "Optional ISO8601 timestamp to limit API retrieval (server-side). "
            "Filtering by created_at still happens client-side."
        ),
    )
    return p


@dataclass
class Row:
    id: int
    created_at: str
    subject: str
    requester_id: int | None
    status: int | None
    priority: int | None
    has_category: bool


def _collect_rows(client, *, since: datetime, updated_since: str | None) -> List[Row]:
    rows: List[Row] = []
    cutoff = since.astimezone(timezone.utc)
    for ticket in client.iter_tickets(updated_since=updated_since):
        created = _parse_iso_dt(ticket.get("created_at"))
        if not created or created < cutoff:
            continue
        category = ticket.get("category") or None
        if category:
            # We only want tickets without a category
            continue
        rows.append(
            Row(
                id=int(ticket.get("id", 0)),
                created_at=created.isoformat(),
                subject=str(ticket.get("subject") or ""),
                requester_id=ticket.get("requester_id"),
                status=ticket.get("status"),
                priority=ticket.get("priority"),
                has_category=False,
            )
        )
    rows.sort(key=lambda r: r.created_at, reverse=True)
    return rows


def _print_table(rows: List[Row]) -> None:
    if not rows:
        print("No tickets found.")
        return
    headers = ["ID", "Created (UTC)", "Requester", "Status", "Priority", "Subject"]
    data = [
        [str(r.id), r.created_at, str(r.requester_id or "-"), str(r.status or "-"), str(r.priority or "-"), r.subject]
        for r in rows
    ]
    # compute widths
    widths = [len(h) for h in headers]
    for row in data:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    header_line = "| " + " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers)) + " |"
    print(sep)
    print(header_line)
    print(sep)
    for row in data:
        print("| " + " | ".join(row[i].ljust(widths[i]) for i in range(len(headers))) + " |")
    print(sep)
    print(f"Total: {len(rows)}")


def _print_csv(rows: List[Row]) -> None:
    writer = csv.writer(sys.stdout)
    writer.writerow(["id", "created_at", "requester_id", "status", "priority", "subject"])
    for r in rows:
        writer.writerow([r.id, r.created_at, r.requester_id or "", r.status or "", r.priority or "", r.subject])


def _print_json(rows: List[Row]) -> None:
    payload: List[Dict[str, Any]] = [
        {
            "id": r.id,
            "created_at": r.created_at,
            "requester_id": r.requester_id,
            "status": r.status,
            "priority": r.priority,
            "subject": r.subject,
        }
        for r in rows
    ]
    json.dump(payload, sys.stdout, indent=2)
    print()


def run(config_path: str | None, *, days: int, output: str, updated_since: str | None) -> int:
    config = load_config(config_path)
    configure_logging(config, base_dir=BASE_DIR)
    client = _create_client(config)

    now = datetime.utcnow().replace(tzinfo=timezone.utc)
    cutoff = now - timedelta(days=max(1, days))
    LOGGER.info("Filtering for tickets created since %s (UTC)", cutoff.isoformat())

    rows = _collect_rows(client, since=cutoff, updated_since=updated_since)
    LOGGER.info("Found %s tickets without a category in last %s days", len(rows), days)

    if output == "table":
        _print_table(rows)
    elif output == "csv":
        _print_csv(rows)
    else:
        _print_json(rows)
    return 0


def main(argv: Iterable[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        exit_code = run(args.config, days=args.days, output=args.output, updated_since=args.updated_since)
    except Exception as exc:  # pragma: no cover - defensive logging
        LOGGER.exception("Failed to list tickets: %s", exc)
        raise SystemExit(1) from exc
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()

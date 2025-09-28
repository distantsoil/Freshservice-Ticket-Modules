#!/usr/bin/env python3
"""Bulk update Freshservice requester organizations from a CSV mapping."""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = BASE_DIR / "config" / "config.yaml"

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from python_common.config import load_config  # type: ignore  # pylint: disable=import-error
from python_common.logging_setup import configure_logging  # type: ignore  # pylint: disable=import-error
from python_common.workflow import _create_client  # type: ignore  # pylint: disable=import-error

LOGGER = logging.getLogger(__name__)


@dataclass
class RequesterUpdate:
    requester_id: Optional[int]
    email: Optional[str]
    organization: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Update Freshservice requester organization fields using a CSV export. "
            "Each row must include an 'organization' column and either a 'requester_id' "
            "(preferred) or 'email' column."
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
        "--csv",
        required=True,
        help="CSV file containing requester mappings and organization values.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log proposed updates without calling the Freshservice API.",
    )
    return parser


def _parse_csv(path: Path) -> List[RequesterUpdate]:
    updates: List[RequesterUpdate] = []
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise ValueError("CSV is missing a header row")
            for row in reader:
                organization = (row.get("organization") or row.get("Organisation") or "").strip()
                if not organization:
                    LOGGER.debug("Skipping row without organization value: %s", row)
                    continue
                requester_id: Optional[int] = None
                if row.get("requester_id"):
                    try:
                        requester_id = int(str(row["requester_id"]).strip())
                    except ValueError as exc:
                        raise ValueError(
                            f"Invalid requester_id '{row['requester_id']}' in CSV"
                        ) from exc
                elif row.get("id"):
                    try:
                        requester_id = int(str(row["id"]).strip())
                    except ValueError as exc:
                        raise ValueError(f"Invalid id '{row['id']}' in CSV") from exc
                email = (row.get("email") or row.get("primary_email") or "").strip()
                if requester_id is None and not email:
                    LOGGER.warning(
                        "Skipping row missing requester_id and email: %s",
                        row,
                    )
                    continue
                updates.append(
                    RequesterUpdate(
                        requester_id=requester_id,
                        email=email or None,
                        organization=organization,
                    )
                )
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"CSV file not found: {path}") from exc
    return updates


def _load_requester_directory(client) -> Dict[int, Dict[str, object]]:
    directory: Dict[int, Dict[str, object]] = {}
    for requester in client.iter_requesters():
        requester_id = requester.get("id")
        if isinstance(requester_id, int):
            directory[requester_id] = requester
    LOGGER.info("Loaded %s requester profiles", len(directory))
    return directory


def _build_email_index(requesters: Dict[int, Dict[str, object]]) -> Dict[str, int]:
    index: Dict[str, int] = {}
    for requester_id, payload in requesters.items():
        email = payload.get("email") or payload.get("primary_email")
        if isinstance(email, str) and email:
            index[email.lower()] = requester_id
    return index


def _resolve_requester_id(
    update: RequesterUpdate,
    *,
    email_index: Dict[str, int],
) -> Optional[int]:
    if update.requester_id is not None:
        return update.requester_id
    if update.email:
        return email_index.get(update.email.lower())
    return None


def _should_update(existing: Dict[str, object] | None, organization: str) -> bool:
    if not existing:
        return True
    current = existing.get("organization")
    if isinstance(current, str):
        return current.strip() != organization
    return True


def run(*, config_path: str | None, csv_path: str, dry_run: bool) -> int:
    updates = _parse_csv(Path(csv_path))
    if not updates:
        LOGGER.error("No valid requester rows found in %s", csv_path)
        return 1

    config = load_config(config_path)
    configure_logging(config, base_dir=BASE_DIR)
    client = _create_client(config)

    requesters = _load_requester_directory(client)
    email_index = _build_email_index(requesters)

    missing_identifiers = 0
    skipped_unchanged = 0
    updated = 0

    for update in updates:
        requester_id = _resolve_requester_id(update, email_index=email_index)
        if requester_id is None:
            LOGGER.warning(
                "Skipping requester with unresolved identifier (email=%s)", update.email
            )
            missing_identifiers += 1
            continue
        existing = requesters.get(requester_id)
        if not _should_update(existing, update.organization):
            LOGGER.info(
                "Requester %s already has organization '%s'; skipping",
                requester_id,
                update.organization,
            )
            skipped_unchanged += 1
            continue
        if dry_run:
            LOGGER.info(
                "[dry-run] Would update requester %s (%s) organization -> %s",
                requester_id,
                (existing or {}).get("email"),
                update.organization,
            )
            updated += 1
            continue
        try:
            client.update_requester(requester_id, {"organization": update.organization})
            LOGGER.info(
                "Updated requester %s organization to '%s'", requester_id, update.organization
            )
            updated += 1
        except Exception as exc:  # pragma: no cover - defensive logging
            LOGGER.exception(
                "Failed to update requester %s (%s): %s",
                requester_id,
                (existing or {}).get("email"),
                exc,
            )

    LOGGER.info(
        "Processed %s rows: %s updated, %s unchanged, %s unresolved",
        len(updates),
        updated,
        skipped_unchanged,
        missing_identifiers,
    )

    return 0 if updated or dry_run else 1 if missing_identifiers == len(updates) else 0


def main(argv: Iterable[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    exit_code = run(config_path=args.config, csv_path=args.csv, dry_run=args.dry_run)
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()

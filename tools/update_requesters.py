#!/usr/bin/env python3
"""Interactively update Freshservice requester profiles."""

from __future__ import annotations

import argparse
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
class FieldUpdate:
    field: str
    value: object


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Update Freshservice requester fields. Provide one or more requester IDs "
            "or email addresses and at least one --set/--unset field option."
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
        "--requester-id",
        dest="requester_ids",
        type=int,
        action="append",
        help="Requester ID to update. May be supplied multiple times.",
    )
    parser.add_argument(
        "--email",
        dest="emails",
        action="append",
        help="Requester email to update. May be supplied multiple times.",
    )
    parser.add_argument(
        "--set",
        dest="set_fields",
        action="append",
        default=[],
        metavar="FIELD=VALUE",
        help=(
            "Field assignment in the form field=value. Repeat for multiple fields. "
            "Values such as true/false/null/ints are converted automatically."
        ),
    )
    parser.add_argument(
        "--set-json",
        dest="set_json_fields",
        action="append",
        default=[],
        metavar="FIELD=JSON",
        help="Field assignment parsed as JSON for complex payloads (objects/arrays).",
    )
    parser.add_argument(
        "--unset",
        dest="unset_fields",
        action="append",
        default=[],
        metavar="FIELD",
        help="Clear the specified field by sending it as null.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log proposed updates without calling the Freshservice API.",
    )
    return parser


def _convert_scalar(value: str) -> object:
    lowered = value.strip().lower()
    if lowered in {"", "null", "none"}:
        return None
    if lowered in {"true", "yes"}:
        return True
    if lowered in {"false", "no"}:
        return False
    try:
        if lowered.startswith("0") and lowered != "0":
            raise ValueError  # preserve leading zeros as string
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _parse_set_fields(entries: Iterable[str]) -> List[FieldUpdate]:
    updates: List[FieldUpdate] = []
    for entry in entries:
        if "=" not in entry:
            raise ValueError(f"--set requires FIELD=VALUE (got '{entry}')")
        field, value = entry.split("=", 1)
        field = field.strip()
        if not field:
            raise ValueError("Field name cannot be empty in --set option")
        updates.append(FieldUpdate(field=field, value=_convert_scalar(value)))
    return updates


def _parse_set_json_fields(entries: Iterable[str]) -> List[FieldUpdate]:
    import json

    updates: List[FieldUpdate] = []
    for entry in entries:
        if "=" not in entry:
            raise ValueError(f"--set-json requires FIELD=JSON (got '{entry}')")
        field, value = entry.split("=", 1)
        field = field.strip()
        if not field:
            raise ValueError("Field name cannot be empty in --set-json option")
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive
            raise ValueError(f"Invalid JSON payload for field '{field}': {value}") from exc
        updates.append(FieldUpdate(field=field, value=parsed))
    return updates


def _build_updates(
    *,
    set_fields: Iterable[str],
    set_json_fields: Iterable[str],
    unset_fields: Iterable[str],
) -> Dict[str, object]:
    updates: Dict[str, object] = {}
    for update in _parse_set_fields(set_fields):
        updates[update.field] = update.value
    for update in _parse_set_json_fields(set_json_fields):
        updates[update.field] = update.value
    for field in unset_fields:
        field_name = field.strip()
        if not field_name:
            raise ValueError("Field name cannot be empty in --unset option")
        updates[field_name] = None
    return updates


def _load_requester_directory(client) -> Dict[int, Dict[str, object]]:
    directory: Dict[int, Dict[str, object]] = {}
    for requester in client.iter_requesters():
        requester_id = requester.get("id")
        if isinstance(requester_id, int):
            directory[requester_id] = requester
    return directory


def _build_email_index(requesters: Dict[int, Dict[str, object]]) -> Dict[str, int]:
    index: Dict[str, int] = {}
    for requester_id, payload in requesters.items():
        email = payload.get("email") or payload.get("primary_email")
        if isinstance(email, str) and email:
            index[email.lower()] = requester_id
    return index


def _resolve_targets(
    *,
    client,
    requester_ids: Iterable[int],
    emails: Iterable[str],
) -> Dict[int, Dict[str, object]]:
    resolved: Dict[int, Dict[str, object]] = {}
    missing: List[str] = []

    ids = list(dict.fromkeys(requester_ids))
    email_list = list(dict.fromkeys(email.lower() for email in emails))

    if email_list:
        directory = _load_requester_directory(client)
        email_index = _build_email_index(directory)
        for email in email_list:
            requester_id = email_index.get(email)
            if requester_id is None:
                missing.append(f"email={email}")
                continue
            resolved[requester_id] = directory[requester_id]

    for requester_id in ids:
        if requester_id in resolved:
            continue
        try:
            profile = client.get_requester(requester_id)
        except Exception as exc:  # pragma: no cover - defensive
            LOGGER.error("Failed to fetch requester %s: %s", requester_id, exc)
            missing.append(f"id={requester_id}")
            continue
        if not profile:
            missing.append(f"id={requester_id}")
            continue
        resolved[requester_id] = profile

    if missing:
        LOGGER.warning("Could not resolve the following requester identifiers: %s", ", ".join(missing))
    return resolved


def _changes_required(existing: Dict[str, object], updates: Dict[str, object]) -> Dict[str, object]:
    changes: Dict[str, object] = {}
    for field, value in updates.items():
        current = existing.get(field)
        if current != value:
            changes[field] = value
    return changes


def run(
    *,
    config_path: Optional[str],
    requester_ids: Optional[Iterable[int]],
    emails: Optional[Iterable[str]],
    updates: Dict[str, object],
    dry_run: bool,
) -> int:
    requester_ids = list(requester_ids or [])
    emails = list(emails or [])
    if not requester_ids and not emails:
        LOGGER.error("At least one --requester-id or --email must be supplied")
        return 1
    if not updates:
        LOGGER.error("Provide at least one field via --set/--set-json/--unset")
        return 1

    config = load_config(config_path)
    configure_logging(config, base_dir=BASE_DIR)
    client = _create_client(config)

    targets = _resolve_targets(
        client=client,
        requester_ids=requester_ids,
        emails=emails,
    )
    if not targets:
        LOGGER.error("No matching requester profiles were found")
        return 1

    updated = 0
    skipped = 0
    for requester_id, profile in targets.items():
        changes = _changes_required(profile, updates)
        if not changes:
            LOGGER.info("Requester %s already matches requested values; skipping", requester_id)
            skipped += 1
            continue
        if dry_run:
            LOGGER.info("[dry-run] Would update requester %s with %s", requester_id, changes)
            updated += 1
            continue
        try:
            client.update_requester(requester_id, changes)
            LOGGER.info("Updated requester %s with %s", requester_id, changes)
            updated += 1
        except Exception:  # pragma: no cover - defensive logging
            LOGGER.exception("Failed to update requester %s", requester_id)
    LOGGER.info(
        "Requester update summary: %s updated, %s skipped, %s total", updated, skipped, len(targets)
    )
    return 0 if updated or skipped else 1


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        updates = _build_updates(
            set_fields=args.set_fields,
            set_json_fields=args.set_json_fields,
            unset_fields=args.unset_fields,
        )
    except ValueError as exc:
        parser.error(str(exc))
        return

    exit_code = run(
        config_path=args.config,
        requester_ids=args.requester_ids,
        emails=args.emails,
        updates=updates,
        dry_run=args.dry_run,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()

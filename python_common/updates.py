"""Apply updates to ticket categories based on review decisions."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

try:  # pragma: no cover - fallback when requests is unavailable during tests
    from requests import HTTPError  # type: ignore
except (ModuleNotFoundError, ImportError):  # pragma: no cover
    try:  # pragma: no cover
        from requests.exceptions import HTTPError  # type: ignore
    except (ModuleNotFoundError, ImportError):  # pragma: no cover
        class HTTPError(Exception):
            def __init__(self, *args: Any, response: Any | None = None, **kwargs: Any) -> None:
                super().__init__(*args)
                self.response = response

from .freshservice_client import FreshserviceClient
from .review import ReviewRow

LOGGER = logging.getLogger(__name__)


@dataclass
class UpdateError:
    """Represents a failed ticket update."""

    ticket_id: int
    message: str
    status_code: Optional[int]
    decision: str
    category_path: str


class UpdateTracker:
    """Persistently track ticket IDs that have already been updated."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._loaded = False
        self._dirty = False
        self._ticket_ids: Set[int] = set()

    def load(self) -> None:
        if self._loaded:
            return
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                text = line.strip()
                if not text or text.startswith("#"):
                    continue
                try:
                    self._ticket_ids.add(int(text))
                except ValueError:
                    LOGGER.warning(
                        "Ignoring invalid ticket id '%s' in update tracker %s", text, self.path
                    )
        self._loaded = True

    def contains(self, ticket_id: int) -> bool:
        self.load()
        return ticket_id in self._ticket_ids

    def mark_updated(self, ticket_id: int) -> None:
        self.load()
        if ticket_id not in self._ticket_ids:
            self._ticket_ids.add(ticket_id)
            self._dirty = True

    def save(self) -> None:
        if not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as handle:
            for ticket_id in sorted(self._ticket_ids):
                handle.write(f"{ticket_id}\n")
        self._dirty = False

    def __len__(self) -> int:  # pragma: no cover - trivial utility
        self.load()
        return len(self._ticket_ids)

    def __bool__(self) -> bool:  # pragma: no cover - ensures truthiness even when empty
        return True


class TicketUpdater:
    """Perform category updates for tickets."""

    def __init__(self, client: FreshserviceClient) -> None:
        self.client = client
        self._last_errors: List[UpdateError] = []

    def update_ticket_categories(
        self,
        updates: Iterable[ReviewRow],
        *,
        dry_run: bool = False,
        skip_tracker: UpdateTracker | None = None,
        force_ticket_ids: Set[int] | None = None,
        force_all: bool = False,
        progress_callback: Callable[[int, Optional[int]], None] | None = None,
        total_rows: Optional[int] = None,
        error_collector: Optional[List[UpdateError]] = None,
    ) -> List[Dict]:
        responses: List[Dict] = []
        collected_errors: List[UpdateError] = []
        force_ticket_ids = force_ticket_ids or set()
        if skip_tracker:
            skip_tracker.load()
        processed = 0
        try:
            for row in updates:
                try:
                    if row.manager_decision != "approve":
                        LOGGER.info(
                            "Skipping ticket %s because decision is %s",
                            row.ticket_id,
                            row.manager_decision,
                        )
                        continue

                    if (
                        skip_tracker
                        and not dry_run
                        and not force_all
                        and row.ticket_id not in force_ticket_ids
                        and skip_tracker.contains(row.ticket_id)
                    ):
                        LOGGER.info(
                            "Skipping ticket %s because it is recorded as already updated in %s",
                            row.ticket_id,
                            skip_tracker.path,
                        )
                        continue

                    desired_category = _normalize_value(row.final_category)
                    desired_sub = _normalize_value(row.final_sub_category)
                    desired_item = _normalize_value(row.final_item_category)

                    if not any([desired_category, desired_sub, desired_item]):
                        LOGGER.warning(
                            "Ticket %s has no fields selected for update", row.ticket_id
                        )
                        continue

                    current_category = _normalize_value(row.current_category)
                    current_sub = _normalize_value(row.current_sub_category)
                    current_item = _normalize_value(row.current_item_category)

                    if (desired_category, desired_sub, desired_item) == (
                        current_category,
                        current_sub,
                        current_item,
                    ):
                        LOGGER.info(
                            "Skipping ticket %s because taxonomy already matches (%s)",
                            row.ticket_id,
                            _summarize_path(current_category, current_sub, current_item),
                        )
                        continue

                    payload: Dict[str, Dict] = {
                        "ticket": {
                            key: value
                            for key, value in (
                                ("category", desired_category),
                                ("sub_category", desired_sub),
                                ("item_category", desired_item),
                            )
                            if value is not None
                        }
                    }

                    if dry_run:
                        LOGGER.info(
                            "Dry run: ticket %s would be updated to %s (confidence=%s)",
                            row.ticket_id,
                            _summarize_path(desired_category, desired_sub, desired_item),
                            row.suggestion_confidence
                            if row.suggestion_confidence is not None
                            else "n/a",
                        )
                        continue

                    LOGGER.debug("Updating ticket %s with payload %s", row.ticket_id, payload)
                    try:
                        response = self._submit_with_retry(row.ticket_id, payload)
                    except HTTPError as exc:
                        message = _describe_http_error(exc, row.ticket_id)
                        LOGGER.error(message)
                        collected_errors.append(
                            UpdateError(
                                ticket_id=row.ticket_id,
                                message=message,
                                status_code=getattr(getattr(exc, "response", None), "status_code", None),
                                decision=row.manager_decision,
                                category_path=_summarize_path(
                                    desired_category,
                                    desired_sub,
                                    desired_item,
                                ),
                            )
                        )
                        continue
                    except Exception as exc:  # pragma: no cover - defensive logging
                        LOGGER.exception(
                            "Unexpected error while updating ticket %s", row.ticket_id
                        )
                        collected_errors.append(
                            UpdateError(
                                ticket_id=row.ticket_id,
                                message=str(exc),
                                status_code=None,
                                decision=row.manager_decision,
                                category_path=_summarize_path(
                                    desired_category,
                                    desired_sub,
                                    desired_item,
                                ),
                            )
                        )
                        continue

                    if response is not None:
                        _log_response_summary(row.ticket_id, response)
                        responses.append(response)
                        if skip_tracker:
                            skip_tracker.mark_updated(row.ticket_id)
                            skip_tracker.save()
                finally:
                    processed += 1
                    if progress_callback:
                        progress_callback(processed, total_rows)
        finally:
            if skip_tracker and not dry_run:
                skip_tracker.save()
        if error_collector is not None:
            error_collector.extend(collected_errors)
        self._last_errors = collected_errors
        return responses

    def get_last_errors(self) -> List[UpdateError]:
        """Return a copy of the most recent update errors."""

        return list(self._last_errors)

    def update_single_ticket(
        self,
        ticket_id: int,
        *,
        category: str | None = None,
        sub_category: str | None = None,
        item_category: str | None = None,
        dry_run: bool = False,
    ) -> Optional[Dict]:
        desired_category = _normalize_value(category)
        desired_sub = _normalize_value(sub_category)
        desired_item = _normalize_value(item_category)

        if not any([desired_category, desired_sub, desired_item]):
            raise ValueError(
                "No category, sub_category, or item_category values were provided for update"
            )

        if dry_run:
            LOGGER.info(
                "Dry run: ticket %s would be updated to %s",
                ticket_id,
                _summarize_path(desired_category, desired_sub, desired_item),
            )
            return None

        current = self.client.get_ticket(ticket_id)
        current_category = _normalize_value(current.get("category"))
        current_sub = _normalize_value(current.get("sub_category"))
        current_item = _normalize_value(current.get("item_category"))
        if (desired_category, desired_sub, desired_item) == (current_category, current_sub, current_item):
            LOGGER.info(
                "Skipping ticket %s because taxonomy already matches (%s)",
                ticket_id,
                _summarize_path(current_category, current_sub, current_item),
            )
            return current

        payload: Dict[str, Dict] = {
            "ticket": {
                key: value
                for key, value in (
                    ("category", desired_category),
                    ("sub_category", desired_sub),
                    ("item_category", desired_item),
                )
                if value is not None
            }
        }
        LOGGER.debug("Updating single ticket %s", ticket_id)
        try:
            response = self._submit_with_retry(ticket_id, payload)
        except HTTPError as exc:
            LOGGER.error(_describe_http_error(exc, ticket_id))
            raise
        except Exception:  # pragma: no cover - defensive logging
            LOGGER.exception("Unexpected error while updating ticket %s", ticket_id)
            raise
        if response is not None:
            _log_response_summary(ticket_id, response)
        return response

    def _submit_with_retry(self, ticket_id: int, payload: Dict[str, Dict]) -> Optional[Dict]:
        max_attempts = 3
        attempt = 0
        while True:
            attempt += 1
            try:
                return self.client.update_ticket(ticket_id, payload)
            except HTTPError as exc:
                if _is_rate_limit_error(exc):
                    delay = _rate_limit_delay(self.client)
                    if delay <= 0 or attempt >= max_attempts:
                        raise
                    LOGGER.warning(
                        "Received 429 from Freshservice while updating %s; sleeping %.2fs before retry",
                        ticket_id,
                        delay,
                    )
                    time.sleep(delay)
                    continue
                raise


def _normalize_value(value: str | None) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _summarize_path(category: Optional[str], sub: Optional[str], item: Optional[str]) -> str:
    parts = [part for part in (category, sub, item) if part]
    return " > ".join(parts) if parts else "<no category>"


def _log_response_summary(ticket_id: int, response: Dict[str, Any]) -> None:
    summary = {
        key: response.get(key)
        for key in ("id", "category", "sub_category", "item_category", "updated_at")
        if key in response
    }
    LOGGER.info("Updated ticket %s response summary: %s", ticket_id, summary)


def _is_rate_limit_error(error: HTTPError) -> bool:
    response = getattr(error, "response", None)
    return getattr(response, "status_code", None) == 429


def _rate_limit_delay(client: FreshserviceClient) -> float:
    delay = getattr(client, "_sleep_between_requests", 0.0)
    try:
        return float(delay)
    except (TypeError, ValueError):
        return 0.0


def _describe_http_error(error: HTTPError, ticket_id: Optional[int] = None) -> str:
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    reason = getattr(response, "reason", "") or ""
    hint_map = {
        400: "Bad Request - verify the payload and category labels",
        401: "Unauthorized - check the API key",
        403: "Forbidden - the API key lacks permission",
        404: "Not Found - the ticket or endpoint may be incorrect",
        409: "Conflict - the ticket may have been updated elsewhere",
        422: "Unprocessable Entity - Freshservice rejected the field values",
        429: "Too Many Requests - rate limit exceeded",
        500: "Internal Server Error",
        502: "Bad Gateway",
        503: "Service Unavailable",
        504: "Gateway Timeout",
    }
    prefix = "Freshservice request failed"
    if ticket_id is not None:
        prefix = f"Freshservice request failed for ticket {ticket_id}"
    if status is not None:
        hint = hint_map.get(status)
        status_part = f"status {status}"
        if reason:
            status_part += f" {reason}".rstrip()
        if hint:
            status_part += f" ({hint})"
        prefix = f"{prefix} with {status_part}"

    detail = ""
    if response is not None:
        parsed = None
        try:
            parsed = response.json()
        except Exception:  # pragma: no cover - fall back to text
            parsed = None
        if isinstance(parsed, dict):
            errors = parsed.get("errors")
            message = parsed.get("message")
            if isinstance(errors, list):
                detail = "; ".join(str(item) for item in errors if item)
            elif isinstance(errors, dict):
                detail = "; ".join(f"{key}: {value}" for key, value in errors.items())
            elif message:
                detail = str(message)
        if not detail:
            text = getattr(response, "text", "")
            if text:
                detail = text.strip()
    if detail:
        snippet = detail if len(detail) <= 500 else detail[:497] + "..."
        prefix = f"{prefix}: {snippet}"
    return prefix


def describe_http_error(error: HTTPError, ticket_id: Optional[int] = None) -> str:
    """Expose the descriptive HTTP error helper for external callers."""

    return _describe_http_error(error, ticket_id)


def summarize_category_path(
    category: Optional[str], sub: Optional[str], item: Optional[str]
) -> str:
    """Public helper mirroring the internal category path summary."""

    return _summarize_path(category, sub, item)

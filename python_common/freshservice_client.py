"""HTTP client for the Freshservice REST API."""
from __future__ import annotations

import logging
import time
from collections.abc import Iterable as IterableABC
from dataclasses import dataclass
from typing import Any, Callable, Dict, Generator, Iterable, Optional, Sequence
from urllib.parse import urljoin

import requests

LOGGER = logging.getLogger(__name__)


@dataclass
class FreshserviceAuth:
    api_key: str

    def as_tuple(self) -> tuple[str, str]:
        return (self.api_key, "X")


class FreshserviceClient:
    """Wrapper around the Freshservice API used for tickets and metadata."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        verify_ssl: bool = True,
        timeout: int = 30,
        per_page: int = 100,
        rate_limit_per_minute: Optional[int] = None,
    ) -> None:
        self.base_url = self._normalise_base_url(base_url)
        if self.base_url.rstrip("/") != base_url.rstrip("/"):
            LOGGER.debug(
                "Normalised Freshservice base URL from %s to %s", base_url, self.base_url
            )
        self.session = requests.Session()
        self.session.auth = FreshserviceAuth(api_key).as_tuple()
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self.per_page = min(max(per_page, 30), 100)  # API maximum is 100
        self.rate_limit_per_minute = rate_limit_per_minute
        self._sleep_between_requests = (
            60.0 / rate_limit_per_minute if rate_limit_per_minute else 0.0
        )
        self._last_request_time: float | None = None

    # -- Low level request helpers -------------------------------------------------
    def _request(self, method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
        url = self._build_url(path)
        if self._sleep_between_requests and self._last_request_time is not None:
            elapsed = time.monotonic() - self._last_request_time
            remaining = self._sleep_between_requests - elapsed
            if remaining > 0:
                LOGGER.debug(
                    "Sleeping %.2fs before %s %s to respect rate limits",
                    remaining,
                    method,
                    url,
                )
                time.sleep(remaining)
        LOGGER.debug("HTTP %s %s payload=%s", method, url, kwargs.get("json"))
        response = self.session.request(
            method,
            url,
            timeout=self.timeout,
            verify=self.verify_ssl,
            **kwargs,
        )
        self._last_request_time = time.monotonic()
        LOGGER.debug("Response status=%s", response.status_code)
        response.raise_for_status()
        if response.content:
            return response.json()
        return {}

    def _normalise_base_url(self, base_url: str) -> str:
        """Trim common API suffixes and return a clean base domain."""

        cleaned = base_url.strip()
        cleaned = cleaned.rstrip("/")
        if cleaned.lower().endswith("/api/v2"):
            cleaned = cleaned[: -len("/api/v2")]
        cleaned = cleaned.rstrip("/")
        return cleaned or base_url.rstrip("/")

    def _build_url(self, path: str) -> str:
        """Safely join the base URL and request path."""

        normalised_path = path.lstrip("/")
        base = self.base_url.rstrip("/") + "/"
        return urljoin(base, normalised_path)

    # -- Public API ----------------------------------------------------------------
    def iter_tickets(
        self,
        *,
        updated_since: Optional[str] = None,
        include: Optional[Sequence[str]] = None,
        progress_callback: Optional[Callable[[int, Optional[int]], None]] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        """Yield tickets from the Freshservice API handling pagination.

        When ``progress_callback`` is provided it is invoked after each page is
        processed with ``(processed_count, total_estimate)`` so callers can
        render progress indicators.
        """
        page = 1
        processed = 0
        total_estimate: Optional[int] = None
        while True:
            params: Dict[str, Any] = {"per_page": self.per_page, "page": page}
            if updated_since:
                params["updated_since"] = updated_since
            if include:
                params["include"] = ",".join(sorted(set(include)))
            payload = self._request("GET", "/api/v2/tickets", params=params)
            tickets = payload.get("tickets", [])
            meta = payload.get("meta") if isinstance(payload, dict) else None
            if isinstance(meta, dict):
                total_value = meta.get("total_items")
                if isinstance(total_value, int) and total_value >= 0:
                    total_estimate = total_value
            LOGGER.info("Fetched %s tickets from page %s", len(tickets), page)
            for ticket in tickets:
                yield ticket
            processed += len(tickets)
            if progress_callback:
                progress_callback(processed, total_estimate)
            if len(tickets) < self.per_page:
                break
            page += 1
            if self._sleep_between_requests:
                LOGGER.debug("Sleeping %.2fs to respect rate limits", self._sleep_between_requests)
                time.sleep(self._sleep_between_requests)

    def get_ticket(self, ticket_id: int) -> Dict[str, Any]:
        payload = self._request("GET", f"/api/v2/tickets/{ticket_id}")
        return payload.get("ticket", {})

    def iter_ticket_fields(self) -> Iterable[Dict[str, Any]]:
        """Yield ticket field metadata from the documented form-fields API.

        Freshservice exposes ticket field definitions via the
        ``/api/v2/ticket_form_fields`` endpoint. Depending on the account, the
        response body may wrap the field collection in either a
        ``ticket_form_fields`` key (per the documentation) or a legacy
        ``ticket_fields``/``fields`` key. We coerce the payload into an
        iterable of dictionaries so downstream taxonomy extraction can operate
        consistently regardless of the wrapper that was returned.
        """

        payload = self._request("GET", "/api/v2/ticket_form_fields")
        if isinstance(payload, dict):
            fields = (
                payload.get("ticket_form_fields")
                or payload.get("ticket_fields")
                or payload.get("fields")
            )
            if fields is not None:
                if isinstance(fields, dict):
                    return list(fields.values())
                if isinstance(fields, list):
                    return fields
                if isinstance(fields, IterableABC) and not isinstance(fields, (str, bytes)):
                    return list(fields)
        # Fall back to an empty list to keep downstream callers predictable.
        return []

    def update_ticket(self, ticket_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        LOGGER.info("Updating ticket %s with payload %s", ticket_id, data)
        payload = self._request("PUT", f"/api/v2/tickets/{ticket_id}", json=data)
        return payload.get("ticket", {})

    def delete_ticket(self, ticket_id: int) -> bool:
        """Remove a ticket via the documented delete endpoint.

        Returns ``True`` when the API confirms the deletion.  Freshservice
        responds with ``204 No Content`` on success, so we simply surface a
        boolean for convenience and to aid unit testing.
        """

        LOGGER.info("Deleting ticket %s", ticket_id)
        self._request("DELETE", f"/api/v2/tickets/{ticket_id}")
        return True

    # -- Requester helpers -------------------------------------------------------

    def iter_requesters(
        self,
        *,
        updated_since: Optional[str] = None,
        progress_callback: Optional[Callable[[int, Optional[int]], None]] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        """Yield Freshservice requesters handling pagination.

        Parameters
        ----------
        updated_since:
            Optional ISO-8601 timestamp used to filter requesters updated after
            the provided value. Mirrors the behaviour of the official
            ``GET /api/v2/requesters`` endpoint.
        progress_callback:
            Optional callable invoked after each page with the cumulative count
            processed and an optional total estimate so callers can surface
            textual progress updates.
        """

        page = 1
        processed = 0
        total_estimate: Optional[int] = None
        while True:
            params: Dict[str, Any] = {"per_page": self.per_page, "page": page}
            if updated_since:
                params["updated_since"] = updated_since
            payload = self._request("GET", "/api/v2/requesters", params=params)
            requesters = payload.get("requesters", []) if isinstance(payload, dict) else []
            meta = payload.get("meta") if isinstance(payload, dict) else None
            if isinstance(meta, dict):
                total_value = meta.get("total_items")
                if isinstance(total_value, int) and total_value >= 0:
                    total_estimate = total_value
            LOGGER.info("Fetched %s requesters from page %s", len(requesters), page)
            for requester in requesters:
                yield requester
            processed += len(requesters)
            if progress_callback:
                progress_callback(processed, total_estimate)
            if len(requesters) < self.per_page:
                break
            page += 1
            if self._sleep_between_requests:
                LOGGER.debug(
                    "Sleeping %.2fs to respect rate limits", self._sleep_between_requests
                )
                time.sleep(self._sleep_between_requests)

    def get_requester(self, requester_id: int) -> Dict[str, Any]:
        payload = self._request("GET", f"/api/v2/requesters/{requester_id}")
        if isinstance(payload, dict):
            return payload.get("requester", {})
        return {}

    def update_requester(self, requester_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        LOGGER.info("Updating requester %s with payload %s", requester_id, data)
        payload = self._request(
            "PUT",
            f"/api/v2/requesters/{requester_id}",
            json={"requester": data},
        )
        if isinstance(payload, dict):
            return payload.get("requester", {})
        return {}

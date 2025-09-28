"""Tests for FreshserviceClient URL normalisation behaviour."""

from __future__ import annotations

from importlib import util
from pathlib import Path
from typing import Any, Optional
from unittest.mock import MagicMock
import sys
import types

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "python_common" / "freshservice_client.py"

requests_stub = types.ModuleType("requests")


def _session_factory() -> MagicMock:
    session = MagicMock()
    session.headers = {}
    session.auth = None
    return session


requests_stub.Session = MagicMock(side_effect=_session_factory)
sys.modules.setdefault("requests", requests_stub)

spec = util.spec_from_file_location("python_common.freshservice_client", MODULE_PATH)
assert spec and spec.loader
freshservice_client = util.module_from_spec(spec)
sys.modules[spec.name] = freshservice_client
spec.loader.exec_module(freshservice_client)
FreshserviceClient = freshservice_client.FreshserviceClient


def _mock_response() -> MagicMock:
    response = MagicMock()
    response.content = b"{}"
    response.json.return_value = {}
    response.status_code = 200
    response.raise_for_status.return_value = None
    return response


@pytest.mark.parametrize(
    "base_url",
    [
        "https://example.freshservice.com",
        "https://example.freshservice.com/",
        "https://example.freshservice.com/api/v2",
        "https://example.freshservice.com/api/v2/",
    ],
)
def test_request_url_normalisation(base_url: str) -> None:
    client = FreshserviceClient(base_url=base_url, api_key="dummy")
    session = client.session
    session.request.return_value = _mock_response()

    client._request("GET", "/api/v2/tickets")

    session.request.assert_called_once()
    method, url, *_ = session.request.call_args[0]
    assert method == "GET"
    assert url == "https://example.freshservice.com/api/v2/tickets"


def test_request_relative_path() -> None:
    client = FreshserviceClient(base_url="https://example.freshservice.com/api/v2", api_key="dummy")
    session = client.session
    session.request.return_value = _mock_response()

    client._request("GET", "api/v2/ticket_form_fields")

    _, url, *_ = session.request.call_args[0]
    assert url == "https://example.freshservice.com/api/v2/ticket_form_fields"


@pytest.mark.parametrize(
    "payload",
    [
        {"ticket_form_fields": [{"id": 1}, {"id": 2}]},
        {"ticket_fields": [{"id": "legacy"}]},
        {"fields": [{"id": "generic"}]},
        {},
    ],
)
def test_iter_ticket_fields_payload_shapes(payload: dict[str, object]) -> None:
    client = FreshserviceClient(base_url="https://example.freshservice.com", api_key="dummy")

    def fake_request(method: str, path: str, **_: object) -> dict[str, object]:
        assert method == "GET"
        assert path == "/api/v2/ticket_form_fields"
        return payload

    client._request = fake_request  # type: ignore[method-assign]

    fields = list(client.iter_ticket_fields())

    raw = (
        payload.get("ticket_form_fields")
        or payload.get("ticket_fields")
        or payload.get("fields")
        or []
    )

    if isinstance(raw, dict):
        expected = list(raw.values())
    elif isinstance(raw, list):
        expected = raw
    else:
        expected = list(raw)

    assert fields == expected


def test_iter_tickets_invokes_progress_callback() -> None:
    client = FreshserviceClient(base_url="https://example.freshservice.com", api_key="dummy", per_page=30)

    first_page = {"tickets": [{"id": idx} for idx in range(1, 31)], "meta": {"total_items": 31}}
    second_page = {"tickets": [{"id": 31}], "meta": {"total_items": 31}}
    pages = [first_page, second_page]

    def fake_request(method: str, path: str, **_: object) -> dict[str, object]:
        assert method == "GET"
        assert path == "/api/v2/tickets"
        assert pages, "unexpected extra page request"
        return pages.pop(0)

    client._request = fake_request  # type: ignore[method-assign]

    progress_updates: list[tuple[int, Optional[int]]] = []
    tickets = list(
        client.iter_tickets(progress_callback=lambda processed, total: progress_updates.append((processed, total)))
    )

    assert len(tickets) == 31
    assert tickets[-1].get("id") == 31
    assert progress_updates == [(30, 31), (31, 31)]


def test_iter_tickets_progress_callback_with_no_results() -> None:
    client = FreshserviceClient(base_url="https://example.freshservice.com", api_key="dummy")

    def fake_request(method: str, path: str, **_: object) -> dict[str, object]:
        assert method == "GET"
        assert path == "/api/v2/tickets"
        return {"tickets": [], "meta": {"total_items": 0}}

    client._request = fake_request  # type: ignore[method-assign]

    progress_updates: list[tuple[int, Optional[int]]] = []
    tickets = list(
        client.iter_tickets(progress_callback=lambda processed, total: progress_updates.append((processed, total)))
    )

    assert tickets == []
    assert progress_updates == [(0, 0)]


def test_iter_tickets_includes_query_parameters() -> None:
    client = FreshserviceClient(base_url="https://example.freshservice.com", api_key="dummy")

    captured_params: dict[str, Any] = {}

    def fake_request(method: str, path: str, **kwargs: object) -> dict[str, object]:
        assert method == "GET"
        assert path == "/api/v2/tickets"
        captured_params.update(kwargs.get("params", {}))
        return {"tickets": [], "meta": {"total_items": 0}}

    client._request = fake_request  # type: ignore[method-assign]

    list(client.iter_tickets(include=["stats", "requester"]))

    assert captured_params["include"] == "requester,stats"


def test_delete_ticket_uses_delete_method() -> None:
    client = FreshserviceClient(base_url="https://example.freshservice.com", api_key="dummy")
    session = client.session
    session.request.return_value = _mock_response()

    assert client.delete_ticket(42) is True

    session.request.assert_called_once()
    method, url, *_ = session.request.call_args[0]
    assert method == "DELETE"
    assert url == "https://example.freshservice.com/api/v2/tickets/42"


def test_iter_requesters_paginates() -> None:
    client = FreshserviceClient(base_url="https://example.freshservice.com", api_key="dummy", per_page=2)
    client.per_page = 2  # override safeguard to simplify pagination test

    pages = [
        {"requesters": [{"id": 1}, {"id": 2}], "meta": {"total_items": 3}},
        {"requesters": [{"id": 3}], "meta": {"total_items": 3}},
    ]

    def fake_request(method: str, path: str, **_: object) -> dict[str, object]:
        assert method == "GET"
        assert path == "/api/v2/requesters"
        assert pages, "unexpected extra page request"
        return pages.pop(0)

    client._request = fake_request  # type: ignore[method-assign]

    seen = list(client.iter_requesters())
    assert [r["id"] for r in seen] == [1, 2, 3]


def test_get_requester_returns_payload() -> None:
    client = FreshserviceClient(base_url="https://example.freshservice.com", api_key="dummy")

    def fake_request(method: str, path: str, **_: object) -> dict[str, object]:
        assert method == "GET"
        assert path == "/api/v2/requesters/123"
        return {"requester": {"id": 123, "organization": "Studios"}}

    client._request = fake_request  # type: ignore[method-assign]

    payload = client.get_requester(123)
    assert payload == {"id": 123, "organization": "Studios"}


def test_update_requester_wraps_payload() -> None:
    client = FreshserviceClient(base_url="https://example.freshservice.com", api_key="dummy")
    session = client.session
    response = _mock_response()
    response.json.return_value = {"requester": {"id": 5, "organization": "New Org"}}
    session.request.return_value = response

    payload = client.update_requester(5, {"organization": "New Org"})

    session.request.assert_called_once()
    method, url = session.request.call_args[0][:2]
    kwargs = session.request.call_args[1]
    assert method == "PUT"
    assert url == "https://example.freshservice.com/api/v2/requesters/5"
    assert kwargs["json"] == {"requester": {"organization": "New Org"}}
    assert payload == {"id": 5, "organization": "New Org"}

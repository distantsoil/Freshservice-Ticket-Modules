"""Tests for the delete_tickets CLI helper."""

from __future__ import annotations

import csv
import sys
import types
from importlib import util
from pathlib import Path
from typing import Dict, List
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "tools" / "delete_tickets.py"


yaml_stub = types.ModuleType("yaml")
yaml_stub.safe_load = lambda stream: {}
sys.modules.setdefault("yaml", yaml_stub)


dateutil_stub = types.ModuleType("dateutil")
parser_stub = types.ModuleType("parser")
parser_stub.parse = lambda value, *_args, **_kwargs: value
parser_stub.isoparse = parser_stub.parse
dateutil_stub.parser = parser_stub
sys.modules.setdefault("dateutil", dateutil_stub)
sys.modules.setdefault("dateutil.parser", parser_stub)


rapidfuzz_stub = types.ModuleType("rapidfuzz")
fuzz_stub = types.SimpleNamespace(token_set_ratio=lambda *_args, **_kwargs: 0)
rapidfuzz_stub.fuzz = fuzz_stub
sys.modules.setdefault("rapidfuzz", rapidfuzz_stub)


def _session_factory() -> MagicMock:
    session = MagicMock()
    session.headers = {}
    session.auth = None
    response = MagicMock()
    response.content = b""
    response.json.return_value = {}
    response.status_code = 204
    response.raise_for_status.return_value = None
    session.request.return_value = response
    return session


requests_stub = types.ModuleType("requests")
requests_stub.Session = MagicMock(side_effect=_session_factory)
sys.modules["requests"] = requests_stub

spec = util.spec_from_file_location("freshservice_tools.delete_tickets", MODULE_PATH)
assert spec and spec.loader
delete_tickets = util.module_from_spec(spec)
sys.modules[spec.name] = delete_tickets
spec.loader.exec_module(delete_tickets)


class DummyClient:
    def __init__(self) -> None:
        self.deleted: List[int] = []

    def delete_ticket(self, ticket_id: int) -> None:
        self.deleted.append(ticket_id)


@pytest.fixture(name="dummy_client")
def fixture_dummy_client() -> DummyClient:
    return DummyClient()


def test_run_deletes_ids(monkeypatch: pytest.MonkeyPatch, dummy_client: DummyClient) -> None:
    config = {
        "freshservice": {"api_key": "dummy", "base_url": "https://example.freshservice.com"},
        "logging": {"console": {"enabled": False}, "file": {"enabled": False}},
    }

    monkeypatch.setattr(delete_tickets, "load_config", lambda path=None: config)
    monkeypatch.setattr(delete_tickets, "configure_logging", lambda *args, **kwargs: None)
    monkeypatch.setattr(delete_tickets, "_create_client", lambda cfg: dummy_client)

    exit_code = delete_tickets.run(
        config_path=str(delete_tickets.DEFAULT_CONFIG_PATH),
        ticket_ids=[101, 99],
        csv_path=None,
        dry_run=False,
    )

    assert exit_code == 0
    assert dummy_client.deleted == [99, 101]


def test_run_reads_csv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, dummy_client: DummyClient) -> None:
    csv_path = tmp_path / "tickets.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["ticket_id"])
        writer.writeheader()
        writer.writerow({"ticket_id": "200"})
        writer.writerow({"ticket_id": "201"})

    config = {
        "freshservice": {"api_key": "dummy", "base_url": "https://example.freshservice.com"},
        "logging": {"console": {"enabled": False}, "file": {"enabled": False}},
    }

    monkeypatch.setattr(delete_tickets, "load_config", lambda path=None: config)
    monkeypatch.setattr(delete_tickets, "configure_logging", lambda *args, **kwargs: None)
    monkeypatch.setattr(delete_tickets, "_create_client", lambda cfg: dummy_client)

    exit_code = delete_tickets.run(
        config_path=str(delete_tickets.DEFAULT_CONFIG_PATH),
        ticket_ids=None,
        csv_path=str(csv_path),
        dry_run=True,
    )

    # Dry-run skips deletions but still succeeds once IDs are discovered.
    assert exit_code == 0
    assert dummy_client.deleted == []


def test_run_requires_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(delete_tickets, "load_config", lambda path=None: {})
    monkeypatch.setattr(delete_tickets, "configure_logging", lambda *args, **kwargs: None)
    monkeypatch.setattr(delete_tickets, "_create_client", lambda cfg: DummyClient())

    exit_code = delete_tickets.run(
        config_path=str(delete_tickets.DEFAULT_CONFIG_PATH),
        ticket_ids=None,
        csv_path=None,
        dry_run=False,
    )

    assert exit_code == 1


@pytest.mark.parametrize(
    "header",
    ["Ticket ID", " ticket id ", "TicketId", "ticket_id", "ID"],
)
def test_parse_csv_accepts_header_variants(tmp_path: Path, header: str) -> None:
    csv_path = tmp_path / "tickets.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[header])
        writer.writeheader()
        writer.writerow({header: "10"})
        writer.writerow({header: " 11 "})

    ids = delete_tickets._parse_csv(csv_path)

    assert ids == [10, 11]


def test_parse_csv_errors_without_ticket_id(tmp_path: Path) -> None:
    csv_path = tmp_path / "tickets.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["wrong_header"])
        writer.writeheader()
        writer.writerow({"wrong_header": "10"})

    with pytest.raises(ValueError):
        delete_tickets._parse_csv(csv_path)

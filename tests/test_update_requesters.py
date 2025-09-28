"""Tests for the generic requester update helper."""
from __future__ import annotations

from importlib import util
from pathlib import Path
import sys
import types

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "tools" / "update_requesters.py"

spec = util.spec_from_file_location("freshservice_tools.update_requesters", MODULE_PATH)
assert spec and spec.loader

# Stub dependencies pulled in during import
python_common_pkg = types.ModuleType("python_common")
python_common_pkg.__path__ = []  # type: ignore[attr-defined]
sys.modules.setdefault("python_common", python_common_pkg)

config_stub = types.ModuleType("python_common.config")
config_stub.load_config = lambda path=None: {}
sys.modules.setdefault("python_common.config", config_stub)

logging_stub = types.ModuleType("python_common.logging_setup")
logging_stub.configure_logging = lambda config, base_dir=None: None
sys.modules.setdefault("python_common.logging_setup", logging_stub)

workflow_stub = types.ModuleType("python_common.workflow")
workflow_stub._create_client = lambda config: None
sys.modules.setdefault("python_common.workflow", workflow_stub)

module = util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def test_build_updates_supports_set_and_unset() -> None:
    updates = module._build_updates(  # type: ignore[attr-defined]
        set_fields=["department=IT", "phone=1234"],
        set_json_fields=["custom_fields={\"city\": \"London\"}"],
        unset_fields=["time_zone"],
    )
    assert updates == {
        "department": "IT",
        "phone": 1234,
        "custom_fields": {"city": "London"},
        "time_zone": None,
    }


def test_run_updates_only_when_values_change(monkeypatch: pytest.MonkeyPatch) -> None:
    class StubClient:
        def __init__(self) -> None:
            self.requesters = {
                1: {"id": 1, "email": "user@example.com", "department": "Old"},
                2: {"id": 2, "email": "other@example.com", "department": "Sales"},
            }
            self.updated: list[tuple[int, dict]] = []

        def iter_requesters(self):
            for requester in self.requesters.values():
                yield requester

        def get_requester(self, requester_id: int):
            return dict(self.requesters.get(requester_id, {}))

        def update_requester(self, requester_id: int, payload: dict) -> None:
            self.updated.append((requester_id, payload))
            self.requesters[requester_id].update(payload)

    client = StubClient()

    monkeypatch.setattr(module, "load_config", lambda path=None: {})  # type: ignore[attr-defined]
    monkeypatch.setattr(module, "configure_logging", lambda config, base_dir=None: None)  # type: ignore[attr-defined]
    monkeypatch.setattr(module, "_create_client", lambda config: client)  # type: ignore[attr-defined]

    exit_code = module.run(  # type: ignore[attr-defined]
        config_path=None,
        requester_ids=[1],
        emails=[],
        updates={"department": "IT"},
        dry_run=False,
    )
    assert exit_code == 0
    assert client.updated == [(1, {"department": "IT"})]

    # Re-run with unchanged values should skip updates but still succeed
    exit_code = module.run(  # type: ignore[attr-defined]
        config_path=None,
        requester_ids=[1, 2],
        emails=["other@example.com"],
        updates={"department": "IT"},
        dry_run=False,
    )
    assert exit_code == 0
    # Only requester 2 should change (email resolves to id=2)
    assert client.updated[-1] == (2, {"department": "IT"})

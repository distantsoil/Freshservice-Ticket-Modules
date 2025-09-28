"""Tests for the requester organization update helper."""
from __future__ import annotations

from importlib import util
from pathlib import Path
import sys
import types

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "tools" / "update_requester_organizations.py"

spec = util.spec_from_file_location("freshservice_tools.update_requester_organizations", MODULE_PATH)
assert spec and spec.loader
# Provide lightweight stubs for external dependencies pulled in during import.
yaml_stub = types.ModuleType("yaml")
yaml_stub.safe_load = lambda stream: {}
sys.modules.setdefault("yaml", yaml_stub)

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

tool = util.module_from_spec(spec)
sys.modules[spec.name] = tool
spec.loader.exec_module(tool)


def test_parse_csv_supports_ids_and_email(tmp_path: Path) -> None:
    csv_path = tmp_path / "orgs.csv"
    csv_path.write_text(
        "requester_id,email,organization\n"
        "101,alice@example.com,Studios\n"
        ",bob@example.com,Finance\n",
        encoding="utf-8",
    )

    updates = tool._parse_csv(csv_path)

    assert len(updates) == 2
    assert updates[0].requester_id == 101
    assert updates[0].email == "alice@example.com"
    assert updates[0].organization == "Studios"
    # Second row relies on the email address
    assert updates[1].requester_id is None
    assert updates[1].email == "bob@example.com"
    assert updates[1].organization == "Finance"


def test_run_updates_only_changed_values(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    csv_path = tmp_path / "orgs.csv"
    csv_path.write_text(
        "requester_id,email,organization\n"
        "1,user@example.com,Studios\n"
        ",other@example.com,Finance\n",
        encoding="utf-8",
    )

    class StubClient:
        def __init__(self) -> None:
            self.updated: list[tuple[int, dict]] = []

        def iter_requesters(self):
            yield {"id": 1, "email": "user@example.com", "organization": "Old Org"}
            yield {"id": 2, "email": "other@example.com", "organization": "Finance"}

        def update_requester(self, requester_id: int, data: dict) -> None:
            self.updated.append((requester_id, data))

    stub_client = StubClient()

    monkeypatch.setattr(tool, "load_config", lambda path: {})
    monkeypatch.setattr(tool, "configure_logging", lambda config, base_dir: None)
    monkeypatch.setattr(tool, "_create_client", lambda config: stub_client)

    exit_code = tool.run(config_path=None, csv_path=str(csv_path), dry_run=False)

    assert exit_code == 0
    assert stub_client.updated == [(1, {"organization": "Studios"})]

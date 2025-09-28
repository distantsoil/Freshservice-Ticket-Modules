import sys
import types
from importlib import util
from pathlib import Path
from typing import Any, Dict, Iterable, List

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Provide lightweight stubs for optional dependencies that the tool imports via
# the shared modules.
yaml_stub = types.ModuleType("yaml")
yaml_stub.safe_load = lambda stream: {}
sys.modules.setdefault("yaml", yaml_stub)


class _DummyResponse:
    status_code = 200
    content = b"{}"

    def json(self) -> Dict[str, Any]:  # type: ignore[override]
        return {}

    def raise_for_status(self) -> None:
        return None


class _DummySession:
    def __init__(self) -> None:
        self.auth = None
        self.headers: Dict[str, Any] = {}

    def request(self, *args: Any, **kwargs: Any) -> _DummyResponse:
        return _DummyResponse()


requests_stub = types.ModuleType("requests")
requests_stub.Session = _DummySession
sys.modules.setdefault("requests", requests_stub)

MODULE_PATH = PROJECT_ROOT / "tools" / "summarize_ticket_categories.py"
spec = util.spec_from_file_location("freshservice_tools.summarize", MODULE_PATH)
assert spec and spec.loader
summary_tool = util.module_from_spec(spec)
sys.modules[spec.name] = summary_tool
spec.loader.exec_module(summary_tool)


class DummyClient:
    def __init__(self, tickets: Iterable[Dict[str, Any]]):
        self._tickets = list(tickets)

    def iter_tickets(self, *, updated_since: str | None = None, include=None):
        return list(self._tickets)


@pytest.fixture(name="sample_tickets")
def fixture_sample_tickets() -> List[Dict[str, Any]]:
    return [
        {"id": 1, "category": "Hardware"},
        {"id": 2, "category": "Hardware"},
        {"id": 3, "category": "Software"},
        {"id": 4, "category": None},
    ]


def test_summary_outputs_expected_table(monkeypatch: pytest.MonkeyPatch, capsys, sample_tickets):
    config = {
        "freshservice": {"api_key": "dummy", "base_url": "https://example.freshservice.com"},
        "logging": {"console": {"enabled": False}, "file": {"enabled": False}},
    }

    monkeypatch.setattr(summary_tool, "load_config", lambda path=None: config)
    monkeypatch.setattr(summary_tool, "configure_logging", lambda *args, **kwargs: None)
    monkeypatch.setattr(summary_tool, "_create_client", lambda cfg: DummyClient(sample_tickets))

    summary_tool.main(["--config", str(summary_tool.DEFAULT_CONFIG_PATH)])

    output = capsys.readouterr().out.strip().splitlines()
    assert output, "Expected summary tool to produce output"
    header_label, header_value = output[0].rsplit("  ", 1)
    assert header_label.strip() == "Category"
    assert header_value.strip() == "Tickets"
    assert set(output[1].replace("-", "")) == {" "}

    observed = {}
    for line in output[2:]:
        label, value = line.rsplit("  ", 1)
        observed[label.strip()] = int(value.strip())

    assert observed == {
        "Hardware": 2,
        "Software": 1,
        "Uncategorised": 1,
        "Total": 4,
    }

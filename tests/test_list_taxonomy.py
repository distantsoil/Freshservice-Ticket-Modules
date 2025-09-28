import sys
import types
from importlib import util
from pathlib import Path
from typing import Any, Dict, Iterable, List

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

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

dateutil_stub = types.ModuleType("dateutil")
dateutil_parser_stub = types.ModuleType("dateutil.parser")
dateutil_parser_stub.parse = lambda value, *args, **kwargs: value
dateutil_parser_stub.isoparse = dateutil_parser_stub.parse
dateutil_stub.parser = dateutil_parser_stub
sys.modules.setdefault("dateutil", dateutil_stub)
sys.modules.setdefault("dateutil.parser", dateutil_parser_stub)

rapidfuzz_stub = types.ModuleType("rapidfuzz")
rapidfuzz_fuzz_stub = types.ModuleType("rapidfuzz.fuzz")
rapidfuzz_fuzz_stub.token_set_ratio = lambda a, b: 0
rapidfuzz_fuzz_stub.partial_ratio = lambda a, b: 0
rapidfuzz_stub.fuzz = rapidfuzz_fuzz_stub
sys.modules.setdefault("rapidfuzz", rapidfuzz_stub)
sys.modules.setdefault("rapidfuzz.fuzz", rapidfuzz_fuzz_stub)

MODULE_PATH = PROJECT_ROOT / "tools" / "list_taxonomy.py"
spec = util.spec_from_file_location("freshservice_tools.list_taxonomy", MODULE_PATH)
assert spec and spec.loader
list_taxonomy = util.module_from_spec(spec)
sys.modules[spec.name] = list_taxonomy
spec.loader.exec_module(list_taxonomy)


class DummyClient:
    def __init__(self, fields: Iterable[Dict[str, Any]]):
        self._fields = list(fields)

    def iter_ticket_fields(self) -> Iterable[Dict[str, Any]]:
        return list(self._fields)


@pytest.fixture(name="sample_fields")
def fixture_sample_fields() -> List[Dict[str, Any]]:
    return [
        {
            "name": "category",
            "choices": [
                {"value": "hardware", "label": "Hardware"},
                {"value": "software", "label": "Software"},
            ],
        },
        {
            "name": "sub_category",
            "choices": {
                "hardware": [
                    {"value": "peripherals", "label": "Peripherals"},
                ],
                "software": [
                    {"value": "creative_design", "label": "Creative &amp; Design"},
                ],
            },
        },
        {
            "name": "item_category",
            "choices": {
                "hardware": {
                    "peripherals": [
                        {"value": "audio_video", "label": "Audio / Video Devices"},
                    ]
                },
                "software": {
                    "creative_design": [
                        {"value": "photoshop", "label": "Photoshop &amp; Lightroom"},
                    ]
                },
            },
        },
    ]


def test_list_taxonomy_outputs_nested_hierarchy(monkeypatch: pytest.MonkeyPatch, capsys, sample_fields):
    config = {
        "freshservice": {"api_key": "dummy", "base_url": "https://example.freshservice.com"},
        "logging": {"console": {"enabled": False}, "file": {"enabled": False}},
    }

    monkeypatch.setattr(list_taxonomy, "load_config", lambda path=None: config)
    monkeypatch.setattr(list_taxonomy, "configure_logging", lambda *args, **kwargs: None)
    monkeypatch.setattr(list_taxonomy, "_create_client", lambda cfg: DummyClient(sample_fields))

    list_taxonomy.main(["--config", str(list_taxonomy.DEFAULT_CONFIG_PATH)])

    captured = capsys.readouterr().out.strip().splitlines()
    assert captured == [
        "- Hardware",
        "-- Peripherals",
        "--- Audio / Video Devices",
        "- Software",
        "-- Creative & Design",
        "--- Photoshop & Lightroom",
    ]

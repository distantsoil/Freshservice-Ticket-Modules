"""Tests for workflow helpers."""

import logging
from importlib import util
from pathlib import Path
import sys
import types

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "python_common"

package = types.ModuleType("python_common")
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules.setdefault("python_common", package)

analysis_stub = types.ModuleType("python_common.analysis")
analysis_stub.TicketAnalyzer = type("TicketAnalyzer", (), {})
analysis_stub.TicketRecord = type("TicketRecord", (), {})
sys.modules.setdefault("python_common.analysis", analysis_stub)

config_stub = types.ModuleType("python_common.config")
config_stub.load_config = lambda *args, **kwargs: {}
def _stub_resolve_path(path, base=None):
    candidate = Path(path)
    if candidate.is_absolute() or base is None:
        return candidate
    return Path(base) / candidate

config_stub.resolve_path = _stub_resolve_path
sys.modules.setdefault("python_common.config", config_stub)

client_stub = types.ModuleType("python_common.freshservice_client")
client_stub.FreshserviceClient = type("FreshserviceClient", (), {})
sys.modules.setdefault("python_common.freshservice_client", client_stub)

logging_stub = types.ModuleType("python_common.logging_setup")
logging_stub.configure_logging = lambda *args, **kwargs: None
sys.modules.setdefault("python_common.logging_setup", logging_stub)

reporting_stub = types.ModuleType("python_common.reporting")
reporting_stub.TicketReportWriter = type("TicketReportWriter", (), {})
sys.modules.setdefault("python_common.reporting", reporting_stub)

review_stub = types.ModuleType("python_common.review")
review_stub.ReviewWorksheet = type("ReviewWorksheet", (), {})
review_stub.ReviewRow = type("ReviewRow", (), {})
sys.modules.setdefault("python_common.review", review_stub)

updates_stub = types.ModuleType("python_common.updates")
updates_stub.TicketUpdater = type("TicketUpdater", (), {})
sys.modules.setdefault("python_common.updates", updates_stub)

spec = util.spec_from_file_location("python_common.workflow", PACKAGE_ROOT / "workflow.py")
assert spec and spec.loader
workflow_module = util.module_from_spec(spec)
sys.modules[spec.name] = workflow_module
spec.loader.exec_module(workflow_module)

_normalize_choices = workflow_module._normalize_choices
_extract_taxonomy = workflow_module._extract_taxonomy
_format_update_summary = workflow_module._format_update_summary


def test_normalize_choices_flattens_nested_structures() -> None:
    field = {
        "name": "sub_category",
        "choices": {
            "hardware": [
                {"label": "Laptop"},
                {"label": "Desktop"},
            ],
            "software": [
                {"value": "password_reset", "label": "Password Reset"},
            ],
        },
    }

    result = _normalize_choices(field)

    labels = [entry.label for entry in result]
    assert labels == ["Laptop", "Desktop", "Password Reset"]


def test_normalize_choices_deduplicates_and_preserves_order() -> None:
    field = {
        "name": "item_category",
        "nested_options": [
            {"label": "VPN Access"},
            {"value": "vpn access"},
            [
                {"title": "Credential Reset"},
                "Credential Reset",
            ],
        ],
    }

    result = _normalize_choices(field)

    labels = [entry.label for entry in result]
    assert labels == ["VPN Access", "vpn access", "Credential Reset"]


def test_extract_taxonomy_preserves_hierarchy() -> None:
    ticket_fields = [
        {
            "name": "category",
            "choices": [
                {"value": "software", "label": "Software"},
                {"value": "hardware", "label": "Hardware"},
            ],
        },
        {
            "name": "sub_category",
            "choices": {
                "software": [
                    {"value": "adobe_suite", "label": "Adobe"},
                    {"value": "vpn", "label": "VPN"},
                ],
                "hardware": [
                    {"value": "laptop", "label": "Laptop"},
                ],
            },
        },
        {
            "name": "item_category",
            "choices": {
                "software": {
                    "adobe_suite": [
                        {"value": "acrobat", "label": "Acrobat"},
                    ]
                }
            },
        },
    ]

    categories, subcategories, item_categories = _extract_taxonomy(ticket_fields)

    assert categories == ["Software", "Hardware"]
    assert "Adobe" not in categories
    assert subcategories["Software"] == ["Adobe", "VPN"]
    assert subcategories["Hardware"] == ["Laptop"]
    assert item_categories[("Software", "Adobe")] == ["Acrobat"]


def test_extract_taxonomy_handles_parent_value_lists() -> None:
    ticket_fields = [
        {
            "name": "category",
            "choices": [
                {"value": "software", "label": "Software"},
                {"value": "remote_access", "label": "Remote Access"},
            ],
        },
        {
            "name": "sub_category",
            "choices": [
                {"value": "adobe_suite", "label": "Adobe", "parent_value": "software"},
                {"value": "vpn_cato", "label": "VPN (CATO)", "parent_value": "remote_access"},
            ],
        },
        {
            "name": "item_category",
            "choices": [
                {"value": "acrobat", "label": "Acrobat", "parent_value": "adobe_suite"},
            ],
        },
    ]

    categories, subcategories, item_categories = _extract_taxonomy(ticket_fields)

    assert categories == ["Software", "Remote Access"]
    assert subcategories["Software"] == ["Adobe"]
    assert subcategories["Remote Access"] == ["VPN (CATO)"]
    assert item_categories[("Software", "Adobe")] == ["Acrobat"]


def test_extract_taxonomy_handles_nested_category_tree_only() -> None:
    ticket_fields = [
        {
            "name": "category",
            "choices": [
                {
                    "value": "software",
                    "label": "Software",
                    "nested_options": [
                        {
                            "value": "adobe_suite",
                            "label": "Adobe",
                            "nested_options": [
                                {"value": "acrobat", "label": "Acrobat"},
                            ],
                        }
                    ],
                }
            ],
        }
    ]

    categories, subcategories, item_categories = _extract_taxonomy(ticket_fields)

    assert categories == ["Software"]
    assert subcategories["Software"] == ["Adobe"]
    assert item_categories[("Software", "Adobe")] == ["Acrobat"]


def test_extract_taxonomy_accepts_subcategory_name_variants() -> None:
    ticket_fields = [
        {
            "name": "category",
            "choices": [
                {"value": "software", "label": "Software"},
            ],
        },
        {
            "name": "subcategory",
            "choices": [
                {"value": "adobe_suite", "label": "Adobe", "parent_value": "software"},
            ],
        },
    ]

    categories, subcategories, item_categories = _extract_taxonomy(ticket_fields)

    assert categories == ["Software"]
    assert subcategories["Software"] == ["Adobe"]


def test_format_update_summary_reports_counts(tmp_path: Path) -> None:
    run_log = tmp_path / "bulk_update.log"
    text = _format_update_summary(
        total=10,
        successes=6,
        errors=2,
        run_log_path=run_log,
        dry_run=False,
    )

    assert "Total tickets processed" in text
    assert "Skipped / unchanged" in text
    assert "Detailed log" in text
    assert str(run_log) in text


def test_prepare_logging_creates_bulk_update_run_log(tmp_path) -> None:
    config = {
        "logging": {
            "console": {"enabled": False},
            "file": {"enabled": False},
            "bulk_update_run": {
                "path_template": "logs/bulk_update_{timestamp}.log",
                "timestamp": "20240101-120000",
            },
        }
    }
    options = workflow_module.ApplyUpdatesOptions(
        config_path=None,
        review_csv=None,
        ticket_ids=None,
        category=None,
        sub_category=None,
        item_category=None,
    )

    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
        handler.close()

    workflow_module._prepare_logging(config, options, base_dir=tmp_path)

    run_path = getattr(options, "run_log_path")
    expected = tmp_path / "logs" / "bulk_update_20240101-120000.log"
    assert run_path == expected
    assert run_path.exists()

    for handler in list(root_logger.handlers):
        handler.close()
        root_logger.removeHandler(handler)

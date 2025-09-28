from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

for module_name in list(sys.modules):
    if module_name == "dateutil" or module_name.startswith("dateutil."):
        sys.modules.pop(module_name, None)

from python_common import workflow
from python_common.report_generation import TicketReportBuilder, TicketSnapshot


def _make_snapshot(**overrides):
    base = {
        "ticket_id": 1,
        "subject": "VPN access failure",
        "description": "Cannot connect to VPN",
        "status": 2,
        "priority": 3,
        "category": "Remote Access",
        "sub_category": "VPN",
        "item_category": "CATO",
        "department_id": 42,
        "responder_id": 101,
        "requester_id": 201,
        "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2024, 1, 1, 12, tzinfo=timezone.utc),
        "due_by": datetime(2024, 1, 2, tzinfo=timezone.utc),
        "fr_due_by": datetime(2024, 1, 1, 4, tzinfo=timezone.utc),
        "resolved_at": datetime(2024, 1, 1, 10, tzinfo=timezone.utc),
        "closed_at": datetime(2024, 1, 1, 11, tzinfo=timezone.utc),
        "first_responded_at": datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        "reopened_at": None,
        "reopened_count": 0,
        "satisfaction_rating": 4.0,
        "satisfaction_comment": "Great",
    }
    base.update(overrides)
    return TicketSnapshot(**base)


def test_ticket_report_builder_metrics_basic():
    tickets = [
        _make_snapshot(ticket_id=1),
        _make_snapshot(
            ticket_id=2,
            responder_id=102,
            created_at=datetime(2024, 1, 3, tzinfo=timezone.utc),
            resolved_at=None,
            closed_at=None,
            status=2,
            satisfaction_rating=None,
            priority=4,
        ),
    ]
    builder = TicketReportBuilder(tickets, now=datetime(2024, 1, 10, tzinfo=timezone.utc))
    metrics = builder.build()

    assert metrics["operational"]["ticket_volume_trend"]["total_created"] == 2
    assert metrics["operational"]["sla_compliance"]["met"] == 1
    assert metrics["operational"]["backlog_and_aging"]["open_tickets"] == 1
    assert metrics["strategic"]["service_risk"]["high_priority_open"] == [2]
    assert metrics["technical"]["data_quality"]["missing_category"] == 0


def test_generate_reports_creates_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
freshservice:
  api_key: dummy
  base_url: https://example.freshservice.com
reporting:
  output_directory: reports
reporting_suite:
  output_directory: reports/advanced
""",
        encoding="utf-8",
    )

    class StubClient:
        def iter_tickets(self, include=None, progress_callback=None):
            ticket = {
                "id": 1,
                "subject": "Email issue",
                "description_text": "Outlook signature missing",
                "status": 2,
                "priority": 2,
                "category": "Software",
                "sub_category": "Productivity",
                "item_category": "MS Office",
                "department_id": 15,
                "responder_id": 1001,
                "requester_id": 2001,
                "created_at": "2024-01-05T09:00:00Z",
                "updated_at": "2024-01-05T10:00:00Z",
                "due_by": "2024-01-06T09:00:00Z",
                "stats": {
                    "resolved_at": "2024-01-05T12:00:00Z",
                    "first_responded_at": "2024-01-05T09:30:00Z",
                    "feedback_rating": 5,
                },
            }
            if progress_callback:
                progress_callback(1, None)
            yield ticket

    monkeypatch.setattr(workflow, "_create_client", lambda config: StubClient())

    options = workflow.ReportOptions(
        config_path=str(config_path),
        output_directory=None,
        start_date=None,
        end_date=None,
        categories=None,
        sub_categories=None,
        formats=["html", "pdf", "images", "json"],
        disable_console=True,
        simple_console=True,
        console_level="ERROR",
        show_console_log=False,
    )

    report_dir = workflow.generate_reports(options, base_dir=tmp_path)

    assert (report_dir / "report.html").exists()
    assert (report_dir / "report.pdf").exists()
    assert (report_dir / "metrics.json").exists()
    images_dir = report_dir / "images"
    assert images_dir.exists()
    assert any(images_dir.iterdir())

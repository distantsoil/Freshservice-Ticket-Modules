"""Tests for ticket update helpers."""

from importlib import util
from pathlib import Path
from typing import List
from unittest.mock import Mock, call
import sys
import types

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "python_common"

package = types.ModuleType("python_common")
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules.setdefault("python_common", package)

client_stub = types.ModuleType("python_common.freshservice_client")
client_stub.FreshserviceClient = type("FreshserviceClient", (), {})
sys.modules.setdefault("python_common.freshservice_client", client_stub)

review_module_spec = util.spec_from_file_location("python_common.review", PACKAGE_ROOT / "review.py")
assert review_module_spec and review_module_spec.loader
review_module = util.module_from_spec(review_module_spec)
sys.modules[review_module_spec.name] = review_module
review_module_spec.loader.exec_module(review_module)

updates_spec = util.spec_from_file_location("python_common.updates", PACKAGE_ROOT / "updates.py")
assert updates_spec and updates_spec.loader
updates_module = util.module_from_spec(updates_spec)
sys.modules[updates_spec.name] = updates_module
updates_spec.loader.exec_module(updates_module)

TicketUpdater = updates_module.TicketUpdater
HTTPError = updates_module.HTTPError
UpdateTracker = updates_module.UpdateTracker
UpdateError = updates_module.UpdateError
describe_http_error = updates_module.describe_http_error


def test_update_single_ticket_raises_when_no_fields_provided() -> None:
    client = Mock()
    updater = TicketUpdater(client)

    with pytest.raises(ValueError):
        updater.update_single_ticket(123)

    client.get_ticket.assert_not_called()
    client.update_ticket.assert_not_called()


def test_update_single_ticket_calls_client_when_fields_supplied() -> None:
    client = Mock()
    updater = TicketUpdater(client)

    client.get_ticket.return_value = {"category": "Software", "sub_category": "Office"}
    client.update_ticket.return_value = {
        "id": 321,
        "category": "Hardware",
        "sub_category": "Laptop",
    }

    updater.update_single_ticket(321, category="Hardware", sub_category="Laptop")

    client.get_ticket.assert_called_once_with(321)
    client.update_ticket.assert_called_once_with(
        321,
        {"ticket": {"category": "Hardware", "sub_category": "Laptop"}},
    )


def test_update_single_ticket_skips_when_values_match() -> None:
    client = Mock()
    client.get_ticket.return_value = {
        "category": "Hardware",
        "sub_category": "Laptop",
        "item_category": "Battery",
    }
    updater = TicketUpdater(client)

    response = updater.update_single_ticket(
        456,
        category="Hardware",
        sub_category="Laptop",
        item_category="Battery",
    )

    client.update_ticket.assert_not_called()
    assert response == client.get_ticket.return_value


def test_update_ticket_categories_dry_run_reports_paths() -> None:
    client = Mock()
    updater = TicketUpdater(client)

    row = review_module.ReviewRow(
        ticket_id=789,
        manager_decision="approve",
        final_category="Security",
        final_sub_category="Remote Access",
        final_item_category="VPN",
        review_notes="",
        current_category="",
        current_sub_category="",
        current_item_category="",
        suggestion_confidence=0.82,
    )

    responses = updater.update_ticket_categories([row], dry_run=True)

    client.update_ticket.assert_not_called()
    assert responses == []


def test_update_ticket_categories_retries_after_rate_limit(monkeypatch) -> None:
    client = Mock()
    client._sleep_between_requests = 1.5
    updater = TicketUpdater(client)

    row = review_module.ReviewRow(
        ticket_id=555,
        manager_decision="approve",
        final_category="Software",
        final_sub_category="Productivity",
        final_item_category="MS Office / Outlook",
        review_notes="",
        current_category="",
        current_sub_category="",
        current_item_category="",
        suggestion_confidence=0.9,
    )

    class DummyResponse:
        status_code = 429

    error = HTTPError(response=DummyResponse())

    success_payload = {
        "id": 555,
        "category": "Software",
        "sub_category": "Productivity",
        "item_category": "MS Office / Outlook",
    }

    client.update_ticket.side_effect = [error, success_payload]

    sleeps = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(updates_module.time, "sleep", fake_sleep)

    responses = updater.update_ticket_categories([row])

    assert responses == [success_payload]
    assert client.update_ticket.call_count == 2
    assert sleeps == [1.5]


def test_describe_http_error_includes_details() -> None:
    class DummyResponse:
        status_code = 400
        reason = "Bad Request"

        @staticmethod
        def json():
            return {"errors": [{"field": "category", "message": "Invalid category"}]}

        text = ""

    error = HTTPError(response=DummyResponse())

    message = describe_http_error(error, ticket_id=123)

    assert "status 400" in message
    assert "Invalid category" in message
    assert "ticket 123" in message


def test_update_tracker_persists_ids(tmp_path) -> None:
    path = tmp_path / "skip.log"
    tracker = UpdateTracker(path)
    tracker.mark_updated(101)
    tracker.save()

    assert path.read_text(encoding="utf-8").strip() == "101"

    reloaded = UpdateTracker(path)
    assert reloaded.contains(101)


def test_update_ticket_categories_skips_tracked_ids(tmp_path) -> None:
    path = tmp_path / "skip.log"
    existing = UpdateTracker(path)
    existing.mark_updated(999)
    existing.save()

    tracker = UpdateTracker(path)
    client = Mock()
    updater = TicketUpdater(client)
    row = review_module.ReviewRow(
        ticket_id=999,
        manager_decision="approve",
        final_category="Security",
        final_sub_category="Remote Access",
        final_item_category="VPN",
        review_notes="",
        current_category="",
        current_sub_category="",
        current_item_category="",
        suggestion_confidence=0.9,
    )

    responses = updater.update_ticket_categories([row], skip_tracker=tracker)

    assert responses == []
    client.update_ticket.assert_not_called()


def test_update_ticket_categories_marks_success_and_appends(tmp_path) -> None:
    path = tmp_path / "skip.log"
    tracker = UpdateTracker(path)
    client = Mock()
    updater = TicketUpdater(client)
    row = review_module.ReviewRow(
        ticket_id=888,
        manager_decision="approve",
        final_category="Software",
        final_sub_category="Productivity",
        final_item_category="Outlook",
        review_notes="",
        current_category="",
        current_sub_category="",
        current_item_category="",
        suggestion_confidence=0.5,
    )

    client.update_ticket.return_value = {
        "id": 888,
        "category": "Software",
        "sub_category": "Productivity",
        "item_category": "Outlook",
    }

    responses = updater.update_ticket_categories([row], skip_tracker=tracker)

    assert responses == [client.update_ticket.return_value]
    assert path.exists()
    assert "888" in path.read_text(encoding="utf-8")


def test_update_ticket_categories_reports_progress(tmp_path) -> None:
    path = tmp_path / "skip.log"
    tracker = UpdateTracker(path)
    client = Mock()
    updater = TicketUpdater(client)
    progress = Mock()

    row_skip = review_module.ReviewRow(
        ticket_id=1001,
        manager_decision="decline",
        final_category="",
        final_sub_category="",
        final_item_category="",
        review_notes="",
        current_category="",
        current_sub_category="",
        current_item_category="",
        suggestion_confidence=None,
    )
    row_update = review_module.ReviewRow(
        ticket_id=1002,
        manager_decision="approve",
        final_category="Software",
        final_sub_category="Productivity",
        final_item_category="Outlook",
        review_notes="",
        current_category="",
        current_sub_category="",
        current_item_category="",
        suggestion_confidence=0.8,
    )

    client.update_ticket.return_value = {
        "id": 1002,
        "category": "Software",
        "sub_category": "Productivity",
        "item_category": "Outlook",
    }

    updater.update_ticket_categories(
        [row_skip, row_update],
        skip_tracker=tracker,
        progress_callback=progress,
        total_rows=2,
    )

    assert progress.call_args_list == [call(1, 2), call(2, 2)]
    client.update_ticket.assert_called_once_with(
        1002,
        {
            "ticket": {
                "category": "Software",
                "sub_category": "Productivity",
                "item_category": "Outlook",
            }
        },
    )

def test_update_ticket_categories_force_override(tmp_path) -> None:
    path = tmp_path / "skip.log"
    tracker_seed = UpdateTracker(path)
    tracker_seed.mark_updated(777)
    tracker_seed.save()

    tracker = UpdateTracker(path)
    client = Mock()
    updater = TicketUpdater(client)
    client.update_ticket.return_value = {"id": 777}

    row = review_module.ReviewRow(
        ticket_id=777,
        manager_decision="approve",
        final_category="Hardware",
        final_sub_category="Computer",
        final_item_category="Mac",
        review_notes="",
        current_category="",
        current_sub_category="",
        current_item_category="",
        suggestion_confidence=0.7,
    )

    responses = updater.update_ticket_categories(
        [row], skip_tracker=tracker, force_ticket_ids={777}
    )

    assert responses == [client.update_ticket.return_value]
    client.update_ticket.assert_called_once()


def test_update_ticket_categories_collects_errors(caplog) -> None:
    client = Mock()
    updater = TicketUpdater(client)

    row_error = review_module.ReviewRow(
        ticket_id=1003,
        manager_decision="approve",
        final_category="Software",
        final_sub_category="Productivity",
        final_item_category="Outlook",
        review_notes="",
        current_category="",
        current_sub_category="",
        current_item_category="",
        suggestion_confidence=0.6,
    )
    row_success = review_module.ReviewRow(
        ticket_id=1004,
        manager_decision="approve",
        final_category="Hardware",
        final_sub_category="Computer",
        final_item_category="Mac",
        review_notes="",
        current_category="",
        current_sub_category="",
        current_item_category="",
        suggestion_confidence=0.7,
    )

    class DummyResponse:
        status_code = 422
        reason = "Unprocessable Entity"

        @staticmethod
        def json():
            return {"message": "Invalid taxonomy mapping"}

        text = ""

    client.update_ticket.side_effect = [HTTPError(response=DummyResponse()), {"id": 1004}]

    collected: List[UpdateError] = []

    with caplog.at_level("ERROR"):
        responses = updater.update_ticket_categories(
            [row_error, row_success], error_collector=collected
        )

    assert responses == [{"id": 1004}]
    assert client.update_ticket.call_count == 2
    assert len(collected) == 1
    failure = collected[0]
    assert failure.ticket_id == 1003
    assert failure.status_code == 422
    assert "Invalid taxonomy mapping" in failure.message
    assert "Unprocessable Entity" in caplog.text

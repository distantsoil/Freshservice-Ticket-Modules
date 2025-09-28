"""Helpers for the manager review workflow."""
from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

LOGGER = logging.getLogger(__name__)


@dataclass
class ReviewRow:
    ticket_id: int
    manager_decision: str
    final_category: str
    final_sub_category: str
    final_item_category: str
    review_notes: str
    current_category: str
    current_sub_category: str
    current_item_category: str
    suggestion_confidence: Optional[float]


class ReviewWorksheet:
    """Load review decisions captured in the review CSV."""

    def __init__(self, review_csv: Path) -> None:
        self.review_csv = review_csv

    def load_rows(self) -> List[ReviewRow]:
        rows: List[ReviewRow] = []
        LOGGER.info("Loading review worksheet from %s", self.review_csv)
        with self.review_csv.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                decision = (row.get("manager_decision") or "").strip().lower()
                if decision not in {"approve", "decline", "skip", "pending"}:
                    LOGGER.debug("Ticket %s has non-actionable decision '%s'", row.get("ticket_id"), decision)
                    continue
                rows.append(
                    ReviewRow(
                        ticket_id=int(row.get("ticket_id", 0)),
                        manager_decision=decision,
                        final_category=(row.get("final_category") or "").strip(),
                        final_sub_category=(row.get("final_sub_category") or "").strip(),
                        final_item_category=(row.get("final_item_category") or "").strip(),
                        review_notes=(row.get("review_notes") or "").strip(),
                        current_category=(row.get("current_category") or "").strip(),
                        current_sub_category=(row.get("current_sub_category") or "").strip(),
                        current_item_category=(row.get("current_item_category") or "").strip(),
                        suggestion_confidence=_safe_float(row.get("suggestion_confidence")),
                    )
                )
        return rows

    @staticmethod
    def filter_rows(rows: Iterable[ReviewRow], *, include_decisions: Iterable[str]) -> List[ReviewRow]:
        include = {decision.lower() for decision in include_decisions}
        return [row for row in rows if row.manager_decision in include]


def _safe_float(value: object) -> Optional[float]:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None

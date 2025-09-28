"""Generate CSV and Markdown reports for ticket analysis."""
from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

from .analysis import SuggestedCategory, TicketRecord

LOGGER = logging.getLogger(__name__)


class TicketReportWriter:
    """Persist ticket analysis output for review."""

    HEADERS: Sequence[str] = (
        "ticket_id",
        "subject",
        "description_text",
        "created_at_utc",
        "current_category",
        "current_sub_category",
        "current_item_category",
        "suggested_category",
        "suggested_sub_category",
        "suggested_item_category",
        "suggestion_confidence",
        "suggestion_rationale",
        "final_category",
        "final_sub_category",
        "final_item_category",
        "suggested_new_category_pattern",
        "suggested_new_category_frequency",
    )

    REVIEW_HEADERS: Sequence[str] = HEADERS + (
        "manager_decision",
        "review_notes",
    )

    def __init__(self, *, output_directory: Path, report_name: str) -> None:
        self.output_directory = output_directory
        self.report_name = report_name
        self.output_directory.mkdir(parents=True, exist_ok=True)

    def write_analysis(
        self,
        tickets: Iterable[TicketRecord],
        suggestions: Dict[int, List[SuggestedCategory]],
        repeating_keywords: List[tuple[str, int]],
    ) -> Path:
        report_path = self.output_directory / self.report_name
        keyword_lookup = {keyword: frequency for keyword, frequency in repeating_keywords}
        LOGGER.info("Writing ticket analysis report to %s", report_path)
        with report_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(self.HEADERS)
            for ticket in tickets:
                suggestion_list = suggestions.get(ticket.id, [])
                suggestion = suggestion_list[0] if suggestion_list else None
                keyword, frequency = self._pick_repeating_keyword(ticket, keyword_lookup)
                writer.writerow(
                    [
                        ticket.id,
                        ticket.subject,
                        ticket.description_text,
                        ticket.created_at_utc or "",
                        ticket.category,
                        ticket.sub_category,
                        ticket.item_category,
                        suggestion.category if suggestion else "",
                        suggestion.sub_category if suggestion else "",
                        suggestion.item_category if suggestion else "",
                        suggestion.confidence if suggestion else "",
                        suggestion.rationale if suggestion else "",
                        ticket.final_category,
                        ticket.final_sub_category,
                        ticket.final_item_category,
                        keyword,
                        frequency,
                    ]
                )
        return report_path

    def create_review_template(self, analysis_path: Path) -> Path:
        review_path = analysis_path.with_name(analysis_path.stem + "_review.csv")
        LOGGER.info("Creating review template at %s", review_path)
        with analysis_path.open("r", encoding="utf-8", newline="") as input_handle, review_path.open(
            "w", encoding="utf-8", newline=""
        ) as output_handle:
            reader = csv.DictReader(input_handle)
            writer = csv.DictWriter(output_handle, fieldnames=self.REVIEW_HEADERS)
            writer.writeheader()
            for row in reader:
                row.update({
                    "manager_decision": "pending",
                    "final_category": row.get("final_category") or row.get("suggested_category", ""),
                    "final_sub_category": row.get("final_sub_category")
                    or row.get("suggested_sub_category", ""),
                    "final_item_category": row.get("final_item_category")
                    or row.get("suggested_item_category", ""),
                    "review_notes": "",
                })
                writer.writerow(row)
        return review_path

    @staticmethod
    def _pick_repeating_keyword(ticket: TicketRecord, keyword_lookup: Dict[str, int]) -> tuple[str, int]:
        text = f"{ticket.subject} {ticket.description_text}".lower()
        for keyword, frequency in keyword_lookup.items():
            if keyword in text:
                return keyword, frequency
        return "", 0

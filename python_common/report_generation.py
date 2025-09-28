"""Advanced reporting utilities for Freshservice analytics outputs."""
from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from dateutil import parser as date_parser
from fpdf import FPDF
from fpdf.errors import FPDFException
from jinja2 import Template

LOGGER = logging.getLogger(__name__)


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = date_parser.isoparse(value)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _hours_between(start: Optional[datetime], end: Optional[datetime]) -> Optional[float]:
    if not start or not end:
        return None
    delta = end - start
    return max(delta.total_seconds() / 3600.0, 0.0)


@dataclass
class TicketSnapshot:
    """Lightweight normalised view of Freshservice ticket data."""

    ticket_id: int
    subject: str
    description: str
    status: Optional[int]
    priority: Optional[int]
    category: Optional[str]
    sub_category: Optional[str]
    item_category: Optional[str]
    department_id: Optional[int]
    responder_id: Optional[int]
    requester_id: Optional[int]
    created_at: datetime
    updated_at: datetime
    due_by: Optional[datetime]
    fr_due_by: Optional[datetime]
    resolved_at: Optional[datetime]
    closed_at: Optional[datetime]
    first_responded_at: Optional[datetime]
    reopened_at: Optional[datetime]
    reopened_count: int
    satisfaction_rating: Optional[float]
    satisfaction_comment: Optional[str]

    @classmethod
    def from_api(cls, payload: Dict[str, Any]) -> "TicketSnapshot":
        stats = payload.get("stats") or {}
        satisfaction = stats.get("feedback_rating")
        if satisfaction is None:
            satisfaction = stats.get("feedback_score")
        if isinstance(satisfaction, str):
            try:
                satisfaction = float(satisfaction)
            except ValueError:
                satisfaction = None
        comment = stats.get("feedback_comment")
        if comment is not None and not isinstance(comment, str):
            comment = str(comment)
        reopened_count = stats.get("reopened_count") or payload.get("reopened_count") or 0
        try:
            reopened_count = int(reopened_count)
        except (TypeError, ValueError):
            reopened_count = 0

        created = _parse_datetime(payload.get("created_at")) or datetime.utcnow().replace(
            tzinfo=timezone.utc
        )
        updated = _parse_datetime(payload.get("updated_at")) or created

        return cls(
            ticket_id=int(payload.get("id", 0)),
            subject=str(payload.get("subject") or ""),
            description=str(payload.get("description_text") or payload.get("description") or ""),
            status=payload.get("status"),
            priority=payload.get("priority"),
            category=payload.get("category") or None,
            sub_category=payload.get("sub_category") or None,
            item_category=payload.get("item_category") or None,
            department_id=payload.get("department_id"),
            responder_id=payload.get("responder_id"),
            requester_id=payload.get("requester_id"),
            created_at=created,
            updated_at=updated,
            due_by=_parse_datetime(payload.get("due_by")),
            fr_due_by=_parse_datetime(payload.get("fr_due_by")),
            resolved_at=_parse_datetime(stats.get("resolved_at") or payload.get("resolved_at")),
            closed_at=_parse_datetime(stats.get("closed_at") or payload.get("closed_at")),
            first_responded_at=_parse_datetime(
                stats.get("first_responded_at") or payload.get("first_responded_at")
            ),
            reopened_at=_parse_datetime(stats.get("reopened_at") or payload.get("reopened_at")),
            reopened_count=reopened_count,
            satisfaction_rating=float(satisfaction) if satisfaction is not None else None,
            satisfaction_comment=comment,
        )

    @property
    def is_resolved(self) -> bool:
        if self.resolved_at:
            return True
        if self.status is None:
            return False
        return int(self.status) in {4, 5, 6}

    @property
    def is_open(self) -> bool:
        return not self.is_resolved

    def age_in_days(self, reference: Optional[datetime] = None) -> float:
        reference = reference or datetime.utcnow().replace(tzinfo=timezone.utc)
        return max((reference - self.created_at).total_seconds() / 86400.0, 0.0)


class TicketReportBuilder:
    """Aggregate Freshservice tickets into operational and strategic metrics."""

    def __init__(self, tickets: Sequence[TicketSnapshot], now: Optional[datetime] = None) -> None:
        self.tickets = list(tickets)
        self.now = now or datetime.utcnow().replace(tzinfo=timezone.utc)

    # -- Operational metrics -------------------------------------------------
    def ticket_volume_trend(self) -> Dict[str, Any]:
        daily_counter: Counter[str] = Counter()
        resolved_counter: Counter[str] = Counter()
        for ticket in self.tickets:
            daily_counter[ticket.created_at.date().isoformat()] += 1
            if ticket.resolved_at:
                resolved_counter[ticket.resolved_at.date().isoformat()] += 1
        trend = [
            {"date": day, "created": daily_counter[day], "resolved": resolved_counter.get(day, 0)}
            for day in sorted(daily_counter)
        ]
        return {
            "total_created": sum(daily_counter.values()),
            "total_resolved": sum(resolved_counter.values()),
            "trend": trend,
        }

    def sla_compliance(self) -> Dict[str, Any]:
        total = 0
        met = 0
        breaches: List[int] = []
        for ticket in self.tickets:
            if not ticket.due_by:
                continue
            total += 1
            resolved = ticket.resolved_at or self.now
            if resolved <= ticket.due_by:
                met += 1
            else:
                breaches.append(ticket.ticket_id)
        breach_count = total - met
        compliance = (met / total) if total else 0.0
        return {
            "tickets_with_sla": total,
            "met": met,
            "breached": breach_count,
            "compliance_rate": round(compliance, 3),
            "breached_ticket_ids": breaches,
        }

    def backlog_and_aging(self) -> Dict[str, Any]:
        open_tickets = [t for t in self.tickets if t.is_open]
        buckets = {
            "0-1 days": 0,
            "1-3 days": 0,
            "3-7 days": 0,
            "7-14 days": 0,
            "14+ days": 0,
        }
        for ticket in open_tickets:
            age = ticket.age_in_days(self.now)
            if age <= 1:
                buckets["0-1 days"] += 1
            elif age <= 3:
                buckets["1-3 days"] += 1
            elif age <= 7:
                buckets["3-7 days"] += 1
            elif age <= 14:
                buckets["7-14 days"] += 1
            else:
                buckets["14+ days"] += 1
        return {
            "open_tickets": len(open_tickets),
            "aging_buckets": buckets,
        }

    def agent_performance(self) -> List[Dict[str, Any]]:
        metrics: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            "tickets_resolved": 0,
            "tickets_open": 0,
            "resolution_hours": [],
            "first_response_hours": [],
        })
        for ticket in self.tickets:
            agent_key = str(ticket.responder_id or "Unassigned")
            entry = metrics[agent_key]
            if ticket.is_resolved:
                entry["tickets_resolved"] += 1
            else:
                entry["tickets_open"] += 1
            res_hours = _hours_between(ticket.created_at, ticket.resolved_at)
            if res_hours is not None:
                entry["resolution_hours"].append(res_hours)
            first_hours = _hours_between(ticket.created_at, ticket.first_responded_at)
            if first_hours is not None:
                entry["first_response_hours"].append(first_hours)
        results: List[Dict[str, Any]] = []
        for agent, data in metrics.items():
            results.append(
                {
                    "agent": agent,
                    "tickets_resolved": data["tickets_resolved"],
                    "tickets_open": data["tickets_open"],
                    "avg_resolution_hours": round(mean(data["resolution_hours"]) if data["resolution_hours"] else 0.0, 2),
                    "avg_first_response_hours": round(
                        mean(data["first_response_hours"]) if data["first_response_hours"] else 0.0,
                        2,
                    ),
                }
            )
        results.sort(key=lambda row: row["tickets_resolved"], reverse=True)
        return results

    def category_breakdown(self) -> Dict[str, Any]:
        category_counter: Counter[str] = Counter()
        subcategory_counter: Counter[Tuple[str, str]] = Counter()
        item_counter: Counter[Tuple[str, str, str]] = Counter()
        for ticket in self.tickets:
            if ticket.category:
                category_counter[ticket.category] += 1
            if ticket.category and ticket.sub_category:
                subcategory_counter[(ticket.category, ticket.sub_category)] += 1
            if ticket.category and ticket.sub_category and ticket.item_category:
                item_counter[(ticket.category, ticket.sub_category, ticket.item_category)] += 1
        return {
            "categories": category_counter,
            "subcategories": subcategory_counter,
            "item_categories": item_counter,
        }

    def response_resolution_summary(self) -> Dict[str, Any]:
        first_responses: List[float] = []
        resolutions: List[float] = []
        for ticket in self.tickets:
            first = _hours_between(ticket.created_at, ticket.first_responded_at)
            if first is not None:
                first_responses.append(first)
            resolved = _hours_between(ticket.created_at, ticket.resolved_at)
            if resolved is not None:
                resolutions.append(resolved)
        def _average(values: Sequence[float]) -> float:
            return round(mean(values), 2) if values else 0.0

        return {
            "average_first_response_hours": _average(first_responses),
            "average_resolution_hours": _average(resolutions),
            "sample_size_first_response": len(first_responses),
            "sample_size_resolution": len(resolutions),
        }

    # -- Strategic metrics ---------------------------------------------------
    def service_risk_indicators(self) -> Dict[str, Any]:
        breached = self.sla_compliance()["breached_ticket_ids"]
        high_priority_open = [
            ticket.ticket_id
            for ticket in self.tickets
            if ticket.is_open and (ticket.priority or 0) >= 3
        ]
        aging = [
            ticket.ticket_id
            for ticket in self.tickets
            if ticket.is_open and ticket.age_in_days(self.now) >= 14
        ]
        return {
            "sla_breaches": breached,
            "high_priority_open": high_priority_open,
            "aging_open_tickets": aging,
        }

    def department_impact(self) -> List[Dict[str, Any]]:
        metrics: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            "tickets": 0,
            "resolved": 0,
            "resolution_hours": [],
        })
        for ticket in self.tickets:
            department = str(ticket.department_id or "Unassigned")
            entry = metrics[department]
            entry["tickets"] += 1
            if ticket.is_resolved:
                entry["resolved"] += 1
            resolved_hours = _hours_between(ticket.created_at, ticket.resolved_at)
            if resolved_hours is not None:
                entry["resolution_hours"].append(resolved_hours)
        report: List[Dict[str, Any]] = []
        for dept, data in metrics.items():
            avg_resolution = round(mean(data["resolution_hours"]) if data["resolution_hours"] else 0.0, 2)
            report.append(
                {
                    "department": dept,
                    "tickets": data["tickets"],
                    "resolved": data["resolved"],
                    "resolution_rate": round(data["resolved"] / data["tickets"], 3) if data["tickets"] else 0.0,
                    "avg_resolution_hours": avg_resolution,
                }
            )
        report.sort(key=lambda row: row["tickets"], reverse=True)
        return report

    def recurring_incidents(self, min_occurrences: int = 5) -> List[Dict[str, Any]]:
        counter: Counter[str] = Counter()
        for ticket in self.tickets:
            key_parts = [ticket.category or "Unclassified"]
            if ticket.sub_category:
                key_parts.append(ticket.sub_category)
            if ticket.item_category:
                key_parts.append(ticket.item_category)
            counter[" > ".join(key_parts)] += 1
        return [
            {"path": path, "count": count}
            for path, count in counter.most_common()
            if count >= min_occurrences
        ]

    def satisfaction_trends(self) -> Dict[str, Any]:
        ratings: List[float] = []
        timeline: Dict[str, List[float]] = defaultdict(list)
        for ticket in self.tickets:
            if ticket.satisfaction_rating is None:
                continue
            ratings.append(ticket.satisfaction_rating)
            timeline[ticket.created_at.date().isoformat()].append(ticket.satisfaction_rating)
        trend = [
            {"date": day, "average_rating": round(mean(scores), 2), "responses": len(scores)}
            for day, scores in sorted(timeline.items())
        ]
        return {
            "response_count": len(ratings),
            "average_rating": round(mean(ratings), 2) if ratings else 0.0,
            "trend": trend,
        }

    def resource_capacity(self) -> Dict[str, Any]:
        agent_metrics = self.agent_performance()
        if not agent_metrics:
            return {"agents": [], "average_tickets_resolved": 0.0}
        avg_resolved = mean(agent["tickets_resolved"] for agent in agent_metrics)
        return {
            "agents": agent_metrics,
            "average_tickets_resolved": round(avg_resolved, 2),
        }

    # -- Technical / audit metrics ------------------------------------------
    def data_quality_audit(self) -> Dict[str, Any]:
        missing_category = 0
        missing_department = 0
        missing_responder = 0
        for ticket in self.tickets:
            if not ticket.category:
                missing_category += 1
            if ticket.department_id is None:
                missing_department += 1
            if ticket.responder_id is None:
                missing_responder += 1
        total = len(self.tickets) or 1
        return {
            "missing_category": missing_category,
            "missing_department": missing_department,
            "missing_responder": missing_responder,
            "missing_category_rate": round(missing_category / total, 3),
            "missing_department_rate": round(missing_department / total, 3),
            "missing_responder_rate": round(missing_responder / total, 3),
        }

    def taxonomy_usage(self) -> List[Dict[str, Any]]:
        counter: Counter[Tuple[str, Optional[str], Optional[str]]] = Counter()
        for ticket in self.tickets:
            counter[(ticket.category, ticket.sub_category, ticket.item_category)] += 1
        usage = [
            {
                "category": cat or "Unclassified",
                "sub_category": sub or "",
                "item_category": item or "",
                "count": count,
            }
            for (cat, sub, item), count in counter.most_common()
        ]
        return usage

    def stale_and_breaches(self, stale_days: int = 14) -> Dict[str, Any]:
        stale: List[int] = []
        breached: List[int] = []
        for ticket in self.tickets:
            if ticket.is_open and ticket.age_in_days(self.now) >= stale_days:
                stale.append(ticket.ticket_id)
            if ticket.due_by and (ticket.resolved_at or self.now) > ticket.due_by:
                breached.append(ticket.ticket_id)
        return {"stale_tickets": stale, "sla_breaches": breached}

    def lifecycle_and_reopens(self) -> Dict[str, Any]:
        lifecycle_hours: List[float] = []
        reopen_counts: List[int] = []
        for ticket in self.tickets:
            end_time = ticket.resolved_at or ticket.closed_at or self.now
            lifecycle = _hours_between(ticket.created_at, end_time)
            if lifecycle is not None:
                lifecycle_hours.append(lifecycle)
            reopen_counts.append(ticket.reopened_count)
        return {
            "average_lifecycle_hours": round(mean(lifecycle_hours), 2) if lifecycle_hours else 0.0,
            "max_lifecycle_hours": round(max(lifecycle_hours), 2) if lifecycle_hours else 0.0,
            "tickets_reopened": sum(1 for count in reopen_counts if count),
            "average_reopens": round(mean(reopen_counts), 2) if reopen_counts else 0.0,
        }

    def build(self) -> Dict[str, Any]:
        return {
            "generated_at": self.now.isoformat(),
            "operational": {
                "ticket_volume_trend": self.ticket_volume_trend(),
                "sla_compliance": self.sla_compliance(),
                "backlog_and_aging": self.backlog_and_aging(),
                "agent_performance": self.agent_performance(),
                "category_breakdown": self.category_breakdown(),
                "response_resolution_summary": self.response_resolution_summary(),
            },
            "strategic": {
                "service_risk": self.service_risk_indicators(),
                "department_impact": self.department_impact(),
                "recurring_incidents": self.recurring_incidents(),
                "satisfaction_trends": self.satisfaction_trends(),
                "resource_capacity": self.resource_capacity(),
            },
            "technical": {
                "data_quality": self.data_quality_audit(),
                "taxonomy_usage": self.taxonomy_usage(),
                "stale_and_breaches": self.stale_and_breaches(),
                "lifecycle": self.lifecycle_and_reopens(),
            },
        }


HTML_TEMPLATE = Template(
    """
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Freshservice Advanced Report</title>
    <style>
      body { font-family: Arial, sans-serif; margin: 2rem; }
      h1, h2, h3 { color: #1f3b4d; }
      table { border-collapse: collapse; width: 100%; margin-bottom: 1.5rem; }
      th, td { border: 1px solid #ccc; padding: 0.4rem 0.6rem; text-align: left; }
      th { background-color: #f0f6fb; }
      .meta { font-size: 0.9rem; color: #555; margin-bottom: 2rem; }
      .section { margin-bottom: 2.5rem; }
    </style>
  </head>
  <body>
    <h1>Freshservice Reporting Suite</h1>
    <div class="meta">
      <strong>Generated:</strong> {{ metrics.generated_at }}<br />
      {% if filters.start_date %}<strong>Start:</strong> {{ filters.start_date }}<br />{% endif %}
      {% if filters.end_date %}<strong>End:</strong> {{ filters.end_date }}<br />{% endif %}
      {% if filters.categories %}<strong>Categories:</strong> {{ filters.categories|join(', ') }}<br />{% endif %}
    </div>

    <div class="section">
      <h2>Operational Insights</h2>
      <h3>Ticket Volume &amp; Trends</h3>
      <table>
        <thead><tr><th>Date</th><th>Created</th><th>Resolved</th></tr></thead>
        <tbody>
          {% for row in metrics.operational.ticket_volume_trend.trend %}
          <tr><td>{{ row.date }}</td><td>{{ row.created }}</td><td>{{ row.resolved }}</td></tr>
          {% endfor %}
        </tbody>
      </table>
      <p>Total Created: {{ metrics.operational.ticket_volume_trend.total_created }}, Total Resolved: {{ metrics.operational.ticket_volume_trend.total_resolved }}</p>

      <h3>SLA Compliance</h3>
      <p>Tickets with SLA: {{ metrics.operational.sla_compliance.tickets_with_sla }} | Met: {{ metrics.operational.sla_compliance.met }} | Breached: {{ metrics.operational.sla_compliance.breached }} | Compliance: {{ metrics.operational.sla_compliance.compliance_rate * 100 }}%</p>

      <h3>Ticket Backlog &amp; Aging</h3>
      <table>
        <thead><tr><th>Aging Bucket</th><th>Count</th></tr></thead>
        <tbody>
        {% for label, count in metrics.operational.backlog_and_aging.aging_buckets.items() %}
          <tr><td>{{ label }}</td><td>{{ count }}</td></tr>
        {% endfor %}
        </tbody>
      </table>

      <h3>Agent Performance Overview</h3>
      <table>
        <thead><tr><th>Agent</th><th>Resolved</th><th>Open</th><th>Avg Resolution (h)</th><th>Avg First Response (h)</th></tr></thead>
        <tbody>
          {% for row in metrics.operational.agent_performance %}
          <tr>
            <td>{{ row.agent }}</td>
            <td>{{ row.tickets_resolved }}</td>
            <td>{{ row.tickets_open }}</td>
            <td>{{ row.avg_resolution_hours }}</td>
            <td>{{ row.avg_first_response_hours }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>

    <div class="section">
      <h2>Strategic &amp; Executive Insights</h2>
      <h3>Service Risk Indicators</h3>
      <p>SLA Breaches: {{ metrics.strategic.service_risk.sla_breaches|length }} | High Priority Open: {{ metrics.strategic.service_risk.high_priority_open|length }} | Aging Open: {{ metrics.strategic.service_risk.aging_open_tickets|length }}</p>

      <h3>Department Impact</h3>
      <table>
        <thead><tr><th>Department</th><th>Tickets</th><th>Resolved</th><th>Resolution Rate</th><th>Avg Resolution (h)</th></tr></thead>
        <tbody>
          {% for row in metrics.strategic.department_impact %}
          <tr>
            <td>{{ row.department }}</td>
            <td>{{ row.tickets }}</td>
            <td>{{ row.resolved }}</td>
            <td>{{ row.resolution_rate }}</td>
            <td>{{ row.avg_resolution_hours }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>

      <h3>Recurring Incident/Problem Areas</h3>
      <table>
        <thead><tr><th>Category Path</th><th>Occurrences</th></tr></thead>
        <tbody>
          {% for row in metrics.strategic.recurring_incidents %}
          <tr><td>{{ row.path }}</td><td>{{ row.count }}</td></tr>
          {% endfor %}
        </tbody>
      </table>
    </div>

    <div class="section">
      <h2>Technical &amp; Audit Insights</h2>
      <h3>Data Quality Audit</h3>
      <p>Missing Category: {{ metrics.technical.data_quality.missing_category }} | Missing Department: {{ metrics.technical.data_quality.missing_department }} | Missing Responder: {{ metrics.technical.data_quality.missing_responder }}</p>

      <h3>Taxonomy Usage</h3>
      <table>
        <thead><tr><th>Category</th><th>Subcategory</th><th>Item</th><th>Count</th></tr></thead>
        <tbody>
          {% for row in metrics.technical.taxonomy_usage[:20] %}
          <tr>
            <td>{{ row.category }}</td>
            <td>{{ row.sub_category }}</td>
            <td>{{ row.item_category }}</td>
            <td>{{ row.count }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>

      <h3>Ticket Lifecycle &amp; Reopens</h3>
      <p>Average Lifecycle (h): {{ metrics.technical.lifecycle.average_lifecycle_hours }} | Max Lifecycle (h): {{ metrics.technical.lifecycle.max_lifecycle_hours }} | Tickets Reopened: {{ metrics.technical.lifecycle.tickets_reopened }} | Avg Reopens: {{ metrics.technical.lifecycle.average_reopens }}</p>
    </div>
  </body>
</html>
"""
)


def render_html(metrics: Dict[str, Any], filters: Dict[str, Any], output_path: Path) -> None:
    html = HTML_TEMPLATE.render(metrics=metrics, filters=filters)
    output_path.write_text(html, encoding="utf-8")


class _PDFReport(FPDF):
    def header(self) -> None:  # pragma: no cover - simple layout call
        self.set_font("Helvetica", "B", 14)
        self.cell(0, 10, "Freshservice Reporting Suite", ln=1, align="C")
        self.ln(5)


def render_pdf(metrics: Dict[str, Any], filters: Dict[str, Any], output_path: Path) -> None:
    pdf = _PDFReport()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", size=10)

    pdf.multi_cell(0, 6, f"Generated: {metrics['generated_at']}")
    if filters.get("start_date"):
        pdf.multi_cell(0, 6, f"Start Date: {filters['start_date']}")
    if filters.get("end_date"):
        pdf.multi_cell(0, 6, f"End Date: {filters['end_date']}")
    if filters.get("categories"):
        pdf.multi_cell(0, 6, "Categories: " + ", ".join(filters["categories"]))
    pdf.ln(4)

    def _section(title: str, body: Iterable[str]) -> None:
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, title, ln=1)
        pdf.set_font("Helvetica", size=10)
        for line in body:
            try:
                pdf.cell(0, 5, line, ln=1)
            except FPDFException:
                trimmed = line if len(line) < 120 else line[:117] + "..."
                pdf.cell(0, 5, trimmed, ln=1)
        pdf.ln(2)

    _section(
        "Operational Overview",
        [
            f"Tickets created: {metrics['operational']['ticket_volume_trend']['total_created']}",
            f"Tickets resolved: {metrics['operational']['ticket_volume_trend']['total_resolved']}",
            f"SLA compliance: {metrics['operational']['sla_compliance']['compliance_rate'] * 100:.1f}%",
            f"Open backlog: {metrics['operational']['backlog_and_aging']['open_tickets']}",
        ],
    )

    top_agents = metrics["operational"]["agent_performance"][:5]
    _section(
        "Top Agent Performance",
        [
            f"{agent['agent']}: {agent['tickets_resolved']} resolved / {agent['tickets_open']} open"
            for agent in top_agents
        ] or ["No agent activity recorded."],
    )

    _section(
        "Strategic Indicators",
        [
            f"SLA breaches: {len(metrics['strategic']['service_risk']['sla_breaches'])}",
            f"High priority open: {len(metrics['strategic']['service_risk']['high_priority_open'])}",
            f"Recurring incidents tracked: {len(metrics['strategic']['recurring_incidents'])}",
        ],
    )

    _section(
        "Technical & Audit",
        [
            f"Missing category rate: {metrics['technical']['data_quality']['missing_category_rate'] * 100:.1f}%",
            f"Average lifecycle (h): {metrics['technical']['lifecycle']['average_lifecycle_hours']}",
            f"Tickets reopened: {metrics['technical']['lifecycle']['tickets_reopened']}",
        ],
    )

    pdf.output(str(output_path))


def _get_pyplot():  # pragma: no cover - thin wrapper around matplotlib import
    matplotlib = import_module("matplotlib")
    if hasattr(matplotlib, "use"):
        try:
            matplotlib.use("Agg")
        except Exception:  # pragma: no cover - defensive fallback
            pass
    return import_module("matplotlib.pyplot")


def _plot_line_chart(data: List[Dict[str, Any]], output_path: Path) -> None:
    if not data:
        return
    plt = _get_pyplot()
    dates = [datetime.fromisoformat(row["date"]) for row in data]
    created = [row["created"] for row in data]
    resolved = [row["resolved"] for row in data]
    plt.figure(figsize=(10, 4))
    plt.plot(dates, created, label="Created", marker="o")
    plt.plot(dates, resolved, label="Resolved", marker="o")
    plt.xlabel("Date")
    plt.ylabel("Tickets")
    plt.title("Ticket Volume Trend")
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()


def _plot_bar_chart(counter: Counter[Any], output_path: Path, title: str, max_items: int = 10) -> None:
    if not counter:
        return
    plt = _get_pyplot()
    most_common = counter.most_common(max_items)
    labels = [str(label) for label, _ in most_common]
    values = [count for _, count in most_common]
    plt.figure(figsize=(10, 4))
    plt.bar(labels, values, color="#1f77b4")
    plt.xticks(rotation=45, ha="right")
    plt.title(title)
    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()


def render_images(metrics: Dict[str, Any], output_dir: Path) -> List[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    generated: List[Path] = []
    trend_path = output_dir / "ticket_volume_trend.png"
    _plot_line_chart(metrics["operational"]["ticket_volume_trend"]["trend"], trend_path)
    if trend_path.exists():
        generated.append(trend_path)

    category_counts = metrics["operational"]["category_breakdown"]["categories"]
    if isinstance(category_counts, dict) and not isinstance(category_counts, Counter):
        category_counts = Counter(category_counts)
    if isinstance(category_counts, Counter):
        cat_path = output_dir / "category_breakdown.png"
        _plot_bar_chart(category_counts, cat_path, "Tickets by Category")
        if cat_path.exists():
            generated.append(cat_path)

    return generated


def _stringify_key(key: Any) -> str:
    if isinstance(key, tuple):
        return " > ".join("" if part is None else str(part) for part in key)
    return str(key)


def _json_default(value: Any) -> Any:
    if isinstance(value, Counter):
        return {_stringify_key(key): count for key, count in value.items()}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _normalise_for_json(value: Any) -> Any:
    if isinstance(value, Counter):
        return {_stringify_key(k): _normalise_for_json(v) for k, v in value.items()}
    if isinstance(value, dict):
        return {str(k): _normalise_for_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalise_for_json(item) for item in value]
    if isinstance(value, tuple):
        return [_normalise_for_json(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return value


def save_metrics_json(metrics: Dict[str, Any], output_path: Path) -> None:
    serialisable = _normalise_for_json(metrics)
    output_path.write_text(json.dumps(serialisable, indent=2), encoding="utf-8")


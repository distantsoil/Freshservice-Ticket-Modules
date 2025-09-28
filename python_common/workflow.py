"""Higher level workflows used by platform specific entry points."""
from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from dateutil import parser as date_parser

from .analysis import TicketAnalyzer, TicketRecord
from .config import load_config, resolve_path
from .freshservice_client import FreshserviceClient
from .logging_setup import configure_logging
from .report_generation import (
    TicketReportBuilder,
    TicketSnapshot,
    render_html,
    render_pdf,
    render_images,
    save_metrics_json,
)
from .reporting import TicketReportWriter
from .review import ReviewWorksheet, ReviewRow
from .updates import (
    TicketUpdater,
    UpdateTracker,
    UpdateError,
    HTTPError,
    describe_http_error,
    summarize_category_path,
)
from .taxonomy import build_taxonomy_model

LOGGER = logging.getLogger(__name__)


def _current_utc_timestamp() -> str:
    """Return a compact UTC timestamp for log and report filenames."""

    return datetime.utcnow().strftime("%Y%m%d-%H%M%S")


def _parse_filter_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    dt = date_parser.isoparse(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass
class FetchAnalyzeOptions:
    config_path: Optional[str]
    output_directory: Optional[str]
    report_name: Optional[str]
    updated_since: Optional[str]
    disable_console: bool = False
    simple_console: bool = False
    console_level: Optional[str] = None
    create_review_template: bool = True
    show_console_log: bool = False


@dataclass
class ReviewOptions:
    review_csv: str
    decision_filter: Optional[List[str]] = None


@dataclass
class ApplyUpdatesOptions:
    config_path: Optional[str]
    review_csv: Optional[str]
    ticket_ids: Optional[List[int]] = None
    category: Optional[str] = None
    sub_category: Optional[str] = None
    item_category: Optional[str] = None
    disable_console: bool = False
    simple_console: bool = False
    console_level: Optional[str] = None
    dry_run: bool = False
    skip_log_path: Optional[str] = None
    force_all: bool = False
    force_ticket_ids: Optional[List[int]] = None
    show_console_log: bool = False


@dataclass
class ReportOptions:
    config_path: Optional[str]
    output_directory: Optional[str]
    start_date: Optional[str]
    end_date: Optional[str]
    categories: Optional[List[str]] = None
    sub_categories: Optional[List[str]] = None
    formats: Optional[List[str]] = None
    disable_console: bool = False
    simple_console: bool = False
    console_level: Optional[str] = None
    show_console_log: bool = False


class _ProgressTask:
    """Lightweight textual progress indicator with optional ETA."""

    _BAR_WIDTH = 30

    def __init__(self, description: str, enabled: bool) -> None:
        self.description = description
        self.enabled = enabled
        self.start_time = time.monotonic()
        self.total: Optional[int] = None
        self.count = 0

    def update(self, count: int, total: Optional[int] = None) -> None:
        if not self.enabled:
            return
        if total is not None and total >= 0:
            self.total = total
        self.count = max(count, 0)
        elapsed = max(time.monotonic() - self.start_time, 0.0)
        rate = self.count / elapsed if elapsed > 0 and self.count > 0 else 0.0
        eta: Optional[float] = None
        if self.total and self.total > 0 and rate > 0:
            remaining = max(self.total - self.count, 0)
            eta = remaining / rate if remaining > 0 else 0.0

        bar = ""
        progress_summary = f"{self.count}"
        if self.total and self.total > 0:
            fraction = min(max(self.count / self.total, 0.0), 1.0)
            filled = min(int(round(fraction * self._BAR_WIDTH)), self._BAR_WIDTH)
            bar = f"[{'#' * filled}{'-' * (self._BAR_WIDTH - filled)}]"
            progress_summary = f"{self.count}/{self.total} ({fraction * 100:5.1f}%)"

        parts = [self.description]
        if bar:
            parts.append(bar)
        parts.append(progress_summary)
        parts.append(f"elapsed {elapsed:6.1f}s")
        parts.append(f"eta {eta:6.1f}s" if eta is not None else "eta --")
        parts.append(f"rate {rate:6.2f}/s" if rate > 0 else "rate --")
        message = " ".join(parts)
        sys.stdout.write("\r" + message)
        sys.stdout.flush()

    def done(self) -> None:
        if not self.enabled:
            return
        self.update(self.count, self.total)
        sys.stdout.write("\n")
        sys.stdout.flush()


def _prepare_logging(config: dict, options: FetchAnalyzeOptions | ApplyUpdatesOptions, *, base_dir: Path) -> None:
    logging_config = config.setdefault("logging", {})
    console_cfg = logging_config.setdefault("console", {})
    if isinstance(options, (FetchAnalyzeOptions, ApplyUpdatesOptions, ReportOptions)):
        if options.disable_console:
            console_cfg["enabled"] = False
        if options.simple_console:
            console_cfg["rich_format"] = False
        if options.console_level:
            console_cfg["level"] = options.console_level
    configure_logging(config, base_dir=base_dir)

    if isinstance(options, ApplyUpdatesOptions):
        run_cfg = logging_config.get("bulk_update_run", {})
        timestamp = run_cfg.get("timestamp", _current_utc_timestamp())
        template = run_cfg.get(
            "path_template",
            "logs/bulk_updates/bulk_update_{timestamp}.log",
        )
        try:
            run_log_path = resolve_path(template.format(timestamp=timestamp), base=base_dir)
        except KeyError as exc:  # pragma: no cover - template mistakes are rare
            raise ValueError(f"Invalid bulk update log template: missing placeholder {exc}")
        run_log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(run_log_path, mode="w", encoding="utf-8")
        level = run_cfg.get("level") or logging_config.get("file", {}).get("level", "DEBUG")
        handler.setLevel(level)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logging.getLogger().addHandler(handler)
        setattr(options, "run_log_path", run_log_path)
        LOGGER.info("Bulk update log file: %s", run_log_path)


def _format_update_summary(
    *,
    total: int,
    successes: int,
    errors: int,
    run_log_path: Optional[Path],
    dry_run: bool,
) -> str:
    """Return a table summarising update outcomes with guidance on log files."""

    skipped = max(total - successes - errors, 0)
    rows = [
        ("Total tickets processed", total),
        ("Successful updates", successes),
        ("Skipped / unchanged", skipped),
        ("Errors", errors),
    ]

    label_width = max(len(label) for label, _ in rows)
    value_width = max(len(str(value)) for _, value in rows)

    border = f"+{'-' * (label_width + 2)}+{'-' * (value_width + 2)}+"
    header = f"| {'Metric'.ljust(label_width)} | {'Count'.rjust(value_width)} |"

    lines = [border, header, border]
    for label, value in rows:
        lines.append(f"| {label.ljust(label_width)} | {str(value).rjust(value_width)} |")
    lines.append(border)

    notes: List[str] = []
    if dry_run:
        notes.append("Dry run enabled: no changes were sent to Freshservice.")

    if run_log_path is not None:
        notes.append(f"Detailed log: {run_log_path}")
    else:
        notes.append("Detailed output written to configured log handlers.")

    if errors:
        notes.append("Review the detailed log for error entries and remediation steps.")

    lines.extend(["", *notes])
    return "\n".join(lines)


def _create_client(config: dict) -> FreshserviceClient:
    fs_cfg = config.get("freshservice", {})
    base_url = fs_cfg.get("endpoint_url") or fs_cfg.get("base_url")
    if not base_url:
        raise ValueError("Configuration missing freshservice.base_url or freshservice.endpoint_url")
    client = FreshserviceClient(
        base_url=base_url,
        api_key=fs_cfg["api_key"],
        verify_ssl=fs_cfg.get("verify_ssl", True),
        timeout=int(fs_cfg.get("timeout", 30)),
        per_page=int(fs_cfg.get("per_page", 100)),
        rate_limit_per_minute=fs_cfg.get("rate_limit_per_minute"),
    )
    return client


def fetch_and_analyze(options: FetchAnalyzeOptions, *, base_dir: Optional[Path] = None) -> Path:
    base_dir = base_dir or Path.cwd()
    config = load_config(options.config_path)
    _prepare_logging(config, options, base_dir=base_dir)

    client = _create_client(config)
    ticket_fields = list(client.iter_ticket_fields())
    categories, subcategories, item_categories = _extract_taxonomy(ticket_fields)
    LOGGER.info(
        "Loaded %s categories, %s subcategories, %s item categories",
        len(categories),
        sum(len(v) for v in subcategories.values()),
        sum(len(v) for v in item_categories.values()),
    )

    taxonomy_cfg = config.get("taxonomy")
    taxonomy_model = build_taxonomy_model(
        taxonomy_cfg,
        available_taxonomy=(categories, subcategories, item_categories),
    )

    progress_enabled = not options.show_console_log

    fetch_progress = _ProgressTask("Fetching tickets", progress_enabled)
    tickets: List[Dict[str, object]] = []
    try:
        for ticket in client.iter_tickets(
            updated_since=options.updated_since,
            progress_callback=fetch_progress.update,
        ):
            tickets.append(ticket)
    finally:
        fetch_progress.done()

    ticket_records = [TicketRecord.from_api(ticket) for ticket in tickets]
    LOGGER.info("Retrieved %s ticket records for analysis", len(ticket_records))

    analysis_cfg = config.get("analysis", {})
    max_suggestions = int(
        analysis_cfg.get(
            "max_suggestions_per_ticket",
            taxonomy_cfg.get("max_suggestions", 3) if taxonomy_cfg else 3,
        )
    )

    analyzer = TicketAnalyzer(
        taxonomy=taxonomy_model,
        keyword_min_length=int(analysis_cfg.get("keyword_min_length", 4)),
        min_keyword_frequency=int(analysis_cfg.get("min_keyword_frequency", 3)),
        max_suggestions_per_ticket=max_suggestions,
        stop_words=analysis_cfg.get("stop_words", []),
        keyword_overrides=analysis_cfg.get("keyword_overrides", {}),
    )

    analysis_progress = _ProgressTask("Analyzing tickets", progress_enabled)
    try:
        suggestions = analyzer.suggest_categories(
            ticket_records, progress_callback=analysis_progress.update
        )
    finally:
        analysis_progress.done()
    repeating = analyzer.detect_repeating_keywords(ticket_records)

    existing_categories = TicketAnalyzer.extract_existing_categories(ticket_records)
    _log_taxonomy(existing_categories, repeating)

    reporting_cfg = config.get("reporting", {})
    output_directory = resolve_path(
        options.output_directory or reporting_cfg.get("output_directory", "reports"), base=base_dir
    )
    report_name = options.report_name or reporting_cfg.get("report_filename", "ticket_analysis.csv")
    writer = TicketReportWriter(output_directory=output_directory, report_name=report_name)
    report_path = writer.write_analysis(ticket_records, suggestions, repeating)

    review_path: Optional[Path] = None
    if options.create_review_template:
        review_path = writer.create_review_template(report_path)

    LOGGER.info("Analysis complete. Report available at %s", report_path)
    if not options.show_console_log:
        print(f"Analysis complete. Report available at {report_path}")
        if review_path is not None:
            print(f"Review template available at {review_path}")
    return report_path


def review_rows(options: ReviewOptions) -> List[ReviewRow]:
    worksheet_path = Path(options.review_csv)
    worksheet = ReviewWorksheet(worksheet_path)
    rows = worksheet.load_rows()
    LOGGER.info("Loaded %s actionable review rows", len(rows))
    if options.decision_filter:
        rows = ReviewWorksheet.filter_rows(rows, include_decisions=options.decision_filter)
        LOGGER.info("Filtered to %s rows after applying decision filter", len(rows))
    return rows


def apply_updates(options: ApplyUpdatesOptions, *, base_dir: Optional[Path] = None) -> List[dict]:
    base_dir = base_dir or Path.cwd()
    config = load_config(options.config_path)
    _prepare_logging(config, options, base_dir=base_dir)
    client = _create_client(config)
    updater = TicketUpdater(client)

    responses: List[dict] = []
    errors: List[UpdateError] = []
    total_attempted = 0
    if options.ticket_ids:
        total = len(options.ticket_ids)
        total_attempted = total
        LOGGER.info("Applying targeted updates to %s tickets", total)
        progress_task = _ProgressTask("Updating tickets", not options.show_console_log)
        try:
            for index, ticket_id in enumerate(options.ticket_ids, start=1):
                try:
                    response = updater.update_single_ticket(
                        ticket_id,
                        category=options.category,
                        sub_category=options.sub_category,
                        item_category=options.item_category,
                        dry_run=options.dry_run,
                    )
                except ValueError as exc:
                    LOGGER.error("Cannot update ticket %s: %s", ticket_id, exc)
                    raise
                except HTTPError as exc:
                    message = describe_http_error(exc, ticket_id)
                    LOGGER.error(message)
                    errors.append(
                        UpdateError(
                            ticket_id=ticket_id,
                            message=message,
                            status_code=getattr(getattr(exc, "response", None), "status_code", None),
                            decision="targeted",
                            category_path=summarize_category_path(
                                options.category,
                                options.sub_category,
                                options.item_category,
                            ),
                        )
                    )
                    continue
                except Exception as exc:  # pragma: no cover - unexpected failure
                    LOGGER.exception("Unexpected error while updating ticket %s", ticket_id)
                    errors.append(
                        UpdateError(
                            ticket_id=ticket_id,
                            message=str(exc),
                            status_code=None,
                            decision="targeted",
                            category_path=summarize_category_path(
                                options.category,
                                options.sub_category,
                                options.item_category,
                            ),
                        )
                    )
                    continue
                finally:
                    progress_task.update(index, total)
                if response is not None:
                    responses.append(response)
        finally:
            progress_task.done()
    elif options.review_csv:
        worksheet = ReviewWorksheet(Path(options.review_csv))
        rows = worksheet.load_rows()
        actionable = [row for row in rows if row.manager_decision == "approve"]
        LOGGER.info("Applying updates for %s approved tickets", len(actionable))
        total_attempted = len(actionable)
        updates_cfg = config.get("updates", {})
        skip_log_setting = (
            options.skip_log_path
            or updates_cfg.get("skip_log")
            or "reports/updated_tickets.log"
        )
        skip_tracker: UpdateTracker | None = None
        if skip_log_setting:
            skip_path = resolve_path(skip_log_setting, base=base_dir)
            skip_tracker = UpdateTracker(skip_path)
            if not options.dry_run and not options.force_all:
                LOGGER.info(
                    "Loaded %s previously updated tickets from %s",
                    len(skip_tracker),
                    skip_path,
                )
        force_ids = set(options.force_ticket_ids or [])
        if skip_tracker and options.force_all:
            LOGGER.info(
                "Force flag supplied; ignoring previously updated tickets recorded in %s",
                skip_tracker.path,
            )
        elif skip_tracker and force_ids:
            LOGGER.info(
                "Force-processing %s tickets despite skip log", len(force_ids)
            )
        total_rows = len(actionable)
        progress_task = _ProgressTask("Applying updates", not options.show_console_log)
        try:
            responses = updater.update_ticket_categories(
                actionable,
                dry_run=options.dry_run,
                skip_tracker=skip_tracker,
                force_ticket_ids=force_ids,
                force_all=options.force_all,
                progress_callback=progress_task.update,
                total_rows=total_rows,
                error_collector=errors,
            )
        finally:
            progress_task.done()
    else:
        raise ValueError("Either ticket_ids or review_csv must be supplied")
    if errors:
        LOGGER.warning(
            "Encountered %s update error(s). See log for detailed entries.", len(errors)
        )
        for failure in errors:
            LOGGER.debug(
                "Failed ticket %s (%s, decision=%s): %s",
                failure.ticket_id,
                failure.category_path,
                failure.decision,
                failure.message,
            )
    successes = len(responses)
    error_count = len(errors)
    summary_text = _format_update_summary(
        total=total_attempted,
        successes=successes,
        errors=error_count,
        run_log_path=getattr(options, "run_log_path", None),
        dry_run=options.dry_run,
    )

    LOGGER.info("Completed updates for %s tickets", successes)
    LOGGER.info("Update summary:\n%s", summary_text)
    print(summary_text)

    return responses


def generate_reports(options: ReportOptions, *, base_dir: Optional[Path] = None) -> Path:
    base_dir = base_dir or Path.cwd()
    config = load_config(options.config_path)
    _prepare_logging(config, options, base_dir=base_dir)

    client = _create_client(config)

    reporting_cfg = config.get("reporting", {})
    suite_cfg = config.get("reporting_suite", {})
    default_output = suite_cfg.get("output_directory") or reporting_cfg.get("output_directory", "reports")
    output_directory = resolve_path(options.output_directory or default_output, base=base_dir)
    timestamp = _current_utc_timestamp()
    report_root = output_directory / f"advanced_report_{timestamp}"
    report_root.mkdir(parents=True, exist_ok=True)

    start_dt = _parse_filter_date(options.start_date)
    end_dt = _parse_filter_date(options.end_date)
    category_filter = {c.strip() for c in options.categories or [] if c and c.strip()}
    subcategory_filter = {c.strip() for c in options.sub_categories or [] if c and c.strip()}

    fetch_progress = _ProgressTask("Fetching tickets for reporting", not options.show_console_log)
    tickets: List[TicketSnapshot] = []
    try:
        for payload in client.iter_tickets(include=["stats"], progress_callback=fetch_progress.update):
            snapshot = TicketSnapshot.from_api(payload)
            if start_dt and snapshot.created_at < start_dt:
                continue
            if end_dt and snapshot.created_at > end_dt:
                continue
            if category_filter and (snapshot.category not in category_filter):
                continue
            if subcategory_filter and (snapshot.sub_category not in subcategory_filter):
                continue
            tickets.append(snapshot)
    finally:
        fetch_progress.done()

    LOGGER.info("Collected %s tickets for advanced reporting", len(tickets))

    builder = TicketReportBuilder(tickets)
    metrics = builder.build()

    filters = {
        "start_date": options.start_date,
        "end_date": options.end_date,
        "categories": sorted(category_filter) if category_filter else [],
        "sub_categories": sorted(subcategory_filter) if subcategory_filter else [],
    }

    formats = options.formats or suite_cfg.get("formats", ["html", "pdf", "images", "json"])
    formats = [fmt.lower() for fmt in formats]

    if "html" in formats:
        html_path = report_root / "report.html"
        render_html(metrics, filters, html_path)
        LOGGER.info("HTML report written to %s", html_path)

    if "pdf" in formats:
        pdf_path = report_root / "report.pdf"
        render_pdf(metrics, filters, pdf_path)
        LOGGER.info("PDF report written to %s", pdf_path)

    if "images" in formats:
        images_dir = report_root / "images"
        generated = render_images(metrics, images_dir)
        if generated:
            LOGGER.info("Generated %s chart images", len(generated))

    if "json" in formats:
        json_path = report_root / "metrics.json"
        save_metrics_json(metrics, json_path)
        LOGGER.info("Metrics JSON written to %s", json_path)

    return report_root


def _extract_taxonomy(
    ticket_fields: Iterable[dict],
) -> tuple[List[str], Dict[str | None, List[str]], Dict[Tuple[str | None, str | None], List[str]]]:
    categories: List[str] = []
    subcategories: Dict[str | None, List[str]] = {}
    item_categories: Dict[Tuple[str | None, str | None], List[str]] = {}

    category_value_to_label: Dict[str, str] = {}
    category_label_lookup: Dict[str, str] = {}
    value_parent_lookup: Dict[str, Optional[str]] = {}
    raw_subcategories: Dict[str | None, List[_ChoiceEntry]] = {}
    raw_item_categories: Dict[Tuple[str | None, str | None], List[_ChoiceEntry]] = {}
    subcategory_value_to_label: Dict[Tuple[str | None, str], str] = {}
    subcategory_label_lookup: Dict[str, str] = {}
    subcategory_parent_by_label: Dict[str, str] = {}
    subcategory_parent_by_value: Dict[str, str] = {}

    for field in ticket_fields:
        raw_name = field.get("name") or ""
        normalised_name = str(raw_name).strip().lower()
        if not normalised_name:
            label_hint = str(field.get("label") or "").strip().lower()
            normalised_name = label_hint

        if normalised_name == "category":
            entries = _normalize_choices(field, flatten_nested=False)
            for entry in entries:
                if entry.label not in categories:
                    categories.append(entry.label)
                if entry.value:
                    category_value_to_label.setdefault(entry.value, entry.label)
                category_label_lookup.setdefault(entry.label.lower(), entry.label)
                if entry.value:
                    value_parent_lookup.setdefault(entry.value, None)

            dependent_root = field.get("choices") or field.get("nested_options")
            dependent_entries = _collect_choice_entries(dependent_root, flatten_nested=True)
            for entry in dependent_entries:
                if entry.value:
                    value_parent_lookup[entry.value] = entry.parent_value
                if entry.depth == 1:
                    parent_key: Optional[str] = entry.parent_value or entry.parent_label
                    raw_subcategories.setdefault(parent_key, []).append(entry)
                elif entry.depth >= 2:
                    sub_hint = entry.parent_value or entry.parent_label
                    category_hint: Optional[str] = None
                    if isinstance(entry.parent_value, str):
                        category_hint = value_parent_lookup.get(entry.parent_value)
                    raw_item_categories.setdefault((category_hint, sub_hint), []).append(entry)
        elif normalised_name in {"sub_category", "subcategory"}:
            choices_root = field.get("choices") or field.get("nested_options")
            if isinstance(choices_root, dict):
                for parent, node in choices_root.items():
                    parent_value = str(parent) if parent is not None else None
                    raw_subcategories[parent_value] = _collect_choice_entries(
                        node,
                        flatten_nested=True,
                        parent_value=parent_value,
                    )
            else:
                raw_subcategories[None] = _collect_choice_entries(
                    choices_root,
                    flatten_nested=True,
                )
        elif normalised_name in {"item_category", "itemcategory", "sub_sub_category", "subsubcategory"}:
            choices_root = field.get("choices") or field.get("nested_options")
            if isinstance(choices_root, dict):
                for category_parent, sub_block in choices_root.items():
                    category_value = str(category_parent) if category_parent is not None else None
                    if isinstance(sub_block, dict):
                        for sub_parent, node in sub_block.items():
                            sub_value = str(sub_parent) if sub_parent is not None else None
                            raw_item_categories[(category_value, sub_value)] = _collect_choice_entries(
                                node,
                                flatten_nested=True,
                                parent_value=sub_value,
                            )
                    else:
                        raw_item_categories[(category_value, None)] = _collect_choice_entries(
                            sub_block,
                            flatten_nested=True,
                        )
            else:
                raw_item_categories[(None, None)] = _collect_choice_entries(choices_root, flatten_nested=True)

    for parent_hint, entries in raw_subcategories.items():
        if not entries:
            continue
        for entry in entries:
            effective_parent_value = entry.parent_value or parent_hint
            parent_label = entry.parent_label
            if effective_parent_value:
                parent_label = parent_label or category_value_to_label.get(effective_parent_value)
                if not parent_label and isinstance(effective_parent_value, str):
                    parent_label = category_label_lookup.get(effective_parent_value.lower())
            if parent_label:
                parent_label = category_label_lookup.get(parent_label.lower(), parent_label)
            parent_key = parent_label or (effective_parent_value if effective_parent_value else None)
            if not parent_key:
                continue
            bucket = subcategories.setdefault(parent_key, [])
            if entry.label not in bucket:
                bucket.append(entry.label)
                if entry.value:
                    if effective_parent_value:
                        subcategory_value_to_label[(effective_parent_value, entry.value)] = entry.label
                        if parent_label:
                            subcategory_parent_by_value[entry.value] = parent_label
                    subcategory_value_to_label[(None, entry.value)] = entry.label
            subcategory_label_lookup.setdefault(entry.label.lower(), entry.label)
            if parent_label:
                subcategory_parent_by_label[entry.label] = parent_label

    for (category_hint, sub_hint), entries in raw_item_categories.items():
        if not entries:
            continue
        for entry in entries:
            category_label: Optional[str] = None
            if category_hint:
                category_label = category_value_to_label.get(category_hint) or (
                    category_label_lookup.get(category_hint.lower()) if isinstance(category_hint, str) else None
                )
            if not category_label and entry.parent_label:
                lookup = category_label_lookup.get(entry.parent_label.lower())
                if lookup in categories:
                    category_label = lookup
            sub_value = entry.parent_value or sub_hint
            sub_label = entry.parent_label
            if sub_value:
                sub_label = sub_label or subcategory_value_to_label.get((category_hint, sub_value))
                sub_label = sub_label or subcategory_value_to_label.get((None, sub_value))
                if not sub_label and isinstance(sub_value, str):
                    sub_label = subcategory_label_lookup.get(sub_value.lower())
            if sub_label:
                sub_label = subcategory_label_lookup.get(sub_label.lower(), sub_label)
            if not category_label:
                if sub_label and sub_label in subcategory_parent_by_label:
                    category_label = subcategory_parent_by_label[sub_label]
                elif sub_value and sub_value in subcategory_parent_by_value:
                    category_label = subcategory_parent_by_value[sub_value]
            if not category_label or not sub_label:
                continue
            key = (category_label, sub_label)
            bucket = item_categories.setdefault(key, [])
            if entry.label not in bucket:
                bucket.append(entry.label)

    return categories, subcategories, item_categories


@dataclass(frozen=True)
class _ChoiceEntry:
    label: str
    value: Optional[str] = None
    parent_value: Optional[str] = None
    parent_label: Optional[str] = None
    depth: int = 0


def _normalize_choices(field: dict, *, flatten_nested: bool = True) -> List[_ChoiceEntry]:
    """Flatten choice structures into label/value pairs while preserving order."""

    root = field.get("choices") or field.get("nested_options")
    if root is None:
        return []
    return _collect_choice_entries(root, flatten_nested=flatten_nested)


def _collect_choice_entries(
    node: object,
    *,
    flatten_nested: bool,
    parent_label: Optional[str] = None,
    parent_value: Optional[str] = None,
    depth: int = 0,
) -> List[_ChoiceEntry]:
    entries: List[_ChoiceEntry] = []
    seen: set[Tuple[Optional[str], Optional[str], str]] = set()

    def _extract_label(mapping: dict) -> Optional[str]:
        for key in ("label", "name", "title", "value"):
            value = mapping.get(key)
            if value is not None:
                text = unescape(str(value)).strip()
                if text:
                    return text
        return None

    def _extract_value(mapping: dict) -> Optional[str]:
        for key in ("value", "id", "key"):
            if mapping.get(key) is not None:
                return str(mapping[key])
        return None

    def _extract_parent_value(mapping: dict) -> Optional[str]:
        for key in ("parent_value", "parent_id", "parent", "parentKey"):
            if key not in mapping:
                continue
            candidate = mapping[key]
            if isinstance(candidate, (str, int)):
                text = unescape(str(candidate)).strip()
                if text:
                    return text
        return None

    def _extract_parent_label(mapping: dict) -> Optional[str]:
        for key in ("parent_label", "parent_name", "parentTitle"):
            value = mapping.get(key)
            if isinstance(value, str):
                text = unescape(value).strip()
                if text:
                    return text
        return None

    def _collect(
        current: object,
        *,
        allow_nested: bool,
        label_ctx: Optional[str],
        value_ctx: Optional[str],
        current_depth: int,
    ) -> None:
        if current is None:
            return
        if isinstance(current, dict):
            label = _extract_label(current)
            value = _extract_value(current)
            derived_parent_value = _extract_parent_value(current) or value_ctx
            derived_parent_label = _extract_parent_label(current) or label_ctx
            effective_parent_label = derived_parent_label
            effective_parent_value = derived_parent_value
            if label:
                key = (effective_parent_label, effective_parent_value, label)
                if key not in seen:
                    seen.add(key)
                    entries.append(
                        _ChoiceEntry(
                            label=label,
                            value=value,
                            parent_value=effective_parent_value,
                            parent_label=effective_parent_label,
                            depth=current_depth,
                        )
                    )
            if allow_nested:
                next_label_ctx = label or effective_parent_label
                next_value_ctx = value if value is not None else effective_parent_value
                for child in current.values():
                    if isinstance(child, (list, dict)):
                        _collect(
                            child,
                            allow_nested=True,
                            label_ctx=next_label_ctx,
                            value_ctx=next_value_ctx,
                            current_depth=current_depth + 1,
                        )
        elif isinstance(current, list):
            next_depth = current_depth
            for item in current:
                _collect(
                    item,
                    allow_nested=allow_nested,
                    label_ctx=label_ctx,
                    value_ctx=value_ctx,
                    current_depth=next_depth,
                )
        else:
            text = unescape(str(current)).strip()
            if text:
                key = (label_ctx, value_ctx, text)
                if key not in seen:
                    seen.add(key)
                    entries.append(
                        _ChoiceEntry(
                            label=text,
                            value=None,
                            parent_value=value_ctx,
                            parent_label=label_ctx,
                            depth=current_depth,
                        )
                    )

    _collect(
        node,
        allow_nested=flatten_nested,
        label_ctx=parent_label,
        value_ctx=parent_value,
        current_depth=depth,
    )
    return entries


def _log_taxonomy(existing_categories: dict, repeating_keywords: List[tuple[str, int]]) -> None:
    LOGGER.info("Existing category coverage: %s", {k: len(v) for k, v in existing_categories.items()})
    top_repeating = repeating_keywords[:10]
    if top_repeating:
        LOGGER.info("Top repeating keyword patterns: %s", top_repeating)

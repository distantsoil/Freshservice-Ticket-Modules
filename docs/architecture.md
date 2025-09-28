# Architecture Guide

This document expands on the architecture diagram in the main README and highlights module responsibilities, data flow, and extension points.

## Module Overview

| Module | Purpose | Key Functions |
|--------|---------|---------------|
| `python_common/config.py` | Load configuration and resolve relative paths. | `load_config`, `resolve_path` |
| `python_common/logging_setup.py` | Configure console/file logging, optionally using `rich`. | `configure_logging` |
| `python_common/freshservice_client.py` | Low-level HTTP client handling authentication, pagination, and updates. | `iter_tickets`, `iter_ticket_fields`, `update_ticket` |
| `python_common/analysis.py` | Convert API payloads into `TicketRecord` objects, compute keyword suggestions, and detect repeating terms. | `TicketAnalyzer`, `TicketRecord` |
| `python_common/reporting.py` | Persist CSV reports and build manager review templates. | `TicketReportWriter` |
| `python_common/report_generation.py` | Compute executive/operational metrics and render HTML/PDF/image bundles. | `TicketReportBuilder`, `render_html`, `render_pdf` |
| `python_common/review.py` | Parse manager review responses. | `ReviewWorksheet` |
| `python_common/updates.py` | Perform approved updates. | `TicketUpdater` |
| `python_common/workflow.py` | High-level orchestration that combines the above components for CLI scripts. | `fetch_and_analyze`, `apply_updates`, `generate_reports` |

The macOS/Windows entry points simply construct option dataclasses and call into `workflow.py` so you can reuse the same functions in notebooks or unit tests.

## Data Flow

1. **Config Loading** – The workflow reads YAML configuration and prepares logging before any network calls.
2. **API Metadata** – Ticket form fields are retrieved from `/api/v2/ticket_form_fields`
   (per the Freshservice documentation) and cross-checked against the config-defined taxonomy tree so suggestions only use labels that exist in Freshservice.
3. **Ticket Fetching** – All tickets are paginated through `iter_tickets`.
4. **Analysis** – `TicketAnalyzer` tokenises ticket text, applies keyword overrides, evaluates config-supplied keywords/regexes/aliases, and scores category suggestions using depth-aware priority rules.
5. **Reporting** – `TicketReportWriter` writes an analysis CSV and optional review template.
6. **Review** – Managers edit the review template manually or run helper scripts for summaries.
7. **Updates** – `TicketUpdater` reads approved rows, skips tickets whose taxonomy already matches the requested path, consults the skip log to avoid reprocessing tickets that were updated in a previous run, supports dry-run previews, and issues API updates.

## Extension Points

- **Keyword Overrides** – Add keyword-to-category mappings inside `analysis.keyword_overrides` within the config file.
- **Stop Words** – Expand the default list to exclude low-value terminology.
- **Custom Reporting** – Extend `TicketReportWriter` or `report_generation.py` to emit Markdown, Excel, or database exports as needed.
- **Authentication** – Swap the `FreshserviceClient` session configuration if you require proxies or custom TLS handling.

## Error Handling Strategy

- API errors raise exceptions via `requests.raise_for_status`, bubbling up to the CLI which records the failure in the log file.
- Configuration loading issues raise `ConfigError` with descriptive hints.
- Update workflows skip tickets without actionable payloads and log a warning to avoid accidental empty updates.

## Rate Limiting Considerations

The client optionally sleeps between paginated requests based on `freshservice.rate_limit_per_minute`. Adjust according to your Freshservice plan to stay within limits. The default of 240 requests/minute is conservative and can be tuned.

> [!WARNING]
> This was something I created after I got frustrated at the lack of options in the Freshservice ticketing system web UI. Be warned that this is not really 'maintained', more that I'm going to update it when I feel it's useful. Be further warned that while I'm great with powershell I really am not great at Python, > so this is vibe coded to hell and back. It was at least tested as far as I could test it in a real environment, and it does what I wanted it to do well enough. 
> 
> This was created as part of an experiment to research just how realistic it was to vibe code something. It is in no way meant to showcase my actual abilities because, well, duh, it's vibe coded by CoPilot and ChatGPT Codex (Mostly by the latter). 

# Freshservice Ticket Intelligence Toolkit

> **Purpose:** empower IT managers to export, analyse, and curate Freshservice ticket metadata so that reporting categories remain accurate across macOS, Windows, and experimental PowerShell workflows.

## 📚 Table of Contents

1. [Project Overview](#project-overview)
2. [High-Level Architecture](#high-level-architecture)
3. [Features at a Glance](#features-at-a-glance)
4. [Quick Start (macOS & Windows)](#quick-start-macos--windows)
5. [Tooling & Utilities](#tooling--utilities)
6. [Configuration & Secrets Management](#configuration--secrets-management)
7. [macOS Workflow Details](#macos-workflow-details)
8. [Windows Workflow Details](#windows-workflow-details)
9. [Review & Approval Process](#review--approval-process)
10. [Bulk Updates](#bulk-updates)
11. [PowerShell (In Development)](#powershell-in-development)
12. [Logging & Observability](#logging--observability)
13. [Dependency Matrix](#dependency-matrix)
14. [Testing & Validation Tips](#testing--validation-tips)
15. [Directory Map](#directory-map)
16. [Freshservice API Coverage](#freshservice-api-coverage)
17. [Reference Documents](#reference-documents)

---

## Project Overview

Freshservice categorisation is the backbone of operational reporting. This toolkit:

- Fetches **every ticket ever opened** (respecting API pagination & rate limits).
- Normalises the ticket taxonomy (category, subcategory, item category) using Freshservice metadata and a configuration-driven taxonomy tree so labels, punctuation, and legacy aliases remain authoritative.
- Performs **keyword-based subject/body analysis** to recommend the best matching existing category or raise potential new taxonomy ideas when repeated language is found.
- Produces a rich CSV dossier that contains ticket number, subject, body, current taxonomy, suggested taxonomy, and keyword insights.
- Records the ticket creation timestamp in UTC so reports can be sliced by date/time without guessing regional offsets.
- Generates a **manager review worksheet** where suggestions can be approved or declined.
- Applies single, multi, or bulk updates back to Freshservice in a controlled fashion.
- Ships with an experimental PowerShell implementation for teams that want to iterate natively.

The scripts are modular so you can run individual functions from an IDE or REPL during validation.

---

## High-Level Architecture

```mermaid
graph LR
    A[Config YAML<br/>API Key, Base URL, Logging] --> B(FreshserviceClient)
    B -->|ticket_form_fields| C[Taxonomy Normaliser]
    B -->|tickets| D[TicketRecord Builder]
    C --> E[TicketAnalyzer]
    D --> E
    E -->|Suggestions & Patterns| F[TicketReportWriter]
    F --> G[Analysis CSV + Review Template]
    G --> H[Manager Approval]
    H --> I[ReviewWorksheet]
    I --> J[TicketUpdater]
    J -->|PUT /tickets/{id}| B
```

---

## Features at a Glance

- ✅ Cross-platform Python scripts for macOS and Windows.
- ✅ Extensive logging with Rich console output (optional) plus rotating log files.
- ✅ Report + review workflow, including optional export helper for filtered decisions.
- ✅ Bulk + targeted update support.
- ✅ Configurable keyword overrides and stop-word lists.
- ✅ Requirements pinned in `requirements.txt`.
- ⚠️ PowerShell workflow delivered as "in development" preview.

---

## Quick Start (macOS & Windows)

1. **Clone this repository** onto the target workstation.
2. **Create your configuration** by copying the sample and inserting credentials:

   ```bash
   cp config/config.example.yaml \
      config/config.yaml
   # Edit config/config.yaml to add your Freshservice API key and domain
   ```

   > Prefer to keep secrets outside the package tree? Create a sibling `config/` directory instead:
   >
   > ```bash
   > mkdir -p config
   > cp config/config.example.yaml config/config.yaml
   > ```
   >
   > Both layouts are supported; when you keep the config at the default path
   > `config/config.yaml` you can omit `--config`
   > entirely. Add `--config /path/to/config.yaml` whenever you store the file
   > elsewhere (for example in a sibling `config/` directory).

3. **Create and activate a Python virtual environment** (Python 3.10+ recommended):

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate  # macOS/Linux
   # or
   .venv\Scripts\activate    # Windows PowerShell
   ```

4. **Install dependencies**:

   ```bash
   pip install -r requirements.txt
   ```

5. **Run the macOS or Windows fetcher** (they share the same internals):

   ```bash
   python macos/fetch_and_analyze.py
   # Windows alternative
   py -3 windows\fetch_and_analyze.py
   # Add --config /custom/path.yaml if you keep credentials outside the default location
   ```

6. Inspect the generated report under `reports/` and use the review worksheet to accept/reject suggestions. (If you override `reporting.output_directory` in `config.yaml`, adjust the paths below accordingly.)
7. Apply updates when ready (see [Bulk Updates](#bulk-updates)).

---

## Tooling & Utilities

Unless noted otherwise, the examples below pass `--config` explicitly for clarity. When your
credentials live at the default `config/config.yaml`, you can drop the
flag entirely.

### List taxonomy

Print the live Freshservice hierarchy without touching ticket data. The helper respects the same
configuration and logging settings as the primary workflows, so you can reuse your existing
`config.yaml` secrets and logging destinations.

```bash
python tools/list_taxonomy.py \
  --config config/config.yaml
```

Sample output:

```text
- Hardware
-- Peripherals
--- Audio / Video Devices
- Software
-- Adobe
--- Photoshop
```

Use the output to verify that the analyzer suggestions map to actual tenant labels or to share the
current taxonomy with stakeholders.

### Ticket category snapshot

Need a quick pulse check on how many tickets have been categorised? The summary helper prints a
console table grouping tickets by their current category and calls out how many remain
uncategorised.

```bash
python tools/summarize_ticket_categories.py
# Optional: limit to recent activity
# python tools/summarize_ticket_categories.py --updated-since 2024-06-01T00:00:00Z
```

Sample output:

```text
Category        Tickets
-------------  -------
Hardware            42
Software            35
Uncategorised        8
Total               85
```

### Delete tickets (dangerous)

Quickly purge tickets by ID when you need to clean up test data or reverse bulk imports. The helper
accepts repeated `--ticket-id` flags for ad-hoc deletions or a CSV with a `ticket_id` column for
larger batches. Always run with `--dry-run` first to confirm the target IDs before issuing the
actual delete call.

**Usage tips**

- The script merges IDs from the CLI and the CSV, deduplicates them, and processes the final list in
  ascending order. You can supply both `--ticket-id` flags and a CSV in the same run.
- CSV files **must** include a header row containing either `ticket_id` or `id`. Additional columns
  are ignored, and blank rows are skipped automatically.
- Each ticket ID must be an integer. Non-numeric values are skipped with a warning so a typo in the
  spreadsheet does not abort the whole batch.
- Start with `--dry-run` to double-check which IDs would be removed, then rerun without the flag to
  actually call the Freshservice API.

Example CSV (`bad_tickets.csv`):

```csv
ticket_id,note
12345,Test import ticket
54321,Duplicate training ticket
```

```bash
# Delete a single ticket
python tools/delete_tickets.py \
  --config config/config.yaml \
  --ticket-id 12345 --dry-run

# Delete several tickets in one command
python tools/delete_tickets.py \
  --ticket-id 12345 --ticket-id 54321

# Bulk delete from a CSV (requires a ticket_id column)
python tools/delete_tickets.py \
  --csv /path/to/bad_tickets.csv
```

### Clean up the virtual environment

After testing you can remove the local virtual environment (and optionally purge the pip cache)
with a single helper. Make sure to run `deactivate` first if the environment is active in your
shell.

```bash
python tools/cleanup_virtualenv.py --venv-path .venv
```

Add `--purge-pip-cache` to run `pip cache purge` after deleting the directory, or `--dry-run` to
preview the operations without making changes.

### Advanced reporting suite

Generate executive-ready artefacts (HTML dashboards, PDFs, and charts) without disrupting the
existing CSV workflows. The reporting suite pulls tickets directly from Freshservice, honours the
same configuration and logging settings as the other scripts, and lets you filter by date range or
category for focused analysis.

```bash
# macOS / Linux
python macos/generate_reports.py \
  --config config/config.yaml \
  --start-date 2024-01-01T00:00:00Z \
  --end-date 2024-03-31T23:59:59Z \
  --category "Software" --format html --format pdf --format images

# Windows
python windows/generate_reports.py \
  --config config\\config.yaml \
  --start-date 2024-01-01T00:00:00Z \
  --format html --format pdf
```

Outputs are written to the directory defined by `reporting_suite.output_directory` (defaults to
`reports/advanced/advanced_report_<timestamp>/`). Each run produces:

- `report.html` – a rich dashboard summarising operational, strategic, and audit metrics.
- `report.pdf` – a condensed briefing suitable for leadership circulation.
- `metrics.json` – structured data for downstream tooling.
- `images/` – PNG charts (ticket volume trends, category breakdowns, etc.).

Omit `--format` to generate the full bundle (HTML, PDF, JSON, and charts). Use repeated
`--category`/`--sub-category` flags to focus on specific taxonomy paths, and supply
`--disable-console-log`/`--show-console-log` to control logging verbosity just like the other
workflows.

> ⚠️ **Irreversible** – Freshservice permanently deletes tickets processed by this script. Double
> check the IDs (or run with `--dry-run`) before proceeding.

### Update requester organizations

Keep requester organization fields aligned with authoritative sources (e.g., Entra ID exports). The
helper expects an `organization` column and either `requester_id` (preferred) or `email` so the row
can be matched to a Freshservice profile. Existing values are skipped automatically, and you can run
in `--dry-run` mode to audit planned changes.

```bash
python tools/update_requester_organizations.py \
  --config config/config.yaml \
  --csv exports/requester_organizations.csv --dry-run
```

Sample CSV (`requester_organizations.csv`):

```csv
requester_id,email,organization
12345,alex@example.com,Studios
23456,casey@example.com,Finance
```

> Tip: include a `requester_id` column when possible—the helper can fall back to email lookups, but
> IDs eliminate ambiguity when multiple accounts share aliases or forwarding rules.

### Update requester fields

Use this flexible helper when you need to adjust arbitrary requester attributes that the Freshservice
UI does not expose. Supply one or more requester IDs or email addresses along with the field
assignments you wish to change.

```bash
python tools/update_requesters.py \
  --config config/config.yaml \
  --requester-id 101 --requester-id 202 \
  --set department="Information Technology" --unset time_zone \
  --set-json custom_fields='{"office_location": "London"}'
```

Key options:

* `--requester-id` / `--email` — identify one or more requesters. You can mix IDs and emails.
* `--set FIELD=VALUE` — assign simple values. Booleans (`true`/`false`), `null`, and integers are
  converted automatically.
* `--set-json FIELD=JSON` — provide structured payloads (for example nested `custom_fields`).
* `--unset FIELD` — clear a field by sending `null`.
* `--dry-run` — preview the updates without contacting the API.

The helper reuses the standard logging pipeline, compares existing values to avoid unnecessary API
calls, and prints a summary detailing how many requesters were updated or skipped.

---

## Configuration & Secrets Management

- **Never hardcode credentials**. Store the API key and Freshservice base URL inside `config/config.yaml` (default) or a sibling `config/config.yaml` and reference it with `--config` when using the alternate location.
- The configuration file supports:
  - `freshservice` block with API key, URL, timeout, pagination, and SSL verification toggle.
    - Supply the tenant root such as `https://yourdomain.freshservice.com`; if you accidentally include the documented API prefix (`/api/v2`), the client now trims it automatically so requests resolve correctly.
  - `logging` block to toggle console/file sinks and levels.
  - `taxonomy` block describing the official category tree, keyword/regex matchers, aliases, and priority order. The loader validates that every configured label exists in Freshservice metadata before analysis begins.
  - `analysis` block to control stop-words, keyword overrides, and threshold values.
  - `reporting` block to adjust output folders and filenames.

---

## Freshservice API Coverage

The toolkit aligns with the official [Freshservice REST API reference](https://api.freshservice.com/#intro).
Regardless of platform, every script limits itself to the endpoints below:

- `GET /api/v2/ticket_form_fields` – fetches the authoritative category, subcategory, and item labels
  so suggestions always reflect the live taxonomy.
- `GET /api/v2/tickets` – paginates through every ticket ever opened and gathers the subject, body,
  metadata, and existing taxonomy values for scoring.
- `GET /api/v2/tickets/{id}` – hydrates individual tickets during targeted review/update scenarios.
- `PUT /api/v2/tickets/{id}` – applies approved taxonomy updates (category, subcategory, item category)
  when the manager chooses to commit changes. **No other ticket fields are modified by this toolkit.**
- `DELETE /api/v2/tickets/{id}` – permanently removes tickets when operators explicitly invoke the
  deletion helper.

Base URLs are normalised with `urllib.parse.urljoin`, which means you can supply either the bare
tenant domain or the documented `/api/v2` root without creating malformed paths. Enabling
`--console-level DEBUG` exposes each HTTP verb and URL in the logs so auditors can verify requests
match the official API specification.

Scripts honour `--config` to point at alternate files, and fallback search paths include `~/.freshservice/config.yaml`. When you keep the configuration at the default package path you can skip the flag entirely.

> 🛡️ Recommendation: place the configuration file within an OS keychain or encrypted volume when not in use.

---

## macOS Workflow Details

1. **Fetch & Analyse** – `macos/fetch_and_analyze.py`
   - Shows a progress bar with elapsed/ETA information by default; enable detailed console logs with `--show-console-log` (legacy switches `--disable-console-log`, `--simple-console`, `--console-level` still apply when logs are visible).
   - Supports incremental syncs via `--updated-since`.
   - Automatically creates a review template unless `--skip-review-template` is supplied.
   - Loads the taxonomy definitions from `config.yaml`, validates them against Freshservice metadata, and records `created_at_utc` in the output CSV for time-based reporting.
   - **Example invocations:**
     ```bash
    # Full refresh with console debug output
    python macos/fetch_and_analyze.py \
      --config config/config.yaml --show-console-log --console-level DEBUG

     # Incremental run for tickets updated since 1 June 2024 without generating a review template
     python macos/fetch_and_analyze.py \
       --config config/config.yaml --updated-since 2024-06-01T00:00:00Z --skip-review-template
     ```
2. **Review Helper** – `macos/review_suggestions.py`
   - Summarises approval stats and can export filtered subsets for stakeholder review.
   - Operates offline; can leverage config-driven logging if provided.
   - **Example invocations:**
     ```bash
     # Display approval summary for the default review worksheet
     python macos/review_suggestions.py \
       reports/ticket_analysis_review.csv

     # Export only declined rows to a new CSV
     python macos/review_suggestions.py \
       reports/ticket_analysis_review.csv --decision decline --export declined_rows.csv
     ```
3. **Apply Updates** – `macos/apply_updates.py`
   - Shows a progress bar with elapsed/ETA metrics by default; include `--show-console-log` to restore the streaming log output (legacy switches such as `--disable-console-log`, `--simple-console`, and `--console-level` still work when logs are visible).
   - Supports both targeted (`--ticket-id`) and bulk (`--review-csv`) modes.
   - Accepts override taxonomy values during targeted testing.
   - Includes a `--dry-run` flag that prints the proposed taxonomy (and suggestion confidence when available) without issuing API calls.
   - Skips API calls when the current Freshservice category path already matches the requested values to keep updates idempotent.
   - Prints a run summary table at the end of each job highlighting how many tickets were updated, skipped, or encountered errors and points to the timestamped run log for deeper triage.
   - **Example invocations:**
     ```bash
     # Test a single ticket update without committing changes
     python macos/apply_updates.py \
       --config config/config.yaml --ticket-id 12345 --category "Hardware" \
       --sub-category "Peripherals" --item-category "Audio / Video Devices" --dry-run

     # Apply all approved decisions from the review worksheet
     python macos/apply_updates.py \
       --config config/config.yaml --review-csv reports/ticket_analysis_review.csv
     ```

Each script can be launched from an IDE by importing the underlying functions (`FetchAnalyzeOptions`, `ApplyUpdatesOptions`, etc.) for granular debugging.

---

## Windows Workflow Details

The Windows scripts mirror macOS behaviour but adjust default messaging for Windows operators. Launch via `py -3` or an activated virtual environment. Progress bars are shown by default; add `--show-console-log` to surface detailed log output. The scripts reuse the same shared modules to guarantee consistent behaviour across platforms.
The Windows entry points expose the same options—including `--dry-run` and idempotent update logic—so test runs behave identically on both platforms.

**Example Windows commands:**

```powershell
# Fetch with verbose logging
py -3 windows\fetch_and_analyze.py --config config\config.yaml --show-console-log --console-level DEBUG

# Summarise review decisions
py -3 windows\review_suggestions.py reports\ticket_analysis_review.csv --decision approve

# Targeted ticket tests (mix and match the override switches)
py -3 windows\apply_updates.py --config config\config.yaml --ticket-id 12345 --category "Hardware"
py -3 windows\apply_updates.py --config config\config.yaml --ticket-id 45678 --category "Software" --sub-category "Productivity"
py -3 windows\apply_updates.py --config config\config.yaml --ticket-id 98765 --category "Software" --sub-category "Creative & Design" --item-category "Adobe"

# Apply all approved updates (dry run first)
py -3 windows\apply_updates.py --config config\config.yaml --review-csv reports\ticket_analysis_review.csv --dry-run
py -3 windows\apply_updates.py --config config\config.yaml --review-csv reports\ticket_analysis_review.csv
```

---

## Review & Approval Process

1. After the fetch script runs, open `reports/ticket_analysis_review.csv` in your spreadsheet editor.
2. For each ticket row:
   - Set `manager_decision` to `approve`, `decline`, `skip`, or leave `pending`. This column records the decision made by the reviewer (typically the manager running the workflow) and drives which rows are eligible for updates.
   - Adjust `final_category`, `final_sub_category`, and `final_item_category` as required.
   - Use `review_notes` for context shared with peers.
   - Managers can rely on the built-in matching logic for quick approvals **or** export the CSV to an external LLM (e.g., ChatGPT) when they want deeper natural-language insight before choosing a category path. Both approaches use the same review worksheet, so you can mix and match as needed.
3. Run the review helper to surface metrics:

   ```bash
   python macos/review_suggestions.py \
     reports/ticket_analysis_review.csv --decision approve
   ```

4. When satisfied, proceed to updates.

> 💡 Tip: store the reviewed CSV in version control or a shared drive to capture audit history.

---

## Bulk Updates

- **Targeted test updates:**

  ```bash
  # Update only the top-level category (useful when no sub-values exist)
  python macos/apply_updates.py \
    --config config/config.yaml --ticket-id 12345 --category "Hardware"

  # Update a category and sub-category pair
  python macos/apply_updates.py \
    --config config/config.yaml --ticket-id 45678 \
    --category "Software" --sub-category "Productivity"

  # Update a full three-level path in one shot
  python macos/apply_updates.py \
    --config config/config.yaml --ticket-id 98765 \
    --category "Software" --sub-category "Creative & Design" \
    --item-category "Adobe"

  # Exercise multiple tickets in a single dry-run before committing
  python macos/apply_updates.py \
    --config config/config.yaml --ticket-id 12345 --ticket-id 67890 \
    --category "Security" --sub-category "Authentication (MFA / Login)" \
    --dry-run
  ```

- **Bulk apply after approval:**

  ```bash
  python macos/apply_updates.py --config config/config.yaml --review-csv reports/ticket_analysis_review.csv
  ```

- **Error handling:** If Freshservice rejects a row, the updater logs the HTTP status, message, and ticket ID, records the failure in the run log, and continues processing the remaining approvals. Review the log after each run to re-queue any failures or adjust the taxonomy where needed.

Add `--dry-run` to either command to preview the changes without calling the API. The update workflow only touches tickets with `manager_decision` set to `approve` and at least one taxonomy field populated in the `final_*` columns, and it will automatically skip tickets whose existing taxonomy already matches the requested path. Only the Freshservice category, subcategory, and item category fields are sent in the update payload; no other ticket attributes are modified. Logging outputs summarised payloads/responses to both the shared workflow log and the timestamped bulk-run log for compliance review, and HTTP errors are translated into human-friendly explanations (e.g., auth failures vs. validation issues) instead of raw stack traces.

### Skip log & forced replays

- Every successful bulk update writes the ticket ID to the skip log defined by `updates.skip_log` in `config.yaml` (default `reports/updated_tickets.log`).
- Subsequent runs read that file and quietly skip tickets that have already been processed so you can rerun the command after a partial failure without hammering Freshservice.
- To reprocess a single ticket, remove its ID (or comment the line out with `#`) and rerun the script. Delete the file to reset everything.
- Override the location with `--skip-log /path/to/file.log` if you want to keep separate ledgers per campaign.
- Supply `--force` to ignore the log entirely, or use `--force-ticket 12345 --force-ticket 67890` to replay a handful of tickets while leaving the remainder untouched.

The skip file is plain text so team members can review, back up, or modify it as part of their normal change-control process.

---

## PowerShell (In Development)

An experimental implementation lives under `powershell/`. It mirrors the Python flow but is intentionally verbose for troubleshooting. Review `powershell/README.md` for prerequisites. Because of previous API interoperability concerns, treat these scripts as a sandbox and validate against test data first.

---

## Logging & Observability

- Rich console logs (if available) default to `INFO`; toggle verbosity via CLI or config.
- File logs are appended to `logs/freshservice_workflow.log` (macOS/Windows) and include timestamps, levels, module names, payloads, and API status codes.
- Each bulk update run additionally emits a dedicated log at `logs/bulk_updates/bulk_update_<timestamp>.log` (customise via `logging.bulk_update_run.path_template`) so you can inspect exactly which tickets were touched and why retries or failures occurred.
- Review helpers maintain their own log files (`logs/review_helper*.log`).
- PowerShell scripts log to dedicated files within `logs/` and support `-QuietConsole` for silent runs.

A dedicated [Logging Deep Dive](docs/logging.md) illustrates sample entries and rotation strategies.

---

## Dependency Matrix

| Component | Language | Key Dependencies |
|-----------|----------|------------------|
| Shared Python modules | Python 3.10+ | `requests`, `PyYAML`, `python-dateutil` |
| macOS scripts | Python (entry points) | Reuse shared modules |
| Windows scripts | Python (entry points) | Reuse shared modules |
| PowerShell preview | PowerShell 7.2+ | `powershell-yaml` module |

Install Python dependencies via `pip install -r requirements.txt`.

---

## Testing & Validation Tips

- Run `pytest` to execute the regression suite (requires `pytest` in your development environment).
- Run `python -m compileall .` to ensure there are no syntax errors before production use.
- Use the `--updated-since` flag to limit fetches during dry runs and reduce API load.
- Set `logging.console.level` to `DEBUG` in the config to trace API payloads.
- Inspect the generated logs in `logs/` after each run to confirm payloads and responses.
- Consider writing unit tests around `python_common` modules if extending functionality.

---

## Directory Map

```text
Freshservice-Ticket-Modules/
├── README.md
├── requirements.txt
├── config/
│   └── config.example.yaml
├── docs/
│   ├── architecture.md
│   ├── logging.md
│   └── workflows.md
├── python_common/
│   ├── analysis.py
│   ├── config.py
│   ├── freshservice_client.py
│   ├── logging_setup.py
│   ├── reporting.py
│   ├── review.py
│   ├── updates.py
│   ├── workflow.py
│   └── __init__.py
├── macos/
│   ├── apply_updates.py
│   ├── fetch_and_analyze.py
│   ├── generate_reports.py
│   └── review_suggestions.py
├── windows/
│   ├── apply_updates.py
│   ├── fetch_and_analyze.py
│   ├── generate_reports.py
│   └── review_suggestions.py
├── tools/
│   ├── cleanup_virtualenv.py
│   ├── delete_tickets.py
│   ├── list_taxonomy.py
│   ├── summarize_ticket_categories.py
│   ├── update_requester_organizations.py
│   └── update_requesters.py
└── powershell/
    ├── ApplyUpdates.ps1
    ├── FetchAndAnalyze.ps1
    └── README.md
```

---

## Reference Documents

Additional deep dives are located under `docs/`:

- [Architecture Guide](docs/architecture.md)
- [Logging Deep Dive](docs/logging.md)
- [Workflow Cookbook](docs/workflows.md)
- Official Freshservice API reference: <https://api.freshservice.com/>

Each document contains diagrams, callouts, and practical tips for a newcomer.

---

Happy analysing! ✨

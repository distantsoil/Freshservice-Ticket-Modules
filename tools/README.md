# Tools

This directory contains small CLI utilities that operate on Freshservice data using the shared configuration and client in this repo. Each tool reads configuration from `config/config.yaml` by default (unless `--config` is provided).

All commands are cross‑platform Python scripts. Where examples show `python3`, Windows users can replace with `python`.

---

## Common prerequisites
- Python 3.10+
- Install dependencies once from the repo root:
  - `pip install -r requirements.txt`
- Create/configure `config/config.yaml` (see `config/config.example.yaml`). At minimum provide:
  - `freshservice.base_url` (or `freshservice.endpoint_url`)
  - `freshservice.api_key`

Most tools support `--config` to point at an alternate config file.

---

## find_tickets_missing_category.py
List tickets created in the last N days that do not have a category.

Usage examples:
- Last 7 days, table output (default):
  - `python3 tools/find_tickets_missing_category.py --days 7`
- Last 14 days, CSV to file:
  - `python3 tools/find_tickets_missing_category.py --days 14 --output csv > missing.csv`
- Speed up fetch with a server‑side bound (optional):
  - `python3 tools/find_tickets_missing_category.py --days 14 --updated-since 2025-09-20T00:00:00Z`

Arguments:
- `--config PATH` – path to config YAML (default `config/config.yaml`)
- `--days N` – lookback window in days (default 7)
- `--output table|csv|json` – formatting (default table)
- `--updated-since ISO8601` – optional API filter (fetch) while client still filters by created_at

Output columns: ID, Created (UTC), Requester, Status, Priority, Subject.

---

## summarize_ticket_categories.py
Quick console summary of ticket counts per category.

Usage:
- `python3 tools/summarize_ticket_categories.py`
- Limit to tickets updated since a timestamp:
  - `python3 tools/summarize_ticket_categories.py --updated-since 2025-09-20T00:00:00Z`

Arguments:
- `--config PATH` – configuration file
- `--updated-since ISO8601` – server‑side time filter

Outputs a simple text table with counts and an Uncategorised/Total footer.

---

## list_taxonomy.py
Print the Freshservice category → subcategory → item‑category hierarchy as a dashed list.

Usage:
- `python3 tools/list_taxonomy.py`

Arguments:
- `--config PATH` – configuration file

---

## update_requesters.py
Interactively update fields on requester profiles.

Usage examples:
- Set department for a requester by ID:
  - `python3 tools/update_requesters.py --requester-id 123 --set department=IT`
- Unset a field and set a JSON array:
  - `python3 tools/update_requesters.py --email user@example.com --unset manager --set-json tags="[\"VIP\", \"Remote\"]"`

Arguments:
- `--config PATH` – configuration file
- `--requester-id ID` (repeatable) – target requester IDs
- `--email ADDRESS` (repeatable) – target requester emails
- `--set FIELD=VALUE` (repeatable) – scalar assignment with basic type coercion
- `--set-json FIELD=JSON` (repeatable) – JSON assignment for complex types
- `--unset FIELD` (repeatable) – clear a field
- `--dry-run` – preview changes without calling the API

---

## update_requester_organizations.py
Bulk update requester `organization` from a CSV export.

CSV must contain `organization` and either `requester_id` (preferred) or `email`.

Usage:
- `python3 tools/update_requester_organizations.py --csv ./data/requester_orgs.csv`
- Dry run preview:
  - `python3 tools/update_requester_organizations.py --csv ./data/requester_orgs.csv --dry-run`

Arguments:
- `--config PATH` – configuration file
- `--csv PATH` – CSV with mappings
- `--dry-run` – preview

---

## delete_tickets.py
Delete tickets by ID or from a CSV file containing a `ticket_id`/`id` column.

Usage examples:
- Ad‑hoc IDs:
  - `python3 tools/delete_tickets.py --ticket-id 111 --ticket-id 222`
- From CSV:
  - `python3 tools/delete_tickets.py --csv ./data/tickets_to_delete.csv`
- Dry run:
  - `python3 tools/delete_tickets.py --csv ./data/tickets_to_delete.csv --dry-run`

Arguments:
- `--config PATH` – configuration file
- `--ticket-id ID` (repeatable) – IDs to delete
- `--csv PATH` – CSV with a `ticket_id`/`id` column
- `--dry-run` – preview

---

## cleanup_virtualenv.py
Remove the local `.venv` and optionally purge pip caches.

Usage:
- `python3 tools/cleanup_virtualenv.py` (removes the default `.venv` in repo root)
- Custom path & dry‑run:
  - `python3 tools/cleanup_virtualenv.py --venv-path ./altvenv --dry-run`
- Purge pip cache too:
  - `python3 tools/cleanup_virtualenv.py --purge-pip-cache`

Arguments:
- `--venv-path PATH` – directory of the virtual environment (default: repo `.venv`)
- `--dry-run` – don’t delete, just log
- `--force` – remove even if currently active
- `--purge-pip-cache` – also clear `pip cache`

---

### Troubleshooting
- Ensure `python-dateutil`, `requests`, and other dependencies are installed via `requirements.txt`.
- Set `freshservice.api_key` and `freshservice.base_url` in `config/config.yaml`.
- For large data pulls, consider using the `rate_limit_per_minute` setting in your config.

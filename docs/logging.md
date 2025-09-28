# Logging Deep Dive

Robust logging is critical for audit trails and debugging during API operations. This guide explains where logs live, how to adjust verbosity, and what a typical entry looks like.

## Locations

| Component | Default Path |
|-----------|--------------|
| Python fetch/analyse/update scripts | `logs/freshservice_workflow.log` |
| Bulk update run logs | `logs/bulk_updates/bulk_update_<timestamp>.log` |
| Review helpers | `logs/review_helper.log` or `logs/review_helper_windows.log` |
| PowerShell preview scripts | `logs/powershell_*.log` |

All directories are created automatically if missing.

## Console Output

- Controlled through the `logging.console` section of the config.
- Set `rich_format: true` (default) for colourised logs when the `rich` package is available.
- Progress bars are shown by default during fetch-and-analyse runs; add `--show-console-log` (or set `logging.console.enabled: true`) to expose the original streaming log output. You can still force silence with `--disable-console-log` or `enabled: false` in the config when required.

## File Output

- File handlers capture DEBUG-level detail, including API payloads. When you run the bulk updater, an additional handler writes to the timestamped file shown above so each execution has an isolated audit trail.
- Log lines follow the pattern:

  ```text
  2024-03-12 10:15:32 | INFO | python_common.freshservice_client | Fetched 100 tickets from page 3
  ```

- Use rotation utilities such as `logrotate` or Windows Task Scheduler to archive logs periodically if the dataset is large.

## Adjusting Verbosity

1. Set `logging.console.level: DEBUG` for interactive troubleshooting.
2. Supply `--console-level DEBUG` on the CLI to override the config temporarily.
3. For PowerShell scripts, the `-QuietConsole` switch suppresses on-screen messages while retaining file logs.

## Troubleshooting Checklist

- **No logs appearing?** Ensure the process user has write permissions to the `logs/` directory.
- **Seeing SSL errors?** Confirm `freshservice.verify_ssl` is set correctly. Disabling SSL verification should only be done for debugging.
- **API rate limit warnings?** Increase `freshservice.rate_limit_per_minute` gradually; the script sleeps between pages according to this value.

## Sample Workflow

1. Run the fetcher with verbose console output:

   ```bash
   python freshservice_ticket_insights/macos/fetch_and_analyze.py --config freshservice_ticket_insights/config/config.yaml --show-console-log --console-level DEBUG
   ```

   Adjust `--config` if you store the YAML outside `freshservice_ticket_insights/config/`.

2. Open the log file while the script runs:

   ```bash
   tail -f logs/freshservice_workflow.log
   ```

3. Look for `HTTP GET` entries to verify pagination and `Updating ticket` entries during updates.
4. After a bulk run, inspect the timestamped bulk log in `logs/bulk_updates/` alongside the skip log (default `reports/updated_tickets.log`) to confirm which ticket IDs were marked as successfully updated and why any were skipped.

## Integrating with Observability Platforms

Because logs are plain text, they can be ingested into Splunk, ELK, Datadog, or similar platforms. Configure your forwarder to watch the `logs/` directory and tag entries with `source=FreshserviceTicketInsights` for easy filtering.

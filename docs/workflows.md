# Workflow Cookbook

This cookbook walks through practical end-to-end scenarios using the toolkit.

## Scenario 1 – Full Historical Analysis

1. Ensure the configuration file contains your production API key.
2. Run the fetcher without the `--updated-since` flag to retrieve all historical tickets.
3. Review the generated CSV and pay close attention to the `suggested_new_category_pattern` column; recurring patterns without existing categories can inform taxonomy changes.
4. Share the review worksheet with senior leadership for sign-off.
5. Apply updates in batches (e.g., 100 tickets at a time) by duplicating the CSV and filtering rows. Each successful run records ticket IDs in the skip log so reruns can skip already-processed work; edit or delete that log if you need to replay specific tickets.

## Scenario 2 – Weekly Incremental Update

1. Determine the last successful run timestamp (available in the logs or your reporting system).
2. Run the fetcher with `--updated-since 2024-02-01T00:00:00Z` (replace with your timestamp).
3. Focus on recently created/updated tickets to maintain taxonomy alignment.
4. Use the review helper to produce a CSV containing only `approve` decisions for quick follow-up.

## Scenario 3 – Targeted Remediation

1. Identify a problematic ticket (e.g., escalations misclassified as "General").
2. Apply a single update:

   ```bash
   python freshservice_ticket_insights/macos/apply_updates.py --config freshservice_ticket_insights/config/config.yaml --ticket-id 98765 --category "Security" --dry-run
   ```

   Update the `--config` argument if your configuration file lives outside
   `freshservice_ticket_insights/config/`.

3. Confirm the proposed path, remove `--dry-run`, and rerun when ready. The updater will skip the ticket automatically if Freshservice already reflects the requested taxonomy.

## Scenario 4 – PowerShell Pilot

1. Install PowerShell 7.2+ and the `powershell-yaml` module.
2. Run `powershell/FetchAndAnalyze.ps1` against a sandbox domain.
3. Compare CSV output against the Python version to confirm parity.
4. Provide feedback in the project notes.

## Scenario 5 – Executive Reporting Bundle

1. Ensure `reporting_suite.output_directory` points at a shared location (for example,
   `reports/advanced`).
2. Run the advanced reporter for the desired window:

   ```bash
   python freshservice_ticket_insights/macos/generate_reports.py \
     --config freshservice_ticket_insights/config/config.yaml \
     --start-date 2024-01-01T00:00:00Z \
     --end-date 2024-03-31T23:59:59Z
   ```

3. Share `report.html` with stakeholders for an interactive overview and circulate the matching
   `report.pdf` during leadership meetings.
4. Embed the generated PNG charts into slide decks or Confluence pages; the JSON metrics can feed
   downstream BI tooling if deeper analysis is needed.

## Tips for Collaboration

- Store reviewed CSVs in a shared SharePoint or Google Drive directory.
- Encourage reviewers to populate the `review_notes` column for context.
- Combine the report with BI tools (Power BI, Tableau) by importing the CSV and building dashboards around `suggested_new_category_pattern` frequency.

## Automation Ideas

- Schedule the Python scripts using `launchd` (macOS) or Task Scheduler (Windows) with secure credential storage.
- Push log files into your SIEM to monitor for failed API calls or unusual activity.
- Extend `TicketReportWriter` to emit JSON for direct ingestion into reporting pipelines.

Happy workflowing!

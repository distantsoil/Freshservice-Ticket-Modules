# PowerShell (In Development) Tooling

> **Status:** In development – provided for testing and feedback. See the main README for the fully supported Python workflows.

These scripts replicate the Freshservice ticket analysis workflow in PowerShell for teams that prefer native tooling. Because of historical reliability issues with PowerShell and the Freshservice API, this edition is offered as an experimental preview. Please validate against a sandbox instance before running against production data.

## Scripts

- `FetchAndAnalyze.ps1` – Retrieves ticket data, performs a keyword-based category suggestion, and exports a CSV report with a companion review template.
- `ApplyUpdates.ps1` – Applies approved category updates either from a review CSV or targeted ticket identifiers.

## Prerequisites

- PowerShell 7.2 or later is recommended.
- Module [`powershell-yaml`](https://www.powershellgallery.com/packages/powershell-yaml/) is required when the shared configuration file is in YAML format.
- Network connectivity to the Freshservice endpoint and valid API credentials stored in `../config/config.yaml` (this path resolves to `freshservice_ticket_insights/config/config.yaml` from the PowerShell folder, but you can point `-ConfigPath` at any alternate location).

## Usage Examples

```powershell
# Fetch tickets and create reports
./FetchAndAnalyze.ps1 -ConfigPath ../config/config.yaml -OutputDirectory ../reports

# Apply a single update
./ApplyUpdates.ps1 -ConfigPath ../config/config.yaml -TicketId 12345 -Category "Hardware"

# Bulk apply after review
./ApplyUpdates.ps1 -ConfigPath ../config/config.yaml -ReviewCsv ../reports/ticket_analysis_review.csv
```

## Logging

Both scripts log verbose information to the console (can be silenced with `-QuietConsole`) and to rotating log files under `../logs/`.

## Feedback

Please document any issues or ideas for improvement in the shared project notes so the Python reference implementation can inform the PowerShell roadmap.

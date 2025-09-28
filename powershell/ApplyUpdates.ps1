<#!
.SYNOPSIS
    Experimental PowerShell workflow for applying Freshservice ticket updates.
.DESCRIPTION
    Reads the reviewed CSV produced by the analysis workflow and applies approved category updates.
    The script is classified as "in development" and should be validated in a test workspace before
    bulk use. Requires powershell-yaml if configuration is stored as YAML.
.EXAMPLE
    ./ApplyUpdates.ps1 -ConfigPath ../config/config.yaml -ReviewCsv ../reports/ticket_analysis_review.csv
!>
param(
    [string]$ConfigPath = "../config/config.yaml",
    [string]$ReviewCsv,
    [int[]]$TicketId,
    [string]$Category,
    [string]$SubCategory,
    [string]$ItemCategory,
    [switch]$QuietConsole
)

$ErrorActionPreference = "Stop"
$script:BaseDirectory = (Resolve-Path (Join-Path $PSScriptRoot ".."))
$script:LogFile = Join-Path $BaseDirectory "logs/powershell_apply_updates.log"
New-Item -ItemType Directory -Force -Path (Split-Path $LogFile) | Out-Null

function Write-Log {
    param(
        [string]$Message,
        [ValidateSet("DEBUG", "INFO", "WARN", "ERROR")] [string]$Level = "INFO"
    )
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "$timestamp | $Level | $Message"
    if (-not $QuietConsole) { Write-Host $line }
    Add-Content -Path $LogFile -Value $line
}

function Load-FreshserviceConfig {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        throw "Configuration file not found at $Path"
    }
    $raw = Get-Content -Path $Path -Raw
    if ($Path.EndsWith(".json")) {
        return $raw | ConvertFrom-Json
    }
    if (-not (Get-Module -ListAvailable -Name powershell-yaml)) {
        Write-Log "powershell-yaml module is required for YAML configuration. Attempting to import." "WARN"
        Import-Module powershell-yaml -ErrorAction Stop
    }
    return ConvertFrom-Yaml -Yaml $raw
}

function Get-BasicAuthHeader {
    param([string]$ApiKey)
    $pair = "$ApiKey:X"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($pair))
    return @{ Authorization = "Basic $encoded"; "Content-Type" = "application/json" }
}

function Invoke-FreshserviceUpdate {
    param(
        [string]$BaseUrl,
        [hashtable]$Headers,
        [int]$TicketId,
        [hashtable]$Payload
    )
    $uriBuilder = [System.UriBuilder]::new($BaseUrl)
    $uriBuilder.Path = ("/api/v2/tickets/$TicketId").TrimStart('/')
    $uri = $uriBuilder.Uri.AbsoluteUri
    $body = $Payload | ConvertTo-Json -Depth 5
    Write-Log "PUT $uri $body" "DEBUG"
    return Invoke-RestMethod -Method Put -Uri $uri -Headers $Headers -Body $body -ContentType "application/json"
}

function Load-ReviewRows {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        throw "Review CSV not found at $Path"
    }
    $rows = Import-Csv -Path $Path
    return $rows | Where-Object { $_.manager_decision -eq "approve" }
}

try {
    Write-Log "Starting PowerShell Freshservice update workflow"
    $config = Load-FreshserviceConfig -Path (Join-Path $BaseDirectory $ConfigPath)
    $fsConfig = $config.freshservice
    $baseUrl = if ($fsConfig.endpoint_url) { $fsConfig.endpoint_url } else { $fsConfig.base_url }
    $headers = Get-BasicAuthHeader -ApiKey $fsConfig.api_key

    $targets = @()
    if ($TicketId) {
        foreach ($id in $TicketId) {
            $payload = @{ ticket = @{} }
            if ($Category) { $payload.ticket.category = $Category }
            if ($SubCategory) { $payload.ticket.sub_category = $SubCategory }
            if ($ItemCategory) { $payload.ticket.item_category = $ItemCategory }
            if ($payload.ticket.Count -eq 0) {
                throw "When using -TicketId you must also provide at least one of -Category, -SubCategory, or -ItemCategory"
            }
            $targets += @{ TicketId = $id; Payload = $payload }
        }
    }
    elseif ($ReviewCsv) {
        $rows = Load-ReviewRows -Path (Join-Path $BaseDirectory $ReviewCsv)
        Write-Log "Loaded $($rows.Count) approved rows from review CSV" "INFO"
        foreach ($row in $rows) {
            $payload = @{ ticket = @{} }
            if ($row.final_category) { $payload.ticket.category = $row.final_category }
            if ($row.final_sub_category) { $payload.ticket.sub_category = $row.final_sub_category }
            if ($row.final_item_category) { $payload.ticket.item_category = $row.final_item_category }
            if ($payload.ticket.Count -eq 0) { continue }
            $targets += @{ TicketId = [int]$row.ticket_id; Payload = $payload }
        }
    }
    else {
        throw "Provide either -TicketId for targeted updates or -ReviewCsv for bulk updates."
    }

    $success = 0
    foreach ($target in $targets) {
        $response = Invoke-FreshserviceUpdate -BaseUrl $baseUrl -Headers $headers -TicketId $target.TicketId -Payload $target.Payload
        if ($response.ticket) {
            $success += 1
            Write-Log "Updated ticket $($target.TicketId)" "INFO"
        }
    }
    Write-Log "Completed updates for $success tickets" "INFO"
}
catch {
    Write-Log "Error: $($_.Exception.Message)" "ERROR"
    throw
}

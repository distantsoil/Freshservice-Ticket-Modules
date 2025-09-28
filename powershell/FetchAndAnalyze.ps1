<#!
.SYNOPSIS
    Experimental PowerShell workflow for fetching and analysing Freshservice tickets.
.DESCRIPTION
    This script mirrors the Python tooling but is classified as "in development". It uses
    Invoke-RestMethod to call the Freshservice API, applies a simplified keyword analysis, and
    exports a CSV plus review template. Logging is written to both the console and a log file.
    Requires the powershell-yaml module when the configuration is stored as YAML.
.EXAMPLE
    ./FetchAndAnalyze.ps1 -ConfigPath ../config/config.yaml -OutputDirectory ../reports
!>
param(
    [string]$ConfigPath = "../config/config.yaml",
    [string]$OutputDirectory = "../reports",
    [string]$ReportName = "ticket_analysis_powershell.csv",
    [string]$UpdatedSince,
    [switch]$QuietConsole,
    [switch]$SkipReviewTemplate
)

$ErrorActionPreference = "Stop"
$script:BaseDirectory = (Resolve-Path (Join-Path $PSScriptRoot ".."))
$script:LogFile = Join-Path $BaseDirectory "logs/powershell_fetch_and_analyze.log"
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

function Invoke-FreshserviceRequest {
    param(
        [string]$BaseUrl,
        [hashtable]$Headers,
        [string]$Method,
        [string]$Path,
        [hashtable]$Query
    )
    $uriBuilder = [System.UriBuilder]::new($BaseUrl)
    $uriBuilder.Path = ($Path.TrimStart('/'))
    if ($Query) {
        $pairs = @()
        foreach ($key in $Query.Keys) {
            $encodedKey = [System.Uri]::EscapeDataString([string]$key)
            $encodedValue = [System.Uri]::EscapeDataString([string]$Query[$key])
            $pairs += "$encodedKey=$encodedValue"
        }
        $uriBuilder.Query = [string]::Join('&', $pairs)
    }
    $uri = $uriBuilder.Uri.AbsoluteUri
    Write-Log "HTTP $Method $uri" "DEBUG"
    return Invoke-RestMethod -Method $Method -Uri $uri -Headers $Headers -TimeoutSec 60
}

function Get-FreshserviceTicketFields {
    param([string]$BaseUrl, [hashtable]$Headers)
    $result = Invoke-FreshserviceRequest -BaseUrl $BaseUrl -Headers $Headers -Method Get -Path "/api/v2/ticket_form_fields"

    if ($null -ne $result.ticket_form_fields) {
        return @($result.ticket_form_fields)
    }
    if ($null -ne $result.ticket_fields) {
        return @($result.ticket_fields)
    }
    if ($null -ne $result.fields) {
        return @($result.fields)
    }
    if ($result -is [System.Collections.IEnumerable]) {
        return @($result)
    }
    return @()
}

function Get-FreshserviceTickets {
    param([string]$BaseUrl, [hashtable]$Headers, [string]$UpdatedSince)
    $page = 1
    $tickets = @()
    do {
        $query = @{ per_page = 100; page = $page }
        if ($UpdatedSince) { $query.updated_since = $UpdatedSince }
        $result = Invoke-FreshserviceRequest -BaseUrl $BaseUrl -Headers $Headers -Method Get -Path "/api/v2/tickets" -Query $query
        $pageTickets = @()
        if ($null -ne $result.tickets) {
            $pageTickets = @($result.tickets)
        }
        if ($pageTickets.Count -gt 0) {
            $tickets += $pageTickets
            Write-Log "Fetched $($pageTickets.Count) tickets from page $page" "INFO"
        }
        $page += 1
    } while ($pageTickets.Count -eq 100)
    return $tickets
}

function Get-ChoiceEntries {
    param(
        $Node,
        [switch]$FlattenNested
    )

    $entries = New-Object 'System.Collections.Generic.List[pscustomobject]'
    $seen = New-Object 'System.Collections.Generic.HashSet[string]'

    function Get-ChoiceLabel {
        param($Mapping)
        foreach ($key in @('label', 'name', 'title', 'value')) {
            if ($Mapping.ContainsKey($key) -and $Mapping[$key]) {
                $text = [string]$Mapping[$key]
                if (-not [string]::IsNullOrWhiteSpace($text)) { return $text }
            }
        }
        return $null
    }

    function Get-ChoiceValue {
        param($Mapping)
        foreach ($key in @('value', 'id', 'key')) {
            if ($Mapping.ContainsKey($key) -and $Mapping[$key] -ne $null) {
                return [string]$Mapping[$key]
            }
        }
        return $null
    }

    $collect = $null
    $collect = {
        param($current, $allowNested)
        if ($null -eq $current) { return }
        if ($current -is [System.Collections.IDictionary] -or $current -is [System.Management.Automation.PSCustomObject]) {
            if ($current -is [System.Management.Automation.PSCustomObject]) {
                $mapping = @{}
                foreach ($prop in $current.PSObject.Properties) { $mapping[$prop.Name] = $prop.Value }
            }
            else {
                $mapping = $current
            }
            $label = Get-ChoiceLabel $mapping
            $value = Get-ChoiceValue $mapping
            if ($label -and $seen.Add($label)) {
                $entries.Add([PSCustomObject]@{ Label = $label; Value = $value }) | Out-Null
            }
            if ($allowNested) {
                foreach ($child in $mapping.Values) {
                    if ($child -is [System.Collections.IDictionary] -or ($child -is [System.Collections.IEnumerable] -and -not ($child -is [string]))) {
                        & $collect $child $true
                    }
                }
            }
        }
        elseif ($current -is [System.Collections.IEnumerable] -and -not ($current -is [string])) {
            foreach ($item in $current) {
                & $collect $item $allowNested
            }
        }
        else {
            $text = [string]$current
            if (-not [string]::IsNullOrWhiteSpace($text) -and $seen.Add($text)) {
                $entries.Add([PSCustomObject]@{ Label = $text; Value = $null }) | Out-Null
            }
        }
    }

    & $collect $Node $FlattenNested.IsPresent
    return $entries.ToArray()
}

function Build-Taxonomy {
    param($Fields)

    $categoryEntries = @()
    $categorySeen = New-Object 'System.Collections.Generic.HashSet[string]'
    $categoryValueMap = @{}
    $categoryLabelMap = @{}

    $rawSubCategories = @{}
    $rawItemCategories = @{}
    $subcategoryValueMap = @{}
    $subcategoryLabelMap = @{}

    foreach ($field in $Fields) {
        $root = if ($field.choices) { $field.choices } elseif ($field.nested_options) { $field.nested_options } else { $null }
        switch ($field.name) {
            "category" {
                $entries = if ($root) { Get-ChoiceEntries -Node $root } else { @() }
                foreach ($entry in $entries) {
                    if ($categorySeen.Add($entry.Label)) {
                        $categoryEntries += ,([PSCustomObject]@{ Label = $entry.Label; Tokens = (Get-LabelTokens -Text $entry.Label) })
                    }
                    if ($entry.Value) { $categoryValueMap[$entry.Value] = $entry.Label }
                    $categoryLabelMap[$entry.Label.ToLower()] = $entry.Label
                }
            }
            "sub_category" {
                if ($null -eq $root) { break }
                if ($root -is [System.Collections.IDictionary]) {
                    foreach ($key in $root.Keys) {
                        $rawSubCategories[[string]$key] = Get-ChoiceEntries -Node $root[$key] -FlattenNested
                    }
                }
                else {
                    $rawSubCategories[""] = Get-ChoiceEntries -Node $root -FlattenNested
                }
            }
            "item_category" {
                if ($null -eq $root) { break }
                if ($root -is [System.Collections.IDictionary]) {
                    foreach ($categoryKey in $root.Keys) {
                        $subBlock = $root[$categoryKey]
                        if ($subBlock -is [System.Collections.IDictionary]) {
                            foreach ($subKey in $subBlock.Keys) {
                                $tupleKey = "{0}||{1}" -f [string]$categoryKey, [string]$subKey
                                $rawItemCategories[$tupleKey] = Get-ChoiceEntries -Node $subBlock[$subKey] -FlattenNested
                            }
                        }
                        else {
                            $tupleKey = "||{0}" -f [string]$categoryKey
                            $rawItemCategories[$tupleKey] = Get-ChoiceEntries -Node $subBlock -FlattenNested
                        }
                    }
                }
                else {
                    $rawItemCategories["||"] = Get-ChoiceEntries -Node $root -FlattenNested
                }
            }
        }
    }

    $subcategoryMap = @{}
    foreach ($parentKey in $rawSubCategories.Keys) {
        $entries = $rawSubCategories[$parentKey]
        if (-not $entries -or $entries.Count -eq 0) { continue }
        $resolvedParent = $null
        if ($parentKey) {
            if ($categoryValueMap.ContainsKey($parentKey)) { $resolvedParent = $categoryValueMap[$parentKey] }
            elseif ($categoryLabelMap.ContainsKey($parentKey.ToLower())) { $resolvedParent = $categoryLabelMap[$parentKey.ToLower()] }
            else { $resolvedParent = $parentKey }
        }
        $bucketKey = if ($resolvedParent) { $resolvedParent } elseif ($parentKey) { $parentKey } else { "" }
        if (-not $subcategoryMap.ContainsKey($bucketKey)) { $subcategoryMap[$bucketKey] = @() }
        foreach ($entry in $entries) {
            if (-not ($subcategoryMap[$bucketKey] | Where-Object { $_.Label -eq $entry.Label })) {
                $subcategoryMap[$bucketKey] += ,([PSCustomObject]@{ Label = $entry.Label; Tokens = (Get-LabelTokens -Text $entry.Label) })
            }
            if ($entry.Value) {
                $subcategoryValueMap[("{0}||{1}" -f $parentKey, $entry.Value)] = $entry.Label
                $subcategoryValueMap[("||{0}" -f $entry.Value)] = $entry.Label
            }
            $subcategoryLabelMap[$entry.Label.ToLower()] = $entry.Label
        }
    }

    $itemMap = @{}
    foreach ($rawKey in $rawItemCategories.Keys) {
        $entries = $rawItemCategories[$rawKey]
        if (-not $entries -or $entries.Count -eq 0) { continue }
        $parts = $rawKey -split '\|\|', 2
        $categoryParent = if ($parts.Length -gt 0) { $parts[0] } else { "" }
        $subParent = if ($parts.Length -gt 1) { $parts[1] } else { "" }

        $resolvedCategory = $null
        if ($categoryParent) {
            if ($categoryValueMap.ContainsKey($categoryParent)) { $resolvedCategory = $categoryValueMap[$categoryParent] }
            elseif ($categoryLabelMap.ContainsKey($categoryParent.ToLower())) { $resolvedCategory = $categoryLabelMap[$categoryParent.ToLower()] }
            else { $resolvedCategory = $categoryParent }
        }
        $resolvedSub = ""
        if ($subParent) {
            $lookupKey = "{0}||{1}" -f $categoryParent, $subParent
            if ($subcategoryValueMap.ContainsKey($lookupKey)) { $resolvedSub = $subcategoryValueMap[$lookupKey] }
            elseif ($subcategoryValueMap.ContainsKey("||{0}" -f $subParent)) { $resolvedSub = $subcategoryValueMap[("||{0}" -f $subParent)] }
            elseif ($subcategoryLabelMap.ContainsKey($subParent.ToLower())) { $resolvedSub = $subcategoryLabelMap[$subParent.ToLower()] }
            else { $resolvedSub = $subParent }
        }
        elseif ($categoryParent -and -not $resolvedCategory) {
            $lookupKey = "||{0}" -f $categoryParent
            if ($subcategoryValueMap.ContainsKey($lookupKey)) { $resolvedSub = $subcategoryValueMap[$lookupKey] }
            elseif ($subcategoryLabelMap.ContainsKey($categoryParent.ToLower())) { $resolvedSub = $subcategoryLabelMap[$categoryParent.ToLower()] }
            else { $resolvedSub = $categoryParent }
            $resolvedCategory = $null
        }
        $bucketKey = "{0}||{1}" -f ($resolvedCategory ?? ""), $resolvedSub
        if (-not $itemMap.ContainsKey($bucketKey)) { $itemMap[$bucketKey] = @() }
        foreach ($entry in $entries) {
            if (-not ($itemMap[$bucketKey] | Where-Object { $_.Label -eq $entry.Label })) {
                $itemMap[$bucketKey] += ,([PSCustomObject]@{ Label = $entry.Label; Tokens = (Get-LabelTokens -Text $entry.Label) })
            }
        }
    }

    return [PSCustomObject]@{
        Categories     = $categoryEntries
        SubCategories  = $subcategoryMap
        ItemCategories = $itemMap
    }
}

function Get-LabelTokens {
    param([string]$Text)
    if (-not $Text) { return @() }
    return (($Text.ToLower() -split "[^a-z0-9_]+") | Where-Object { $_ })
}

function Analyse-Tickets {
    param(
        $Tickets,
        $Taxonomy
    )
    $stopWords = @("the", "and", "with", "from", "this", "that", "have", "into")
    $patternFrequency = @{}
    $analysis = @()

    foreach ($ticket in $Tickets) {
        $text = ("$($ticket.subject) $($ticket.description_text)").ToLower()
        $rawTokens = ($text -split "[^a-z0-9_]+").Where({ $_ })
        $rawSet = New-Object 'System.Collections.Generic.HashSet[string]'
        foreach ($token in $rawTokens) { [void]$rawSet.Add($token) }
        $tokens = $rawTokens.Where({ $_.Length -ge 4 -and ($_ -notin $stopWords) })
        foreach ($token in ($tokens | Sort-Object -Unique)) {
            if (-not $patternFrequency.ContainsKey($token)) { $patternFrequency[$token] = 0 }
            $patternFrequency[$token] += 1
        }

        $matchedCategories = @()
        foreach ($category in $Taxonomy.Categories) {
            if (Test-LabelTokens -Entry $category -TokenSet $rawSet) {
                $matchedCategories += ,$category
            }
        }

        $matchedSubCategories = @()
        foreach ($key in $Taxonomy.SubCategories.Keys) {
            $entries = $Taxonomy.SubCategories[$key]
            $resolvedCategory = if ($key) { $key } else { $null }
            foreach ($entry in $entries) {
                if (Test-LabelTokens -Entry $entry -TokenSet $rawSet) {
                    $matchedSubCategories += ,([PSCustomObject]@{ Category = $resolvedCategory; Entry = $entry })
                }
            }
        }

        $matchedItems = @()
        foreach ($pairKey in $Taxonomy.ItemCategories.Keys) {
            $entries = $Taxonomy.ItemCategories[$pairKey]
            $parts = $pairKey -split '\|\|', 2
            $categoryLabel = if ($parts.Length -gt 0 -and $parts[0]) { $parts[0] } else { $null }
            $subLabel = if ($parts.Length -gt 1 -and $parts[1]) { $parts[1] } else { $null }
            foreach ($entry in $entries) {
                if (Test-LabelTokens -Entry $entry -TokenSet $rawSet) {
                    $matchedItems += ,([PSCustomObject]@{ Category = $categoryLabel; SubCategory = $subLabel; Entry = $entry })
                }
            }
        }

        $suggestedCategory = $null
        $suggestedSubCategory = $null
        $suggestedItem = $null
        $rationales = @()

        if ($matchedItems.Count -gt 0) {
            $firstItem = $matchedItems[0]
            $suggestedCategory = $firstItem.Category
            $suggestedSubCategory = $firstItem.SubCategory
            $suggestedItem = $firstItem.Entry.Label
            $rationales += "Matched label tokens '$(($firstItem.Entry.Tokens -join ' '))'"
        }

        if (-not $suggestedSubCategory -and $matchedSubCategories.Count -gt 0) {
            $firstSub = $matchedSubCategories[0]
            if (-not $suggestedCategory -and $firstSub.Category) { $suggestedCategory = $firstSub.Category }
            $suggestedSubCategory = $firstSub.Entry.Label
            $rationales += "Matched label tokens '$(($firstSub.Entry.Tokens -join ' '))'"
        }

        if (-not $suggestedCategory -and $matchedCategories.Count -gt 0) {
            $firstCategory = $matchedCategories[0]
            $suggestedCategory = $firstCategory.Label
            $rationales += "Matched label tokens '$(($firstCategory.Tokens -join ' '))'"
        }

        $confidence = if ($rationales.Count -gt 0) { 0.7 } else { "" }

        $analysis += [PSCustomObject]@{
            ticket_id                         = $ticket.id
            subject                           = $ticket.subject
            description_text                  = $ticket.description_text
            current_category                  = $ticket.category
            current_sub_category              = $ticket.sub_category
            current_item_category             = $ticket.item_category
            suggested_category                = $suggestedCategory
            suggested_sub_category            = $suggestedSubCategory
            suggested_item_category           = $suggestedItem
            suggestion_confidence             = $confidence
            suggestion_rationale              = ($rationales -join '; ')
            suggested_new_category_pattern    = ""
            suggested_new_category_frequency  = 0
        }
    }

    foreach ($row in $analysis) {
        $keywords = ($row.subject + " " + $row.description_text).ToLower()
        foreach ($pattern in $patternFrequency.Keys) {
            if ($keywords -like "*${pattern}*") {
                $row.suggested_new_category_pattern = $pattern
                $row.suggested_new_category_frequency = $patternFrequency[$pattern]
                break
            }
        }
    }

    return @{ Rows = $analysis; Frequency = $patternFrequency }
}

function Test-LabelTokens {
    param(
        $Entry,
        $TokenSet
    )
    if (-not $Entry -or -not $Entry.Tokens -or $Entry.Tokens.Count -eq 0) { return $false }
    foreach ($token in $Entry.Tokens) {
        if (-not $TokenSet.Contains($token)) { return $false }
    }
    return $true
}

function Write-Report {
    param($Rows, [string]$OutputDirectory, [string]$ReportName)
    $outputDir = Join-Path $BaseDirectory $OutputDirectory
    New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
    $analysisPath = Join-Path $outputDir $ReportName
    $Rows | Export-Csv -NoTypeInformation -Path $analysisPath -Encoding UTF8
    Write-Log "Analysis report written to $analysisPath" "INFO"
    return $analysisPath
}

function Write-ReviewTemplate {
    param([string]$AnalysisPath)
    $reviewPath = [System.IO.Path]::Combine([System.IO.Path]::GetDirectoryName($AnalysisPath),
        ([System.IO.Path]::GetFileNameWithoutExtension($AnalysisPath) + "_review.csv"))
    $rows = Import-Csv -Path $AnalysisPath
    foreach ($row in $rows) {
        $row | Add-Member -NotePropertyName manager_decision -NotePropertyValue "pending" -Force
        $row | Add-Member -NotePropertyName final_category -NotePropertyValue $row.suggested_category -Force
        $row | Add-Member -NotePropertyName final_sub_category -NotePropertyValue $row.suggested_sub_category -Force
        $row | Add-Member -NotePropertyName final_item_category -NotePropertyValue $row.suggested_item_category -Force
        $row | Add-Member -NotePropertyName review_notes -NotePropertyValue "" -Force
    }
    $rows | Export-Csv -NoTypeInformation -Encoding UTF8 -Path $reviewPath
    Write-Log "Review template generated at $reviewPath" "INFO"
}

if ($MyInvocation.InvocationName -ne '.') {
    try {
        Write-Log "Starting PowerShell Freshservice analysis workflow"
        $config = Load-FreshserviceConfig -Path (Join-Path $BaseDirectory $ConfigPath)
        $fsConfig = $config.freshservice
        $baseUrl = if ($fsConfig.endpoint_url) { $fsConfig.endpoint_url } else { $fsConfig.base_url }
        $headers = Get-BasicAuthHeader -ApiKey $fsConfig.api_key

        $fields = Get-FreshserviceTicketFields -BaseUrl $baseUrl -Headers $headers
        $taxonomy = Build-Taxonomy -Fields $fields
        Write-Log "Loaded $($taxonomy.Categories.Count) categories and $($taxonomy.SubCategories.Count) subcategories" "INFO"

        $tickets = Get-FreshserviceTickets -BaseUrl $baseUrl -Headers $headers -UpdatedSince $UpdatedSince
        Write-Log "Retrieved $($tickets.Count) tickets" "INFO"

        $analysis = Analyse-Tickets -Tickets $tickets -Taxonomy $taxonomy
        $analysisPath = Write-Report -Rows $analysis.Rows -OutputDirectory $OutputDirectory -ReportName $ReportName
        if (-not $SkipReviewTemplate) {
            Write-ReviewTemplate -AnalysisPath $analysisPath
        }
        Write-Log "Keyword frequency summary: $($analysis.Frequency.GetEnumerator() | Sort-Object -Property Value -Descending | Select-Object -First 10 | ForEach-Object { "$_" } | Out-String)" "DEBUG"
        Write-Log "Workflow completed successfully"
    }
    catch {
        Write-Log "Error: $($_.Exception.Message)" "ERROR"
        throw
    }
}

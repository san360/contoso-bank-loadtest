<#
.SYNOPSIS
    Compares completed Azure Load Testing runs from the terminal.

.DESCRIPTION
    Reads two or more Azure Load Testing test runs with `az load test-run show`
    and prints a side-by-side comparison of the headline client-side statistics:
    sample count, error percentage, throughput and response-time percentiles.

    The verdict is derived from passFailCriteria.passFailMetrics on each run, not
    from the aggregate testResult field. Those two settle at different times; see
    docs/07-analyzing-results-and-bottlenecks.md §7.2 and the long comment in
    tools/e2e-test.ps1.

    This script never starts a load test. It only lists and reads existing runs.

.PARAMETER RunIds
    Two or more run ids to compare. Comma-separated and array forms both work:
    -RunIds before,after.

.PARAMETER TestId
    Test definition whose recent runs should be compared. Use with -Last.

.PARAMETER Last
    Number of most recent runs to compare for -TestId. Defaults to 2.

.PARAMETER LoadTestName
    Optional Azure Load Testing resource name. Defaults to LOAD_TEST_NAME from the
    process environment or `azd env get-values`.

.PARAMETER ResourceGroup
    Optional Azure resource group. Defaults to AZURE_RESOURCE_GROUP from the
    process environment or `azd env get-values`.

.PARAMETER SubscriptionId
    Optional Azure subscription id. Defaults to AZURE_SUBSCRIPTION_ID from the
    process environment or `azd env get-values`.

.EXAMPLE
    pwsh -File tools\compare_runs.ps1 -RunIds contoso-before,contoso-after

.EXAMPLE
    pwsh -File tools\compare_runs.ps1 -TestId contoso-bank-pr-gate -Last 2
#>

[CmdletBinding(DefaultParameterSetName = 'ByRunId')]
param(
    [Parameter(Mandatory = $true, ParameterSetName = 'ByRunId')]
    [string[]]$RunIds,

    [Parameter(Mandatory = $true, ParameterSetName = 'ByLatest')]
    [string]$TestId,

    [Parameter(ParameterSetName = 'ByLatest')]
    [ValidateRange(2, 50)]
    [int]$Last = 2,

    [string]$LoadTestName,
    [string]$ResourceGroup,
    [string]$SubscriptionId
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$global:LASTEXITCODE = 0

$script:RepoRoot = Split-Path -Parent $PSScriptRoot
$script:AzdValues = $null

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host $Message -ForegroundColor Cyan
}

function Invoke-External {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [string]$WorkingDirectory = $script:RepoRoot
    )

    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $global:LASTEXITCODE = 0
    try {
        Push-Location $WorkingDirectory
        $output = & $FilePath @Arguments 2>&1
        $exitCode = $LASTEXITCODE
        return @{
            Ok       = ($exitCode -eq 0)
            ExitCode = $exitCode
            Output   = ($output | Out-String).Trim()
        }
    }
    finally {
        Pop-Location
        $ErrorActionPreference = $previousPreference
    }
}

function Assert-Tool {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "'$Name' is not on PATH. Install it before running this script."
    }
}

function Read-AzdValues {
    if ($null -ne $script:AzdValues) { return $script:AzdValues }

    $values = @{}
    if (Get-Command 'azd' -ErrorAction SilentlyContinue) {
        $result = Invoke-External 'azd' @('env', 'get-values')
        if ($result.Ok) {
            foreach ($line in ($result.Output -split "`r?`n")) {
                if ($line -match '^([^=]+)=(.*)$') {
                    $values[$matches[1]] = $matches[2].Trim().Trim('"')
                }
            }
        }
    }
    $script:AzdValues = $values
    return $values
}

function Get-EnvironmentValue {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [string]$Override,
        [switch]$Required
    )

    if (-not [string]::IsNullOrWhiteSpace($Override)) { return $Override }

    $value = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($value)) {
        $values = Read-AzdValues
        if ($values.ContainsKey($Name)) { $value = $values[$Name] }
    }

    if ($Required -and [string]::IsNullOrWhiteSpace($value)) {
        throw "Could not resolve '$Name'. Run 'azd up' first, pass it as a parameter, or export it as an environment variable."
    }
    return $value
}

function Get-ObjectPropertyValue {
    param([object]$Object, [string]$Name)

    if ($null -eq $Object) { return $null }
    foreach ($property in $Object.PSObject.Properties) {
        if ($property.Name -eq $Name) { return $property.Value }
    }
    return $null
}

function Convert-ToMetricNumber {
    param([object]$Value)

    if ($null -eq $Value) { return $null }
    if ($Value -is [double] -or $Value -is [decimal] -or $Value -is [int] -or $Value -is [long]) {
        return [double]$Value
    }

    $text = [string]$Value
    if ([string]::IsNullOrWhiteSpace($text)) { return $null }

    $number = 0.0
    $styles = [System.Globalization.NumberStyles]::Float -bor [System.Globalization.NumberStyles]::AllowThousands
    if ([double]::TryParse($text, $styles, [System.Globalization.CultureInfo]::InvariantCulture, [ref]$number)) {
        return $number
    }
    if ([double]::TryParse($text, [ref]$number)) { return $number }
    return $null
}

function Get-PassFailMetricValues {
    param([object]$Run)

    $criteria = Get-ObjectPropertyValue (Get-ObjectPropertyValue $Run 'passFailCriteria') 'passFailMetrics'
    if ($null -eq $criteria) { return @() }

    if ($criteria -is [System.Array]) { return @($criteria) }

    $properties = @($criteria.PSObject.Properties)
    if ($properties.Count -eq 0) { return @() }

    if ($properties.Name -contains 'result') { return @($criteria) }
    return @($properties | ForEach-Object { $_.Value })
}

function Get-RunVerdict {
    param([object]$Run)

    $metrics = @(Get-PassFailMetricValues -Run $Run)
    $failed = @(
        $metrics | Where-Object {
            $result = [string](Get-ObjectPropertyValue $_ 'result')
            $result.Equals('failed', [System.StringComparison]::OrdinalIgnoreCase)
        }
    )

    # Take the pessimistic reading of both signals. The per-criterion results and
    # the aggregate testResult settle together, but ~97s AFTER the run reaches a
    # terminal state (measured - see docs/07 §7.2.4). A run read during that window
    # shows criteria with no result at all, which must never be reported as a pass.
    $reported = [string](Get-ObjectPropertyValue $Run 'testResult')
    $status = [string](Get-ObjectPropertyValue $Run 'status')

    $verdict = if ($metrics.Count -eq 0) { 'NOT_APPLICABLE' }
    elseif ($status -ne 'DONE') { 'NO_VERDICT' }
    elseif ($failed.Count -gt 0 -or $reported -eq 'FAILED') { 'FAILED' }
    elseif ($reported -eq 'PASSED') { 'PASSED' }
    else { 'UNSETTLED' }

    return [pscustomobject]@{
        Verdict        = $verdict
        ReportedResult = $reported
        CriteriaCount  = $metrics.Count
        FailedCount    = $failed.Count
        Failed         = $failed
    }
}

function Get-RunTotalStatistics {
    param([object]$Run)

    $stats = Get-ObjectPropertyValue $Run 'testRunStatistics'
    $total = Get-ObjectPropertyValue $stats 'Total'

    return [pscustomobject]@{
        sampleCount = Convert-ToMetricNumber (Get-ObjectPropertyValue $total 'sampleCount')
        errorPct    = Convert-ToMetricNumber (Get-ObjectPropertyValue $total 'errorPct')
        throughput  = Convert-ToMetricNumber (Get-ObjectPropertyValue $total 'throughput')
        meanResTime = Convert-ToMetricNumber (Get-ObjectPropertyValue $total 'meanResTime')
        pct1ResTime = Convert-ToMetricNumber (Get-ObjectPropertyValue $total 'pct1ResTime')
        pct2ResTime = Convert-ToMetricNumber (Get-ObjectPropertyValue $total 'pct2ResTime')
        pct3ResTime = Convert-ToMetricNumber (Get-ObjectPropertyValue $total 'pct3ResTime')
    }
}

function Format-MetricValue {
    param([object]$Value)

    $number = Convert-ToMetricNumber $Value
    if ($null -eq $number) { return '-' }
    if ([Math]::Abs($number - [Math]::Round($number)) -lt 0.005) {
        return $number.ToString('0', [System.Globalization.CultureInfo]::InvariantCulture)
    }
    return $number.ToString('0.##', [System.Globalization.CultureInfo]::InvariantCulture)
}

function Format-Delta {
    param([object]$Baseline, [object]$Value)

    $baseNumber = Convert-ToMetricNumber $Baseline
    $valueNumber = Convert-ToMetricNumber $Value
    if ($null -eq $baseNumber -or $null -eq $valueNumber) { return 'n/a' }

    $delta = $valueNumber - $baseNumber
    $deltaText = Format-MetricValue $delta
    if ($delta -gt 0) { $deltaText = "+$deltaText" }

    if ([Math]::Abs($baseNumber) -lt 0.0000001) {
        $pctText = if ([Math]::Abs($delta) -lt 0.0000001) { '0%' } else { 'n/a' }
    }
    else {
        $pct = ($delta / [Math]::Abs($baseNumber)) * 100
        $pctText = $pct.ToString('+0.#;-0.#;0', [System.Globalization.CultureInfo]::InvariantCulture) + '%'
    }

    return "$deltaText / $pctText"
}

function Get-ColumnLabel {
    param([string]$RunId, [int]$Index)

    # Full run ids are up to 50 characters, which wraps Format-Table into an
    # unreadable mess - and ids that share a timestamp suffix are indistinguishable
    # when truncated. So columns are plain aliases and the legend above the table
    # maps each alias back to its run id.
    return "[$([char](65 + $Index))]"
}

function New-ComparisonRows {
    param([object[]]$RunSummaries)

    $metrics = @(
        @{ Name = 'sampleCount'; Label = 'sampleCount' },
        @{ Name = 'errorPct'; Label = 'errorPct' },
        @{ Name = 'throughput'; Label = 'throughput (req/s)' },
        @{ Name = 'meanResTime'; Label = 'meanResTime (ms)' },
        @{ Name = 'pct1ResTime'; Label = 'pct1ResTime / p90 (ms)' },
        @{ Name = 'pct2ResTime'; Label = 'pct2ResTime / p95 (ms)' },
        @{ Name = 'pct3ResTime'; Label = 'pct3ResTime / p99 (ms)' }
    )

    $baseline = $RunSummaries[0]
    $baselineLabel = Get-ColumnLabel -RunId $baseline.RunId -Index 0

    foreach ($metric in $metrics) {
        $row = [ordered]@{ Metric = $metric.Label }
        $baselineValue = Get-ObjectPropertyValue $baseline.Statistics $metric.Name
        $row[$baselineLabel] = Format-MetricValue $baselineValue

        for ($i = 1; $i -lt $RunSummaries.Count; $i++) {
            $run = $RunSummaries[$i]
            $label = Get-ColumnLabel -RunId $run.RunId -Index $i
            $value = Get-ObjectPropertyValue $run.Statistics $metric.Name
            $row[$label] = Format-MetricValue $value
            $row["$label delta"] = Format-Delta -Baseline $baselineValue -Value $value
        }

        [pscustomobject]$row
    }
}

function Get-RunIdentifier {
    param([object]$Run, [string]$Fallback)

    foreach ($name in @('testRunId', 'runId', 'name')) {
        $value = [string](Get-ObjectPropertyValue $Run $name)
        if (-not [string]::IsNullOrWhiteSpace($value)) { return $value }
    }
    return $Fallback
}

function Get-RunSortKey {
    param([object]$Run)

    foreach ($name in @('endDateTime', 'startDateTime', 'createdDateTime', 'lastModifiedDateTime')) {
        $value = [string](Get-ObjectPropertyValue $Run $name)
        if (-not [string]::IsNullOrWhiteSpace($value)) { return $value }
    }
    return [string](Get-ObjectPropertyValue $Run 'testRunId')
}

function Ensure-Prerequisites {
    Assert-Tool 'az'

    $account = Invoke-External 'az' @('account', 'show', '--only-show-errors', '-o', 'none')
    if (-not $account.Ok) {
        throw "Azure CLI is not logged in. Run 'az login' and retry.`n$($account.Output)"
    }

    $extension = Invoke-External 'az' @('extension', 'show', '--name', 'load', '--only-show-errors', '-o', 'none')
    if ($extension.Ok) { return }

    Write-Host "  Azure CLI 'load' extension is missing. Installing it now..."
    $install = Invoke-External 'az' @('extension', 'add', '--name', 'load', '--only-show-errors', '-o', 'none')
    if (-not $install.Ok) {
        throw "Could not install Azure CLI 'load' extension.`n$($install.Output)"
    }
}

function Invoke-LoadCli {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $cliArgs = @('load') + $Arguments + @('--load-test-resource', $script:LoadTestName, '--resource-group', $script:ResourceGroup, '--only-show-errors')
    if ($script:SubscriptionId) { $cliArgs += @('--subscription', $script:SubscriptionId) }
    return Invoke-External 'az' $cliArgs
}

function Resolve-RunIdsFromLatest {
    param([string]$TestIdToRead, [int]$Count)

    $result = Invoke-LoadCli @('test-run', 'list', '--test-id', $TestIdToRead, '-o', 'json')
    if (-not $result.Ok) {
        throw "Could not list runs for test '$TestIdToRead'.`n$($result.Output)"
    }

    $runs = @()
    if (-not [string]::IsNullOrWhiteSpace($result.Output)) {
        $runs = @($result.Output | ConvertFrom-Json)
    }
    if ($runs.Count -lt $Count) {
        throw "Test '$TestIdToRead' only has $($runs.Count) run(s); need $Count for comparison."
    }

    return @(
        $runs |
            Sort-Object -Property @{ Expression = { Get-RunSortKey $_ }; Descending = $true } |
            Select-Object -First $Count |
            ForEach-Object { Get-RunIdentifier -Run $_ -Fallback '' } |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
}

function Split-RunIdArguments {
    param([string[]]$Values)

    return @(
        foreach ($value in $Values) {
            foreach ($part in ($value -split ',')) {
                $trimmed = $part.Trim()
                if (-not [string]::IsNullOrWhiteSpace($trimmed)) { $trimmed }
            }
        }
    )
}

function Read-RunSummaries {
    param([string[]]$Ids)

    $summaries = [System.Collections.Generic.List[object]]::new()
    foreach ($id in $Ids) {
        $result = Invoke-LoadCli @('test-run', 'show', '--test-run-id', $id, '-o', 'json')
        if (-not $result.Ok) {
            throw "Could not read run '$id'.`n$($result.Output)"
        }

        $run = $result.Output | ConvertFrom-Json
        $verdict = Get-RunVerdict -Run $run
        $statistics = Get-RunTotalStatistics -Run $run
        $summaries.Add([pscustomobject]@{
                RunId      = Get-RunIdentifier -Run $run -Fallback $id
                TestId     = [string](Get-ObjectPropertyValue $run 'testId')
                Status     = [string](Get-ObjectPropertyValue $run 'status')
                Started    = [string](Get-ObjectPropertyValue $run 'startDateTime')
                Ended      = [string](Get-ObjectPropertyValue $run 'endDateTime')
                Verdict    = $verdict
                Statistics = $statistics
                Raw        = $run
            })
    }
    return @($summaries)
}

try {
    Ensure-Prerequisites

    $script:LoadTestName = Get-EnvironmentValue 'LOAD_TEST_NAME' -Override $LoadTestName -Required
    $script:ResourceGroup = Get-EnvironmentValue 'AZURE_RESOURCE_GROUP' -Override $ResourceGroup -Required
    $script:SubscriptionId = Get-EnvironmentValue 'AZURE_SUBSCRIPTION_ID' -Override $SubscriptionId

    $ids = if ($PSCmdlet.ParameterSetName -eq 'ByLatest') {
        Resolve-RunIdsFromLatest -TestIdToRead $TestId -Count $Last
    }
    else {
        Split-RunIdArguments -Values $RunIds
    }

    if ($ids.Count -lt 2) {
        throw "Provide at least two run ids, or use -TestId with -Last 2 or more."
    }

    Write-Step "Comparing $($ids.Count) run(s) in '$script:LoadTestName'"
    $summaries = Read-RunSummaries -Ids $ids
    $baseline = $summaries[0].RunId

    Write-Host "  Baseline: $baseline"
    Write-Host "  Non-baseline runs get a value column and a delta column against the baseline."

    Write-Step "Run verdicts"
    $summaries |
        ForEach-Object {
            [pscustomobject]@{
                RunId          = $_.RunId
                TestId         = $_.TestId
                Status         = $_.Status
                Verdict        = $_.Verdict.Verdict
                ReportedResult = $_.Verdict.ReportedResult
                Criteria       = "$($_.Verdict.FailedCount)/$($_.Verdict.CriteriaCount) failed"
                Started        = $_.Started
                Ended          = $_.Ended
            }
        } |
        Format-Table -AutoSize |
        Out-String |
        Write-Host

    foreach ($summary in $summaries) {
        if ($summary.Verdict.ReportedResult -and $summary.Verdict.ReportedResult -cne $summary.Verdict.Verdict) {
            Write-Host "  NOTE: $($summary.RunId) reports testResult='$($summary.Verdict.ReportedResult)' but criteria derive '$($summary.Verdict.Verdict)'." -ForegroundColor Yellow
        }
    }

    Write-Step "Client-side statistics"
    for ($i = 0; $i -lt $summaries.Count; $i++) {
        Write-Host ("  [{0}] = {1}" -f [char](65 + $i), $summaries[$i].RunId) -ForegroundColor DarkGray
    }
    New-ComparisonRows -RunSummaries $summaries |
        Format-Table -AutoSize |
        Out-String -Width 200 |
        Write-Host

    Write-Host "Read raw data with:"
    foreach ($id in $ids) {
        Write-Host "  az load test-run show --load-test-resource $script:LoadTestName --resource-group $script:ResourceGroup --test-run-id $id"
    }
    exit 0
}
catch {
    Write-Host ""
    Write-Host "Failed to compare load test runs." -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}

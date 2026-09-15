<#
.SYNOPSIS
    Starts one of the provisioned Contoso Bank load test definitions and reports the verdict.

.DESCRIPTION
    `azd up` creates the test definitions but deliberately never runs them, because
    running spends virtual user hours. This script is the explicit "run it now" step.

    It resolves the Azure Load Testing resource from the azd environment (or from
    parameters / environment variables), starts a run, polls until the run reaches a
    terminal state, and prints the status, the derived pass/fail verdict, the breached
    criteria and the headline client-side statistics.

    The verdict is derived from passFailCriteria.passFailMetrics on the run, not from
    the aggregate testResult field. Those two settle at different times — see
    docs/07-analyzing-results-and-bottlenecks.md §7.2 and the long comment in
    tools/e2e-test.ps1.

.PARAMETER TestId
    The test definition to run, e.g. contoso-bank-peak-load. Use -List to see them all.

.PARAMETER RunId
    Optional test run id. Defaults to "<testId>-yyyyMMdd-HHmmss". Run ids are unique
    per resource and become the audit record, so pick something meaningful for a demo
    (for example demo-run-before / demo-run-after).

.PARAMETER List
    List the available test definitions and exit without spending any VUH.

.PARAMETER Preset
    Optionally POST an app preset ('bottlenecked' or 'optimized') to the demo API before
    starting the run. This is the before/after demo arc in one command.

.PARAMETER NoWait
    Start the run and return immediately instead of polling to completion.

.PARAMETER TimeoutMinutes
    How long to poll before giving up on the run. Default 40.

.PARAMETER DownloadResults
    Download the results (CSV/JTL) and the JMeter HTML report once the run finishes.

.PARAMETER OutputDirectory
    Where -DownloadResults writes to. Defaults to .loadtest-results/<runId>.

.EXAMPLE
    pwsh -File tools/run_loadtest.ps1 -List

.EXAMPLE
    pwsh -File tools/run_loadtest.ps1 -TestId contoso-bank-url-basic

.EXAMPLE
    pwsh -File tools/run_loadtest.ps1 -TestId contoso-bank-pr-gate -Preset bottlenecked -RunId demo-run-before -DownloadResults

.NOTES
    Exit codes: 0 = run completed and no criterion failed (or -NoWait / -List),
                1 = run failed, was cancelled, breached a criterion, or the script errored,
                2 = the run did not reach a terminal state before -TimeoutMinutes.
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)][string]$TestId,
    [string]$RunId,
    [switch]$List,
    [ValidateSet('bottlenecked', 'optimized')][string]$Preset,
    [switch]$NoWait,
    [int]$TimeoutMinutes = 40,
    [switch]$DownloadResults,
    [string]$OutputDirectory,
    [string]$LoadTestName,
    [string]$ResourceGroup,
    [string]$SubscriptionId
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$global:LASTEXITCODE = 0

$script:RepoRoot = Split-Path -Parent $PSScriptRoot
$script:AzdValues = $null
$script:MaxRunIdLength = 50

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

function Get-LocalTestCatalog {
    $configDir = Join-Path $script:RepoRoot 'loadtests/config'
    if (-not (Test-Path -LiteralPath $configDir)) { return @() }

    $catalog = foreach ($file in (Get-ChildItem -LiteralPath $configDir -File -Filter '*.yaml' | Sort-Object Name)) {
        $text = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8
        $id = [regex]::Match($text, '(?m)^\s*testId:\s*["'']?([^"''\s#]+)').Groups[1].Value
        if ([string]::IsNullOrWhiteSpace($id)) { continue }

        [pscustomobject]@{
            TestId          = $id
            DisplayName     = [regex]::Match($text, '(?m)^\s*displayName:\s*["'']?([^"''#]+)').Groups[1].Value.Trim()
            TestType        = [regex]::Match($text, '(?m)^\s*testType:\s*["'']?([^"''\s#]+)').Groups[1].Value
            EngineInstances = [regex]::Match($text, '(?m)^\s*engineInstances:\s*["'']?([0-9]+)').Groups[1].Value
            Config          = "loadtests/config/$($file.Name)"
        }
    }
    return @($catalog)
}

function Show-TestCatalog {
    Write-Step "Test definitions in '$script:LoadTestName'"

    $remote = Invoke-LoadCli @('test', 'list', '-o', 'json')
    $remoteIds = @()
    if ($remote.Ok -and -not [string]::IsNullOrWhiteSpace($remote.Output)) {
        $remoteIds = @(($remote.Output | ConvertFrom-Json) | ForEach-Object { Get-ObjectPropertyValue $_ 'testId' })
    }
    else {
        Write-Warning "Could not list tests from Azure; showing the definitions in loadtests/config only."
    }

    $local = Get-LocalTestCatalog
    $localIds = @($local | ForEach-Object { $_.TestId })
    $rows = [System.Collections.Generic.List[object]]::new()

    foreach ($item in $local) {
        $deployed = if ($remoteIds -contains $item.TestId) { 'yes' } else { 'no' }
        $rows.Add([pscustomobject]@{
                TestId   = $item.TestId
                Type     = $item.TestType
                Engines  = $item.EngineInstances
                Deployed = $deployed
                Config   = $item.Config
            })
    }

    foreach ($id in $remoteIds) {
        if ([string]::IsNullOrWhiteSpace($id) -or ($localIds -contains $id)) { continue }
        $rows.Add([pscustomobject]@{ TestId = $id; Type = '-'; Engines = '-'; Deployed = 'yes'; Config = '(not in this repo)' })
    }

    $rows | Format-Table -AutoSize | Out-String | Write-Host

    Write-Host "Run one with:  pwsh -File tools/run_loadtest.ps1 -TestId <testId>"
    Write-Host "Anything marked Deployed=no needs 'pwsh -File tools/provision_loadtests.ps1' first."
}

function Set-AppPreset {
    param([Parameter(Mandatory = $true)][string]$PresetName)

    $apiUri = Get-EnvironmentValue 'SERVICE_API_URI'
    if ([string]::IsNullOrWhiteSpace($apiUri)) {
        $apiHost = Get-EnvironmentValue 'SERVICE_API_HOSTNAME'
        if ([string]::IsNullOrWhiteSpace($apiHost)) {
            throw "-Preset needs SERVICE_API_URI or SERVICE_API_HOSTNAME from the azd environment."
        }
        $apiUri = "https://$apiHost"
    }

    Write-Step "Switching the demo app to the '$PresetName' preset"
    $response = Invoke-RestMethod -Method Post -Uri "$($apiUri.TrimEnd('/'))/admin/config" `
        -ContentType 'application/json' -Body (@{ preset = $PresetName } | ConvertTo-Json) -TimeoutSec 30
    $applied = Get-ObjectPropertyValue $response 'applied'
    $settingCount = if ($applied) { @($applied.PSObject.Properties).Count } else { 0 }
    Write-Host "  [OK] $apiUri now runs the '$PresetName' preset ($settingCount setting(s) applied)."

    # App Service needs a moment before the new config is reflected on every worker.
    Start-Sleep -Seconds 5
}

function New-DefaultRunId {
    param([Parameter(Mandatory = $true)][string]$TestId)

    # Azure Load Testing caps testRunId at 50 characters, so long test ids have to
    # give way to the timestamp - the timestamp is what makes the id unique.
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $budget = $script:MaxRunIdLength - ($stamp.Length + 1)
    $prefix = if ($TestId.Length -gt $budget) { $TestId.Substring(0, $budget) } else { $TestId }
    return "$($prefix.TrimEnd('-'))-$stamp"
}

function Wait-TestRun {
    <#
      Waits for the run to reach a terminal state AND for its verdict to settle.

      MEASURED, not assumed. On this subscription, at the moment `status` first
      flips to DONE the run reports testResult = NOT_APPLICABLE *and* every entry
      in passFailCriteria.passFailMetrics still reads result = "passed". The
      criteria only settle to their real values some time later.

      Evidence - run contoso-bank-pr-gate-runall-20260915-102506 was read as
      0/4 criteria failed at first DONE, and minutes later as:
        failed  avg(response_time_ms) > 500   actual 2725
        failed  p95(response_time_ms) > 1500  actual 17900
        failed  p95(response_time_ms) > 800   actual 870
      Same for contoso-bank-url-smoke and contoso-bank-secrets-kv in the same batch.

      So reading the criteria at first DONE produces a FALSE GREEN - the single
      most dangerous outcome for a performance gate. We therefore keep polling
      after DONE until the verdict settles:

        criteria exist  -> wait until testResult is PASSED or FAILED, then the
                           per-criterion results can be trusted
        no criteria     -> testResult stays NOT_APPLICABLE for ever, so settle
                           immediately (this is the real false green of docs/07 7.2)

      If the verdict has not settled within -SettleTimeoutSeconds we return the
      run marked unsettled, and the caller fails rather than reporting green.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$RunIdToWatch,
        [int]$TimeoutMinutes,
        [int]$SettleTimeoutSeconds = 300
    )

    $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
    $terminal = @('DONE', 'FAILED', 'CANCELLED')
    $lastStatus = ''

    while ((Get-Date) -lt $deadline) {
        $result = Invoke-LoadCli @('test-run', 'show', '--test-run-id', $RunIdToWatch, '-o', 'json')
        if ($result.Ok -and -not [string]::IsNullOrWhiteSpace($result.Output)) {
            $run = $result.Output | ConvertFrom-Json
            $status = [string](Get-ObjectPropertyValue $run 'status')

            if ($terminal -contains $status) {
                return (Wait-RunVerdictSettled -Run $run -RunIdToWatch $RunIdToWatch -SettleTimeoutSeconds $SettleTimeoutSeconds)
            }

            if ($status -ne $lastStatus) {
                Write-Host "  $status..." -ForegroundColor DarkGray
                $lastStatus = $status
            }
        }
        Start-Sleep -Seconds 15
    }

    return $null
}

function Get-CriteriaMetrics {
    param([object]$Run)

    $criteria = Get-ObjectPropertyValue (Get-ObjectPropertyValue $Run 'passFailCriteria') 'passFailMetrics'
    if (-not $criteria) { return @() }
    return @($criteria.PSObject.Properties | ForEach-Object { $_.Value })
}

function Wait-RunVerdictSettled {
    param(
        [Parameter(Mandatory = $true)][object]$Run,
        [Parameter(Mandatory = $true)][string]$RunIdToWatch,
        [int]$SettleTimeoutSeconds = 300
    )

    $status = [string](Get-ObjectPropertyValue $Run 'status')
    $metrics = @(Get-CriteriaMetrics -Run $Run)

    # A criteria-less test can never resolve past NOT_APPLICABLE, and a run that
    # never completed has no verdict to wait for.
    if ($metrics.Count -eq 0 -or $status -ne 'DONE') {
        return [pscustomobject]@{ Run = $Run; Settled = $true; SettleSeconds = 0 }
    }

    $settleDeadline = (Get-Date).AddSeconds($SettleTimeoutSeconds)
    $started = Get-Date
    $announced = $false
    $current = $Run

    while ((Get-Date) -lt $settleDeadline) {
        $reported = [string](Get-ObjectPropertyValue $current 'testResult')
        if ($reported -in @('PASSED', 'FAILED')) {
            return [pscustomobject]@{
                Run           = $current
                Settled       = $true
                SettleSeconds = [math]::Round(((Get-Date) - $started).TotalSeconds)
            }
        }

        if (-not $announced) {
            Write-Host "  DONE - waiting for the pass/fail verdict to settle..." -ForegroundColor DarkGray
            $announced = $true
        }

        Start-Sleep -Seconds 10
        $result = Invoke-LoadCli @('test-run', 'show', '--test-run-id', $RunIdToWatch, '-o', 'json')
        if ($result.Ok -and -not [string]::IsNullOrWhiteSpace($result.Output)) {
            $current = $result.Output | ConvertFrom-Json
        }
    }

    return [pscustomobject]@{
        Run           = $current
        Settled       = $false
        SettleSeconds = [math]::Round(((Get-Date) - $started).TotalSeconds)
    }
}

function Get-RunVerdict {
    param([object]$Run, [bool]$Settled = $true)

    # Read BOTH signals and take the pessimistic one. The per-criterion results
    # and the aggregate testResult settle at different times (see Wait-TestRun),
    # so trusting either alone can produce a false green.
    # PowerShell unrolls an empty array returned from a function into $null, so
    # the @() wrapper here is load-bearing: without it a criteria-less run throws.
    $metrics = @(Get-CriteriaMetrics -Run $Run)
    $failed = @($metrics | Where-Object { (Get-ObjectPropertyValue $_ 'result') -eq 'failed' })
    $reported = [string](Get-ObjectPropertyValue $Run 'testResult')
    $status = [string](Get-ObjectPropertyValue $Run 'status')

    $verdict = if ($metrics.Count -eq 0) { 'NOT_APPLICABLE' }
    elseif ($status -ne 'DONE') { 'NO_VERDICT' }
    elseif (-not $Settled) { 'UNSETTLED' }
    elseif ($failed.Count -gt 0 -or $reported -eq 'FAILED') { 'FAILED' }
    elseif ($reported -eq 'PASSED') { 'PASSED' }
    else { 'UNSETTLED' }

    return [pscustomobject]@{
        Verdict        = $verdict
        ReportedResult = $reported
        CriteriaCount  = $metrics.Count
        Failed         = $failed
    }
}

function Show-RunSummary {
    param([object]$Run, [object]$Verdict)

    $status = [string](Get-ObjectPropertyValue $Run 'status')

    Write-Step "Run summary"
    Write-Host "  test id       : $script:TestId"
    Write-Host "  run id        : $script:RunId"
    Write-Host "  status        : $status"

    $colour = switch ($Verdict.Verdict) {
        'PASSED' { 'Green' }
        'FAILED' { 'Red' }
        default { 'Yellow' }
    }
    Write-Host "  verdict       : $($Verdict.Verdict)  ($($Verdict.Failed.Count)/$($Verdict.CriteriaCount) criteria failed, testResult=$($Verdict.ReportedResult))" -ForegroundColor $colour

    if ($Verdict.Verdict -eq 'NO_VERDICT') {
        Write-Host "  warning       : the run did not complete, so there is no performance conclusion." -ForegroundColor Yellow
        Write-Host "                  status=$status means the test infrastructure, script, secrets or network path failed" -ForegroundColor DarkGray
        Write-Host "                  - your test is broken, not necessarily your app. Download the engine logs:" -ForegroundColor DarkGray
        Write-Host "                  az load test-run download-files --test-run-id $script:RunId --path ./logs --log --force" -ForegroundColor DarkGray
    }
    elseif ($Verdict.Verdict -eq 'UNSETTLED') {
        Write-Host "  warning       : the verdict had not settled when this script gave up waiting." -ForegroundColor Yellow
        Write-Host "                  Re-read the run before trusting it - do NOT treat this as a pass." -ForegroundColor DarkGray
    }
    elseif ($Verdict.ReportedResult -eq 'FAILED' -and $Verdict.Failed.Count -eq 0) {
        Write-Host "  note          : testResult says FAILED while every criterion still reads 'passed'." -ForegroundColor Yellow
        Write-Host "                  The per-criterion results lag; the failing verdict is taken as authoritative." -ForegroundColor DarkGray
    }

    if ($Verdict.CriteriaCount -eq 0) {
        Write-Host "  warning       : this test defines no failure criteria, so it can never fail." -ForegroundColor Yellow
        Write-Host "                  A green run here proves nothing - see docs/07 section 7.2." -ForegroundColor DarkGray
    }

    foreach ($criterion in $Verdict.Failed) {
        $aggregate = Get-ObjectPropertyValue $criterion 'aggregate'
        $metric = Get-ObjectPropertyValue $criterion 'clientMetric'
        $condition = Get-ObjectPropertyValue $criterion 'condition'
        $value = Get-ObjectPropertyValue $criterion 'value'
        $actual = Get-ObjectPropertyValue $criterion 'actualValue'
        Write-Host "  breached      : $aggregate($metric) $condition $value - actual $actual" -ForegroundColor Red
    }

    $stats = Get-ObjectPropertyValue $Run 'testRunStatistics'
    $total = Get-ObjectPropertyValue $stats 'Total'
    if ($total) {
        Write-Host ""
        Write-Host "  samples       : $(Get-ObjectPropertyValue $total 'sampleCount')"
        Write-Host "  errors        : $(Get-ObjectPropertyValue $total 'errorCount') ($(Get-ObjectPropertyValue $total 'errorPct')%)"
        Write-Host "  throughput    : $(Get-ObjectPropertyValue $total 'throughput') req/s"
        Write-Host "  response ms   : mean $(Get-ObjectPropertyValue $total 'meanResTime') / p90 $(Get-ObjectPropertyValue $total 'pct1ResTime') / p95 $(Get-ObjectPropertyValue $total 'pct2ResTime') / p99 $(Get-ObjectPropertyValue $total 'pct3ResTime')"
    }

    if ($script:LoadTestResourceId) {
        Write-Host ""
        Write-Host "  portal        : https://portal.azure.com/#resource$script:LoadTestResourceId/tests"
    }
    Write-Host "  cli           : az load test-run show --load-test-resource $script:LoadTestName --resource-group $script:ResourceGroup --test-run-id $script:RunId"
}

function Save-RunArtefacts {
    param([Parameter(Mandatory = $true)][string]$Destination)

    Write-Step "Downloading results and report"
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null

    $download = Invoke-LoadCli @('test-run', 'download-files', '--test-run-id', $script:RunId, '--path', $Destination, '--result', '--report', '--force')
    if (-not $download.Ok) {
        Write-Warning "Could not download the run artefacts.`n$($download.Output)"
        return
    }

    $files = @(Get-ChildItem -LiteralPath $Destination -Recurse -File -ErrorAction SilentlyContinue)
    Write-Host "  [OK] $($files.Count) file(s) in $Destination"
}

try {
    Ensure-Prerequisites

    $script:LoadTestName = Get-EnvironmentValue 'LOAD_TEST_NAME' -Override $LoadTestName -Required
    $script:ResourceGroup = Get-EnvironmentValue 'AZURE_RESOURCE_GROUP' -Override $ResourceGroup -Required
    $script:SubscriptionId = Get-EnvironmentValue 'AZURE_SUBSCRIPTION_ID' -Override $SubscriptionId
    $script:LoadTestResourceId = Get-EnvironmentValue 'LOAD_TEST_RESOURCE_ID'

    if ([string]::IsNullOrWhiteSpace($script:LoadTestResourceId) -and -not [string]::IsNullOrWhiteSpace($script:SubscriptionId)) {
        $script:LoadTestResourceId = "/subscriptions/$($script:SubscriptionId)/resourceGroups/$($script:ResourceGroup)/providers/Microsoft.LoadTestService/loadTests/$($script:LoadTestName)"
    }

    if ($List -or [string]::IsNullOrWhiteSpace($TestId)) {
        Show-TestCatalog
        if (-not $List) {
            Write-Host ""
            Write-Warning "No -TestId supplied, so nothing was run and no virtual user hours were spent."
        }
        exit 0
    }

    $script:TestId = $TestId

    $exists = Invoke-LoadCli @('test', 'show', '--test-id', $script:TestId, '-o', 'none')
    if (-not $exists.Ok) {
        throw "Test '$($script:TestId)' does not exist in '$($script:LoadTestName)'. Run 'pwsh -File tools/provision_loadtests.ps1' first, or use -List to see what is deployed.`n$($exists.Output)"
    }

    if ($Preset) { Set-AppPreset -PresetName $Preset }

    $script:RunId = if ([string]::IsNullOrWhiteSpace($RunId)) { New-DefaultRunId -TestId $script:TestId } else { $RunId }

    # The service rejects anything longer than 50 characters with a generic
    # InvalidRequest, after the run has already been accepted as a command.
    # Fail here instead, where the message can say what to do about it.
    if ($script:RunId.Length -gt $script:MaxRunIdLength -or $script:RunId.Length -lt 2) {
        throw "Run id '$($script:RunId)' is $($script:RunId.Length) characters. Azure Load Testing requires 2-$($script:MaxRunIdLength). Pass a shorter -RunId."
    }

    Write-Step "Starting run '$($script:RunId)' for test '$($script:TestId)'"
    Write-Host "  This spends virtual user hours. Estimate the cost with 'python tools/vuh_calculator.py' if you need to."

    $start = Invoke-LoadCli @('test-run', 'create', '--test-id', $script:TestId, '--test-run-id', $script:RunId, '--no-wait')
    if (-not $start.Ok) {
        throw "Could not start the test run.`n$($start.Output)"
    }
    Write-Host "  [OK] Run accepted."

    if ($NoWait) {
        Write-Host ""
        Write-Host "Not waiting (-NoWait). Check progress with:"
        Write-Host "  az load test-run show --load-test-resource $($script:LoadTestName) --resource-group $($script:ResourceGroup) --test-run-id $($script:RunId) --query status -o tsv"
        exit 0
    }

    Write-Step "Waiting for the run to finish (timeout ${TimeoutMinutes}m)"
    $outcome = Wait-TestRun -RunIdToWatch $script:RunId -TimeoutMinutes $TimeoutMinutes
    if ($null -eq $outcome) {
        Write-Warning "Run '$($script:RunId)' had not reached a terminal state after $TimeoutMinutes minute(s). It is still running in Azure."
        exit 2
    }

    $run = $outcome.Run
    if ($outcome.SettleSeconds -gt 0) {
        Write-Host "  verdict settled $($outcome.SettleSeconds)s after the run reached a terminal state." -ForegroundColor DarkGray
    }

    $verdict = Get-RunVerdict -Run $run -Settled $outcome.Settled
    Show-RunSummary -Run $run -Verdict $verdict

    if ($DownloadResults) {
        $destination = if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
            Join-Path $script:RepoRoot ".loadtest-results/$($script:RunId)"
        }
        else { $OutputDirectory }
        Save-RunArtefacts -Destination $destination
    }

    # Anything that is not an unambiguous pass exits non-zero. A gate that cannot
    # prove the run passed must not report success.
    $status = [string](Get-ObjectPropertyValue $run 'status')
    if ($status -ne 'DONE' -or $verdict.Verdict -in @('FAILED', 'UNSETTLED', 'NO_VERDICT')) { exit 1 }
    exit 0
}
catch {
    Write-Host ""
    Write-Host "Failed to run the load test." -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}

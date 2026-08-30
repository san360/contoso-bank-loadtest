<#
.SYNOPSIS
    End-to-end validation of the Contoso Bank Azure Load Testing demo kit.

.DESCRIPTION
    Runs every scenario the demo depends on, in dependency order, and reports a
    pass/fail summary. Designed to be run ONCE after `azd up`, before you
    present, so that anything broken surfaces now rather than on camera.

    Phases:
      0  Preflight        - tooling, login, azd environment
      1  Infrastructure   - resources exist, managed identity, RBAC
      2  Functional smoke - every API endpoint responds correctly
      3  Bottlenecks      - the planted defects actually manifest
      4  Load tests       - the four result-status scenarios + abort criteria
      5  Portal-free      - metrics and artefact retrieval via CLI only
      6  Reset            - leave the app in the "before" state

.PARAMETER Phase
    Which phases to run. Default 'all'. Accepts a comma-separated list of
    numbers, e.g. -Phase 0,1,2 to validate the deployment without spending
    virtual user hours.

.PARAMETER IncludePeakLoad
    Also run the 1,000-VU peak load test (~250 VUH, ~$37). Off by default:
    phase 4 uses the cheap pr-gate profile, which exercises identical code
    paths for about $0.63 a run.

.PARAMETER SkipLoadTests
    Shorthand for running phases 0-3 and 6 only. Costs nothing beyond the
    App Service plan.

.EXAMPLE
    .\tools\e2e-test.ps1
    Full run using the cheap load profile. ~35 minutes, ~$3.

.EXAMPLE
    .\tools\e2e-test.ps1 -SkipLoadTests
    Validate deployment and app behaviour only. ~5 minutes, ~free.

.EXAMPLE
    .\tools\e2e-test.ps1 -IncludePeakLoad
    Everything including the 1,000-VU run. ~60 minutes, ~$40.

.NOTES
    Safe to re-run. Every step is idempotent. Scratch config files are removed
    on exit, including on Ctrl-C. Phase 6 resets the app to 'bottlenecked'; if
    you interrupt the run before then, re-run with -Phase 6 to reset it.
#>

[CmdletBinding()]
param(
    [string]$Phase = 'all',
    [switch]$IncludePeakLoad,
    [switch]$SkipLoadTests
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Referenced by Invoke-Safe before any native command has necessarily run;
# StrictMode would throw on an unset automatic variable.
$global:LASTEXITCODE = 0

# Start-ThreadJob (phase 3) ships with PowerShell 7+; Windows PowerShell 5.1
# would also need the ThreadJob module and differs in HTTP error handling.
if ($PSVersionTable.PSVersion.Major -lt 7) {
    Write-Host ""
    Write-Host "  This script requires PowerShell 7 or later." -ForegroundColor Red
    Write-Host "  You are running $($PSVersionTable.PSVersion)." -ForegroundColor Red
    Write-Host "  Install:  winget install Microsoft.PowerShell" -ForegroundColor Yellow
    Write-Host "  Then run: pwsh -File tools\e2e-test.ps1" -ForegroundColor Yellow
    Write-Host ""
    exit 1
}

# Scratch configs this script generates. Tracked so they are always removed,
# even on Ctrl-C: a leftover criteria-less config is exactly the thing the
# repo's own PR gate is built to reject.
$script:ScratchFiles = [System.Collections.Generic.List[string]]::new()

function Remove-Scratch {
    foreach ($f in $script:ScratchFiles) {
        if ($f -and (Test-Path $f)) { Remove-Item $f -Force -ErrorAction SilentlyContinue }
    }
    $script:ScratchFiles.Clear()
}

# Belt and braces: clean up on an unhandled error and on Ctrl-C / session exit.
trap { Remove-Scratch }
Register-EngineEvent -SourceIdentifier PowerShell.Exiting -SupportEvent -Action {
    foreach ($f in $script:ScratchFiles) {
        if ($f -and (Test-Path $f)) { Remove-Item $f -Force -ErrorAction SilentlyContinue }
    }
} | Out-Null

# -----------------------------------------------------------------------------
# Result tracking
# -----------------------------------------------------------------------------
$script:Results = [System.Collections.Generic.List[object]]::new()
$script:StartTime = Get-Date

function Add-Result {
    param(
        [string]$Phase,
        [string]$Name,
        [ValidateSet('PASS', 'FAIL', 'WARN', 'SKIP')][string]$Status,
        [string]$Detail = ''
    )
    $script:Results.Add([pscustomobject]@{
        Phase = $Phase; Name = $Name; Status = $Status; Detail = $Detail
    })

    $colour = switch ($Status) {
        'PASS' { 'Green' } 'FAIL' { 'Red' } 'WARN' { 'Yellow' } 'SKIP' { 'DarkGray' }
    }
    $glyph = switch ($Status) {
        'PASS' { '[PASS]' } 'FAIL' { '[FAIL]' } 'WARN' { '[WARN]' } 'SKIP' { '[SKIP]' }
    }
    Write-Host ("  {0,-6} {1,-46} {2}" -f $glyph, $Name, $Detail) -ForegroundColor $colour
}

function Write-Phase {
    param([string]$Number, [string]$Title)
    Write-Host ""
    Write-Host ("=" * 78) -ForegroundColor Cyan
    Write-Host ("  PHASE $Number - $Title") -ForegroundColor Cyan
    Write-Host ("=" * 78) -ForegroundColor Cyan
}

function Test-PhaseEnabled {
    param([string]$Number)
    if ($SkipLoadTests -and $Number -in @('4', '5')) { return $false }
    if ($Phase -eq 'all') { return $true }
    return ($Phase -split ',').Trim() -contains $Number
}

# Invoke a command, returning stdout; never throws. Used so a single failing
# check cannot abort the whole suite.
function Invoke-Safe {
    param([scriptblock]$Script)
    # az/azd write progress and preview warnings to stderr. With 2>&1 those
    # become ErrorRecords, which ErrorActionPreference=Stop would turn into a
    # thrown terminating error. Localise the preference so they stay data.
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $global:LASTEXITCODE = 0
    try {
        $out = & $Script 2>&1
        return @{ Ok = ($LASTEXITCODE -eq 0); Output = ($out | Out-String).Trim() }
    } catch {
        return @{ Ok = $false; Output = $_.Exception.Message }
    } finally {
        $ErrorActionPreference = $prev
    }
}

# HTTP helper. Deliberately avoids -SkipHttpErrorCheck / -StatusCodeVariable:
# their availability varies across PowerShell 7.x point releases. Reading the
# status off the exception works on every 7.x.
function Invoke-Api {
    param(
        [string]$Method = 'GET',
        [string]$Path,
        [object]$Body,
        [int]$TimeoutSec = 120
    )
    $params = @{
        Method = $Method; Uri = "$script:ApiBase$Path"; TimeoutSec = $TimeoutSec
    }
    if ($null -ne $Body) {
        $params.Body = ($Body | ConvertTo-Json -Compress -Depth 6)
        $params.ContentType = 'application/json'
    }
    try {
        $r = Invoke-RestMethod @params
        # No throw means a 2xx.
        return @{ Ok = $true; Code = 200; Body = $r }
    } catch {
        $code = 0
        # A network-level failure (DNS, TLS) has no .Response at all, and
        # StrictMode throws on a missing property - hence the guarded read.
        $resp = $null
        try { $resp = $_.Exception.Response } catch { $resp = $null }
        if ($null -ne $resp) {
            $sc = $null
            try { $sc = $resp.StatusCode } catch { $sc = $null }
            # HttpStatusCode is an enum; casting to int gives the number.
            if ($null -ne $sc) { $code = [int]$sc }
        }
        # ErrorDetails.Message carries the response body for a failed request.
        $detail = $_.Exception.Message
        if ($_.ErrorDetails -and $_.ErrorDetails.Message) { $detail = $_.ErrorDetails.Message }
        return @{ Ok = $false; Code = $code; Body = $detail }
    }
}

function Set-Preset {
    param([string]$Preset)
    $r = Invoke-Api -Method POST -Path '/admin/config' -Body @{ preset = $Preset }
    if ($r.Ok) { Invoke-Api -Method POST -Path '/admin/reset' | Out-Null }
    return $r.Ok
}

# -----------------------------------------------------------------------------
# PHASE 0 - Preflight
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "  Contoso Bank - Azure Load Testing end-to-end validation" -ForegroundColor White
Write-Host "  Started $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor DarkGray

Write-Phase '0' 'Preflight'

foreach ($tool in @('az', 'azd')) {
    $r = Invoke-Safe { & $tool version }
    Add-Result '0' "$tool is installed" $(if ($r.Ok) { 'PASS' } else { 'FAIL' })
    if (-not $r.Ok) {
        Write-Host "`n  Cannot continue without $tool. See docs/18 section 18.3.`n" -ForegroundColor Red
        exit 1
    }
}

$r = Invoke-Safe { az extension show --name load --query version -o tsv }
if ($r.Ok -and $r.Output) {
    Add-Result '0' 'az load extension present' 'PASS' "v$($r.Output)"
} else {
    Write-Host "  Installing the 'load' extension..." -ForegroundColor Yellow
    $i = Invoke-Safe { az extension add --name load --only-show-errors }
    Add-Result '0' 'az load extension present' $(if ($i.Ok) { 'PASS' } else { 'FAIL' }) 'auto-installed'
}

$r = Invoke-Safe { az account show --query name -o tsv }
Add-Result '0' 'Logged in to Azure' $(if ($r.Ok) { 'PASS' } else { 'FAIL' }) $r.Output
if (-not $r.Ok) { Write-Host "`n  Run: az login`n" -ForegroundColor Red; exit 1 }

# Read the azd environment. Everything downstream depends on these values.
$envRaw = Invoke-Safe { azd env get-values }
if (-not $envRaw.Ok) {
    Add-Result '0' 'azd environment readable' 'FAIL' 'run azd up first'
    exit 1
}
$azdEnv = @{}
foreach ($line in ($envRaw.Output -split "`n")) {
    if ($line -match '^\s*([A-Za-z0-9_]+)\s*=\s*(.*)$') {
        $azdEnv[$Matches[1]] = $Matches[2].Trim().Trim('"')
    }
}

$required = @('AZURE_RESOURCE_GROUP', 'SERVICE_API_URI', 'LOAD_TEST_NAME', 'SERVICE_API_RESOURCE_ID')
$missing = $required | Where-Object { -not $azdEnv.ContainsKey($_) -or -not $azdEnv[$_] }
if ($missing) {
    Add-Result '0' 'azd outputs present' 'FAIL' "missing: $($missing -join ', ')"
    exit 1
}
Add-Result '0' 'azd outputs present' 'PASS' "$($azdEnv.Count) values"

$script:ApiBase       = $azdEnv['SERVICE_API_URI'].TrimEnd('/')
$script:ResourceGroup = $azdEnv['AZURE_RESOURCE_GROUP']
$script:LoadTestName  = $azdEnv['LOAD_TEST_NAME']
$script:ApiResourceId = $azdEnv['SERVICE_API_RESOURCE_ID']
$script:ApiHost       = ([Uri]$script:ApiBase).Host

Write-Host "    API           : $script:ApiBase"       -ForegroundColor DarkGray
Write-Host "    Resource group: $script:ResourceGroup" -ForegroundColor DarkGray
Write-Host "    Load test     : $script:LoadTestName"  -ForegroundColor DarkGray

# The single most common demo-killer: configs still pointing at the placeholder.
$cfgPath = Join-Path $PSScriptRoot '..\loadtests\config\pr-gate.yaml'
if (Test-Path $cfgPath) {
    $cfg = Get-Content $cfgPath -Raw
    if ($cfg -match 'contoso-bank-api\.azurewebsites\.net') {
        Add-Result '0' 'Load test configs retargeted' 'FAIL' 'still on placeholder host'
        Write-Host "`n  Run:  python tools\set_target_host.py --from-azd`n" -ForegroundColor Red
        exit 1
    } elseif ($cfg -match [regex]::Escape($script:ApiHost)) {
        Add-Result '0' 'Load test configs retargeted' 'PASS' $script:ApiHost
    } else {
        Add-Result '0' 'Load test configs retargeted' 'WARN' 'points at a different host'
    }
}

# -----------------------------------------------------------------------------
# PHASE 1 - Infrastructure
# -----------------------------------------------------------------------------
if (Test-PhaseEnabled '1') {
    Write-Phase '1' 'Infrastructure'

    $r = Invoke-Safe {
        az resource list --resource-group $script:ResourceGroup --query "[].type" -o tsv
    }
    $types = if ($r.Ok) { $r.Output -split "`n" | ForEach-Object { $_.Trim() } } else { @() }

    $expected = @{
        'Microsoft.Web/serverfarms'          = 'App Service plan'
        'Microsoft.Web/sites'                = 'App Service'
        'Microsoft.LoadTestService/loadTests' = 'Load Testing resource'
        'Microsoft.OperationalInsights/workspaces' = 'Log Analytics'
        'Microsoft.Insights/components'      = 'Application Insights'
    }
    foreach ($t in $expected.Keys) {
        $found = $types -contains $t
        Add-Result '1' $expected[$t] $(if ($found) { 'PASS' } else { 'FAIL' })
    }

    # The App Service plan must have exactly one worker, or the bottleneck hides.
    $r = Invoke-Safe {
        az appservice plan list --resource-group $script:ResourceGroup `
            --query "[0].{cap:sku.capacity,tier:sku.tier}" -o tsv
    }
    if ($r.Ok -and $r.Output) {
        $parts = $r.Output -split "\s+"
        $cap = $parts[0]
        Add-Result '1' 'Plan has 1 worker (bottleneck visible)' `
            $(if ($cap -eq '1') { 'PASS' } else { 'WARN' }) "capacity=$($r.Output)"
    }

    # Managed identity + Monitoring Reader. THE step everyone forgets.
    $r = Invoke-Safe {
        az load show --name $script:LoadTestName --resource-group $script:ResourceGroup `
            --query "identity.principalId" -o tsv
    }
    if ($r.Ok -and $r.Output) {
        $principalId = $r.Output.Trim()
        Add-Result '1' 'Load test has managed identity' 'PASS'

        $ra = Invoke-Safe {
            az role assignment list --assignee $principalId --all `
                --query "[?roleDefinitionName=='Monitoring Reader'].roleDefinitionName" -o tsv
        }
        if ($ra.Ok -and $ra.Output -match 'Monitoring Reader') {
            Add-Result '1' 'Monitoring Reader assigned' 'PASS' 'server metrics will populate'
        } else {
            Add-Result '1' 'Monitoring Reader assigned' 'FAIL' 'server-side charts will be EMPTY'
        }
    } else {
        Add-Result '1' 'Load test has managed identity' 'FAIL'
    }
}

# -----------------------------------------------------------------------------
# PHASE 2 - Functional smoke
# -----------------------------------------------------------------------------
if (Test-PhaseEnabled '2') {
    Write-Phase '2' 'Functional smoke'

    # Cold start can take a while on first hit; retry before declaring failure.
    $healthy = $false
    for ($i = 1; $i -le 6; $i++) {
        $r = Invoke-Api -Path '/health' -TimeoutSec 90
        if ($r.Ok) { $healthy = $true; break }
        Write-Host "    waiting for app to warm up ($i/6)..." -ForegroundColor DarkGray
        Start-Sleep -Seconds 15
    }
    Add-Result '2' 'GET /health' $(if ($healthy) { 'PASS' } else { 'FAIL' })
    if (-not $healthy) {
        Write-Host "`n  App not responding. Check the App Service log stream.`n" -ForegroundColor Red
        exit 1
    }

    $checks = @(
        @{ N = 'GET /ready';                     P = '/ready' }
        @{ N = 'GET /api/accounts';              P = '/api/accounts?limit=5' }
        @{ N = 'GET /api/accounts/{id}/balance'; P = '/api/accounts/ACC-1001/balance' }
        @{ N = 'GET .../transactions';           P = '/api/accounts/ACC-1001/transactions?page_size=10' }
        @{ N = 'GET /api/statements/{id}';       P = '/api/statements/ACC-1001?months=1' }
        @{ N = 'GET /admin/config';              P = '/admin/config' }
    )
    foreach ($c in $checks) {
        $r = Invoke-Api -Path $c.P
        Add-Result '2' $c.N $(if ($r.Ok) { 'PASS' } else { 'FAIL' }) $(if (-not $r.Ok) { "HTTP $($r.Code)" } else { '' })
    }

    # Write path, then read back by the id it returned - proves correlation works,
    # which is what the JMeter JSON extractor depends on.
    $body = @{
        from_account = 'ACC-1001'; to_account = 'ACC-1002'
        amount_minor = 250; currency = 'GBP'; reference = 'e2e-smoke'
    }
    $tr = Invoke-Api -Method POST -Path '/api/transfers' -Body $body
    Add-Result '2' 'POST /api/transfers' $(if ($tr.Ok) { 'PASS' } else { 'FAIL' }) $(if (-not $tr.Ok) { "HTTP $($tr.Code)" } else { '' })

    if ($tr.Ok) {
        $txId = $null
        foreach ($p in @('transaction_id', 'transactionId', 'id')) {
            if ($tr.Body.PSObject.Properties.Name -contains $p) { $txId = $tr.Body.$p; break }
        }
        if ($txId) {
            $g = Invoke-Api -Path "/api/transfers/$txId"
            Add-Result '2' 'GET /api/transfers/{id} (correlation)' $(if ($g.Ok) { 'PASS' } else { 'FAIL' })
        } else {
            Add-Result '2' 'GET /api/transfers/{id} (correlation)' 'WARN' 'no id field in response'
        }
    }

    # snake_case vs camelCase. src/main.py sets extra="forbid" on ConfigUpdate
    # specifically so this returns 422 rather than a silent 200 that ignores
    # the field. If this regresses to 200, every "apply the fix" step in the
    # demo can appear to work while changing nothing.
    $bad = Invoke-Api -Method POST -Path '/admin/config' -Body @{ enableCache = $true }
    Add-Result '2' 'Rejects camelCase (expects 422)' `
        $(if ($bad.Code -eq 422) { 'PASS' } else { 'FAIL' }) "HTTP $($bad.Code)"

    foreach ($p in @('bottlenecked', 'optimized')) {
        $ok = Set-Preset $p
        Add-Result '2' "Preset '$p' applies" $(if ($ok) { 'PASS' } else { 'FAIL' })
    }
    Set-Preset 'bottlenecked' | Out-Null
}

# -----------------------------------------------------------------------------
# PHASE 3 - Bottlenecks actually manifest
# -----------------------------------------------------------------------------
if (Test-PhaseEnabled '3') {
    Write-Phase '3' 'Bottleneck validation'

    # Fire N requests at a path with limited concurrency and return p95 (ms).
    function Measure-Endpoint {
        param([string]$Path, [int]$Count = 40, [int]$Concurrency = 8)

        $jobs = [System.Collections.Generic.List[object]]::new()
        $latencies = [System.Collections.Concurrent.ConcurrentBag[double]]::new()
        $url = "$script:ApiBase$Path"

        $batch = [Math]::Ceiling($Count / $Concurrency)
        for ($c = 0; $c -lt $Concurrency; $c++) {
            $jobs.Add((Start-ThreadJob -ScriptBlock {
                param($u, $n, $bag)
                for ($i = 0; $i -lt $n; $i++) {
                    $sw = [System.Diagnostics.Stopwatch]::StartNew()
                    try { Invoke-WebRequest -Uri $u -TimeoutSec 90 -UseBasicParsing | Out-Null } catch { }
                    $sw.Stop(); $bag.Add($sw.Elapsed.TotalMilliseconds)
                }
            } -ArgumentList $url, $batch, $latencies))
        }
        $jobs | Wait-Job -Timeout 300 | Out-Null
        $jobs | Remove-Job -Force -ErrorAction SilentlyContinue

        $arr = @($latencies) | Sort-Object
        if ($arr.Count -eq 0) { return -1 }
        return [Math]::Round($arr[[Math]::Max(0, [int]($arr.Count * 0.95) - 1)], 1)
    }

    if (-not (Get-Module -ListAvailable -Name ThreadJob) -and -not (Get-Command Start-ThreadJob -ErrorAction SilentlyContinue)) {
        Add-Result '3' 'Concurrency harness available' 'SKIP' 'ThreadJob module missing'
    } else {
        $endpoints = @(
            @{ N = 'balance';      P = '/api/accounts/ACC-1001/balance' }
            @{ N = 'transactions'; P = '/api/accounts/ACC-1001/transactions?page_size=20' }
            @{ N = 'statement';    P = '/api/statements/ACC-1001?months=3' }
        )

        $before = @{}; $after = @{}

        Set-Preset 'bottlenecked' | Out-Null
        Start-Sleep -Seconds 2
        Write-Host "    measuring 'bottlenecked'..." -ForegroundColor DarkGray
        foreach ($e in $endpoints) {
            Measure-Endpoint -Path $e.P -Count 16 -Concurrency 4 | Out-Null   # warm-up
            $before[$e.N] = Measure-Endpoint -Path $e.P
        }

        Set-Preset 'optimized' | Out-Null
        Start-Sleep -Seconds 2
        Write-Host "    measuring 'optimized'..." -ForegroundColor DarkGray
        foreach ($e in $endpoints) {
            Measure-Endpoint -Path $e.P -Count 16 -Concurrency 4 | Out-Null   # warm-up
            $after[$e.N] = Measure-Endpoint -Path $e.P
        }

        foreach ($e in $endpoints) {
            $b = $before[$e.N]; $a = $after[$e.N]
            if ($b -le 0 -or $a -le 0) {
                Add-Result '3' "$($e.N) improves" 'WARN' 'no measurement'
                continue
            }
            $factor = [Math]::Round($b / $a, 1)
            $detail = "p95 ${b}ms -> ${a}ms  (${factor}x)"
            # 1.3x is a deliberately loose bar: this is a coarse harness over the
            # public internet, not the precise one in validate_bottlenecks.py.
            Add-Result '3' "$($e.N) improves" $(if ($factor -ge 1.3) { 'PASS' } else { 'WARN' }) $detail
        }

        Set-Preset 'bottlenecked' | Out-Null
    }
}

# -----------------------------------------------------------------------------
# PHASE 4 - Load test scenarios
# -----------------------------------------------------------------------------
function Wait-TestRun {
    <#
      az load test-run create blocks by default, but we re-query afterwards so
      the script never depends on that behaviour, and so we can report both
      fields of the status taxonomy.
    #>
    param([string]$RunId, [int]$TimeoutMinutes = 25)

    $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
    $terminal = @('DONE', 'FAILED', 'CANCELLED')

    while ((Get-Date) -lt $deadline) {
        $r = Invoke-Safe {
            az load test-run show --load-test-resource $script:LoadTestName `
                --resource-group $script:ResourceGroup --test-run-id $RunId `
                --query "{s:status,t:testResult}" -o json
        }
        if ($r.Ok -and $r.Output) {
            try {
                $j = $r.Output | ConvertFrom-Json
                if ($j.s -in $terminal) {
                    return @{ Status = $j.s; Result = $j.t }
                }
                Write-Host "      $($j.s)..." -ForegroundColor DarkGray
            } catch { }
        }
        Start-Sleep -Seconds 20
    }
    return @{ Status = 'TIMEOUT'; Result = $null }
}

function Invoke-Scenario {
    param(
        [string]$Name,
        [string]$TestId,
        [string]$ConfigFile,
        [string]$ExpectStatus,
        [string]$ExpectResult,
        [string]$Because
    )

    Write-Host ""
    Write-Host "  --- $Name" -ForegroundColor White
    Write-Host "      expecting status=$ExpectStatus testResult=$ExpectResult  ($Because)" -ForegroundColor DarkGray

    $cfg = Join-Path $PSScriptRoot "..\$ConfigFile"
    if (-not (Test-Path $cfg)) {
        Add-Result '4' $Name 'FAIL' "config not found: $ConfigFile"
        return
    }

    # Create or update the test definition. --test-id is required even though
    # the YAML declares testId.
    $c = Invoke-Safe {
        az load test create --load-test-resource $script:LoadTestName `
            --resource-group $script:ResourceGroup --test-id $TestId `
            --load-test-config-file $cfg --only-show-errors
    }
    if (-not $c.Ok) {
        # Already exists - update it instead.
        $c = Invoke-Safe {
            az load test update --load-test-resource $script:LoadTestName `
                --resource-group $script:ResourceGroup --test-id $TestId `
                --load-test-config-file $cfg --only-show-errors
        }
    }
    if (-not $c.Ok) {
        Add-Result '4' $Name 'FAIL' 'could not create/update test'
        return
    }

    # App components make it a diagnosis rather than a black box. Non-fatal.
    Invoke-Safe {
        az load test app-component add --load-test-resource $script:LoadTestName `
            --resource-group $script:ResourceGroup --test-id $TestId `
            --app-component-id $script:ApiResourceId `
            --app-component-type 'Microsoft.Web/sites' `
            --app-component-name 'contoso-bank-api' --only-show-errors
    } | Out-Null

    $runId = "$TestId-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    Write-Host "      run id: $runId" -ForegroundColor DarkGray

    Invoke-Safe {
        az load test-run create --load-test-resource $script:LoadTestName `
            --resource-group $script:ResourceGroup --test-id $TestId `
            --test-run-id $runId --no-wait --only-show-errors
    } | Out-Null

    $outcome = Wait-TestRun -RunId $runId
    $script:LastRunId = $runId

    $statusOk = ($outcome.Status -eq $ExpectStatus)
    $resultOk = ([string]::IsNullOrEmpty($ExpectResult)) -or ($outcome.Result -eq $ExpectResult)
    $detail = "status=$($outcome.Status) testResult=$($outcome.Result)"

    Add-Result '4' $Name $(if ($statusOk -and $resultOk) { 'PASS' } else { 'FAIL' }) $detail
}

if (Test-PhaseEnabled '4') {
    Write-Phase '4' 'Load test scenarios'

    Write-Host "  Each scenario runs a real load test. Allow 8-12 minutes each." -ForegroundColor DarkGray

    # --- 4a: the useful failure. Bottlenecked app + criteria -> DONE/FAILED ---
    Set-Preset 'bottlenecked' | Out-Null
    Invoke-Scenario -Name 'Bottlenecked app breaches SLOs' `
        -TestId 'e2e-pr-gate' -ConfigFile 'loadtests/config/pr-gate.yaml' `
        -ExpectStatus 'DONE' -ExpectResult 'FAILED' `
        -Because 'fail criteria defined and breached'

    # --- 4b: the green run. Optimized app + criteria -> DONE/PASSED ---
    Set-Preset 'optimized' | Out-Null
    Start-Sleep -Seconds 5
    Invoke-Scenario -Name 'Optimized app meets SLOs' `
        -TestId 'e2e-pr-gate' -ConfigFile 'loadtests/config/pr-gate.yaml' `
        -ExpectStatus 'DONE' -ExpectResult 'PASSED' `
        -Because 'same test, defects switched off'

    # --- 4c: the false green. 100% errors, no criteria -> DONE/NOT_APPLICABLE --
    Write-Host ""
    Write-Host "  --- The false-green scenario (the reviewer's question)" -ForegroundColor White

    $noCriteria = Join-Path $PSScriptRoot '..\loadtests\config\_e2e-no-criteria.yaml'
    $script:ScratchFiles.Add($noCriteria)
    $src = Get-Content (Join-Path $PSScriptRoot '..\loadtests\config\pr-gate.yaml') -Raw

    # Strip the failureCriteria block: from the key to the next top-level key.
    $stripped = [regex]::Replace($src, '(?ms)^failureCriteria:.*?(?=^\S|\Z)', '')
    $stripped = $stripped -replace '(?m)^testId:.*$', 'testId: e2e-no-criteria'
    $stripped = $stripped -replace '(?m)^displayName:.*$', 'displayName: E2E - no fail criteria (false green)'
    Set-Content -Path $noCriteria -Value $stripped -Encoding UTF8 -NoNewline

    if ($stripped -match '(?m)^failureCriteria:') {
        Add-Result '4' 'Built criteria-less config' 'FAIL' 'strip failed'
    } else {
        Add-Result '4' 'Built criteria-less config' 'PASS'

        # Make the app fail 100% of requests.
        Invoke-Api -Method POST -Path '/admin/config' -Body @{ error_rate_percent = 100 } | Out-Null
        $chk = Invoke-Api -Path '/admin/config'
        $rate = if ($chk.Ok) { $chk.Body.config.error_rate_percent } else { 'unknown' }
        Add-Result '4' 'Error injection at 100%' $(if ("$rate" -eq '100') { 'PASS' } else { 'WARN' }) "error_rate_percent=$rate"

        Invoke-Scenario -Name 'FALSE GREEN: 100% errors, no criteria' `
            -TestId 'e2e-no-criteria' -ConfigFile 'loadtests/config/_e2e-no-criteria.yaml' `
            -ExpectStatus 'DONE' -ExpectResult 'NOT_APPLICABLE' `
            -Because 'every request fails, yet the run does not report failure'

        Invoke-Api -Method POST -Path '/admin/config' -Body @{ error_rate_percent = 0 } | Out-Null
        Remove-Item $noCriteria -ErrorAction SilentlyContinue
    }

    # --- 4d: abort criteria (AutoStop) -> CANCELLED ------------------------
    Write-Host ""
    Write-Host "  --- Abort criteria (AutoStop)" -ForegroundColor White

    $autoStop = Join-Path $PSScriptRoot '..\loadtests\config\_e2e-autostop.yaml'
    $script:ScratchFiles.Add($autoStop)
    $as = $src -replace '(?m)^testId:.*$', 'testId: e2e-autostop'
    $as = $as -replace '(?m)^displayName:.*$', 'displayName: E2E - abort criteria'
    # Trip AutoStop quickly: 20% errors over a 30s window.
    if ($as -match '(?ms)^autoStop:') {
        $as = [regex]::Replace($as, '(?ms)^autoStop:.*?(?=^\S|\Z)', "autoStop:`n  errorPercentage: 20`n  timeWindow: 30`n")
    } else {
        $as = $as.TrimEnd() + "`n`nautoStop:`n  errorPercentage: 20`n  timeWindow: 30`n"
    }
    Set-Content -Path $autoStop -Value $as -Encoding UTF8 -NoNewline

    Invoke-Api -Method POST -Path '/admin/config' -Body @{ error_rate_percent = 60 } | Out-Null

    Invoke-Scenario -Name 'AutoStop aborts a failing run' `
        -TestId 'e2e-autostop' -ConfigFile 'loadtests/config/_e2e-autostop.yaml' `
        -ExpectStatus 'CANCELLED' -ExpectResult '' `
        -Because '60% errors exceeds the 20% abort threshold'

    Write-Host "      NOTE: record the testResult above - it is documented as" -ForegroundColor Yellow
    Write-Host "            unverified in docs/07 section 7.9. This run settles it." -ForegroundColor Yellow

    Invoke-Api -Method POST -Path '/admin/config' -Body @{ error_rate_percent = 0 } | Out-Null
    Remove-Item $autoStop -ErrorAction SilentlyContinue

    # --- 4e: peak load, opt-in --------------------------------------------
    if ($IncludePeakLoad) {
        Set-Preset 'bottlenecked' | Out-Null
        Invoke-Scenario -Name 'Peak load 1,000 VU (bottlenecked)' `
            -TestId 'contoso-bank-peak-load' -ConfigFile 'loadtests/config/peak-load.yaml' `
            -ExpectStatus 'DONE' -ExpectResult 'FAILED' `
            -Because 'the headline demo run'
    } else {
        Add-Result '4' 'Peak load 1,000 VU' 'SKIP' 'use -IncludePeakLoad (~$37)'
    }
}

# -----------------------------------------------------------------------------
# PHASE 5 - Portal-free result retrieval
# -----------------------------------------------------------------------------
if (Test-PhaseEnabled '5') {
    if (-not (Get-Variable -Name LastRunId -Scope Script -ErrorAction SilentlyContinue)) {
        # Phase 5 inspects the run phase 4 produced. Running it alone has
        # nothing to inspect - say so rather than skipping silently.
        Write-Phase '5' 'Portal-free result retrieval'
        Add-Result '5' 'Retrieval checks' 'SKIP' 'no run in this session; run phase 4 too'
    } else {
    Write-Phase '5' 'Portal-free result retrieval'

    $r = Invoke-Safe {
        az load test-run list --load-test-resource $script:LoadTestName `
            --resource-group $script:ResourceGroup --query "length(@)" -o tsv
    }
    Add-Result '5' 'List runs via CLI' $(if ($r.Ok) { 'PASS' } else { 'FAIL' }) "$($r.Output) run(s)"

    $r = Invoke-Safe {
        az load test-run metrics list --load-test-resource $script:LoadTestName `
            --resource-group $script:ResourceGroup --test-run-id $script:LastRunId `
            --metric-namespace LoadTestRunMetrics -o json
    }
    Add-Result '5' 'Client metrics via CLI' $(if ($r.Ok) { 'PASS' } else { 'WARN' }) 'LoadTestRunMetrics'

    # Download into a per-run subfolder. Counting files in .e2e-results itself
    # would also count summary.csv from a previous run and report a false PASS.
    $outDir = Join-Path $PSScriptRoot "..\.e2e-results\artefacts-$script:LastRunId"
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
    $r = Invoke-Safe {
        az load test-run download-files --load-test-resource $script:LoadTestName `
            --resource-group $script:ResourceGroup --test-run-id $script:LastRunId `
            --path $outDir --result --report --force --only-show-errors
    }
    $files = @(Get-ChildItem $outDir -Recurse -File -ErrorAction SilentlyContinue)
    Add-Result '5' 'Download artefacts via CLI' `
        $(if ($files.Count -gt 0) { 'PASS' } else { 'WARN' }) "$($files.Count) file(s) in $(Split-Path $outDir -Leaf)"

    Add-Result '5' 'Server metrics need Azure Monitor' 'PASS' 'client metrics are NOT in Azure Monitor'
    }
}

# -----------------------------------------------------------------------------
# PHASE 6 - Reset
# -----------------------------------------------------------------------------
if (Test-PhaseEnabled '6') {
    Write-Phase '6' 'Reset to demo start state'
    Invoke-Api -Method POST -Path '/admin/config' -Body @{ error_rate_percent = 0 } | Out-Null
    $ok = Set-Preset 'bottlenecked'
    Add-Result '6' "App reset to 'bottlenecked'" $(if ($ok) { 'PASS' } else { 'WARN' }) 'opens on the problem'
}

# Always remove scratch configs, whichever phases ran.
Remove-Scratch

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
$elapsed = [Math]::Round(((Get-Date) - $script:StartTime).TotalMinutes, 1)
$pass = @($script:Results | Where-Object Status -eq 'PASS').Count
$fail = @($script:Results | Where-Object Status -eq 'FAIL').Count
$warn = @($script:Results | Where-Object Status -eq 'WARN').Count
$skip = @($script:Results | Where-Object Status -eq 'SKIP').Count

Write-Host ""
Write-Host ("=" * 78) -ForegroundColor Cyan
Write-Host "  SUMMARY" -ForegroundColor Cyan
Write-Host ("=" * 78) -ForegroundColor Cyan
Write-Host ""
Write-Host "  Passed  : $pass" -ForegroundColor Green
Write-Host "  Failed  : $fail" -ForegroundColor $(if ($fail) { 'Red' } else { 'DarkGray' })
Write-Host "  Warnings: $warn" -ForegroundColor $(if ($warn) { 'Yellow' } else { 'DarkGray' })
Write-Host "  Skipped : $skip" -ForegroundColor DarkGray
Write-Host "  Elapsed : $elapsed min"
Write-Host ""

if ($fail -gt 0) {
    Write-Host "  Failures:" -ForegroundColor Red
    $script:Results | Where-Object Status -eq 'FAIL' | ForEach-Object {
        Write-Host "    - [$($_.Phase)] $($_.Name)  $($_.Detail)" -ForegroundColor Red
    }
    Write-Host ""
    Write-Host "  Troubleshooting: docs/18-deploy-and-first-run.md section 18.10" -ForegroundColor Yellow
    Write-Host ""
}

$csv = Join-Path $PSScriptRoot '..\.e2e-results\summary.csv'
New-Item -ItemType Directory -Force -Path (Split-Path $csv) | Out-Null
$script:Results | Export-Csv -Path $csv -NoTypeInformation
Write-Host "  Full results: $csv" -ForegroundColor DarkGray
Write-Host ""

exit $(if ($fail -gt 0) { 1 } else { 0 })

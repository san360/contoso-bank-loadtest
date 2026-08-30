<#
.SYNOPSIS
    Creates the Contoso Bank Azure Load Testing definitions after deployment.

.DESCRIPTION
    `azd up` should leave the customer with a live app AND a populated Load
    Testing resource. This hook runs after deploy, retargets the versioned test
    artefacts to the generated App Service hostname, then creates or updates
    every YAML test definition under loadtests/config.

    It deliberately does not start a test run. Creating definitions is free;
    running them spends virtual user hours and stays an explicit presenter step.
#>

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$global:LASTEXITCODE = 0

$script:RepoRoot = Split-Path -Parent $PSScriptRoot
$script:AzdValues = $null
$script:Summary = [System.Collections.Generic.List[object]]::new()

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
        throw "'$Name' is not on PATH. Install it before running this hook."
    }
}

function Read-AzdValues {
    if ($null -ne $script:AzdValues) { return $script:AzdValues }
    Assert-Tool 'azd'

    $result = Invoke-External 'azd' @('env', 'get-values')
    if (-not $result.Ok) {
        throw "Could not read the azd environment. Run 'azd up' first, or export the required hook variables.`n$result.Output"
    }

    $values = @{}
    foreach ($line in ($result.Output -split "`r?`n")) {
        if ($line -match '^([^=]+)=(.*)$') {
            $values[$matches[1]] = $matches[2].Trim().Trim('"')
        }
    }
    $script:AzdValues = $values
    return $values
}

function Get-HookValue {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [switch]$Required
    )

    $value = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($value)) {
        $values = Read-AzdValues
        if ($values.ContainsKey($Name)) { $value = $values[$Name] }
    }

    if ($Required -and [string]::IsNullOrWhiteSpace($value)) {
        throw "Required azd output '$Name' is missing from the hook environment."
    }
    return $value
}

function Get-RelativePath {
    param([string]$Path)
    if ([System.IO.Path].GetMethods().Name -contains 'GetRelativePath') {
        return [System.IO.Path]::GetRelativePath($script:RepoRoot, $Path).Replace('\', '/')
    }

    $root = (Resolve-Path -LiteralPath $script:RepoRoot).Path.TrimEnd('\')
    $full = (Resolve-Path -LiteralPath $Path).Path
    if ($full.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
        return $full.Substring($root.Length).TrimStart('\').Replace('\', '/')
    }
    return $Path.Replace('\', '/')
}

function Normalize-Host {
    param([string]$HostName)
    return (($HostName.Trim() -replace '^https?://', '') -replace '/$', '')
}

function Update-TargetHostNative {
    param([Parameter(Mandatory = $true)][string]$TargetHost)

    $patterns = @('*.yaml', '*.yml', '*.json', '*.conf', '*.properties', '*.jmx')
    $files = foreach ($pattern in $patterns) {
        Get-ChildItem -Path (Join-Path $script:RepoRoot 'loadtests') -Recurse -File -Filter $pattern -ErrorAction SilentlyContinue
    }

    $hostPattern = [regex]'\b[a-z0-9][a-z0-9-]*\.azurewebsites\.net\b'
    $changed = 0
    $hits = 0

    foreach ($file in ($files | Sort-Object FullName -Unique)) {
        $original = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8

        # Count first. A MatchEvaluator scriptblock runs in a child scope, so
        # incrementing a counter inside it silently updates a copy and the caller
        # always sees zero.
        $localHits = @($hostPattern.Matches($original) | Where-Object {
                -not $_.Value.Equals($TargetHost, [StringComparison]::OrdinalIgnoreCase)
            }).Count

        $updated = $hostPattern.Replace($original, {
                param($match)
                return $TargetHost
            })

        if ($updated -cne $original) {
            Set-Content -LiteralPath $file.FullName -Value $updated -Encoding UTF8 -NoNewline
            $changed++
            $hits += $localHits
            Write-Host ("  ->  {0}  ({1} occurrence{2} updated by PowerShell fallback)" -f (Get-RelativePath $file.FullName), $localHits, $(if ($localHits -eq 1) { '' } else { 's' }))
        }
    }

    if ($hits -eq 0) {
        Write-Host "  =  no remaining load-test hostnames needed PowerShell fallback changes"
    }
    else {
        Write-Host "  PowerShell fallback rewrote $hits occurrence(s) across $changed file(s)."
    }
}

function Retarget-LoadTestArtifacts {
    param(
        [Parameter(Mandatory = $true)][string]$ServiceHostName
    )

    $targetHost = Normalize-Host $ServiceHostName
    if ($targetHost -notmatch '^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9-]+)+$') {
        throw "SERVICE_API_HOSTNAME '$ServiceHostName' does not look like a hostname."
    }

    Write-Step "Retargeting load test artefacts to $targetHost"

    $python = Get-Command 'python' -ErrorAction SilentlyContinue
    if ($python) {
        $result = Invoke-External $python.Source @((Join-Path $script:RepoRoot 'tools\set_target_host.py'), $targetHost)
        if (-not $result.Ok) {
            throw "Python retargeting failed.`n$result.Output"
        }
        if ($result.Output) { Write-Host $result.Output }
        Write-Host ""
        Write-Host "Checking for any newly-added artefacts not listed in set_target_host.py..."
    }
    else {
        Write-Host "  Python is not on PATH. Using the native PowerShell fallback promised by azure.yaml."
    }
    Update-TargetHostNative -TargetHost $targetHost
}

function Update-KeyVaultReferences {
    param([Parameter(Mandatory = $true)][string]$KeyVaultName)

    Write-Step "Retargeting Key Vault secret references to $KeyVaultName"

    $patterns = @('*.yaml', '*.yml', '*.json')
    $files = foreach ($pattern in $patterns) {
        Get-ChildItem -Path (Join-Path $script:RepoRoot 'loadtests') -Recurse -File -Filter $pattern -ErrorAction SilentlyContinue
    }

    # Deliberately drops any trailing /<version> segment. A pinned version dies the
    # moment the vault is recreated; the version-less form always resolves to the
    # current secret.
    $vaultPattern = [regex]'https://[a-z0-9-]+\.vault\.azure\.net/secrets/([A-Za-z0-9-]+)(?:/[0-9a-fA-F]+)?'
    $hits = 0

    foreach ($file in ($files | Sort-Object FullName -Unique)) {
        $original = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8

        # Count first. A MatchEvaluator scriptblock runs in a child scope, so
        # incrementing a counter inside it silently updates a copy and the caller
        # always sees zero.
        $localHits = @($vaultPattern.Matches($original) | Where-Object {
                $_.Value -cne "https://$KeyVaultName.vault.azure.net/secrets/$($_.Groups[1].Value)"
            }).Count

        $updated = $vaultPattern.Replace($original, {
                param($match)
                return "https://$KeyVaultName.vault.azure.net/secrets/$($match.Groups[1].Value)"
            })

        if ($updated -cne $original) {
            Set-Content -LiteralPath $file.FullName -Value $updated -Encoding UTF8 -NoNewline
            $hits += $localHits
            Write-Host ("  ->  {0}  ({1} secret URI(s) updated)" -f (Get-RelativePath $file.FullName), $localHits)
        }
    }

    if ($hits -eq 0) {
        Write-Host "  =  no Key Vault secret URIs needed updating"
    }
}

function Ensure-Prerequisites {
    Write-Step "Checking Azure CLI prerequisites"
    Assert-Tool 'az'

    $account = Invoke-External 'az' @('account', 'show', '--only-show-errors', '-o', 'none')
    if (-not $account.Ok) {
        throw "Azure CLI is not logged in. Run 'az login' and retry.`n$($account.Output)"
    }
    Write-Host "  [OK] Azure CLI is installed and logged in."

    $extension = Invoke-External 'az' @('extension', 'show', '--name', 'load', '--only-show-errors', '-o', 'none')
    if ($extension.Ok) {
        Write-Host "  [OK] Azure CLI 'load' extension is installed."
        return
    }

    Write-Host "  Azure CLI 'load' extension is missing. Installing it now..."
    $install = Invoke-External 'az' @('extension', 'add', '--name', 'load', '--only-show-errors', '-o', 'none')
    if (-not $install.Ok) {
        throw "Could not install Azure CLI 'load' extension.`n$($install.Output)"
    }
    Write-Host "  [OK] Azure CLI 'load' extension installed."
}

function Read-TestConfigMetadata {
    param([Parameter(Mandatory = $true)][string]$Path)

    $text = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    $testId = [regex]::Match($text, '(?m)^\s*testId:\s*["'']?([^"''\s#]+)').Groups[1].Value
    if ([string]::IsNullOrWhiteSpace($testId)) {
        throw "No testId found in $(Get-RelativePath $Path)."
    }

    $testType = [regex]::Match($text, '(?m)^\s*testType:\s*["'']?([^"''\s#]+)').Groups[1].Value
    if ([string]::IsNullOrWhiteSpace($testType)) { $testType = 'unknown' }

    $engineInstances = [regex]::Match($text, '(?m)^\s*engineInstances:\s*["'']?([0-9]+)').Groups[1].Value
    if ([string]::IsNullOrWhiteSpace($engineInstances)) { $engineInstances = '1' }

    return [pscustomobject]@{
        Path            = $Path
        TestId          = $testId
        TestType        = $testType
        EngineInstances = $engineInstances
    }
}

function Test-AlreadyExists {
    param([string]$Output)
    return ($Output -match '(?i)already\s+exists|already\s+exist|conflict|409')
}

function Get-LoadTestDetails {
    param(
        [string]$LoadTestName,
        [string]$ResourceGroup,
        [string]$SubscriptionId,
        [string]$TestId
    )

    $args = @(
        'load', 'test', 'show',
        '--load-test-resource', $LoadTestName,
        '--resource-group', $ResourceGroup,
        '--test-id', $TestId,
        '--only-show-errors',
        '-o', 'json'
    )
    if ($SubscriptionId) { $args += @('--subscription', $SubscriptionId) }

    $result = Invoke-External 'az' $args
    if (-not $result.Ok) { return $null }
    return ($result.Output | ConvertFrom-Json)
}

function Get-ObjectPropertyValue {
    param(
        [object]$Object,
        [string]$Name
    )

    if ($null -eq $Object) { return $null }
    foreach ($property in $Object.PSObject.Properties) {
        if ($property.Name -eq $Name) { return $property.Value }
    }
    return $null
}

function Get-ValidationStatus {
    param([object]$Details)

    $statuses = [System.Collections.Generic.List[string]]::new()
    $artifacts = Get-ObjectPropertyValue $Details 'inputArtifacts'
    $testScript = Get-ObjectPropertyValue $artifacts 'testScriptFileInfo'
    $urlConfig = Get-ObjectPropertyValue $artifacts 'urlTestConfigFileInfo'

    $scriptStatus = Get-ObjectPropertyValue $testScript 'validationStatus'
    $urlStatus = Get-ObjectPropertyValue $urlConfig 'validationStatus'

    if ($scriptStatus) {
        $statuses.Add([string]$scriptStatus)
    }
    if ($urlStatus) {
        $statuses.Add([string]$urlStatus)
    }

    if ($statuses.Count -eq 0) { return 'UNKNOWN' }
    if ($statuses -contains 'VALIDATION_FAILURE') { return 'VALIDATION_FAILURE' }
    if (@($statuses | Where-Object { $_ -ne 'VALIDATION_SUCCESS' }).Count -eq 0) { return 'VALIDATION_SUCCESS' }
    return (($statuses | Sort-Object -Unique) -join ',')
}

function Get-ResourceInfo {
    param(
        [Parameter(Mandatory = $true)][string]$ResourceId,
        [string]$SubscriptionId
    )

    $args = @('resource', 'show', '--ids', $ResourceId, '--only-show-errors', '-o', 'json')
    if ($SubscriptionId) { $args += @('--subscription', $SubscriptionId) }

    $result = Invoke-External 'az' $args
    if (-not $result.Ok) {
        throw "Could not read Azure resource '$ResourceId'.`n$($result.Output)"
    }

    return ($result.Output | ConvertFrom-Json)
}

function Invoke-LoadTestList {
    param(
        [string]$Command,
        [string]$LoadTestName,
        [string]$ResourceGroup,
        [string]$SubscriptionId,
        [string]$TestId
    )

    $args = @(
        'load', 'test', $Command, 'list',
        '--load-test-resource', $LoadTestName,
        '--resource-group', $ResourceGroup,
        '--test-id', $TestId,
        '--only-show-errors',
        '-o', 'json'
    )
    if ($SubscriptionId) { $args += @('--subscription', $SubscriptionId) }

    $result = Invoke-External 'az' $args
    if ($result.Ok) { return $result.Output }
    if ($result.Output -match '(?i)notfound|not found|not.*config') { return '' }
    throw "Could not list $Command for test '$TestId'.`n$($result.Output)"
}

function Add-AppComponentIfMissing {
    param(
        [string]$TestId,
        [object]$Component,
        [string]$ExistingJson,
        [string]$LoadTestName,
        [string]$ResourceGroup,
        [string]$SubscriptionId
    )

    if ($ExistingJson.IndexOf($Component.id, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
        return $false
    }

    $args = @(
        'load', 'test', 'app-component', 'add',
        '--load-test-resource', $LoadTestName,
        '--resource-group', $ResourceGroup,
        '--test-id', $TestId,
        '--app-component-id', $Component.id,
        '--app-component-name', $Component.name,
        '--app-component-type', $Component.type,
        '--only-show-errors',
        '-o', 'none'
    )
    if ($Component.kind) { $args += @('--app-component-kind', $Component.kind) }
    if ($SubscriptionId) { $args += @('--subscription', $SubscriptionId) }

    $result = Invoke-External 'az' $args
    if (-not $result.Ok -and -not (Test-AlreadyExists $result.Output)) {
        throw "Could not add app component '$($Component.id)' to test '$TestId'.`n$($result.Output)"
    }
    return $true
}

function Add-ServerMetricIfMissing {
    param(
        [string]$TestId,
        [object]$Component,
        [string]$MetricName,
        [string]$MetricNamespace,
        [string]$Aggregation,
        [string]$ExistingJson,
        [string]$LoadTestName,
        [string]$ResourceGroup,
        [string]$SubscriptionId
    )

    $metricIdName = [System.Uri]::EscapeDataString($MetricName)
    $rawMetricId = "$($Component.id)/providers/microsoft.insights/metricdefinitions/$MetricName"
    $metricId = "$($Component.id)/providers/microsoft.insights/metricdefinitions/$metricIdName"
    $hasMetric = (
        $ExistingJson.IndexOf($metricId, [StringComparison]::OrdinalIgnoreCase) -ge 0 -or
        $ExistingJson.IndexOf($rawMetricId, [StringComparison]::OrdinalIgnoreCase) -ge 0
    )
    if ($hasMetric) { return $false }

    $args = @(
        'load', 'test', 'server-metric', 'add',
        '--load-test-resource', $LoadTestName,
        '--resource-group', $ResourceGroup,
        '--test-id', $TestId,
        '--app-component-id', $Component.id,
        '--app-component-type', $Component.type,
        '--metric-id', $metricId,
        '--metric-name', $MetricName,
        '--metric-namespace', $MetricNamespace,
        '--aggregation', $Aggregation,
        '--only-show-errors',
        '-o', 'none'
    )
    if ($SubscriptionId) { $args += @('--subscription', $SubscriptionId) }

    $result = Invoke-External 'az' $args
    if (-not $result.Ok -and -not (Test-AlreadyExists $result.Output)) {
        if ($MetricName.Contains('/') -and $result.Output -match 'metric-id is not a valid Azure Resource ID') {
            Write-Warning "  Skipping server metric '$MetricName' for test '$TestId': the current Azure CLI load extension rejects this Azure Monitor metric definition ID."
            return $false
        }
        throw "Could not add server metric '$MetricName' to test '$TestId'.`n$($result.Output)"
    }
    return $true
}

function Enable-ServerSideMonitoring {
    param(
        [string]$LoadTestName,
        [string]$ResourceGroup,
        [string]$SubscriptionId,
        [string]$ServiceApiResourceId,
        [string]$ApplicationInsightsResourceId,
        [string]$AppServicePlanResourceId
    )

    Write-Step "Configuring server-side metrics for diagnostic tests"

    $components = @{
        AppService          = Get-ResourceInfo -ResourceId $ServiceApiResourceId -SubscriptionId $SubscriptionId
        ApplicationInsights = Get-ResourceInfo -ResourceId $ApplicationInsightsResourceId -SubscriptionId $SubscriptionId
        AppServicePlan      = Get-ResourceInfo -ResourceId $AppServicePlanResourceId -SubscriptionId $SubscriptionId
    }

    $targetTests = @(
        'contoso-bank-peak-load',
        'contoso-bank-peak-load-locust',
        'contoso-bank-pr-gate'
    )

    $metrics = @(
        [pscustomobject]@{ Component = 'AppServicePlan';      Name = 'CpuPercentage';                         Namespace = 'Microsoft.Web/serverfarms';     Aggregation = 'Average' },
        [pscustomobject]@{ Component = 'AppServicePlan';      Name = 'MemoryPercentage';                      Namespace = 'Microsoft.Web/serverfarms';     Aggregation = 'Average' },
        [pscustomobject]@{ Component = 'AppServicePlan';      Name = 'HttpQueueLength';                       Namespace = 'Microsoft.Web/serverfarms';     Aggregation = 'Maximum' },
        [pscustomobject]@{ Component = 'AppService';          Name = 'HttpResponseTime';                      Namespace = 'Microsoft.Web/sites';           Aggregation = 'Average' },
        [pscustomobject]@{ Component = 'AppService';          Name = 'Requests';                              Namespace = 'Microsoft.Web/sites';           Aggregation = 'Total' },
        [pscustomobject]@{ Component = 'AppService';          Name = 'Http5xx';                               Namespace = 'Microsoft.Web/sites';           Aggregation = 'Total' },
        [pscustomobject]@{ Component = 'AppService';          Name = 'MemoryWorkingSet';                      Namespace = 'Microsoft.Web/sites';           Aggregation = 'Average' },
        [pscustomobject]@{ Component = 'AppService';          Name = 'InstanceCount';                         Namespace = 'Microsoft.Web/sites';           Aggregation = 'Average' },
        [pscustomobject]@{ Component = 'ApplicationInsights'; Name = 'requests/duration';                     Namespace = 'microsoft.insights/components'; Aggregation = 'Average' },
        [pscustomobject]@{ Component = 'ApplicationInsights'; Name = 'requests/count';                        Namespace = 'microsoft.insights/components'; Aggregation = 'Count' },
        [pscustomobject]@{ Component = 'ApplicationInsights'; Name = 'requests/failed';                       Namespace = 'microsoft.insights/components'; Aggregation = 'Count' },
        [pscustomobject]@{ Component = 'ApplicationInsights'; Name = 'requests/rate';                         Namespace = 'microsoft.insights/components'; Aggregation = 'Average' },
        [pscustomobject]@{ Component = 'ApplicationInsights'; Name = 'performanceCounters/requestsInQueue';   Namespace = 'microsoft.insights/components'; Aggregation = 'Maximum' },
        [pscustomobject]@{ Component = 'ApplicationInsights'; Name = 'performanceCounters/processCpuPercentage'; Namespace = 'microsoft.insights/components'; Aggregation = 'Average' },
        [pscustomobject]@{ Component = 'ApplicationInsights'; Name = 'performanceCounters/processPrivateBytes';  Namespace = 'microsoft.insights/components'; Aggregation = 'Average' }
    )

    foreach ($testId in $targetTests) {
        Write-Host "  Ensuring App Components and metrics on '$testId'..."
        $existingComponents = Invoke-LoadTestList -Command 'app-component' -LoadTestName $LoadTestName -ResourceGroup $ResourceGroup -SubscriptionId $SubscriptionId -TestId $testId
        $componentsAdded = 0
        foreach ($component in $components.Values) {
            if (Add-AppComponentIfMissing -TestId $testId -Component $component -ExistingJson $existingComponents -LoadTestName $LoadTestName -ResourceGroup $ResourceGroup -SubscriptionId $SubscriptionId) {
                $componentsAdded++
            }
        }

        $existingMetrics = Invoke-LoadTestList -Command 'server-metric' -LoadTestName $LoadTestName -ResourceGroup $ResourceGroup -SubscriptionId $SubscriptionId -TestId $testId
        $metricsAdded = 0
        foreach ($metric in $metrics) {
            $component = $components[$metric.Component]
            if (Add-ServerMetricIfMissing -TestId $testId -Component $component -MetricName $metric.Name -MetricNamespace $metric.Namespace -Aggregation $metric.Aggregation -ExistingJson $existingMetrics -LoadTestName $LoadTestName -ResourceGroup $ResourceGroup -SubscriptionId $SubscriptionId) {
                $metricsAdded++
            }
        }

        Write-Host "    [OK] App Components present ($componentsAdded added); server metrics present ($metricsAdded added)."
    }
}

function New-OrUpdate-LoadTest {
    param(
        [object]$Config,
        [string]$LoadTestName,
        [string]$ResourceGroup,
        [string]$SubscriptionId
    )

    $common = @(
        '--load-test-resource', $LoadTestName,
        '--resource-group', $ResourceGroup,
        '--test-id', $Config.TestId,
        '--load-test-config-file', $Config.Path,
        '--only-show-errors',
        '-o', 'none'
    )
    if ($SubscriptionId) { $common += @('--subscription', $SubscriptionId) }

    Write-Host "  Creating $(Get-RelativePath $Config.Path) as '$($Config.TestId)'..."
    $create = Invoke-External 'az' (@('load', 'test', 'create') + $common)
    $operation = 'created'

    if (-not $create.Ok) {
        $exists = Test-AlreadyExists $create.Output
        if (-not $exists) {
            $details = Get-LoadTestDetails -LoadTestName $LoadTestName -ResourceGroup $ResourceGroup -SubscriptionId $SubscriptionId -TestId $Config.TestId
            $exists = ($null -ne $details)
        }

        if (-not $exists) {
            throw "Failed to create test '$($Config.TestId)'.`n$($create.Output)"
        }

        Write-Host "    Test already exists; updating it instead."
        $update = Invoke-External 'az' (@('load', 'test', 'update') + $common)
        if (-not $update.Ok) {
            throw "Failed to update existing test '$($Config.TestId)'.`n$($update.Output)"
        }
        $operation = 'updated'
    }

    $detailsAfter = $null
    for ($attempt = 1; $attempt -le 6; $attempt++) {
        $detailsAfter = Get-LoadTestDetails -LoadTestName $LoadTestName -ResourceGroup $ResourceGroup -SubscriptionId $SubscriptionId -TestId $Config.TestId
        if ($null -ne $detailsAfter) {
            $status = Get-ValidationStatus $detailsAfter
            if ($status -notmatch 'VALIDATION_INITIATED|VALIDATION_NOT_STARTED|UNKNOWN') { break }
        }
        Start-Sleep -Seconds 2
    }

    if ($null -eq $detailsAfter) {
        throw "Test '$($Config.TestId)' was $operation but could not be read back."
    }

    $engineCount = $Config.EngineInstances
    $loadTestConfiguration = Get-ObjectPropertyValue $detailsAfter 'loadTestConfiguration'
    $engineInstances = Get-ObjectPropertyValue $loadTestConfiguration 'engineInstances'
    if ($engineInstances) {
        $engineCount = [string]$engineInstances
    }

    $validation = Get-ValidationStatus $detailsAfter
    $script:Summary.Add([pscustomobject]@{
        'Test id'           = $Config.TestId
        'Test type'         = $Config.TestType
        'Engines'           = $engineCount
        'Validation status' = $validation
    })

    if ($validation -ne 'VALIDATION_SUCCESS') {
        throw "Test '$($Config.TestId)' reported validation status '$validation'."
    }
}

function Set-DemoSecret {
    param(
        [string]$KeyVaultName,
        [string]$SubscriptionId
    )

    if ([string]::IsNullOrWhiteSpace($KeyVaultName)) {
        Write-Host "  Key Vault not present in this environment; skipping demo secret seed."
        return
    }

    $showArgs = @('keyvault', 'show', '--name', $KeyVaultName, '--only-show-errors', '-o', 'none')
    if ($SubscriptionId) { $showArgs += @('--subscription', $SubscriptionId) }
    $show = Invoke-External 'az' $showArgs
    if (-not $show.Ok) {
        Write-Warning "  Key Vault output is set, but the vault was not found or is not visible yet. Skipping optional secret seed.`n$($show.Output)"
        return
    }

    Write-Host "  Seeding Key Vault secret 'demo-api-key' in '$KeyVaultName'..."
    $args = @(
        'keyvault', 'secret', 'set',
        '--vault-name', $KeyVaultName,
        '--name', 'demo-api-key',
        '--value', 'contoso-demo-api-key',
        '--only-show-errors',
        '-o', 'none'
    )
    if ($SubscriptionId) { $args += @('--subscription', $SubscriptionId) }

    for ($attempt = 1; $attempt -le 5; $attempt++) {
        $result = Invoke-External 'az' $args
        if ($result.Ok) {
            Write-Host "  [OK] Key Vault demo secret is present."
            return
        }

        # Distinguish a genuinely transient RBAC propagation delay from a permanent
        # network block. Retrying a network block just burns 45 seconds and then
        # reports the wrong cause, which sends people hunting for a role assignment
        # that is already correct.
        if ($result.Output -match '(?i)public network access is disabled|ForbiddenByConnection|not from a trusted service') {
            Write-Warning "  Key Vault '$KeyVaultName' has publicNetworkAccess=Disabled, so its data plane is unreachable from this machine."
            Write-Host    "    This is an Azure Policy 'modify' effect in this subscription, not a misconfiguration in this repo:"
            Write-Host    "    the vault is created with public access enabled and the platform silently overrides it."
            Write-Host    "    Consequence: 'contoso-bank-secrets-kv' will validate at create time but FAIL at run time."
            Write-Host    "    That test is kept deliberately as the governance cautionary tale - see docs/11 and docs/20."
            Write-Host    "    For a secret that actually resolves, use the CI pipeline-secret path in docs/08."
            return
        }

        if ($result.Output -match '(?i)403|forbidden|authorization') {
            Write-Warning "  Key Vault RBAC is not ready yet (attempt $attempt/5). Retrying shortly..."
            Start-Sleep -Seconds (3 * $attempt)
            continue
        }

        Write-Warning "  Could not seed the optional Key Vault demo secret. The load-test definitions still continue.`n$($result.Output)"
        return
    }

    Write-Warning "  Key Vault RBAC did not become ready in time. Re-run this hook later to seed 'demo-api-key'."
}

try {
    $resourceGroup = Get-HookValue 'AZURE_RESOURCE_GROUP' -Required
    $subscriptionId = Get-HookValue 'AZURE_SUBSCRIPTION_ID'
    $serviceHostName = Get-HookValue 'SERVICE_API_HOSTNAME' -Required
    $loadTestName = Get-HookValue 'LOAD_TEST_NAME' -Required
    $loadTestResourceId = Get-HookValue 'LOAD_TEST_RESOURCE_ID'
    $keyVaultName = Get-HookValue 'KEY_VAULT_NAME'
    $serviceApiResourceId = Get-HookValue 'SERVICE_API_RESOURCE_ID' -Required
    $applicationInsightsResourceId = Get-HookValue 'APPLICATIONINSIGHTS_RESOURCE_ID' -Required
    $appServicePlanResourceId = Get-HookValue 'APP_SERVICE_PLAN_RESOURCE_ID' -Required

    if ([string]::IsNullOrWhiteSpace($loadTestResourceId) -and -not [string]::IsNullOrWhiteSpace($subscriptionId)) {
        $loadTestResourceId = "/subscriptions/$subscriptionId/resourceGroups/$resourceGroup/providers/Microsoft.LoadTestService/loadTests/$loadTestName"
    }

    Ensure-Prerequisites
    Retarget-LoadTestArtifacts -ServiceHostName $serviceHostName
    if (-not [string]::IsNullOrWhiteSpace($keyVaultName)) {
        Update-KeyVaultReferences -KeyVaultName $keyVaultName
    }

    Write-Step "Seeding optional Key Vault demo secret"
    Set-DemoSecret -KeyVaultName $keyVaultName -SubscriptionId $subscriptionId

    Write-Step "Creating or updating Azure Load Testing definitions"
    $configs = Get-ChildItem -Path (Join-Path $script:RepoRoot 'loadtests\config') -File -Filter '*.yaml' | Sort-Object Name
    if ($configs.Count -eq 0) {
        throw "No load test YAML configs found under loadtests/config."
    }

    foreach ($file in $configs) {
        $metadata = Read-TestConfigMetadata -Path $file.FullName
        New-OrUpdate-LoadTest -Config $metadata -LoadTestName $loadTestName -ResourceGroup $resourceGroup -SubscriptionId $subscriptionId
    }

    Enable-ServerSideMonitoring `
        -LoadTestName $loadTestName `
        -ResourceGroup $resourceGroup `
        -SubscriptionId $subscriptionId `
        -ServiceApiResourceId $serviceApiResourceId `
        -ApplicationInsightsResourceId $applicationInsightsResourceId `
        -AppServicePlanResourceId $appServicePlanResourceId

    Write-Step "Provisioned load test definitions"
    $script:Summary | Format-Table -AutoSize

    if ($loadTestResourceId) {
        Write-Host "Portal: https://portal.azure.com/#resource$loadTestResourceId/tests"
    }
    Write-Host "No load tests were run. Start a run explicitly when you are ready to spend VUH."
}
catch {
    Write-Host ""
    Write-Host "Failed to provision Azure Load Testing definitions." -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}

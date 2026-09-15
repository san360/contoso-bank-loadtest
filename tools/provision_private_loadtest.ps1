<#
.SYNOPSIS
    Creates the VNet-injected Contoso Bank load test against a subnet you already own.

.DESCRIPTION
    `azd up` creates every definition in loadtests/config/ automatically. It cannot
    create the private one, because that needs a subnet resource ID specific to the
    environment, which must not be hard-coded in source control. This script is that
    missing step.

    Before it creates anything it pre-flights the subnet against the documented
    requirements, because every one of these fails at run time - on stage - rather
    than at create time:

      - subnet must NOT be delegated to another service (Container Apps, ASE, DNS
        resolvers and similar delegate their subnets and therefore cannot be used)
      - subnet must be in the same subscription and region as the load test resource
      - subnet must not be named AzureFirewallSubnet
      - subnet needs enough free IPv4 addresses for the engine count
      - Microsoft.Batch must be registered in the subscription
      - any attached route table must still permit outbound access
      - the caller needs subnet join/action (Network Contributor or a parent role)

.PARAMETER SubnetId
    Full resource ID of the subnet the engines are injected into. Defaults to the
    LOAD_TEST_SUBNET_ID environment variable or the azd environment.

.PARAMETER TestId
    Test id to create or update. Defaults to contoso-bank-private-vnet.

.PARAMETER ConfigFile
    Load test YAML to create it from. Defaults to loadtests/config-private/private-vnet.yaml.

.PARAMETER DisablePublicIP
    Pass --disable-public-ip, so Azure Load Testing does NOT deploy a public IP,
    load balancer or NSG into your subnet. You must then provide your own outbound
    route (NAT Gateway, Azure Firewall or an NVA) or the run will fail: the engines
    still need outbound access to the Azure Load Testing service.

.PARAMETER SkipPreflight
    Skip the subnet checks and go straight to creating the test.

.EXAMPLE
    pwsh -File tools/provision_private_loadtest.ps1 -SubnetId "/subscriptions/.../subnets/snet-loadtest"

.EXAMPLE
    pwsh -File tools/provision_private_loadtest.ps1 -DisablePublicIP

.NOTES
    Exit codes: 0 = test created or updated, 1 = pre-flight failed or the create failed.
#>

[CmdletBinding()]
param(
    [string]$SubnetId,
    [string]$TestId = 'contoso-bank-private-vnet',
    [string]$ConfigFile = 'loadtests/config-private/private-vnet.yaml',
    [switch]$DisablePublicIP,
    [switch]$SkipPreflight,
    [string]$LoadTestName,
    [string]$ResourceGroup,
    [string]$SubscriptionId
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$global:LASTEXITCODE = 0

$script:RepoRoot = Split-Path -Parent $PSScriptRoot
$script:AzdValues = $null
$script:Problems = [System.Collections.Generic.List[string]]::new()

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host $Message -ForegroundColor Cyan
}

function Write-Check {
    param([string]$Name, [bool]$Ok, [string]$Detail, [switch]$WarnOnly)

    if ($Ok) {
        Write-Host ("  [OK]   {0,-34} {1}" -f $Name, $Detail) -ForegroundColor Green
        return
    }
    if ($WarnOnly) {
        Write-Host ("  [WARN] {0,-34} {1}" -f $Name, $Detail) -ForegroundColor Yellow
        return
    }
    Write-Host ("  [FAIL] {0,-34} {1}" -f $Name, $Detail) -ForegroundColor Red
    $script:Problems.Add("$Name - $Detail")
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
    param([string]$Name, [string]$Override, [switch]$Required)

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

function Get-SubnetParts {
    param([Parameter(Mandatory = $true)][string]$Id)

    $pattern = '^/subscriptions/(?<sub>[^/]+)/resourceGroups/(?<rg>[^/]+)/providers/Microsoft\.Network/virtualNetworks/(?<vnet>[^/]+)/subnets/(?<subnet>[^/]+)$'
    $match = [regex]::Match($Id.Trim(), $pattern, 'IgnoreCase')
    if (-not $match.Success) {
        throw "'$Id' is not a subnet resource ID. Expected /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Network/virtualNetworks/<vnet>/subnets/<subnet>."
    }

    return [pscustomobject]@{
        SubscriptionId = $match.Groups['sub'].Value
        ResourceGroup  = $match.Groups['rg'].Value
        VNet           = $match.Groups['vnet'].Value
        Subnet         = $match.Groups['subnet'].Value
    }
}

function Get-EngineInstances {
    param([string]$Path)

    $text = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    $value = [regex]::Match($text, '(?m)^\s*engineInstances:\s*["'']?([0-9]+)').Groups[1].Value
    if ([string]::IsNullOrWhiteSpace($value)) { return 1 }
    return [int]$value
}

function Get-UsableAddressCount {
    param([string]$Prefix)

    # Azure reserves five addresses in every subnet: network, broadcast, gateway
    # and two DNS. A /29 is the smallest supported IPv4 subnet.
    $bits = [int]($Prefix -split '/')[1]
    if ($bits -lt 0 -or $bits -gt 32) { return 0 }
    return [math]::Max(0, [math]::Pow(2, 32 - $bits) - 5)
}

function Test-SubnetPrerequisites {
    param(
        [Parameter(Mandatory = $true)][object]$Parts,
        [Parameter(Mandatory = $true)][int]$EngineInstances,
        [Parameter(Mandatory = $true)][string]$LoadTestLocation
    )

    Write-Step "Pre-flighting subnet '$($Parts.Subnet)' in '$($Parts.VNet)'"

    $cliArgs = @('network', 'vnet', 'subnet', 'show', '-g', $Parts.ResourceGroup, '--vnet-name', $Parts.VNet, '-n', $Parts.Subnet, '--subscription', $Parts.SubscriptionId, '--only-show-errors', '-o', 'json')
    $result = Invoke-External 'az' $cliArgs
    if (-not $result.Ok) {
        Write-Check 'subnet is readable' $false "cannot read the subnet - check the ID and your permissions"
        return
    }

    $subnet = $result.Output | ConvertFrom-Json

    # 1. Delegation is the single most common blocker: a Container Apps, ASE or DNS
    #    resolver subnet is delegated and can never host load test engines.
    $delegations = @(Get-ObjectPropertyValue $subnet 'delegations')
    $delegationNames = @($delegations | ForEach-Object { Get-ObjectPropertyValue $_ 'serviceName' } | Where-Object { $_ })
    Write-Check 'not delegated to a service' ($delegationNames.Count -eq 0) $(
        if ($delegationNames.Count -eq 0) { 'no delegations' } else { "delegated to $($delegationNames -join ', ') - pick a different subnet" }
    )

    # 2. Reserved subnet name.
    Write-Check 'subnet name is usable' ($Parts.Subnet -ne 'AzureFirewallSubnet') $(
        if ($Parts.Subnet -eq 'AzureFirewallSubnet') { 'AzureFirewallSubnet cannot be used' } else { $Parts.Subnet }
    )

    # 3. Capacity. Budget more than one address per engine: the injected load
    #    balancer and platform plumbing consume addresses too.
    $prefix = [string](Get-ObjectPropertyValue $subnet 'addressPrefix')
    if ([string]::IsNullOrWhiteSpace($prefix)) {
        $prefixes = @(Get-ObjectPropertyValue $subnet 'addressPrefixes')
        if ($prefixes.Count -gt 0) { $prefix = [string]$prefixes[0] }
    }
    $usable = Get-UsableAddressCount -Prefix $prefix
    $inUse = @(Get-ObjectPropertyValue $subnet 'ipConfigurations').Count
    $free = $usable - $inUse
    Write-Check 'enough free IPv4 addresses' ($free -ge ($EngineInstances + 5)) "$prefix -> $usable usable, $inUse in use, $free free (need >= $EngineInstances engines plus headroom)"

    # 4. Region and subscription must match the load testing resource.
    $vnetArgs = @('network', 'vnet', 'show', '-g', $Parts.ResourceGroup, '-n', $Parts.VNet, '--subscription', $Parts.SubscriptionId, '--only-show-errors', '-o', 'json')
    $vnetResult = Invoke-External 'az' $vnetArgs
    if ($vnetResult.Ok) {
        $vnet = $vnetResult.Output | ConvertFrom-Json
        $vnetLocation = [string](Get-ObjectPropertyValue $vnet 'location')
        Write-Check 'same region as load test resource' ($vnetLocation -eq $LoadTestLocation) "vnet=$vnetLocation, loadtest=$LoadTestLocation"
    }
    Write-Check 'same subscription' ($Parts.SubscriptionId -eq $script:SubscriptionId) "subnet=$($Parts.SubscriptionId)"

    # 5. Outbound connectivity. Engines must reach the Azure Load Testing service.
    $nsg = Get-ObjectPropertyValue $subnet 'networkSecurityGroup'
    if ($nsg) {
        $nsgName = ([string](Get-ObjectPropertyValue $nsg 'id') -split '/')[-1]
        Write-Check 'NSG attached' $true "$nsgName - confirm it allows the engines' outbound traffic" -WarnOnly
    }
    else {
        Write-Check 'NSG attached' $true 'none - default outbound applies'
    }

    $routeTable = Get-ObjectPropertyValue $subnet 'routeTable'
    if ($routeTable) {
        $routeName = ([string](Get-ObjectPropertyValue $routeTable 'id') -split '/')[-1]
        Write-Check 'route table' $true "$routeName - ensure it still permits outbound to Azure Load Testing" -WarnOnly
    }
    else {
        Write-Check 'route table' $true 'none attached'
    }

    # 6. Microsoft.Batch powers the injected engine fleet.
    $providerArgs = @('provider', 'show', '-n', 'Microsoft.Batch', '--subscription', $Parts.SubscriptionId, '--query', 'registrationState', '--only-show-errors', '-o', 'tsv')
    $provider = Invoke-External 'az' $providerArgs
    $registered = $provider.Ok -and $provider.Output.Trim() -eq 'Registered'
    Write-Check 'Microsoft.Batch registered' $registered $(
        if ($registered) { 'Registered' } else { "register it: az provider register -n Microsoft.Batch" }
    )

    if ($DisablePublicIP) {
        Write-Check 'alternative egress required' $true '-DisablePublicIP set: engines get NO public IP, load balancer or NSG. Provide NAT Gateway / Firewall / NVA egress or the run WILL fail' -WarnOnly
    }
}

try {
    Assert-Tool 'az'

    $account = Invoke-External 'az' @('account', 'show', '--only-show-errors', '-o', 'none')
    if (-not $account.Ok) { throw "Azure CLI is not logged in. Run 'az login' and retry." }

    $extension = Invoke-External 'az' @('extension', 'show', '--name', 'load', '--only-show-errors', '-o', 'none')
    if (-not $extension.Ok) {
        Write-Host "Installing the Azure CLI 'load' extension..."
        $install = Invoke-External 'az' @('extension', 'add', '--name', 'load', '--only-show-errors', '-o', 'none')
        if (-not $install.Ok) { throw "Could not install Azure CLI 'load' extension.`n$($install.Output)" }
    }

    $script:LoadTestName = Get-EnvironmentValue 'LOAD_TEST_NAME' -Override $LoadTestName -Required
    $script:ResourceGroup = Get-EnvironmentValue 'AZURE_RESOURCE_GROUP' -Override $ResourceGroup -Required
    $script:SubscriptionId = Get-EnvironmentValue 'AZURE_SUBSCRIPTION_ID' -Override $SubscriptionId
    $resolvedSubnet = Get-EnvironmentValue 'LOAD_TEST_SUBNET_ID' -Override $SubnetId -Required

    $configPath = Join-Path $script:RepoRoot $ConfigFile
    if (-not (Test-Path -LiteralPath $configPath)) {
        throw "Load test config '$ConfigFile' was not found."
    }

    $parts = Get-SubnetParts -Id $resolvedSubnet
    $engineInstances = Get-EngineInstances -Path $configPath

    $loadTestArgs = @('load', 'show', '--name', $script:LoadTestName, '--resource-group', $script:ResourceGroup, '--only-show-errors', '-o', 'json')
    if ($script:SubscriptionId) { $loadTestArgs += @('--subscription', $script:SubscriptionId) }
    $loadTestResult = Invoke-External 'az' $loadTestArgs
    if (-not $loadTestResult.Ok) { throw "Could not read load testing resource '$($script:LoadTestName)'.`n$($loadTestResult.Output)" }
    $loadTestLocation = [string](Get-ObjectPropertyValue ($loadTestResult.Output | ConvertFrom-Json) 'location')

    if (-not $SkipPreflight) {
        Test-SubnetPrerequisites -Parts $parts -EngineInstances $engineInstances -LoadTestLocation $loadTestLocation

        if ($script:Problems.Count -gt 0) {
            Write-Host ""
            Write-Host "Pre-flight failed. Fix these before creating the private test:" -ForegroundColor Red
            foreach ($problem in $script:Problems) { Write-Host "  - $problem" -ForegroundColor Red }
            Write-Host ""
            Write-Host "Re-run with -SkipPreflight to create it anyway (the run will probably fail)." -ForegroundColor DarkGray
            exit 1
        }
    }

    Write-Step "Creating private test '$TestId' from $ConfigFile"

    $createArgs = @(
        'load', 'test', 'create',
        '--load-test-resource', $script:LoadTestName,
        '--resource-group', $script:ResourceGroup,
        '--test-id', $TestId,
        '--load-test-config-file', $ConfigFile,
        '--subnet-id', $resolvedSubnet,
        '--only-show-errors', '-o', 'none'
    )
    if ($script:SubscriptionId) { $createArgs += @('--subscription', $script:SubscriptionId) }
    if ($DisablePublicIP) { $createArgs += '--disable-public-ip' }

    $create = Invoke-External 'az' $createArgs
    if (-not $create.Ok) {
        if ($create.Output -match '(?i)already\s+exists|conflict|409') {
            Write-Host "  Test already exists; updating it instead."
            $updateArgs = @($createArgs | ForEach-Object { if ($_ -eq 'create') { 'update' } else { $_ } })
            $update = Invoke-External 'az' $updateArgs
            if (-not $update.Ok) { throw "Could not update the private test.`n$($update.Output)" }
        }
        else {
            # ALTVNET004 means the caller lacks subnet join/action.
            if ($create.Output -match 'ALTVNET004') {
                throw "Azure rejected the subnet join (ALTVNET004). Grant Network Contributor on the VNet to the principal creating this test.`n$($create.Output)"
            }
            throw "Could not create the private test.`n$($create.Output)"
        }
    }

    Write-Host "  [OK] Private test is ready."

    Write-Step "Next steps"
    Write-Host "  1. Run it:      pwsh -File tools/run_loadtest.ps1 -TestId $TestId"
    Write-Host "  2. Run the public equivalent for contrast: pwsh -File tools/run_loadtest.ps1 -TestId contoso-bank-url-smoke"
    Write-Host "  3. Compare:     pwsh -File tools/compare_runs.ps1 -RunIds <private-run>,<public-run>"
    Write-Host ""
    Write-Host "  Until the target's public access is disabled, BOTH tests pass. Injection on its" -ForegroundColor DarkGray
    Write-Host "  own proves the engines are in your network - it does not prove the app is private." -ForegroundColor DarkGray
    Write-Host "  See docs/23 section 23.11 for the full arc." -ForegroundColor DarkGray
    exit 0
}
catch {
    Write-Host ""
    Write-Host "Failed to provision the private load test." -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}

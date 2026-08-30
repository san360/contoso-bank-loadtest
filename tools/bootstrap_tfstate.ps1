<#
.SYNOPSIS
    Bootstraps the Azure Storage backend used by azd + Terraform.

.DESCRIPTION
    Terraform cannot create the storage account that stores its own state. Run
    this once before `azd up` when you want remote state for CI/CD or team demos.
    The script is idempotent: it creates or reuses the resource group, storage
    account and blob container, then prints the azd env commands to persist the
    backend settings.
#>

[CmdletBinding()]
param(
    [string]$EnvironmentName = $(if ($env:AZURE_ENV_NAME) { $env:AZURE_ENV_NAME } else { 'loadtest' }),
    [string]$Location = $(if ($env:AZURE_LOCATION) { $env:AZURE_LOCATION } else { 'swedencentral' }),
    [string]$ResourceGroupName = $(if ($env:RS_RESOURCE_GROUP) { $env:RS_RESOURCE_GROUP } else { "rg-$EnvironmentName-tfstate" }),
    [string]$ContainerName = $(if ($env:RS_CONTAINER_NAME) { $env:RS_CONTAINER_NAME } else { 'tfstate' }),
    [string]$StorageAccountName = $null,
    [string]$SubscriptionId = $(if ($env:AZURE_SUBSCRIPTION_ID) { $env:AZURE_SUBSCRIPTION_ID } else { '' }),
    [string]$TenantId = $(if ($env:AZURE_TENANT_ID) { $env:AZURE_TENANT_ID } else { '' }),
    [string]$PrincipalId = '',
    [ValidateSet('User', 'ServicePrincipal', 'Group')]
    [string]$PrincipalType = 'User'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Invoke-AzJson {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $output = & az @Arguments --only-show-errors -o json 2>&1
    if ($LASTEXITCODE -ne 0) { throw ($output | Out-String) }
    if ([string]::IsNullOrWhiteSpace(($output | Out-String))) { return $null }
    return ($output | ConvertFrom-Json)
}

function Invoke-AzNone {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $output = & az @Arguments --only-show-errors -o none 2>&1
    if ($LASTEXITCODE -ne 0) { throw ($output | Out-String) }
}

function Get-StateStorageAccountName {
    param([string]$NameSeed, [string]$SubscriptionSeed, [string]$LocationSeed)

    $normalized = ($NameSeed.ToLowerInvariant() -replace '[^a-z0-9]', '')
    if ($normalized.Length -eq 0) { $normalized = 'azd' }
    if ($normalized.Length -gt 12) { $normalized = $normalized.Substring(0, 12) }

    $bytes = [System.Text.Encoding]::UTF8.GetBytes("$SubscriptionSeed-$NameSeed-$LocationSeed")
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $hashBytes = $sha.ComputeHash($bytes)
    }
    finally {
        $sha.Dispose()
    }
    $hash = -join ($hashBytes | ForEach-Object { $_.ToString('x2') })

    $name = "st$normalized$($hash.Substring(0, 10))"
    if ($name.Length -lt 3 -or $name.Length -gt 24 -or $name -notmatch '^[a-z0-9]+$') {
        throw "Generated storage account name '$name' violates Azure naming rules. Pass -StorageAccountName explicitly."
    }
    return $name
}

if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    throw "Azure CLI is not on PATH. Install it, then run: az login --tenant <tenant-id>"
}

try {
    Invoke-AzJson @('account', 'show') | Out-Null
}
catch {
    $loginHint = if ($TenantId) { "az login --tenant $TenantId" } else { 'az login --tenant <tenant-id>' }
    throw "Azure CLI is not logged in. Run '$loginHint' first."
}

if (-not $SubscriptionId) {
    $SubscriptionId = (Invoke-AzJson @('account', 'show')).id
}
Invoke-AzNone @('account', 'set', '--subscription', $SubscriptionId)

if (-not $StorageAccountName) {
    $StorageAccountName = Get-StateStorageAccountName -NameSeed $EnvironmentName -SubscriptionSeed $SubscriptionId -LocationSeed $Location
}
if ($StorageAccountName -notmatch '^[a-z0-9]{3,24}$') {
    throw "Storage account name '$StorageAccountName' must be 3-24 lowercase letters or digits."
}
if ($ContainerName -notmatch '^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?$') {
    throw "Container name '$ContainerName' must be 3-63 lowercase letters, digits or hyphens, and cannot start/end with a hyphen."
}

Write-Host "Creating or reusing Terraform state resource group '$ResourceGroupName' in '$Location'..."
Invoke-AzNone @('group', 'create', '--name', $ResourceGroupName, '--location', $Location)

Write-Host "Creating or reusing storage account '$StorageAccountName'..."
# Shared-key auth is deliberately disabled from the start.
#
# The obvious bootstrap trick - create the account with keys enabled, use a key
# to create the container, then turn keys off - does not survive a subscription
# that carries an Azure Policy forbidding key-based authentication on storage.
# The policy wins silently, and `az storage container create --account-key ...`
# fails with KeyBasedAuthenticationNotPermitted regardless of what was asked for.
#
# Creating the account key-less and using Entra ID for the data plane works with
# or without that policy, and it matches use_azuread_auth = true in
# infra/provider.conf.json, so Terraform authenticates the same way afterwards.
Invoke-AzNone @(
    'storage', 'account', 'create',
    '--name', $StorageAccountName,
    '--resource-group', $ResourceGroupName,
    '--location', $Location,
    '--sku', 'Standard_LRS',
    '--kind', 'StorageV2',
    '--min-tls-version', 'TLS1_2',
    '--allow-blob-public-access', 'false',
    '--allow-shared-key-access', 'false'
)

Invoke-AzNone @('storage', 'account', 'blob-service-properties', 'update', '--account-name', $StorageAccountName, '--resource-group', $ResourceGroupName, '--enable-versioning', 'true')

if (-not $PrincipalId) {
    $PrincipalId = (& az ad signed-in-user show --query id -o tsv --only-show-errors 2>$null)
}

$scope = "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroupName/providers/Microsoft.Storage/storageAccounts/$StorageAccountName"
if ($PrincipalId) {
    Write-Host "Ensuring Storage Blob Data Contributor for '$PrincipalId' on the state account..."
    $assignment = & az role assignment create --assignee-object-id $PrincipalId --assignee-principal-type $PrincipalType --role 'Storage Blob Data Contributor' --scope $scope --only-show-errors -o none 2>&1
    if ($LASTEXITCODE -ne 0 -and (($assignment | Out-String) -notmatch '(?i)already exists|RoleAssignmentExists')) {
        Write-Warning "Could not create the data-plane role assignment. Add Storage Blob Data Contributor manually before terraform init.`n$assignment"
    }
}
else {
    Write-Warning "Could not resolve the signed-in principal. Assign Storage Blob Data Contributor on the state account manually before running azd up."
}

# Data-plane RBAC is eventually consistent. The role assignment above routinely
# needs 30-60 seconds before the blob endpoint honours it, so the first container
# create fails with AuthorizationPermissionMismatch. Retry instead of making the
# operator rerun the whole script and wonder what they did wrong.
Write-Host "Creating or reusing blob container '$ContainerName' (Entra ID auth)..."
$containerReady = $false
for ($attempt = 1; $attempt -le 10; $attempt++) {
    $out = & az storage container create --name $ContainerName --account-name $StorageAccountName --auth-mode login --only-show-errors -o none 2>&1
    if ($LASTEXITCODE -eq 0) { $containerReady = $true; break }

    $text = ($out | Out-String)
    if ($text -match '(?i)AuthorizationPermissionMismatch|AuthorizationFailure|does not have authorization|ResourceNotFound') {
        Write-Host "  waiting for the role assignment to propagate ($attempt/10)..."
        Start-Sleep -Seconds 15
        continue
    }
    throw $text
}
if (-not $containerReady) {
    throw "Could not create blob container '$ContainerName' on '$StorageAccountName'. Confirm you hold Storage Blob Data Contributor on that account, then rerun this script."
}

Write-Host ""
Write-Host "Terraform remote state is ready. Persist these values in the azd environment:"
Write-Host "azd env set RS_STORAGE_ACCOUNT $StorageAccountName"
Write-Host "azd env set RS_CONTAINER_NAME $ContainerName"
Write-Host "azd env set RS_RESOURCE_GROUP $ResourceGroupName"
Write-Host ""
Write-Host "Then run: azd up"


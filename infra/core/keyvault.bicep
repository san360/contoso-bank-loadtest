// =============================================================================
// Key Vault: runtime secret resolution for Azure Load Testing
// =============================================================================
// Test definitions store only Key Vault secret URIs. The secret values remain in
// this vault and are fetched at run time by the load test resource's managed
// identity.
//
// RBAC authorization is used instead of legacy access policies so access is
// auditable and managed with the same role assignment model as Azure Monitor.
// Purge protection is intentionally disabled: this is a disposable demo
// environment, and purge protection would prevent a full teardown/recreate loop.
// =============================================================================

@description('Azure region for the Key Vault.')
param location string

@description('Resource name prefix.')
param prefix string

@description('Tags applied to every resource.')
param tags object = {}

@description('Principal ID of the load testing resource managed identity.')
param loadTestPrincipalId string

@description('Object ID of the principal running the deployment.')
param deployerPrincipalId string

// Key Vault Secrets User. Lets the load test managed identity read secret values.
var keyVaultSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'

// Key Vault Secrets Officer. Lets the deploying principal create the demo secret.
var keyVaultSecretsOfficerRoleId = 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: 'kv-${prefix}'
  location: location
  tags: tags
  properties: {
    tenantId: tenant().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: 'Allow'
    }
    accessPolicies: []
  }
}

resource loadTestSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, loadTestPrincipalId, keyVaultSecretsUserRoleId)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      keyVaultSecretsUserRoleId
    )
    principalId: loadTestPrincipalId
    // Required for freshly created managed identities to avoid Entra replication
    // timing issues during the same deployment.
    principalType: 'ServicePrincipal'
  }
}

resource deployerSecretsOfficer 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, deployerPrincipalId, keyVaultSecretsOfficerRoleId)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      keyVaultSecretsOfficerRoleId
    )
    principalId: deployerPrincipalId
  }
}

output keyVaultId string = keyVault.id
output keyVaultName string = keyVault.name
output keyVaultUri string = keyVault.properties.vaultUri

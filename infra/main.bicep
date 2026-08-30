// =============================================================================
// Contoso Bank - Azure Load Testing demo
// =============================================================================
// Subscription-scoped entry point, deployed by `azd up`.
//
// Creates:
//   * Log Analytics + Application Insights   (so you can see WHY it is slow)
//   * Linux App Service Plan + Python web app (the system under test)
//   * Azure Load Testing resource             (the load generator)
//   * Key Vault                              (secrets resolved at test run time)
//   * Monitoring Reader role assignment       (the step everyone forgets)
//
// Deploy:
//   azd up
//
// Or without azd:
//   az deployment sub create \
//     --location uksouth \
//     --template-file infra/main.bicep \
//     --parameters infra/main.parameters.json
// =============================================================================

targetScope = 'subscription'

@minLength(1)
@maxLength(24)
@description('Environment name. Used to derive resource names. azd supplies this.')
param environmentName string

@minLength(1)
@description('Azure region. Must be a region where Azure Load Testing is available.')
param location string

@description('App Service Plan SKU. S1 or higher is required for the autoscale demo.')
param appServicePlanSku string = 'S1'

@description('Optional resource group name. Defaults to rg-<environmentName>.')
param resourceGroupName string = ''

@description('Object ID of the principal running the deployment. azd supplies this from AZURE_PRINCIPAL_ID.')
param principalId string

// A short deterministic token keeps globally-unique names (the web app) stable
// across redeploys of the same environment.
var resourceToken = toLower(uniqueString(subscription().id, environmentName, location))
var prefix = '${take(replace(toLower(environmentName), '_', '-'), 12)}-${take(resourceToken, 6)}'

var tags = {
  'azd-env-name': environmentName
  workload: 'contoso-bank-loadtest-demo'
  costCentre: 'demo'
}

resource rg 'Microsoft.Resources/resourceGroups@2021-04-01' = {
  name: !empty(resourceGroupName) ? resourceGroupName : 'rg-${environmentName}'
  location: location
  tags: tags
}

module monitoring 'core/monitoring.bicep' = {
  name: 'monitoring'
  scope: rg
  params: {
    location: location
    prefix: prefix
    tags: tags
  }
}

module host 'core/host.bicep' = {
  name: 'host'
  scope: rg
  params: {
    location: location
    prefix: prefix
    tags: tags
    appServicePlanSku: appServicePlanSku
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
  }
}

module loadTest 'core/loadtest.bicep' = {
  name: 'loadtest'
  scope: rg
  params: {
    location: location
    prefix: prefix
    tags: tags
  }
}

module keyVault 'core/keyvault.bicep' = {
  name: 'keyvault'
  scope: rg
  params: {
    location: location
    prefix: prefix
    tags: tags
    loadTestPrincipalId: loadTest.outputs.loadTestPrincipalId
    deployerPrincipalId: principalId
  }
}

module rbac 'core/rbac.bicep' = {
  name: 'rbac'
  scope: rg
  params: {
    loadTestPrincipalId: loadTest.outputs.loadTestPrincipalId
  }
}

// -----------------------------------------------------------------------------
// Outputs. azd writes these to the .env file for the environment, so the demo
// scripts and the CI/CD workflow can pick them up without hard-coded names.
// -----------------------------------------------------------------------------
output AZURE_LOCATION string = location
output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_SUBSCRIPTION_ID string = subscription().subscriptionId

output SERVICE_API_NAME string = host.outputs.webAppName
output SERVICE_API_URI string = host.outputs.webAppUri
output SERVICE_API_HOSTNAME string = host.outputs.webAppHostName
output SERVICE_API_RESOURCE_ID string = host.outputs.webAppId

output LOAD_TEST_NAME string = loadTest.outputs.loadTestName
output LOAD_TEST_RESOURCE_ID string = loadTest.outputs.loadTestId
output LOAD_TEST_DATA_PLANE_URI string = loadTest.outputs.loadTestDataPlaneUri

output KEY_VAULT_NAME string = keyVault.outputs.keyVaultName
output KEY_VAULT_URI string = keyVault.outputs.keyVaultUri
output KEY_VAULT_RESOURCE_ID string = keyVault.outputs.keyVaultId

output APPLICATIONINSIGHTS_NAME string = monitoring.outputs.appInsightsName
output APPLICATIONINSIGHTS_RESOURCE_ID string = monitoring.outputs.appInsightsId
output APP_SERVICE_PLAN_RESOURCE_ID string = host.outputs.appServicePlanId

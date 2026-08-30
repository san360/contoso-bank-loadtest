// =============================================================================
// RBAC: let the load test resource read server-side metrics
// =============================================================================
// THIS IS THE STEP EVERYONE FORGETS.
//
// You can add App Components to a test in the portal, run the test, and see an
// empty server-side metrics chart with no error message that explains why. The
// cause is almost always this role assignment being absent.
//
// Monitoring Reader on the resource group lets the load test resource's managed
// identity read Azure Monitor metrics for every App Component in that group -
// the App Service plan, the web app, and Application Insights.
//
// Called out in docs/05 (IaC) and docs/07 (analysing results).
// =============================================================================

@description('Principal ID of the load testing resource managed identity.')
param loadTestPrincipalId string

// Monitoring Reader. Built-in role, fixed GUID across all Azure clouds.
var monitoringReaderRoleId = '43d0d8ad-25c7-4714-9337-8ba259a9fe05'

resource monitoringReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  // Deterministic GUID so redeploys are idempotent rather than erroring on a
  // duplicate role assignment.
  name: guid(resourceGroup().id, loadTestPrincipalId, monitoringReaderRoleId)
  scope: resourceGroup()
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      monitoringReaderRoleId
    )
    principalId: loadTestPrincipalId
    // Required. Without it, deployment can fail with a replication delay error
    // because ARM tries to look up the principal type before the identity has
    // propagated through Entra ID.
    principalType: 'ServicePrincipal'
  }
}

output roleAssignmentId string = monitoringReader.id

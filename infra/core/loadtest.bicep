// =============================================================================
// Azure Load Testing resource
// =============================================================================
// Note how small this is. That is the point of the "why as a service" argument
// in docs/02: the entire load-generation tier - engine fleet, orchestration,
// metrics pipeline, dashboards, retention - is 20 lines of Bicep and no VMs to
// patch.
//
// The system-assigned identity is what lets the service read Key Vault secrets
// and pull server-side metrics from your App Components.
// =============================================================================

@description('Azure region for the load testing resource.')
param location string

@description('Resource name prefix.')
param prefix string

@description('Tags applied to every resource.')
param tags object = {}

@description('Optional description shown in the portal.')
param resourceDescription string = 'Contoso Bank load testing demo'

resource loadTest 'Microsoft.LoadTestService/loadTests@2022-12-01' = {
  name: 'alt-${prefix}'
  location: location
  tags: tags
  identity: {
    // System-assigned is enough for the demo. Use a user-assigned identity when
    // you need the same identity across environments, or customer-managed keys
    // (CMK requires a user-assigned identity - see docs/11).
    type: 'SystemAssigned'
  }
  properties: {
    description: resourceDescription
  }
}

output loadTestId string = loadTest.id
output loadTestName string = loadTest.name
output loadTestDataPlaneUri string = loadTest.properties.dataPlaneURI
output loadTestPrincipalId string = loadTest.identity.principalId

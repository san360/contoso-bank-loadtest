// =============================================================================
// Monitoring: Log Analytics workspace + Application Insights
// =============================================================================
// This is not optional decoration. App Components in Azure Load Testing pull
// server-side metrics from Azure Monitor, and the "why is it slow" half of the
// demo depends entirely on this resource existing and being readable by the
// load test resource's managed identity.
// =============================================================================

@description('Azure region for all resources.')
param location string

@description('Resource name prefix.')
param prefix string

@description('Tags applied to every resource.')
param tags object = {}

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${prefix}'
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    // 30 days is plenty for a demo and keeps the cost near zero.
    retentionInDays: 30
    features: {
      searchVersion: 1
    }
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${prefix}'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    // Workspace-based App Insights. Classic is retired; do not use it.
    WorkspaceResourceId: logAnalytics.id
    IngestionMode: 'LogAnalytics'
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

output logAnalyticsId string = logAnalytics.id
output logAnalyticsName string = logAnalytics.name
output appInsightsId string = appInsights.id
output appInsightsName string = appInsights.name
output appInsightsConnectionString string = appInsights.properties.ConnectionString
output appInsightsInstrumentationKey string = appInsights.properties.InstrumentationKey

// =============================================================================
// Host: Linux App Service Plan + Python web app (the system under test)
// =============================================================================
// Deliberate choices worth calling out during the Bicep walkthrough:
//
//  * S1 (not B1/F1). The autoscale demo in docs/13 needs a plan tier that
//    supports autoscale rules. Free and Basic tiers do not.
//  * A SINGLE worker by default. The demo is about finding a bottleneck; if the
//    platform silently scales out, the bottleneck disappears and so does the
//    story. Scale out on purpose, on camera.
//  * alwaysOn: true. Otherwise the first 200 virtual users pay for a cold start
//    and your p95 is a lie.
// =============================================================================

@description('Azure region for all resources.')
param location string

@description('Resource name prefix.')
param prefix string

@description('Tags applied to every resource.')
param tags object = {}

@description('App Service Plan SKU. S1 or higher is required for the autoscale demo.')
param appServicePlanSku string = 'S1'

@description('Application Insights connection string.')
param appInsightsConnectionString string

@description('Number of workers. Keep at 1 so the planted bottlenecks are visible.')
param workerCount int = 1

var appName = 'app-${prefix}'

resource appServicePlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: 'plan-${prefix}'
  location: location
  tags: tags
  sku: {
    name: appServicePlanSku
    capacity: workerCount
  }
  kind: 'linux'
  properties: {
    reserved: true // required for Linux
  }
}

resource webApp 'Microsoft.Web/sites@2023-12-01' = {
  name: appName
  location: location
  tags: union(tags, { 'azd-service-name': 'api' })
  kind: 'app,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: appServicePlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.12'
      alwaysOn: true
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      http20Enabled: true

      // Gunicorn with uvicorn workers. NOTE for the demo: the simulated
      // connection pool is PER PROCESS, so effective pool = workers x
      // db_pool_size. One worker keeps the maths honest.
      appCommandLine: 'gunicorn main:app --workers 1 --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000 --timeout 600 --access-logfile - --error-logfile -'

      appSettings: [
        {
          // Tells Oryx to build the app remotely, so no local Docker or
          // Python toolchain is needed to deploy.
          name: 'SCM_DO_BUILD_DURING_DEPLOYMENT'
          value: 'true'
        }
        {
          name: 'ENABLE_ORYX_BUILD'
          value: 'true'
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsightsConnectionString
        }
        {
          name: 'PYTHON_ENABLE_GUNICORN_MULTIWORKERS'
          value: 'false'
        }
        {
          name: 'WEBSITES_CONTAINER_START_TIME_LIMIT'
          value: '600'
        }
        // ---- Demo starts in the "before" state, on purpose. ----
        {
          name: 'ENABLE_CACHE'
          value: 'false'
        }
        {
          name: 'DB_POOL_SIZE'
          value: '2'
        }
        {
          name: 'DB_QUERY_MS'
          value: '25'
        }
        {
          name: 'ENABLE_TRANSFER_LOCK'
          value: 'true'
        }
        {
          name: 'ENABLE_N_PLUS_ONE'
          value: 'true'
        }
        {
          name: 'FRAUD_CHECK_PARALLEL'
          value: 'false'
        }
        {
          name: 'STATEMENT_CPU_ROUNDS'
          value: '30000'
        }
      ]
    }
  }
}

// Autoscale rules, disabled by default. Enable them live in docs/13.8 to show
// that a load test is how you prove your autoscale policy actually works.
resource autoscale 'Microsoft.Insights/autoscalesettings@2022-10-01' = {
  name: 'autoscale-${prefix}'
  location: location
  tags: tags
  properties: {
    name: 'autoscale-${prefix}'
    enabled: false
    targetResourceUri: appServicePlan.id
    profiles: [
      {
        name: 'CPU based'
        capacity: {
          minimum: '1'
          maximum: '5'
          default: '1'
        }
        rules: [
          {
            metricTrigger: {
              metricName: 'CpuPercentage'
              metricResourceUri: appServicePlan.id
              timeGrain: 'PT1M'
              statistic: 'Average'
              timeWindow: 'PT5M'
              timeAggregation: 'Average'
              operator: 'GreaterThan'
              threshold: 70
            }
            scaleAction: {
              direction: 'Increase'
              type: 'ChangeCount'
              value: '1'
              cooldown: 'PT5M'
            }
          }
          {
            metricTrigger: {
              metricName: 'CpuPercentage'
              metricResourceUri: appServicePlan.id
              timeGrain: 'PT1M'
              statistic: 'Average'
              timeWindow: 'PT10M'
              timeAggregation: 'Average'
              operator: 'LessThan'
              threshold: 30
            }
            scaleAction: {
              direction: 'Decrease'
              type: 'ChangeCount'
              value: '1'
              cooldown: 'PT10M'
            }
          }
        ]
      }
    ]
  }
}

output webAppId string = webApp.id
output webAppName string = webApp.name
output webAppHostName string = webApp.properties.defaultHostName
output webAppUri string = 'https://${webApp.properties.defaultHostName}'
output appServicePlanId string = appServicePlan.id

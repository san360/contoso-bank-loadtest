#==============================================================================
# Contoso Bank - full Terraform stack for azd
#==============================================================================
# SCOPE: Terraform now owns the complete demo environment that `azd up` needs:
# the observability resources, the deliberately slow Python App Service, the Azure
# Load Testing resource, Key Vault, RBAC and diagnostic wiring.
#
# The demo still has three layers, but the layer-1 boundary changed:
#
#   Layer 1  Azure resources + system under test  Terraform via azd provision
#   Layer 2  Test definitions                     az load + YAML postdeploy hook
#   Layer 3  Execution + gates                     az load in CI/CD or live demo
#
# Terraform's azurerm_load_test manages the resource container ONLY. It cannot
# create test definitions, upload a JMX file, set fail criteria or attach server
# metrics to tests. Those remain data-plane operations performed by the existing
# tools/provision_loadtests.* hook after azd deploy finishes.
#
# Important azd traps this file is designed around:
#   * The App Service has the tag azd-service-name = "api". Without it,
#     `azd deploy api` cannot find a deployment target.
#   * Terraform authenticates with Azure CLI, not azd's token cache. Run:
#       azd config set auth.useAzCliAuth true
#       az login --tenant <tenant-id>
#   * All outputs are the uppercase azd environment variables consumed by the
#     postdeploy hook and by the teaching material.
#==============================================================================

locals {
  # Bicep used uniqueString(subscription, environmentName, location). Terraform
  # does not have Azure's exact uniqueString implementation, so we keep the same
  # shape and stability guarantee with a deterministic six-character hash. A clean
  # cutover may create new resource names; azd outputs keep the rest of the kit
  # decoupled from the concrete names.
  environment_token = substr(replace(lower(var.environment_name), "/[^a-z0-9-]/", "-"), 0, 12)
  resource_token    = substr(sha256("${data.azurerm_client_config.current.subscription_id}-${var.environment_name}-${var.location}"), 0, 6)
  prefix            = "${local.environment_token}-${local.resource_token}"

  resource_group_name = var.resource_group_name != "" ? var.resource_group_name : "rg-${var.environment_name}"

  tags = merge(
    var.tags,
    {
      "azd-env-name" = var.environment_name
    }
  )

  demo_secret_name = "demo-api-key"

  api_startup_command = "gunicorn main:app --workers 1 --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000 --timeout 600 --access-logfile - --error-logfile -"

  api_app_settings = {
    # Tells Oryx to build the app remotely, so no local Docker or Python
    # toolchain is needed to deploy from a presenter machine.
    SCM_DO_BUILD_DURING_DEPLOYMENT = "true"
    ENABLE_ORYX_BUILD              = "true"

    APPLICATIONINSIGHTS_CONNECTION_STRING = azurerm_application_insights.this.connection_string

    PYTHON_ENABLE_GUNICORN_MULTIWORKERS = "false"
    WEBSITES_CONTAINER_START_TIME_LIMIT = "600"

    # ---- Demo starts in the "before" state, on purpose. ----
    ENABLE_CACHE         = "false"
    DB_POOL_SIZE         = "2"
    DB_QUERY_MS          = "25"
    ENABLE_TRANSFER_LOCK = "true"
    ENABLE_N_PLUS_ONE    = "true"
    FRAUD_CHECK_PARALLEL = "false"
    STATEMENT_CPU_ROUNDS = "30000"
  }
}

data "azurerm_client_config" "current" {}

#------------------------------------------------------------------------------
# Resource group
#------------------------------------------------------------------------------
# azd's Bicep deployment created rg-<environmentName> unless overridden. Keeping
# the same default means existing runbooks and screenshots still make sense while
# Terraform becomes the only IaC provider.
#------------------------------------------------------------------------------
resource "azurerm_resource_group" "this" {
  name     = local.resource_group_name
  location = var.location
  tags     = local.tags
}

#------------------------------------------------------------------------------
# Log Analytics - target for diagnostic settings
#------------------------------------------------------------------------------
resource "azurerm_log_analytics_workspace" "this" {
  name                = "log-${local.prefix}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = local.tags
}

#------------------------------------------------------------------------------
# Application Insights - workspace based, not classic
#------------------------------------------------------------------------------
# App Components in Azure Load Testing pull server-side metrics from Azure Monitor,
# and the "why is it slow" part of the demo depends on those metrics being wired
# to the app under test.
#------------------------------------------------------------------------------
resource "azurerm_application_insights" "this" {
  name                = "appi-${local.prefix}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  workspace_id        = azurerm_log_analytics_workspace.this.id
  application_type    = "web"

  internet_ingestion_enabled = true
  internet_query_enabled     = true

  tags = local.tags
}

#------------------------------------------------------------------------------
# App Service Plan - Linux S1, one worker
#------------------------------------------------------------------------------
# S1 is deliberate: the autoscale demo needs a tier that supports autoscale rules.
# One worker is also deliberate: if the platform silently scales out, the planted
# bottleneck disappears and the customer loses the troubleshooting story.
#------------------------------------------------------------------------------
resource "azurerm_service_plan" "this" {
  name                = "plan-${local.prefix}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  os_type             = "Linux"
  sku_name            = var.app_service_plan_sku
  worker_count        = var.worker_count
  tags                = local.tags
}

#------------------------------------------------------------------------------
# Python App Service - the system under test
#------------------------------------------------------------------------------
# The azd-service-name tag is not decorative. azd resolves `azd deploy api` by
# finding the App Service resource with azd-service-name = "api". If that tag is
# missing or misspelled, provisioning may succeed and deployment still fails.
#
# Bicep did not configure an App Service health check path, so Terraform leaves it
# unset for parity. The application still exposes /health for humans and scripts.
#------------------------------------------------------------------------------
resource "azurerm_linux_web_app" "api" {
  name                = "app-${local.prefix}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  service_plan_id     = azurerm_service_plan.this.id
  https_only          = true

  identity {
    type = "SystemAssigned"
  }

  site_config {
    always_on           = true
    ftps_state          = "Disabled"
    minimum_tls_version = "1.2"
    http2_enabled       = true
    app_command_line    = local.api_startup_command

    application_stack {
      python_version = "3.12"
    }
  }

  app_settings = local.api_app_settings

  tags = merge(
    local.tags,
    {
      "azd-service-name" = "api"
    }
  )
}

#------------------------------------------------------------------------------
# Disabled autoscale policy for the live teaching chapter
#------------------------------------------------------------------------------
# The rule exists but starts disabled so the presenter can enable it live in the
# autoscale chapter and prove the change with a load test instead of a guess.
#------------------------------------------------------------------------------
resource "azurerm_monitor_autoscale_setting" "this" {
  name                = "autoscale-${local.prefix}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  target_resource_id  = azurerm_service_plan.this.id
  enabled             = false
  tags                = local.tags

  profile {
    name = "CPU based"

    capacity {
      default = 1
      minimum = 1
      maximum = 5
    }

    rule {
      metric_trigger {
        metric_name        = "CpuPercentage"
        metric_resource_id = azurerm_service_plan.this.id
        time_grain         = "PT1M"
        statistic          = "Average"
        time_window        = "PT5M"
        time_aggregation   = "Average"
        operator           = "GreaterThan"
        threshold          = 70
      }

      scale_action {
        direction = "Increase"
        type      = "ChangeCount"
        value     = 1
        cooldown  = "PT5M"
      }
    }

    rule {
      metric_trigger {
        metric_name        = "CpuPercentage"
        metric_resource_id = azurerm_service_plan.this.id
        time_grain         = "PT1M"
        statistic          = "Average"
        time_window        = "PT10M"
        time_aggregation   = "Average"
        operator           = "LessThan"
        threshold          = 30
      }

      scale_action {
        direction = "Decrease"
        type      = "ChangeCount"
        value     = 1
        cooldown  = "PT10M"
      }
    }
  }
}

#------------------------------------------------------------------------------
# Azure Load Testing resource
#------------------------------------------------------------------------------
# Note how small this resource is. That is the point of the "why as a service"
# argument: the load-generation fleet, orchestration and portal experience are a
# managed service, not VMs the customer has to patch.
#------------------------------------------------------------------------------
resource "azurerm_load_test" "this" {
  name                = "alt-${local.prefix}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  description         = "Contoso Bank load testing demo"

  identity {
    type = "SystemAssigned"
  }

  tags = local.tags
}

#------------------------------------------------------------------------------
# RBAC - THIS IS THE STEP EVERYONE FORGETS
#------------------------------------------------------------------------------
# Without Monitoring Reader, you can add App Components to a test, run it, and get
# an EMPTY server-side metrics chart with no useful error. Scope it to the whole
# resource group so the load test identity can read the App Service plan, web app
# and Application Insights metrics that the postdeploy hook attaches.
#------------------------------------------------------------------------------
resource "azurerm_role_assignment" "monitoring_reader" {
  scope                = azurerm_resource_group.this.id
  role_definition_name = "Monitoring Reader"
  principal_id         = azurerm_load_test.this.identity[0].principal_id

  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "load_test_contributor" {
  for_each = toset(var.load_test_contributor_principal_ids)

  scope                = azurerm_load_test.this.id
  role_definition_name = "Load Test Contributor"
  principal_id         = each.value
}

#------------------------------------------------------------------------------
# Key Vault - runtime secret resolution for Azure Load Testing
#------------------------------------------------------------------------------
# Test definitions store only Key Vault secret URIs. Azure Load Testing resolves
# the secret value at run time through the load test resource's managed identity.
#
# Known customer-policy gotcha: this subscription has policy that can silently
# rewrite publicNetworkAccess to Disabled. ARM returns success, no compliance
# finding is shown, and the effective value stays Disabled. Do not fight that in
# Terraform: the intended value remains true below, while ignore_changes prevents
# noisy perpetual drift. Consequence to explain in the demo: Load Testing cannot
# resolve ${__GetSecret(...)} from a vault whose public access is disabled unless
# the customer adds a private networking design for the service.
#------------------------------------------------------------------------------
resource "azurerm_key_vault" "this" {
  name                = "kv-${local.prefix}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  tenant_id           = data.azurerm_client_config.current.tenant_id
  sku_name            = "standard"

  rbac_authorization_enabled    = true
  soft_delete_retention_days    = 7
  purge_protection_enabled      = false
  public_network_access_enabled = var.key_vault_public_network_access_enabled

  network_acls {
    bypass         = "AzureServices"
    default_action = "Allow"
  }

  tags = local.tags

  lifecycle {
    ignore_changes = [
      public_network_access_enabled
    ]
  }
}

resource "azurerm_role_assignment" "key_vault_secrets_user" {
  scope                = azurerm_key_vault.this.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_load_test.this.identity[0].principal_id

  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "key_vault_secrets_officer" {
  scope                = azurerm_key_vault.this.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = trimspace(var.principal_id) != "" ? var.principal_id : data.azurerm_client_config.current.object_id
}

# Use ARM for the demo secret so `azd up` does not depend on the presenter's
# workstation having data-plane network access to the vault. The value is fake,
# but it is still in Terraform state; do not replace it with a real credential.
resource "azapi_resource" "demo_api_key" {
  type      = "Microsoft.KeyVault/vaults/secrets@2023-07-01"
  name      = local.demo_secret_name
  parent_id = azurerm_key_vault.this.id

  body = {
    properties = {
      value = var.demo_api_key_value
    }
  }

  depends_on = [
    azurerm_role_assignment.key_vault_secrets_officer
  ]
}

#------------------------------------------------------------------------------
# Diagnostic settings
#------------------------------------------------------------------------------
# OperationLogs answer governance questions such as who created, ran or deleted a
# test. Client-side load-test results are not emitted to Azure Monitor; those stay
# in the Load Testing data plane and are created by the postdeploy hook.
#
# Microsoft.LoadTestService does NOT support metric export. Adding an
# `enabled_metric { category = "AllMetrics" }` block makes the control plane
# reject the whole diagnostic setting with:
#   400 BadRequest - "Metric export is not enabled."
# Logs-only is the supported configuration for this resource type.
#------------------------------------------------------------------------------
resource "azurerm_monitor_diagnostic_setting" "load_test" {
  name                       = "diag-to-law"
  target_resource_id         = azurerm_load_test.this.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id

  enabled_log {
    category = "OperationLogs"
  }
}




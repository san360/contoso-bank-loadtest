#==============================================================================
# Azure Load Testing - platform layer (Terraform)
#==============================================================================
# SCOPE: this configuration provisions the load testing PLATFORM only - the
# resource, its identity, RBAC and diagnostics. It deliberately does NOT create
# the system under test.
#
# That split is intentional and worth explaining in the session:
#
#   Layer 1  Resource        Terraform      platform team, changes rarely
#   Layer 2  Test definitions  az load + YAML   perf engineers, changes often
#   Layer 3  Execution       az load in CI    pipeline, every commit
#
# Terraform's azurerm_load_test manages the resource container ONLY. It cannot
# create test definitions, upload a JMX file, or set fail criteria - those are
# data-plane operations. See docs/05 and docs/17.
#==============================================================================

locals {
  suffix              = substr(sha256(local.rg_id_seed), 0, 6)
  rg_id_seed          = "${var.name_prefix}-${var.location}"
  resource_group_name = coalesce(var.resource_group_name, "rg-${var.name_prefix}-loadtest")

  # Azure Load Testing resource names: 1-64 chars, alphanumerics and hyphens.
  load_test_name   = "alt-${var.name_prefix}-${local.suffix}"
  law_name         = "log-${var.name_prefix}-${local.suffix}"
  key_vault_name   = "kv-${substr(var.name_prefix, 0, 12)}-${local.suffix}"
  demo_secret_name = "demo-api-key"
}

data "azurerm_client_config" "current" {}

locals {
  key_vault_secrets_officer_principal_id = coalesce(
    var.key_vault_secrets_officer_principal_id,
    data.azurerm_client_config.current.object_id
  )

  # Non-sensitive fallback for a disposable demo. For real credentials, pass
  # demo_api_key_value from a protected CI variable or local tfvars file that is
  # never committed.
  demo_api_key_value = coalesce(var.demo_api_key_value, "contoso-demo-api-key-${local.suffix}")
}

#------------------------------------------------------------------------------
# Resource group
#------------------------------------------------------------------------------
resource "azurerm_resource_group" "this" {
  count = var.create_resource_group ? 1 : 0

  name     = local.resource_group_name
  location = var.location
  tags     = var.tags
}

data "azurerm_resource_group" "existing" {
  count = var.create_resource_group ? 0 : 1

  name = local.resource_group_name
}

locals {
  rg_name = coalesce(
    one(azurerm_resource_group.this[*].name),
    one(data.azurerm_resource_group.existing[*].name)
  )
  rg_id = coalesce(
    one(azurerm_resource_group.this[*].id),
    one(data.azurerm_resource_group.existing[*].id)
  )
}

#------------------------------------------------------------------------------
# Log Analytics - target for diagnostic settings
#------------------------------------------------------------------------------
resource "azurerm_log_analytics_workspace" "this" {
  count = var.log_analytics_workspace_id == null ? 1 : 0

  name                = local.law_name
  location            = var.location
  resource_group_name = local.rg_name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = var.tags
}

locals {
  law_id = coalesce(
    var.log_analytics_workspace_id,
    one(azurerm_log_analytics_workspace.this[*].id)
  )
}

#------------------------------------------------------------------------------
# The Azure Load Testing resource
#------------------------------------------------------------------------------
resource "azurerm_load_test" "this" {
  name                = local.load_test_name
  location            = var.location
  resource_group_name = local.rg_name
  description         = "Contoso Bank performance testing - managed by Terraform"

  identity {
    type = "SystemAssigned"
  }

  tags = var.tags
}

#------------------------------------------------------------------------------
# RBAC - THIS IS THE STEP EVERYONE FORGETS
#------------------------------------------------------------------------------
# Without Monitoring Reader, you can add App Components to a test, run it, and
# get an EMPTY server-side metrics chart with no error explaining why.
#
# Mirrors infra/core/rbac.bicep.
#------------------------------------------------------------------------------
resource "azurerm_role_assignment" "monitoring_reader" {
  scope                = coalesce(var.monitoring_reader_scope_id, local.rg_id)
  role_definition_name = "Monitoring Reader"
  principal_id         = azurerm_load_test.this.identity[0].principal_id

  # Avoids the "principal does not exist" failure caused by Entra ID
  # replication delay immediately after the identity is created.
  skip_service_principal_aad_check = true
}

#------------------------------------------------------------------------------
# RBAC - who may author and run tests
#------------------------------------------------------------------------------
resource "azurerm_role_assignment" "load_test_contributor" {
  for_each = toset(var.load_test_contributor_principal_ids)

  scope                = azurerm_load_test.this.id
  role_definition_name = "Load Test Contributor"
  principal_id         = each.value
}

#------------------------------------------------------------------------------
# Key Vault - runtime secret resolution for load tests
#------------------------------------------------------------------------------
# This is platform, not application code: Azure Load Testing receives only a
# Key Vault secret URI in the test definition, then resolves the value at run
# time through the load test resource's managed identity. The script and YAML can
# be safely reviewed in Git because the credential value is not in either file.
#
# Mirrors infra/core/keyvault.bicep.
#------------------------------------------------------------------------------
resource "azurerm_key_vault" "this" {
  name                = local.key_vault_name
  location            = var.location
  resource_group_name = local.rg_name
  tenant_id           = data.azurerm_client_config.current.tenant_id
  sku_name            = "standard"

  enable_rbac_authorization = true
  soft_delete_retention_days = 7

  # Purge protection is intentionally OFF. This is a disposable demo
  # environment, and purge protection would block a full destroy/recreate loop
  # for the retention period.
  purge_protection_enabled = false

  public_network_access_enabled = true

  network_acls {
    bypass         = "AzureServices"
    default_action = "Allow"
  }

  tags = var.tags
}

#------------------------------------------------------------------------------
# RBAC - THIS IS ALSO THE STEP EVERYONE FORGETS
#------------------------------------------------------------------------------
# Without Key Vault Secrets User on the vault, the test can be created and even
# validated, but the run later fails while resolving ${__GetSecret(API_KEY)}.
# That failure is a runtime secret-resolution problem, not a JMeter syntax issue.
#------------------------------------------------------------------------------
resource "azurerm_role_assignment" "key_vault_secrets_user" {
  scope                = azurerm_key_vault.this.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_load_test.this.identity[0].principal_id

  # Avoids the "principal does not exist" failure caused by Entra ID
  # replication delay immediately after the load test identity is created.
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "key_vault_secrets_officer" {
  scope                = azurerm_key_vault.this.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = local.key_vault_secrets_officer_principal_id
}

resource "azurerm_key_vault_secret" "demo_api_key" {
  name         = local.demo_secret_name
  value        = local.demo_api_key_value
  key_vault_id = azurerm_key_vault.this.id

  # Genuine gotcha: Key Vault data-plane RBAC can lag the role assignment. Keep
  # the dependency explicit so Terraform at least waits for the assignment
  # resource before it tries to write the secret.
  depends_on = [
    azurerm_role_assignment.key_vault_secrets_officer
  ]
}

#------------------------------------------------------------------------------
# Diagnostic settings
#------------------------------------------------------------------------------
# NOTE FOR THE SESSION: this emits OperationLogs - an audit trail of who created,
# ran and deleted tests. It does NOT emit load test performance results.
# Client-side metrics are not published to Azure Monitor. See docs/17.
#------------------------------------------------------------------------------
resource "azurerm_monitor_diagnostic_setting" "this" {
  name                       = "diag-to-law"
  target_resource_id         = azurerm_load_test.this.id
  log_analytics_workspace_id = local.law_id

  enabled_log {
    category = "OperationLogs"
  }

  metric {
    category = "AllMetrics"
    enabled  = true
  }
}

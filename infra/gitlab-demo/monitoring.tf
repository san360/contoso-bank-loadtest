locals {
  monitoring_targets = {
    staging     = azurerm_linux_web_app_slot.staging.id
    shared_plan = azurerm_service_plan.demo.id
  }
}

resource "azurerm_role_assignment" "alt_metrics" {
  for_each             = local.monitoring_targets
  scope                = each.value
  role_definition_name = "Monitoring Reader"
  principal_id         = azurerm_load_test.demo.identity[0].principal_id
}

resource "azurerm_role_assignment" "runner_metrics" {
  for_each             = local.monitoring_targets
  scope                = each.value
  role_definition_name = "Monitoring Reader"
  principal_id         = azurerm_user_assigned_identity.runner.principal_id
}

resource "azurerm_role_assignment" "runner_logs" {
  scope                = azurerm_log_analytics_workspace.demo.id
  role_definition_name = "Log Analytics Reader"
  principal_id         = azurerm_user_assigned_identity.runner.principal_id
}

resource "azurerm_monitor_diagnostic_setting" "app_logs" {
  for_each = {
    staging    = azurerm_linux_web_app_slot.staging.id
    production = azurerm_linux_web_app.demo.id
  }
  name                           = "demo-application-logs"
  target_resource_id             = each.value
  log_analytics_workspace_id     = azurerm_log_analytics_workspace.demo.id
  log_analytics_destination_type = "Dedicated"
  enabled_log {
    category = "AppServiceConsoleLogs"
  }
  enabled_log {
    category = "AppServiceHTTPLogs"
  }
}

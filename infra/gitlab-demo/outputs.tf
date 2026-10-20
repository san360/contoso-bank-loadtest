output "demo" {
  value = {
    subscription_id          = var.subscription_id
    tenant_id                = var.tenant_id
    resource_group           = azurerm_resource_group.demo.name
    location                 = var.location
    gitlab_url               = local.gitlab_url
    gitlab_app_name          = local.gitlab_name
    gitlab_hosting           = "vm-with-stateless-aca-proxy"
    gitlab_vm_name           = var.deploy_gitlab_vm ? azurerm_linux_virtual_machine.gitlab[0].name : null
    gitlab_vm_id             = var.deploy_gitlab_vm ? azurerm_linux_virtual_machine.gitlab[0].id : null
    gitlab_vm_private_ip     = var.deploy_gitlab_vm ? azurerm_network_interface.gitlab_vm[0].private_ip_address : null
    gitlab_data_disk_id      = var.deploy_gitlab_vm ? azurerm_managed_disk.gitlab_data[0].id : null
    gitlab_vm_lock_id        = var.gitlab_vm_readonly_lock ? azurerm_management_lock.gitlab_vm[0].id : null
    gitlab_data_disk_lock_id = var.deploy_gitlab_vm ? azurerm_management_lock.gitlab_data[0].id : null
    registry                 = azurerm_container_registry.demo.name
    registry_server          = azurerm_container_registry.demo.login_server
    environment              = var.use_clean_environment ? azurerm_container_app_environment.clean[0].name : azurerm_container_app_environment.demo.name
    retained_environment     = var.use_clean_environment ? azurerm_container_app_environment.demo.name : null
    retained_failed_apps     = var.use_clean_environment ? [for app in data.azurerm_resources.retained_gitlab[0].resources : app.id] : []
    retained_diagnostic_jobs = var.use_clean_environment ? [for job in data.azurerm_resources.diagnostic_job[0].resources : job.id] : []
    storage_account          = azurerm_storage_account.gitlab.name
    app_name                 = azurerm_linux_web_app.demo.name
    app_id                   = azurerm_linux_web_app.demo.id
    staging_id               = azurerm_linux_web_app_slot.staging.id
    plan_id                  = azurerm_service_plan.demo.id
    workspace_id             = azurerm_log_analytics_workspace.demo.id
    workspace_customer_id    = azurerm_log_analytics_workspace.demo.workspace_id
    staging_url              = "https://${azurerm_linux_web_app_slot.staging.default_hostname}"
    production_url           = "https://${azurerm_linux_web_app.demo.default_hostname}"
    load_test_name           = azurerm_load_test.demo.name
    load_test_id             = azurerm_load_test.demo.id
    load_test_endpoint       = azurerm_load_test.demo.data_plane_uri
    runner_client_id         = azurerm_user_assigned_identity.runner.client_id
    portal_url               = "https://portal.azure.com/#resource${azurerm_resource_group.demo.id}"
  }
}

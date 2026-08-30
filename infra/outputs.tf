#==============================================================================
# azd environment outputs
#==============================================================================
# azd writes Terraform outputs into the environment, and the postdeploy hook reads
# these exact uppercase names. Keep them in lockstep with the former Bicep outputs
# and with tools/provision_loadtests.ps1 / .sh.
#==============================================================================

output "AZURE_LOCATION" {
  description = "Azure region used by this azd environment."
  value       = var.location
}

output "AZURE_RESOURCE_GROUP" {
  description = "Resource group containing the Contoso Bank demo resources."
  value       = azurerm_resource_group.this.name
}

output "AZURE_SUBSCRIPTION_ID" {
  description = "Subscription ID used for the deployment."
  value       = data.azurerm_client_config.current.subscription_id
}

output "SERVICE_API_NAME" {
  description = "Name of the Python App Service that azd deploys the api service to."
  value       = azurerm_linux_web_app.api.name
}

output "SERVICE_API_URI" {
  description = "HTTPS URI of the deployed Contoso Bank API."
  value       = "https://${azurerm_linux_web_app.api.default_hostname}"
}

output "SERVICE_API_HOSTNAME" {
  description = "Hostname used to retarget the load-test artefacts after deployment."
  value       = azurerm_linux_web_app.api.default_hostname
}

output "SERVICE_API_RESOURCE_ID" {
  description = "Resource ID attached as an Azure Load Testing app component."
  value       = azurerm_linux_web_app.api.id
}

output "LOAD_TEST_NAME" {
  description = "Name of the Azure Load Testing resource."
  value       = azurerm_load_test.this.name
}

output "LOAD_TEST_RESOURCE_ID" {
  description = "Resource ID used for portal links and test setup."
  value       = azurerm_load_test.this.id
}

output "LOAD_TEST_DATA_PLANE_URI" {
  description = "Data plane endpoint used by Azure Load Testing clients."
  value       = azurerm_load_test.this.data_plane_uri
}

output "KEY_VAULT_NAME" {
  description = "Key Vault used for demo secret resolution."
  value       = azurerm_key_vault.this.name
}

output "KEY_VAULT_URI" {
  description = "Vault URI referenced by load test secrets."
  value       = azurerm_key_vault.this.vault_uri
}

output "KEY_VAULT_RESOURCE_ID" {
  description = "Resource ID of the demo Key Vault."
  value       = azurerm_key_vault.this.id
}

output "APPLICATIONINSIGHTS_NAME" {
  description = "Name of the workspace-based Application Insights component."
  value       = azurerm_application_insights.this.name
}

output "APPLICATIONINSIGHTS_RESOURCE_ID" {
  description = "Resource ID attached as an Azure Load Testing app component."
  value       = azurerm_application_insights.this.id
}

output "APP_SERVICE_PLAN_RESOURCE_ID" {
  description = "Resource ID attached for App Service plan server-side metrics."
  value       = azurerm_service_plan.this.id
}

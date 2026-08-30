output "load_test_name" {
  description = "Name of the Azure Load Testing resource. Pass to --load-test-resource."
  value       = azurerm_load_test.this.name
}

output "resource_group_name" {
  description = "Resource group name. Pass to --resource-group."
  value       = local.rg_name
}

output "load_test_data_plane_uri" {
  description = "Data plane endpoint used by the REST API and the az load extension."
  value       = azurerm_load_test.this.data_plane_uri
}

output "load_test_principal_id" {
  description = "System-assigned managed identity object ID. Useful when granting access to Key Vault or a system under test in another resource group."
  value       = azurerm_load_test.this.identity[0].principal_id
}

output "key_vault_name" {
  description = "Name of the Key Vault used for Azure Load Testing secret resolution."
  value       = azurerm_key_vault.this.name
}

output "key_vault_uri" {
  description = "Vault URI. Test definitions reference secrets under this URI; they never contain secret values."
  value       = azurerm_key_vault.this.vault_uri
}

output "key_vault_demo_api_key_secret_uri" {
  description = "Full URI for the demo-api-key secret that can be mapped to API_KEY in Azure Load Testing YAML."
  value       = azurerm_key_vault_secret.demo_api_key.id
}

# Convenience: the exact next command to run. Terraform stops at layer 1, and
# this makes the handover to layer 2 explicit rather than implied.
output "next_command" {
  description = "Create the test definition from version-controlled YAML."
  value = format(
    "az load test create --load-test-resource %s --resource-group %s --test-id contoso-bank-peak --load-test-config-file loadtests/config/peak-load.yaml",
    azurerm_load_test.this.name,
    local.rg_name
  )
}

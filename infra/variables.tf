#==============================================================================
# Azure Developer CLI variables
#==============================================================================
# azd follows the Terraform templates documented at:
#   https://learn.microsoft.com/azure/developer/azure-developer-cli/use-terraform-for-azd
# and exemplified by Azure-Samples/todo-nodejs-mongo-terraform. It supplies:
#   * environment_name  <- AZURE_ENV_NAME
#   * location          <- AZURE_LOCATION
#   * principal_id      <- AZURE_PRINCIPAL_ID
# Keep these names snake_case; do not use the old Bicep parameter names here.
#==============================================================================

variable "environment_name" {
  description = "The azd environment name. azd supplies this from AZURE_ENV_NAME."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_-]{1,24}$", var.environment_name))
    error_message = "environment_name must be 1-24 letters, digits, hyphens or underscores."
  }
}

variable "location" {
  description = "Azure region. azd supplies this from AZURE_LOCATION. Must support Azure Load Testing and Linux App Service."
  type        = string
}

variable "principal_id" {
  description = "Object ID of the principal running azd. azd supplies this from AZURE_PRINCIPAL_ID; standalone Terraform falls back to the Azure CLI principal."
  type        = string
  default     = ""
}

variable "subscription_id" {
  description = "Optional subscription ID for azurerm v4. azd/CLI users can leave this null when ARM_SUBSCRIPTION_ID or Azure CLI context is set."
  type        = string
  default     = null
}

variable "tenant_id" {
  description = "Optional tenant ID for azurerm/azapi. Leave null to use the Azure CLI tenant selected by az login."
  type        = string
  default     = null
}

variable "resource_group_name" {
  description = "Optional override. By default the demo keeps azd's Bicep-era convention: rg-<environment_name>."
  type        = string
  default     = ""
}

variable "app_service_plan_sku" {
  description = "App Service Plan SKU. S1 or higher is required for the autoscale teaching chapter."
  type        = string
  default     = "S1"
}

variable "worker_count" {
  description = "Number of App Service plan workers. Keep at 1 so the planted bottlenecks are visible until you scale deliberately."
  type        = number
  default     = 1
}

variable "load_test_contributor_principal_ids" {
  description = "Optional Entra ID object IDs, usually groups, granted Load Test Contributor on the Azure Load Testing resource."
  type        = list(string)
  default     = []
}

variable "demo_api_key_value" {
  description = "Fake demo credential stored as Key Vault secret demo-api-key. Override from protected automation for private demos; never commit real credentials."
  type        = string
  default     = "contoso-demo-api-key"
  sensitive   = true
}

variable "key_vault_public_network_access_enabled" {
  description = "Intended Key Vault public network access. Some customer policies rewrite this to false after create; see main.tf before changing."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Business tags merged with azd-env-name and applied to every Azure resource."
  type        = map(string)
  default = {
    workload   = "contoso-bank-loadtest-demo"
    costCentre = "demo"
  }
}

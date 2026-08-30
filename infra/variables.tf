variable "name_prefix" {
  description = "Short prefix for all resource names. Keep it lowercase and alphanumeric."
  type        = string
  default     = "contosobank"

  validation {
    condition     = can(regex("^[a-z0-9]{3,16}$", var.name_prefix))
    error_message = "name_prefix must be 3-16 lowercase alphanumeric characters."
  }
}

variable "location" {
  description = "Azure region. Must be a region where Azure Load Testing is available."
  type        = string
  default     = "uksouth"
}

variable "resource_group_name" {
  description = "Resource group to deploy into. Created if create_resource_group is true."
  type        = string
  default     = null
}

variable "create_resource_group" {
  description = "Set false in environments where the platform team pre-creates resource groups."
  type        = bool
  default     = true
}

variable "log_analytics_workspace_id" {
  description = "Existing Log Analytics workspace resource ID for diagnostic settings. If null, one is created."
  type        = string
  default     = null
}

variable "load_test_contributor_principal_ids" {
  description = "Entra ID object IDs (typically groups) granted Load Test Contributor. Least privilege: give groups, not users."
  type        = list(string)
  default     = []
}

variable "monitoring_reader_scope_id" {
  description = "Scope the load test identity gets Monitoring Reader on, so App Components can read server-side metrics. Defaults to the resource group. Set this to the resource group ID of the system under test if it lives elsewhere."
  type        = string
  default     = null
}

variable "key_vault_secrets_officer_principal_id" {
  description = "Optional Entra ID object ID to receive Key Vault Secrets Officer on the demo vault. Defaults to the principal running Terraform."
  type        = string
  default     = null
}

variable "demo_api_key_value" {
  description = "Optional fake demo credential written to Key Vault as demo-api-key. Override from a protected variable for real demos; do not commit real values."
  type        = string
  default     = null
  sensitive   = true
}

variable "tags" {
  description = "Tags applied to every resource. FSI customers will have mandatory tag policy - align these before you deploy."
  type        = map(string)
  default = {
    workload           = "load-testing"
    environment        = "demo"
    dataClassification = "non-production"
    managedBy          = "terraform"
  }
}

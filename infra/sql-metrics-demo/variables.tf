variable "subscription_id" {
  description = "Explicitly authorized subscription. The shared CLI default is never changed."
  type        = string
  validation {
    condition     = can(regex("^[0-9a-fA-F-]{36}$", var.subscription_id))
    error_message = "Supply a subscription GUID."
  }
}

variable "tenant_id" {
  description = "Directory of the selected subscription and Entra administrator."
  type        = string
}

variable "demo_name" {
  description = "Unique, disposable demo instance; not an existing application environment."
  type        = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,19}$", var.demo_name))
    error_message = "Use 3-20 lowercase letters, digits or hyphens, starting with a letter."
  }
}

variable "location" {
  description = "Must match the existing Load Testing resource; prepare discovers it."
  type        = string
}

variable "load_test_resource_id" {
  description = "Existing ALT resource to read and reuse, never manage or replace."
  type        = string
  validation {
    condition     = can(regex("(?i)^/subscriptions/${var.subscription_id}/resourceGroups/[^/]+/providers/Microsoft.LoadTestService/loadTests/[^/]+$", var.load_test_resource_id))
    error_message = "Use an ALT ARM ID in the explicitly selected subscription."
  }
}

variable "entra_admin_object_id" {
  description = "Object ID of the signed-in bootstrap operator in the selected tenant."
  type        = string
}

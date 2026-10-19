variable "subscription_id" {
  description = "Explicit target subscription; never change the CLI's global default."
  type        = string
}

variable "tenant_id" {
  description = "Directory containing the target subscription."
  type        = string
}

variable "location" {
  description = "Region for the isolated proof of concept."
  type        = string
  default     = "swedencentral"
}

variable "resource_group_name" {
  type    = string
  default = "rg-contoso-gitlab-demo"
}

variable "owner" {
  type    = string
  default = "sanjaysingh"
}

variable "deploy_gitlab" {
  description = "Enable after ACR images have been built. Not a per-MR operation."
  type        = bool
  default     = false
}

variable "deploy_runner" {
  description = "Enable after a project-specific runner authentication token exists."
  type        = bool
  default     = false
}

variable "use_clean_environment" {
  description = "One explicit recovery environment/subnet, retaining the original failed target."
  type        = bool
  default     = false
}

variable "gitlab_revision_mode" {
  description = "Keep Single normally; use Multiple only during a stopped, two-phase image update."
  type        = string
  default     = "Single"
  validation {
    condition     = contains(["Single", "Multiple"], var.gitlab_revision_mode)
    error_message = "GitLab revision mode must be Single or Multiple."
  }
}

variable "gitlab_root_password" {
  type      = string
  sensitive = true
}

variable "gitlab_bootstrap_token" {
  description = "Short-lived local GitLab bootstrap credential, never an Azure credential."
  type        = string
  sensitive   = true
}

variable "runner_token" {
  type      = string
  sensitive = true
  default   = ""
}

variable "app_admin_token" {
  type      = string
  sensitive = true
}

variable "gitlab_image_tag" {
  type    = string
  default = "gitlab:19.4.2-demo-v3"
}

variable "runner_image_tag" {
  type    = string
  default = "runner:19.4.0-demo-v2"
}

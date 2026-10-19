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
  description = "Enable the stateless public proxy after official GitLab on the VM is ready. Not a per-MR operation."
  type        = bool
  default     = false
  validation {
    condition     = !var.deploy_gitlab || var.deploy_gitlab_vm
    error_message = "The public GitLab proxy requires the managed-disk VM."
  }
}

variable "deploy_runner" {
  description = "Enable after a project-specific runner authentication token exists."
  type        = bool
  default     = false
}

variable "use_clean_environment" {
  description = "Retain the selected environment/domain from the historical migration; do not toggle for code cleanup."
  type        = bool
  default     = false
}

variable "gitlab_revision_mode" {
  description = "The stateless proxy is Single normally; Multiple is reserved for controlled migration/maintenance."
  type        = string
  default     = "Single"
  validation {
    condition     = contains(["Single", "Multiple"], var.gitlab_revision_mode)
    error_message = "GitLab revision mode must be Single or Multiple."
  }
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

variable "runner_image_tag" {
  type    = string
  default = "runner:19.4.0-demo-v3"
}

variable "deploy_gitlab_vm" {
  description = "Provision the private managed-disk GitLab host without changing the public front door yet."
  type        = bool
  default     = false
}

variable "gitlab_vm_ssh_public_key" {
  description = "Provisioning-only public key; the private VM has no inbound SSH rule or public IP."
  type        = string
  default     = ""
}

variable "gitlab_proxy_image" {
  description = "Immutable ACR reference to the unmodified official nginx image."
  type        = string
  default     = "nginx-proxy:stable-alpine"
}

variable "gitlab_vm_readonly_lock" {
  description = "Apply only after migration/admin/backup/reboot verification; VM-only, not resource-group protection."
  type        = bool
  default     = false
  validation {
    condition     = !var.gitlab_vm_readonly_lock || (var.deploy_gitlab_vm && var.deploy_gitlab)
    error_message = "ReadOnly protection requires a provisioned, cut-over GitLab VM."
  }
}

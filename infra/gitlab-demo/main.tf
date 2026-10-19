locals {
  token = substr(sha256("${var.subscription_id}-${var.resource_group_name}"), 0, 6)
  tags = {
    SecurityControl = "Ignore"
    owner           = var.owner
    project         = "contoso-gitlab-demo"
    purpose         = "experimental-aca-only-poc"
  }
  active_environment_id     = var.use_clean_environment ? azurerm_container_app_environment.clean[0].id : azurerm_container_app_environment.demo.id
  active_environment_domain = var.use_clean_environment ? azurerm_container_app_environment.clean[0].default_domain : azurerm_container_app_environment.demo.default_domain
  gitlab_name               = var.use_clean_environment ? "gitlab-clean" : "gitlab"
  gitlab_storage_name       = var.use_clean_environment ? azurerm_container_app_environment_storage.clean[0].name : azurerm_container_app_environment_storage.gitlab.name
  gitlab_url                = "https://${local.gitlab_name}.${local.active_environment_domain}"
  app_settings = {
    SCM_DO_BUILD_DURING_DEPLOYMENT      = "true"
    ENABLE_ORYX_BUILD                   = "true"
    WEBSITES_CONTAINER_START_TIME_LIMIT = "600"
    PYTHON_ENABLE_GUNICORN_MULTIWORKERS = "false"
    ENABLE_CACHE                        = "true"
    DB_POOL_SIZE                        = "64"
    DB_QUERY_MS                         = "25"
    ENABLE_TRANSFER_LOCK                = "false"
    ENABLE_N_PLUS_ONE                   = "false"
    FRAUD_CHECK_PARALLEL                = "true"
    STATEMENT_CPU_ROUNDS                = "1500"
    ERROR_RATE_PERCENT                  = "0"
    LOG_REQUESTS                        = "true"
    DEMO_ADMIN_TOKEN                    = var.app_admin_token
  }
}

resource "azurerm_resource_group" "demo" {
  name     = var.resource_group_name
  location = var.location
  tags     = local.tags
}

resource "azurerm_virtual_network" "demo" {
  name                = "vnet-gitlab-demo"
  location            = var.location
  resource_group_name = azurerm_resource_group.demo.name
  address_space       = ["10.74.0.0/16"]
  tags                = local.tags
}

resource "azurerm_subnet" "aca" {
  name                 = "container-apps"
  resource_group_name  = azurerm_resource_group.demo.name
  virtual_network_name = azurerm_virtual_network.demo.name
  address_prefixes     = ["10.74.0.0/23"]
  service_endpoint {
    service = "Microsoft.Storage"
  }

  delegation {
    name = "container-apps"
    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_subnet" "clean" {
  count                = var.use_clean_environment ? 1 : 0
  name                 = "container-apps-clean"
  resource_group_name  = azurerm_resource_group.demo.name
  virtual_network_name = azurerm_virtual_network.demo.name
  address_prefixes     = ["10.74.2.0/23"]
  service_endpoint {
    service = "Microsoft.Storage"
  }
  delegation {
    name = "container-apps"
    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_log_analytics_workspace" "demo" {
  name                = "log-gitlab-${local.token}"
  location            = var.location
  resource_group_name = azurerm_resource_group.demo.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  daily_quota_gb      = 1
  tags                = local.tags
}

resource "azurerm_container_app_environment" "demo" {
  name                       = "cae-gitlab-demo"
  location                   = var.location
  resource_group_name        = azurerm_resource_group.demo.name
  infrastructure_subnet_id   = azurerm_subnet.aca.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.demo.id
  logs_destination           = "log-analytics"

  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
    minimum_count         = 0
    maximum_count         = 0
  }

  tags = local.tags
}

resource "azurerm_container_app_environment" "clean" {
  count                      = var.use_clean_environment ? 1 : 0
  name                       = "cae-gitlab-demo-clean"
  location                   = var.location
  resource_group_name        = azurerm_resource_group.demo.name
  infrastructure_subnet_id   = azurerm_subnet.clean[0].id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.demo.id
  logs_destination           = "log-analytics"
  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
    minimum_count         = 0
    maximum_count         = 0
  }
  tags = local.tags
}

# AzureRM cannot import a Failed app because listSecrets rejects that state.
# Inventory it read-only, preserving the failed target rather than deleting it.
data "azurerm_resources" "retained_gitlab" {
  count               = var.use_clean_environment ? 1 : 0
  resource_group_name = azurerm_resource_group.demo.name
  name                = "gitlab"
  type                = "Microsoft.App/containerApps"
}

data "azurerm_resources" "diagnostic_job" {
  count               = var.use_clean_environment ? 1 : 0
  resource_group_name = azurerm_resource_group.demo.name
  name                = "aca-platform-probe"
  type                = "Microsoft.App/jobs"
}

# Experimental only: GitLab explicitly advises against Azure Files/NFS.
# ACA NFS has no in-transit encryption; only this VNet can reach the share.
resource "azurerm_storage_account" "gitlab" {
  name                            = "stgitlab${local.token}"
  location                        = var.location
  resource_group_name             = azurerm_resource_group.demo.name
  account_kind                    = "FileStorage"
  account_tier                    = "Premium"
  account_replication_type        = "LRS"
  https_traffic_only_enabled      = false
  shared_access_key_enabled       = false
  allow_nested_items_to_be_public = false
  min_tls_version                 = "TLS1_2"

  network_rules {
    default_action = "Deny"
    bypass         = ["AzureServices"]
    virtual_network_subnet_ids = concat(
      [azurerm_subnet.aca.id],
      var.use_clean_environment ? [azurerm_subnet.clean[0].id] : []
    )
  }

  tags = local.tags
}

resource "azurerm_storage_share" "gitlab" {
  name               = "gitlab"
  storage_account_id = azurerm_storage_account.gitlab.id
  quota              = 100
  enabled_protocol   = "NFS"
}

resource "azurerm_container_app_environment_storage" "gitlab" {
  name                         = "gitlab-nfs"
  container_app_environment_id = azurerm_container_app_environment.demo.id
  share_name                   = "/${azurerm_storage_account.gitlab.name}/${azurerm_storage_share.gitlab.name}"
  nfs_server_url               = "${azurerm_storage_account.gitlab.name}.file.core.windows.net"
  access_mode                  = "ReadWrite"
}

resource "azurerm_container_app_environment_storage" "clean" {
  count                        = var.use_clean_environment ? 1 : 0
  name                         = "gitlab-nfs"
  container_app_environment_id = azurerm_container_app_environment.clean[0].id
  share_name                   = "/${azurerm_storage_account.gitlab.name}/${azurerm_storage_share.gitlab.name}"
  nfs_server_url               = "${azurerm_storage_account.gitlab.name}.file.core.windows.net"
  access_mode                  = "ReadWrite"
}

resource "azurerm_container_registry" "demo" {
  name                = "acrgitlab${local.token}"
  location            = var.location
  resource_group_name = azurerm_resource_group.demo.name
  sku                 = "Basic"
  admin_enabled       = false
  tags                = local.tags
}

resource "azurerm_user_assigned_identity" "gitlab" {
  name                = "mi-gitlab-demo"
  location            = var.location
  resource_group_name = azurerm_resource_group.demo.name
  tags                = local.tags
}

resource "azurerm_user_assigned_identity" "runner" {
  name                = "mi-gitlab-demo-runner"
  location            = var.location
  resource_group_name = azurerm_resource_group.demo.name
  tags                = local.tags
}

resource "azurerm_role_assignment" "image_pull" {
  for_each = {
    gitlab = azurerm_user_assigned_identity.gitlab.principal_id
    runner = azurerm_user_assigned_identity.runner.principal_id
  }
  scope                = azurerm_container_registry.demo.id
  role_definition_name = "AcrPull"
  principal_id         = each.value
}

resource "azurerm_service_plan" "demo" {
  name                = "plan-gitlab-demo"
  location            = var.location
  resource_group_name = azurerm_resource_group.demo.name
  os_type             = "Linux"
  sku_name            = "S1"
  worker_count        = 1
  tags                = local.tags
}

resource "azurerm_linux_web_app" "demo" {
  name                                           = "app-gitlab-demo-${local.token}"
  location                                       = var.location
  resource_group_name                            = azurerm_resource_group.demo.name
  service_plan_id                                = azurerm_service_plan.demo.id
  https_only                                     = true
  ftp_publish_basic_authentication_enabled       = false
  webdeploy_publish_basic_authentication_enabled = false

  site_config {
    always_on           = true
    ftps_state          = "Disabled"
    minimum_tls_version = "1.2"
    app_command_line    = "gunicorn main:app --workers 1 --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000 --timeout 600 --access-logfile - --error-logfile -"
    application_stack {
      python_version = "3.12"
    }
  }

  logs {
    application_logs {
      file_system_level = "Information"
    }
    http_logs {
      file_system {
        retention_in_days = 3
        retention_in_mb   = 35
      }
    }
  }
  app_settings = local.app_settings
  tags         = local.tags
}

resource "azurerm_linux_web_app_slot" "staging" {
  name                                           = "staging"
  app_service_id                                 = azurerm_linux_web_app.demo.id
  https_only                                     = true
  ftp_publish_basic_authentication_enabled       = false
  webdeploy_publish_basic_authentication_enabled = false

  site_config {
    always_on           = true
    ftps_state          = "Disabled"
    minimum_tls_version = "1.2"
    app_command_line    = azurerm_linux_web_app.demo.site_config[0].app_command_line
    application_stack {
      python_version = "3.12"
    }
  }

  logs {
    application_logs {
      file_system_level = "Information"
    }
    http_logs {
      file_system {
        retention_in_days = 3
        retention_in_mb   = 35
      }
    }
  }
  app_settings = local.app_settings
  tags         = local.tags
}

resource "azurerm_load_test" "demo" {
  name                = "alt-gitlab-demo-${local.token}"
  location            = var.location
  resource_group_name = azurerm_resource_group.demo.name
  description         = "Isolated GitLab transaction enrichment regression gate"
  identity {
    type = "SystemAssigned"
  }
  tags = local.tags
}

resource "azurerm_role_assignment" "runner_deploy" {
  scope                = azurerm_linux_web_app.demo.id
  role_definition_name = "Website Contributor"
  principal_id         = azurerm_user_assigned_identity.runner.principal_id
}

resource "azurerm_role_assignment" "runner_load" {
  scope                = azurerm_load_test.demo.id
  role_definition_name = "Load Test Contributor"
  principal_id         = azurerm_user_assigned_identity.runner.principal_id
}

resource "azurerm_role_assignment" "presenter_load" {
  scope                = azurerm_load_test.demo.id
  role_definition_name = "Load Test Contributor"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_container_app" "gitlab" {
  count                        = var.deploy_gitlab ? 1 : 0
  name                         = local.gitlab_name
  resource_group_name          = azurerm_resource_group.demo.name
  container_app_environment_id = local.active_environment_id
  revision_mode                = var.gitlab_revision_mode
  max_inactive_revisions       = 1

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.gitlab.id]
  }
  registry {
    server   = azurerm_container_registry.demo.login_server
    identity = azurerm_user_assigned_identity.gitlab.id
  }
  secret {
    name  = "root-password"
    value = var.gitlab_root_password
  }
  secret {
    name  = "bootstrap-token"
    value = var.gitlab_bootstrap_token
  }
  ingress {
    external_enabled           = true
    allow_insecure_connections = false
    target_port                = 80
    transport                  = "http"
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }
  template {
    min_replicas                     = 1
    max_replicas                     = 1
    termination_grace_period_seconds = 120

    volume {
      name          = "gitlab"
      storage_name  = local.gitlab_storage_name
      storage_type  = "NfsAzureFile"
      mount_options = "nfsvers=4.1,hard,timeo=600,retrans=2"
    }
    init_container {
      name    = "prepare-storage"
      image   = "${azurerm_container_registry.demo.login_server}/${var.gitlab_image_tag}"
      cpu     = 0.25
      memory  = "0.5Gi"
      command = ["/bin/sh", "-ec"]
      args    = ["mkdir -p /storage/config /storage/data /storage/logs"]
      volume_mounts {
        name = "gitlab"
        path = "/storage"
      }
    }
    container {
      name  = "gitlab"
      image = "${azurerm_container_registry.demo.login_server}/${var.gitlab_image_tag}"
      # The init container and main container together must fit Consumption's 4/8 limit.
      cpu    = 3.75
      memory = "7.5Gi"

      env {
        name  = "GITLAB_EXTERNAL_URL"
        value = local.gitlab_url
      }
      env {
        name        = "GITLAB_ROOT_PASSWORD"
        secret_name = "root-password"
      }
      env {
        name        = "GITLAB_BOOTSTRAP_TOKEN"
        secret_name = "bootstrap-token"
      }
      volume_mounts {
        name     = "gitlab"
        path     = "/etc/gitlab"
        sub_path = "config"
      }
      volume_mounts {
        name     = "gitlab"
        path     = "/var/opt/gitlab"
        sub_path = "data"
      }
      volume_mounts {
        name     = "gitlab"
        path     = "/var/log/gitlab"
        sub_path = "logs"
      }
      startup_probe {
        transport               = "HTTP"
        port                    = 9090
        path                    = "/"
        interval_seconds        = 10
        timeout                 = 3
        failure_count_threshold = 180
      }
      readiness_probe {
        transport        = "HTTP"
        port             = 9090
        path             = "/"
        interval_seconds = 10
        timeout          = 3
      }
      liveness_probe {
        transport               = "HTTP"
        port                    = 9090
        path                    = "/live"
        interval_seconds        = 30
        timeout                 = 5
        failure_count_threshold = 10
      }
    }
  }
  tags = local.tags

  depends_on = [azurerm_role_assignment.image_pull]
}

resource "azurerm_container_app" "runner" {
  count                        = var.deploy_runner ? 1 : 0
  name                         = "gitlab-runner"
  resource_group_name          = azurerm_resource_group.demo.name
  container_app_environment_id = local.active_environment_id
  revision_mode                = "Single"
  max_inactive_revisions       = 1
  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.runner.id]
  }
  registry {
    server   = azurerm_container_registry.demo.login_server
    identity = azurerm_user_assigned_identity.runner.id
  }
  secret {
    name  = "runner-token"
    value = var.runner_token
  }
  template {
    min_replicas                     = 1
    max_replicas                     = 1
    termination_grace_period_seconds = 120
    container {
      name   = "runner"
      image  = "${azurerm_container_registry.demo.login_server}/${var.runner_image_tag}"
      cpu    = 1
      memory = "2Gi"
      env {
        name  = "GITLAB_URL"
        value = local.gitlab_url
      }
      env {
        name        = "GITLAB_RUNNER_TOKEN"
        secret_name = "runner-token"
      }
    }
  }
  tags       = local.tags
  depends_on = [azurerm_role_assignment.image_pull]
}

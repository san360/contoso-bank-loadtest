locals {
  token               = substr(sha256("${var.subscription_id}/${lower(var.load_test_resource_id)}/${var.demo_name}"), 0, 10)
  resource_group_name = "rg-sqlmetrics-${var.demo_name}-${substr(local.token, 0, 6)}"
  tags = {
    demo     = "azure-sql-metrics"
    demo-id  = local.token
    instance = var.demo_name
  }
  alt_parts = split("/", var.load_test_resource_id)
}

data "azurerm_load_test" "existing" {
  name                = local.alt_parts[8]
  resource_group_name = local.alt_parts[4]
}

resource "azurerm_resource_group" "demo" {
  name     = local.resource_group_name
  location = var.location
  tags     = local.tags

  lifecycle {
    precondition {
      condition     = lower(data.azurerm_load_test.existing.location) == lower(var.location)
      error_message = "ALT and its injected subnet must be in the same region."
    }
  }
}

resource "azurerm_virtual_network" "demo" {
  name                = "vnet-sqlmetrics"
  location            = azurerm_resource_group.demo.location
  resource_group_name = azurerm_resource_group.demo.name
  address_space       = ["10.247.0.0/24"]
  tags                = local.tags
}

resource "azurerm_subnet" "engines" {
  name                 = "load-engines"
  resource_group_name  = azurerm_resource_group.demo.name
  virtual_network_name = azurerm_virtual_network.demo.name
  address_prefixes     = ["10.247.0.0/26"]
}

resource "azurerm_subnet" "private_endpoint" {
  name                 = "sql-private-endpoint"
  resource_group_name  = azurerm_resource_group.demo.name
  virtual_network_name = azurerm_virtual_network.demo.name
  address_prefixes     = ["10.247.0.64/27"]
}

resource "azurerm_mssql_server" "demo" {
  name                          = "sql-metrics-${local.token}"
  resource_group_name           = azurerm_resource_group.demo.name
  location                      = azurerm_resource_group.demo.location
  version                       = "12.0"
  minimum_tls_version           = "1.2"
  connection_policy             = "Proxy"
  public_network_access_enabled = false

  azuread_administrator {
    login_username              = "sql-metrics-bootstrap-operator"
    object_id                   = var.entra_admin_object_id
    tenant_id                   = var.tenant_id
    azuread_authentication_only = true
  }

  tags = local.tags
}

resource "azurerm_mssql_database" "demo" {
  name                 = "metrics-demo"
  server_id            = azurerm_mssql_server.demo.id
  sku_name             = "Basic"
  max_size_gb          = 2
  storage_account_type = "Local"

  short_term_retention_policy {
    retention_days = 7
  }

  tags = local.tags
}

resource "azurerm_private_dns_zone" "sql" {
  name                = "privatelink.database.windows.net"
  resource_group_name = azurerm_resource_group.demo.name
  tags                = local.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "demo" {
  name                 = "only-demo-vnet"
  private_dns_zone_id  = azurerm_private_dns_zone.sql.id
  virtual_network_id   = azurerm_virtual_network.demo.id
  registration_enabled = false
  tags                 = local.tags
}

resource "azurerm_private_endpoint" "sql" {
  name                = "sql-metrics"
  location            = azurerm_resource_group.demo.location
  resource_group_name = azurerm_resource_group.demo.name
  subnet_id           = azurerm_subnet.private_endpoint.id
  tags                = local.tags

  private_service_connection {
    name                           = "only-demo-sql"
    private_connection_resource_id = azurerm_mssql_server.demo.id
    subresource_names              = ["sqlServer"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "default"
    private_dns_zone_ids = [azurerm_private_dns_zone.sql.id]
  }
}

resource "azurerm_role_assignment" "metrics_reader" {
  scope                            = azurerm_mssql_database.demo.id
  role_definition_name             = "Monitoring Reader"
  principal_id                     = data.azurerm_load_test.existing.identity[0].principal_id
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}

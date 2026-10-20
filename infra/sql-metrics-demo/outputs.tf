output "resources" {
  value = {
    resource_group_id      = azurerm_resource_group.demo.id
    server_id              = azurerm_mssql_server.demo.id
    server_fqdn            = azurerm_mssql_server.demo.fully_qualified_domain_name
    database_id            = azurerm_mssql_database.demo.id
    subnet_id              = azurerm_subnet.engines.id
    private_endpoint_id    = azurerm_private_endpoint.sql.id
    private_dns_zone_id    = azurerm_private_dns_zone.sql.id
    load_test_resource_id  = data.azurerm_load_test.existing.id
    load_test_principal_id = data.azurerm_load_test.existing.identity[0].principal_id
  }
}

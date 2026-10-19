resource "azurerm_subnet" "gitlab_vm" {
  count                           = var.deploy_gitlab_vm ? 1 : 0
  name                            = "gitlab-vm"
  resource_group_name             = azurerm_resource_group.demo.name
  virtual_network_name            = azurerm_virtual_network.demo.name
  address_prefixes                = ["10.74.4.0/24"]
  default_outbound_access_enabled = false
  service_endpoint {
    service = "Microsoft.Storage"
  }
}

resource "azurerm_public_ip" "gitlab_egress" {
  count               = var.deploy_gitlab_vm ? 1 : 0
  name                = "pip-gitlab-vm-egress"
  location            = var.location
  resource_group_name = azurerm_resource_group.demo.name
  allocation_method   = "Static"
  sku                 = "Standard"
  tags                = local.tags
  lifecycle {
    # Preserve the platform-injected FirstPartyUsage metadata without replacing egress.
    ignore_changes = [ip_tags]
  }
}

resource "azurerm_nat_gateway" "gitlab_vm" {
  count                   = var.deploy_gitlab_vm ? 1 : 0
  name                    = "nat-gitlab-vm"
  location                = var.location
  resource_group_name     = azurerm_resource_group.demo.name
  sku_name                = "Standard"
  idle_timeout_in_minutes = 10
  tags                    = local.tags
}

resource "azurerm_nat_gateway_public_ip_association" "gitlab_vm" {
  count                = var.deploy_gitlab_vm ? 1 : 0
  nat_gateway_id       = azurerm_nat_gateway.gitlab_vm[0].id
  public_ip_address_id = azurerm_public_ip.gitlab_egress[0].id
}

resource "azurerm_subnet_nat_gateway_association" "gitlab_vm" {
  count          = var.deploy_gitlab_vm ? 1 : 0
  subnet_id      = azurerm_subnet.gitlab_vm[0].id
  nat_gateway_id = azurerm_nat_gateway.gitlab_vm[0].id
}

resource "azurerm_network_security_group" "gitlab_vm" {
  count               = var.deploy_gitlab_vm ? 1 : 0
  name                = "nsg-gitlab-vm"
  location            = var.location
  resource_group_name = azurerm_resource_group.demo.name
  tags                = local.tags

  security_rule {
    name                       = "GitLabHTTPFromACA"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "80"
    source_address_prefixes    = concat(["10.74.0.0/23"], var.use_clean_environment ? ["10.74.2.0/23"] : [])
    destination_address_prefix = "10.74.4.4"
  }
  security_rule {
    name                       = "DenyOtherInbound"
    priority                   = 200
    direction                  = "Inbound"
    access                     = "Deny"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }
}

resource "azurerm_subnet_network_security_group_association" "gitlab_vm" {
  count                     = var.deploy_gitlab_vm ? 1 : 0
  subnet_id                 = azurerm_subnet.gitlab_vm[0].id
  network_security_group_id = azurerm_network_security_group.gitlab_vm[0].id
}

resource "azurerm_network_interface" "gitlab_vm" {
  count                          = var.deploy_gitlab_vm ? 1 : 0
  name                           = "nic-gitlab-vm"
  location                       = var.location
  resource_group_name            = azurerm_resource_group.demo.name
  accelerated_networking_enabled = true
  tags                           = local.tags
  ip_configuration {
    name                          = "private"
    subnet_id                     = azurerm_subnet.gitlab_vm[0].id
    private_ip_address_allocation = "Static"
    private_ip_address            = "10.74.4.4"
  }
}

resource "azurerm_linux_virtual_machine" "gitlab" {
  count                           = var.deploy_gitlab_vm ? 1 : 0
  name                            = "vm-gitlab-demo"
  computer_name                   = "gitlab-vm"
  location                        = var.location
  resource_group_name             = azurerm_resource_group.demo.name
  size                            = "Standard_D4as_v5"
  admin_username                  = "azureadmin"
  disable_password_authentication = true
  network_interface_ids           = [azurerm_network_interface.gitlab_vm[0].id]
  secure_boot_enabled             = true
  vtpm_enabled                    = true
  disk_controller_type            = "SCSI"
  patch_mode                      = "ImageDefault"
  custom_data                     = filebase64("${path.module}/vm-cloud-init.yml")
  tags                            = merge(local.tags, { purpose = "gitlab-managed-ssd-demo" })

  admin_ssh_key {
    username   = "azureadmin"
    public_key = var.gitlab_vm_ssh_public_key
  }
  source_image_reference {
    publisher = "Canonical"
    offer     = "ubuntu-24_04-lts"
    sku       = "server"
    version   = "24.04.202610160"
  }
  os_disk {
    name                 = "disk-gitlab-vm-os"
    caching              = "ReadWrite"
    storage_account_type = "StandardSSD_LRS"
    disk_size_gb         = 64
  }
  boot_diagnostics {}
  lifecycle {
    # Cloud-init is creation-only; existing hosts receive verified Run Command maintenance.
    ignore_changes = [custom_data]
  }
  depends_on = [
    azurerm_nat_gateway_public_ip_association.gitlab_vm,
    azurerm_subnet_nat_gateway_association.gitlab_vm,
    azurerm_subnet_network_security_group_association.gitlab_vm,
  ]
}

resource "azurerm_managed_disk" "gitlab_data" {
  count                         = var.deploy_gitlab_vm ? 1 : 0
  name                          = "disk-gitlab-vm-data"
  location                      = var.location
  resource_group_name           = azurerm_resource_group.demo.name
  storage_account_type          = "Premium_LRS"
  create_option                 = "Empty"
  disk_size_gb                  = 128
  public_network_access_enabled = false
  network_access_policy         = "DenyAll"
  tags                          = local.tags
}

resource "azurerm_virtual_machine_data_disk_attachment" "gitlab" {
  count              = var.deploy_gitlab_vm ? 1 : 0
  managed_disk_id    = azurerm_managed_disk.gitlab_data[0].id
  virtual_machine_id = azurerm_linux_virtual_machine.gitlab[0].id
  lun                = 0
  caching            = "None"
}

resource "azurerm_management_lock" "gitlab_data" {
  count      = var.deploy_gitlab_vm ? 1 : 0
  name       = "preserve-gitlab-data"
  scope      = azurerm_managed_disk.gitlab_data[0].id
  lock_level = "CanNotDelete"
  notes      = "Preserve migrated GitLab state; deliberate authorized cleanup must remove this lock first."
}

resource "azurerm_management_lock" "gitlab_vm" {
  count      = var.gitlab_vm_readonly_lock ? 1 : 0
  name       = "protect-gitlab-power-operations"
  scope      = azurerm_linux_virtual_machine.gitlab[0].id
  lock_level = "ReadOnly"
  notes      = "User-approved after migration verification. Blocks ARM power/management writes, not guest OS or platform failures. Authorized maintenance requires explicit unlock."
}

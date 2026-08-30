terraform {
  required_version = ">= 1.5.0"

  required_providers {
    azurerm = {
      source = "hashicorp/azurerm"
      # azurerm_load_test was introduced in provider version 3.70.0.
      version = "~> 3.70"
    }
  }

  # For a bank, state lives in a locked-down storage account, not on a laptop.
  # Fill this in per environment, or pass it with -backend-config at init time.
  #
  # backend "azurerm" {
  #   resource_group_name  = "rg-tfstate"
  #   storage_account_name = "sttfstateprod"
  #   container_name       = "tfstate"
  #   key                  = "loadtesting.tfstate"
  #   use_azuread_auth     = true
  # }
}

provider "azurerm" {
  features {}
}

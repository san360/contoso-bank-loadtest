#==============================================================================
# Terraform providers and state backend
#==============================================================================
# azd's Terraform support runs this folder directly. The empty backend block below
# is intentional: when infra/provider.conf.json is present, azd substitutes values
# from the environment and passes them to `terraform init -backend-config`.
#
# For a quick single-presenter demo without remote state, run Terraform directly
# with `terraform init -backend=false`, or temporarily move provider.conf.json out
# of the infra folder before using azd. For CI/CD and team demos, bootstrap the
# storage account first with tools/bootstrap_tfstate.ps1 or .sh.
#==============================================================================

terraform {
  required_version = ">= 1.5.0, < 2.0.0"

  # ---------------------------------------------------------------------------
  # State backend: LOCAL by default, on purpose.
  # ---------------------------------------------------------------------------
  # `azd up` must work out of the box, so this kit ships with local state and no
  # backend block. Terraform writes terraform.tfstate into infra/ (gitignored).
  #
  # To switch to remote state (required before `azd pipeline config`):
  #   1. Run tools/bootstrap_tfstate.ps1 / .sh
  #   2. Rename infra/provider.conf.json.example -> infra/provider.conf.json
  #   3. Uncomment the backend block below
  #   4. azd env set RS_STORAGE_ACCOUNT / RS_CONTAINER_NAME / RS_RESOURCE_GROUP
  #
  # backend "azurerm" {}
  #
  # KNOWN BLOCKER IN THIS SUBSCRIPTION - read before you spend an hour on it.
  # An Azure Policy forces `publicNetworkAccess: Disabled` onto new storage
  # accounts and also forbids shared-key auth. `az storage account update
  # --public-network-access Enabled` returns exit code 0 and the value stays
  # Disabled, and nothing is ever reported as non-compliant. The blob endpoint is
  # therefore unreachable from a developer workstation AND from a GitHub-hosted
  # runner, so remote state here needs a private endpoint plus a runner inside
  # that VNet (self-hosted, or an Azure DevOps managed pool). See infra/README.md.
  # ---------------------------------------------------------------------------

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.2"
    }

    # Key Vault public network access may be disabled by customer policy. The
    # azapi secret resource writes the demo secret through ARM, avoiding a hard
    # data-plane dependency during `azd up` while still leaving the secret in
    # Terraform state like any other demo secret value.
    azapi = {
      source  = "Azure/azapi"
      version = "~> 2.0"
    }
  }
}

provider "azurerm" {
  subscription_id = var.subscription_id
  tenant_id       = var.tenant_id

  features {
    key_vault {
      purge_soft_delete_on_destroy = false
    }
  }
}

provider "azapi" {
  subscription_id = var.subscription_id
  tenant_id       = var.tenant_id
}




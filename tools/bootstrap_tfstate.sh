#!/usr/bin/env sh
# =============================================================================
# Contoso Bank - Terraform state bootstrap for azd
# =============================================================================
# Terraform cannot create the storage account that stores its own state. Run this
# once before `azd up` when you want remote state for CI/CD or team demos. The
# script is idempotent and prints the azd env commands needed by infra/provider.conf.json.
# =============================================================================

set -eu

say() { printf '%s\n' "$1"; }
fail() { printf 'Failed to bootstrap Terraform state. %s\n' "$1" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || fail "'$1' is not on PATH."; }

sha256_hex() {
    if command -v sha256sum >/dev/null 2>&1; then
        printf '%s' "$1" | sha256sum | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        printf '%s' "$1" | shasum -a 256 | awk '{print $1}'
    else
        fail "sha256sum or shasum is required to derive a deterministic storage account name."
    fi
}

state_account_name() {
    env_name=$1
    subscription_id=$2
    location=$3
    normalized=$(printf '%s' "$env_name" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9' | cut -c 1-12)
    [ -n "$normalized" ] || normalized=azd
    hash=$(sha256_hex "$subscription_id-$env_name-$location" | cut -c 1-10)
    name="st${normalized}${hash}"
    printf '%s' "$name" | grep -Eq '^[a-z0-9]{3,24}$' || fail "Generated storage account name '$name' violates Azure naming rules. Set STORAGE_ACCOUNT_NAME explicitly."
    printf '%s' "$name"
}

need az

ENVIRONMENT_NAME=${ENVIRONMENT_NAME:-${AZURE_ENV_NAME:-loadtest}}
LOCATION=${LOCATION:-${AZURE_LOCATION:-swedencentral}}
SUBSCRIPTION_ID=${SUBSCRIPTION_ID:-${AZURE_SUBSCRIPTION_ID:-}}
TENANT_ID=${TENANT_ID:-${AZURE_TENANT_ID:-}}
RESOURCE_GROUP_NAME=${RESOURCE_GROUP_NAME:-${RS_RESOURCE_GROUP:-rg-${ENVIRONMENT_NAME}-tfstate}}
CONTAINER_NAME=${CONTAINER_NAME:-${RS_CONTAINER_NAME:-tfstate}}
PRINCIPAL_ID=${PRINCIPAL_ID:-}
PRINCIPAL_TYPE=${PRINCIPAL_TYPE:-User}

if ! az account show --only-show-errors -o none >/dev/null 2>&1; then
    if [ -n "$TENANT_ID" ]; then
        fail "Azure CLI is not logged in. Run 'az login --tenant $TENANT_ID' first."
    else
        fail "Azure CLI is not logged in. Run 'az login --tenant <tenant-id>' first."
    fi
fi

if [ -z "$SUBSCRIPTION_ID" ]; then
    SUBSCRIPTION_ID=$(az account show --query id -o tsv --only-show-errors)
fi
az account set --subscription "$SUBSCRIPTION_ID" --only-show-errors -o none

STORAGE_ACCOUNT_NAME=${STORAGE_ACCOUNT_NAME:-$(state_account_name "$ENVIRONMENT_NAME" "$SUBSCRIPTION_ID" "$LOCATION")}
printf '%s' "$STORAGE_ACCOUNT_NAME" | grep -Eq '^[a-z0-9]{3,24}$' || fail "Storage account name '$STORAGE_ACCOUNT_NAME' must be 3-24 lowercase letters or digits."
printf '%s' "$CONTAINER_NAME" | grep -Eq '^[a-z0-9]([a-z0-9-]{1,61}[a-z0-9])?$' || fail "Container name '$CONTAINER_NAME' must be 3-63 lowercase letters, digits or hyphens, and cannot start/end with a hyphen."

say "Creating or reusing Terraform state resource group '$RESOURCE_GROUP_NAME' in '$LOCATION'..."
az group create --name "$RESOURCE_GROUP_NAME" --location "$LOCATION" --only-show-errors -o none

say "Creating or reusing storage account '$STORAGE_ACCOUNT_NAME'..."
az storage account create \
    --name "$STORAGE_ACCOUNT_NAME" \
    --resource-group "$RESOURCE_GROUP_NAME" \
    --location "$LOCATION" \
    --sku Standard_LRS \
    --kind StorageV2 \
    --min-tls-version TLS1_2 \
    --allow-blob-public-access false \
    --allow-shared-key-access true \
    --only-show-errors -o none

say "Creating or reusing blob container '$CONTAINER_NAME'..."
KEY=$(az storage account keys list --account-name "$STORAGE_ACCOUNT_NAME" --resource-group "$RESOURCE_GROUP_NAME" --query '[0].value' -o tsv --only-show-errors)
az storage container create --name "$CONTAINER_NAME" --account-name "$STORAGE_ACCOUNT_NAME" --account-key "$KEY" --only-show-errors -o none >/dev/null
az storage account blob-service-properties update --account-name "$STORAGE_ACCOUNT_NAME" --resource-group "$RESOURCE_GROUP_NAME" --enable-versioning true --only-show-errors -o none
az storage account update --name "$STORAGE_ACCOUNT_NAME" --resource-group "$RESOURCE_GROUP_NAME" --allow-shared-key-access false --only-show-errors -o none

if [ -z "$PRINCIPAL_ID" ]; then
    PRINCIPAL_ID=$(az ad signed-in-user show --query id -o tsv --only-show-errors 2>/dev/null || true)
fi
if [ -n "$PRINCIPAL_ID" ]; then
    SCOPE="/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP_NAME/providers/Microsoft.Storage/storageAccounts/$STORAGE_ACCOUNT_NAME"
    say "Ensuring Storage Blob Data Contributor for '$PRINCIPAL_ID' on the state account..."
    az role assignment create --assignee-object-id "$PRINCIPAL_ID" --assignee-principal-type "$PRINCIPAL_TYPE" --role 'Storage Blob Data Contributor' --scope "$SCOPE" --only-show-errors -o none 2>/dev/null || true
fi

say ""
say "Terraform remote state is ready. Persist these values in the azd environment:"
say "azd env set RS_STORAGE_ACCOUNT $STORAGE_ACCOUNT_NAME"
say "azd env set RS_CONTAINER_NAME $CONTAINER_NAME"
say "azd env set RS_RESOURCE_GROUP $RESOURCE_GROUP_NAME"
say ""
say "Then run: azd up"

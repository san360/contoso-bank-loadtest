# `infra/` — Terraform only

This folder now contains the **single** IaC implementation for the Contoso Bank Azure Load Testing demo. `azd up` uses Terraform to provision the whole environment, including the Python App Service that `azd deploy api` targets.

> Terraform support in Azure Developer CLI is beta. It is not behind an `alpha.terraform` feature flag in azd 1.32.0.

---

## Prerequisite — the Terraform CLI

azd shells out to a real `terraform` binary; it does not embed one. If it is missing, `azd up` fails before a single resource is touched.

```powershell
winget install --exact --id Hashicorp.Terraform
```

> **Gotcha that costs ten confusing minutes.** winget adds Terraform to the **persisted** user `PATH`, but already-running processes keep the environment they started with. A terminal (or editor, or agent session) that was open *before* the install will still report `terraform: The term 'terraform' is not recognized` — while a brand-new terminal works fine. **Close and reopen the terminal after installing**, then confirm:

```powershell
terraform version   # expect v1.5.0 or newer
```

To verify this folder without touching Azure or remote state:

```powershell
terraform init -backend=false
terraform validate
```

---

## Required authentication

Terraform's `azurerm` provider authenticates through **Azure CLI**, not the azd credential cache. Before `azd up`, sign in this way:

```bash
azd config set auth.useAzCliAuth true
az login --tenant <tenant-id>
azd auth login   # only needed if you do not delegate azd auth to Azure CLI
```

If you only run `azd auth login`, provisioning fails with `ERROR: Please run 'az login' to setup account`.

---

## The two template files azd requires

With `provider: terraform`, azd generates **two** files before it ever calls Terraform, and each one is read from a template that must exist in `infra/`. Neither is optional, and neither is created for you.

| Template | Purpose | Contents here |
| --- | --- | --- |
| `infra/main.tfvars.json` | Parameter template. azd substitutes `${...}` from the azd environment and passes the result to Terraform as tfvars. | Supplies the two required variables plus `principal_id` and `subscription_id`. |
| `infra/provider.conf.json` | Backend config template, passed to `terraform init -backend-config`. | `{}` — see below. |

`main.tfvars.json` is what feeds `environment_name` and `location`, the two variables in `variables.tf` that have no default. If the file is missing, `azd up` fails before Terraform runs:

```text
ERROR: creating parameters file: reading parameter file template:
open infra/main.tfvars.json: The system cannot find the file specified.
```

Its contents:

```json
{
  "environment_name": "${AZURE_ENV_NAME}",
  "location": "${AZURE_LOCATION}",
  "principal_id": "${AZURE_PRINCIPAL_ID}",
  "subscription_id": "${AZURE_SUBSCRIPTION_ID}"
}
```

> `terraform.tfvars.example` is a different thing and serves a different audience: it is for running `terraform apply` directly, without azd. azd never reads it.

---

## State: local by default

This kit ships with **local state** so that `azd up` works immediately after clone. Terraform writes `infra/terraform.tfstate`, which `.gitignore` excludes. That is the right default for a single presenter driving a demo.

### Why `provider.conf.json` exists but is empty

`infra/provider.conf.json` contains `{}` — deliberately, and it must not be deleted.

When the provider is Terraform, azd **always** generates a backend config file and passes it to `terraform init`. Removing the file does not select local state; it aborts the run before Terraform is even invoked:

```text
ERROR: terraform init failed: creating terraform backend config file:
reading parameter file template: open infra/provider.conf.json:
The system cannot find the file specified.
```

An empty JSON object satisfies azd, and because `providers.tf` declares no `backend` block, Terraform initialises the local backend and simply notes that there is nothing to override. The real remote-state template is kept alongside it as `provider.conf.json.example`.

Remote state is only needed for CI/CD and shared environments — and in *this* subscription it is currently blocked. Read the next section before trying.

---

## Remote state for CI/CD

Remote state must be enabled **before** `azd pipeline config`. Do not switch an existing environment from local to remote state without tearing it down first.

```powershell
./tools/bootstrap_tfstate.ps1 -SubscriptionId <subscription-id> -TenantId <tenant-id> -EnvironmentName loadtest -Location swedencentral
```

```bash
./tools/bootstrap_tfstate.sh
```

The script creates or reuses a deterministic storage account and container, then prints:

```bash
azd env set RS_STORAGE_ACCOUNT <name>
azd env set RS_CONTAINER_NAME tfstate
azd env set RS_RESOURCE_GROUP <name>
```

To activate it: rename `provider.conf.json.example` to `provider.conf.json`, then uncomment the `backend "azurerm" {}` block in `providers.tf`. azd substitutes the `RS_*` values and initialises the backend automatically.

### ⚠️ Why remote state does not currently work in this subscription

Two Azure Policies block it, and **both fail silently** — which is what makes this expensive to diagnose:

1. **Shared-key auth is forbidden.** `az storage container create --account-key ...` fails with `KeyBasedAuthenticationNotPermitted`. The common bootstrap trick — create the account with keys on, make the container, turn keys off — cannot work. `bootstrap_tfstate.ps1` therefore creates the account key-less and uses Entra ID (`--auth-mode login`), matching `use_azuread_auth = true` in the backend config.

2. **`publicNetworkAccess` is forced to `Disabled`.** This is the blocker. A policy `modify` effect rewrites the property on create. Critically:

   ```powershell
   az storage account update -n <acct> -g <rg> --public-network-access Enabled   # exit code 0
   az storage account show   -n <acct> -g <rg> --query publicNetworkAccess       # -> "Disabled"
   ```

   The command **succeeds** and the value **does not change**. Nothing is ever reported as non-compliant, so the portal shows a healthy account whose blob endpoint is unreachable.

The consequence is architectural, not a bug to fix in this repo: the state container cannot be reached from a developer workstation *or* from a GitHub-hosted runner. Remote state here requires a private endpoint plus a runner inside that VNet — a self-hosted GitHub runner, an Azure DevOps managed pool, or a container job with VNet integration.

> This is the same policy pattern that disables the demo Key Vault's public access (see below). Assume any new data-plane-dependent resource in this subscription will land private-only, and design for it up front rather than discovering it mid-demo.

---

## What Terraform provisions

| Resource | Why it exists |
|---|---|
| Resource group | Keeps the demo self-contained under `rg-<environment>` unless overridden. |
| Log Analytics | Receives Azure Load Testing diagnostic logs and metrics. |
| Application Insights | Workspace-based telemetry for the Python API. |
| Linux App Service Plan | S1 / one worker so the autoscale and bottleneck demos behave predictably. |
| Linux Web App | The deliberately slow FastAPI system under test. It is tagged `azd-service-name = "api"` so `azd deploy api` can find it. |
| Azure Load Testing | Managed load generation with a system-assigned identity. |
| RBAC | Monitoring Reader for server-side metrics, Key Vault Secrets User for test-time secret resolution, and Key Vault Secrets Officer for the deployer. |
| Key Vault | Holds the fake `demo-api-key` secret used in the teaching flow. |
| Diagnostic settings | Sends `OperationLogs` from Azure Load Testing to Log Analytics. Metric export is not supported by `Microsoft.LoadTestService`, so the setting is logs-only. |

Terraform deliberately does **not** create load-test definitions, upload JMX files, attach App Components, or run tests. Those are data-plane actions handled by `tools/provision_loadtests.ps1` / `.sh` after deployment.

---

## Policy note: Key Vault public access

The customer subscription can silently rewrite Key Vault `publicNetworkAccess` to `Disabled`. Terraform declares the intended value (`true`) and ignores drift for that property so every plan is not polluted by a policy change it cannot fix. The consequence is real: Azure Load Testing cannot resolve `${__GetSecret(...)}` against a vault with public access disabled unless the customer adds a private-network path for the service.

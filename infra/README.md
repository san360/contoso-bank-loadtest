# `infra/` — two IaC flavours, one folder

This folder contains **both** IaC implementations of the Azure Load Testing platform. They are alternatives — pick one, don't run both against the same resource.

| Flavour | Files | Status in this kit |
|---|---|---|
| **Terraform** | `providers.tf`, `variables.tf`, `main.tf`, `outputs.tf`, `terraform.tfvars.example` | **Primary** — the internally recommended tool for the customer this kit was built for |
| **Bicep / AVM** | `main.bicep`, `main.parameters.json`, `core/*.bicep` | **Appendix** — Microsoft-native, and what `azd up` uses to stand up the whole demo including the app under test |

> In a production repo you would split these into `infra/terraform/` and `infra/bicep/`. They share a folder here only to keep the demo kit flat.

---

## Which one to use when

**Use Bicep (`azd up`) to stand up the demo.** It provisions the load testing resource *and* the Contoso Bank app under test, then deploys the app — one command, nothing else to do.

```bash
azd auth login
azd up
```

**Use Terraform to walk through the governed platform deployment.** It provisions the load testing platform only — resource, managed identity, RBAC, diagnostics — which is the correct scope for a platform team.

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars   # then edit
terraform init
terraform plan
terraform apply
```

Terraform reads only `*.tf` files, so the Bicep files in this folder are invisible to it, and `azd` targets `main.bicep` explicitly. They do not interfere.

---

## What Terraform does *not* do

`azurerm_load_test` manages the **resource container only**. It cannot create test definitions, upload a JMX file, or set fail criteria — those are data-plane operations.

That is not a gap to apologise for; it is the correct separation:

| Layer | Owns | Tool | Cadence |
|---|---|---|---|
| 1 | Resource, identity, RBAC, diagnostics | **Terraform** | Rarely |
| 2 | Test plans, scripts, fail + abort criteria | **`az load` + YAML in git** | Per change |
| 3 | Run, gate, collect results | **`az load` in CI/CD** | Per commit |

`terraform output next_command` prints the exact layer-2 command to run next.

See [`../docs/05-deployment-and-operations-as-code.md`](../docs/05-deployment-and-operations-as-code.md) for the full walkthrough and [`../docs/17-portal-free-operations-and-observability.md`](../docs/17-portal-free-operations-and-observability.md) for operating the service without portal access.

---

## The one thing not to delete

Both flavours grant **Monitoring Reader** to the load test resource's managed identity (`azurerm_role_assignment.monitoring_reader` / `core/rbac.bicep`).

Without it, App Components silently render an **empty server-side metrics chart with no error message**. It is the single most common cause of "the demo doesn't work" — and the bottleneck-hunting demo in doc 07 depends on it entirely.

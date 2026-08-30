# 05 (superseded) — Bicep / AVM Reference

> ## ⚠️ This document has been superseded
>
> After review, section 05 was rewritten to be **Terraform-first with the Azure CLI as a co-equal operating path**, because Terraform is the customer's internally recommended IaC tool and **portal access is not permitted in higher environments**.
>
> ### 👉 Use [`05-deployment-and-operations-as-code.md`](05-deployment-and-operations-as-code.md) in the session.
>
> This file is retained **only** as extended Bicep/AVM reference material. A condensed version of it lives in Appendix A of the new document. Do not present from this file.

---

<details>
<summary>Original Bicep-only content (click to expand)</summary>

## 5.1 Framing: why IaC for a *testing* tool

Points to make before showing code:

- The load testing resource holds **secrets references, network configuration, encryption keys and RBAC** — it is a security-relevant resource, not a developer toy.
- FSI customers typically require every subscription resource to be deployable from a pipeline with approved modules, mandatory tags, diagnostic settings and policy compliance. Load testing must fit that model or it won't get approved.
- IaC also enables **ephemeral test environments**: spin up the load testing resource, run the suite, tear it down. Since billing is consumption-based, there's no reason to keep it if you don't need it.

---

## 5.2 The resource type

```
Microsoft.LoadTestService/loadTests
```

Minimum viable definition — name, location, and (usually) an identity:

```bicep
resource loadTest 'Microsoft.LoadTestService/loadTests@2022-12-01' = {
  name: loadTestName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    description: 'Contoso Bank performance testing'
  }
}
```

> Check the current API version against the [ARM template reference](https://learn.microsoft.com/en-us/azure/templates/microsoft.loadtestservice/loadtests) before the session — API versions move.

---

## 5.3 Recommended approach: Azure Verified Modules (AVM)

**Lead with this.** AVM is Microsoft's supported, standards-compliant module library — using it signals "this is the governed path", which is exactly the language a bank's cloud platform team speaks.

**Bicep:**
```bicep
module loadTest 'br/public:avm/res/load-test-service/load-test:<version>' = {
  name: 'loadTestDeployment'
  params: {
    name: loadTestName
    location: location
    tags: tags
    managedIdentities: {
      systemAssigned: true
      userAssignedResourceIds: [ userAssignedIdentity.id ]
    }
    roleAssignments: [
      {
        principalId: performanceTeamGroupObjectId
        roleDefinitionIdOrName: 'Load Test Contributor'
        principalType: 'Group'
      }
    ]
    diagnosticSettings: [
      {
        workspaceResourceId: logAnalyticsWorkspaceId
      }
    ]
    customerManagedKey: {
      keyName: cmkKeyName
      keyVaultResourceId: keyVault.id
      userAssignedIdentityResourceId: userAssignedIdentity.id
    }
    lock: { kind: 'CanNotDelete' }
  }
}
```

- Repo: `https://github.com/Azure/bicep-registry-modules/tree/main/avm/res/load-test-service/load-test`
- Reference: `br/public:avm/res/load-test-service/load-test:<version>` — **always pin a version**.

**Terraform equivalent:** `Azure/terraform-azurerm-avm-res-loadtestservice-loadtest`
⚠️ Note honestly: at time of writing this Terraform AVM module is **pre-1.0.0**. Usable, but flag it if the customer is Terraform-first and requires GA modules.

### What to highlight in the AVM module walkthrough
Point at each of these parameters and name the governance requirement it satisfies:

| AVM capability | Governance requirement it satisfies |
|---|---|
| `managedIdentities` | No credentials in config; Key Vault access via identity |
| `roleAssignments` | Least privilege, separation of duties, auditable access |
| `diagnosticSettings` | Central log collection to Log Analytics / Event Hub / Storage for audit retention |
| `customerManagedKey` | Encryption key ownership and rotation control |
| `tags` | Cost allocation, ownership, data classification |
| `lock` | Prevents accidental deletion of an audited resource |
| Pinned module version | Change control and reproducibility |

---

## 5.4 Walkthrough of the demo's own IaC

The demo environment in this repo is deployed from [`../infra`](../infra) via `azd up`. Walk the files in this order:

1. **`main.bicep`** — subscription-scope entry point; creates the resource group and wires the modules together. Point out the parameter surface and the tagging strategy.
2. **`core/monitoring.bicep`** — Log Analytics workspace + Application Insights. *"We create the observability first, because the load test is only half the picture."*
3. **`core/host.bicep`** — App Service plan + the Contoso Bank API. Point out the SKU — deliberately modest, so the bottleneck is reachable in a demo.
4. **`core/loadtest.bicep`** — the `Microsoft.LoadTestService/loadTests` resource with a system-assigned identity.
5. **`core/rbac.bicep`** — the role assignment that lets the load testing resource **read Azure Monitor metrics** from the app resources. *This is the step everyone forgets, and without it server-side metrics silently don't appear.*
6. **`main.parameters.json`** — environment-specific values.

**Key talking point:** *"One command produces the app, the observability, the load testing resource and the permissions between them. Then one command destroys all of it. Your performance environment is now cattle, not a pet."*

---

## 5.5 Deploying it live

Two options, both fast — pick one for the session.

**Option A — `azd` (what this demo uses):**
```powershell
azd auth login
azd env new contoso-bank-demo
azd up          # provisions infra + deploys the app
# ... run the demo ...
azd down --purge --force
```

**Option B — plain Azure CLI + Bicep (what their pipeline would do):**
```powershell
az group create --name rg-contoso-loadtest --location westeurope

az deployment group create `
  --resource-group rg-contoso-loadtest `
  --template-file infra/core/loadtest.bicep `
  --parameters name=alt-contoso-demo location=westeurope
```

Verify:
```powershell
az load show --name alt-contoso-demo --resource-group rg-contoso-loadtest -o table
```

> **Presenter tip:** don't run a full `azd up` live — it takes minutes and adds risk. Deploy *just* the load testing resource live (it's quick), and have the full environment pre-deployed.

---

## 5.6 Beyond the resource — deploying the *tests* as code

Make the point that IaC covers the resource, and the **YAML test config file** covers the test itself. Together, the entire performance testing capability is in source control.

```yaml
version: v0.1
testId: contoso-bank-peak-load
displayName: Contoso Bank - Peak Hour Transfers
testType: JMX
testPlan: loadtests/jmeter/contoso-bank.jmx
engineInstances: 4
```

Then, under the covers, the `azd up` post-deploy hook runs the equivalent of:
```powershell
az load test create `
  --load-test-resource alt-contoso-demo `
  --resource-group rg-contoso-loadtest `
  --test-id contoso-bank-peak-load `
  --load-test-config-file loadtests/config/peak-load.yaml
```

You can still run the command manually after editing a YAML file; the hook makes
the first setup complete so the Tests blade is not empty after deployment.

**Say out loud:** *"The resource is Bicep. The test is YAML. Both are in Git, both go through pull request review, both are auditable. A performance test is now a reviewable change like any other."*

---

## 5.7 Governance patterns worth mentioning

- **Azure Policy** — enforce tagging, region restriction, diagnostic settings and CMK on `Microsoft.LoadTestService/loadTests` like any other resource type.
- **Landing zone placement** — decide whether the load testing resource lives in the workload subscription (per-team autonomy, easier VNet injection) or a shared testing subscription (central cost control). Discuss the trade-off; there's no single right answer.
- **Naming & tagging** — apply their CAF naming standard; tag with cost centre, environment, data classification and owner.
- **Cost guardrails** — budgets and alerts on the resource group; combine with AutoStop and engine quota limits (doc 12).
- **Ephemeral vs persistent** — persistent resource for the audit history of test runs (remember: runs are your evidence trail and have 6-month retention); ephemeral for one-off capacity exercises.

---

## Reference links

- [ARM template reference — Microsoft.LoadTestService/loadTests](https://learn.microsoft.com/en-us/azure/templates/microsoft.loadtestservice/loadtests)
- [Azure Verified Modules — Bicep load test module](https://github.com/Azure/bicep-registry-modules/tree/main/avm/res/load-test-service/load-test)
- [Azure Verified Modules — Terraform load test module](https://github.com/Azure/terraform-azurerm-avm-res-loadtestservice-loadtest)
- [Load test YAML configuration reference](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/reference-test-config-yaml)
- [Use a managed identity](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/how-to-use-a-managed-identity)
- [Configure customer-managed keys](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/how-to-configure-customer-managed-keys)

</details>

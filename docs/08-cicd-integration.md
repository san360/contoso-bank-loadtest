# 08 — Integration with CI/CD

> **Section goal:** Move load testing from "an event before a big release" to "a gate on every change". This is the section that changes engineering behaviour, and it is usually the strongest ROI argument for a platform engineering audience.

**Duration:** 20 minutes · **Format:** Live (GitHub Actions) + code walkthrough (Azure DevOps) · **Audience:** Platform engineering, DevOps, release management

---

## 8.1 Framing: three levels of automation maturity

| Level | Pattern | Runs when | Duration | Purpose |
|---|---|---|---|---|
| **1 — Smoke gate** | Short, small test on every PR / merge | Every pull request or merge to `main` | 2–5 min | Catch catastrophic regressions early; keep the loop fast |
| **2 — Regression gate** | Fuller test after merge / on release candidate | Every merge, or nightly | 15–30 min | Detect creeping degradation against a baseline |
| **3 — Capacity / soak** | High-scale or long-duration test | Scheduled, or pre-release | 1–24 h | Prove peak capacity, find leaks, validate autoscale |

**Key advice for the customer:** *"Don't try to run a 30-minute 10,000-user test on every pull request — you'll create a bottleneck in your own pipeline and the team will disable it within a month. Layer it."*

---

## 8.2 Tests as code — the config YAML

Everything starts with the test config file in the repo. This is what makes the test reviewable.

```yaml
version: v0.1
testId: contoso-bank-pr-gate
displayName: Contoso Bank - PR performance gate
description: Fast smoke gate on every pull request
testType: JMX
testPlan: ../jmeter/contoso-bank.jmx
engineInstances: 1
configurationFiles:
  - ../data/accounts.csv
splitAllCSVs: true
env:
  - name: BASE_URL
    value: contoso-bank-api-staging.azurewebsites.net
  - name: duration
    value: '180'
  - name: threads
    value: '25'
failureCriteria:
  clientMetrics:
    - percentage(error) > 2
    - avg(response_time_ms) > 500
    - GetBalance: p95(response_time_ms) > 800
autoStop:
  errorPercentage: 90
  timeWindow: 60
```

### Full schema reference — worth showing on screen

| Property | Purpose |
|---|---|
| `version` | Always `v0.1` |
| `testId` | Unique ID, 2–50 chars, `[a-z0-9_-]` |
| `displayName` / `description` | Portal display (description max 100 chars) |
| `testType` | `URL`, `JMX` or `Locust` |
| `testPlan` | Path to the `.jmx`, `.py`, or URL-requests JSON |
| `engineInstances` | Parallel engine count — your scale dial |
| `configurationFiles` | CSVs, JMX fragments, supporting assets |
| `splitAllCSVs` | Split CSV rows evenly across engines |
| `properties.userPropertyFile` | JMeter `.properties` or Locust `.conf`/`.ini`/`.toml` |
| `failureCriteria` | Client and/or server metric thresholds — **the gate** |
| `autoStop` | `disable`, or `{errorPercentage, timeWindow}` |
| `env` / `secrets` / `certificates` | Parameters; YAML secrets are Key Vault references, pipeline secrets can be supplied at run time |
| `appComponents` | Azure resources for server-side metrics |
| `subnetId` / `publicIPDisabled` | VNet injection |
| `regionalLoadTestConfig` | Multi-region load generation |
| `referenceIdentities` / `keyVaultReferenceIdentity` | Managed identities for `KeyVault`, `Metrics`, `Engine` scenarios |

**Say out loud:** *"This file is the entire performance contract for the service — scale, thresholds, data, secrets and monitoring. It's diffed in a pull request. Someone has to approve a change to your performance SLO, the same way they approve a code change."*

---

## 8.3 GitHub Actions — what is now in this repo

The repo contains three workflow files:

| Workflow | Trigger | Purpose |
|---|---|---|
| [`.github/workflows/infra-ci.yml`](../.github/workflows/infra-ci.yml) | Pull request / push to `main`, path-filtered to `infra/` plus the provisioning scripts and load-test YAML safety net | `terraform fmt`, `terraform init -backend=false`, `terraform validate`, shell/parser checks for the postdeploy hooks, and the failure-criteria pre-flight guard |
| [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml) | `workflow_dispatch` only | Installs azd, authenticates with GitHub OIDC, removes `src/.venv`, then runs `azd provision` and `azd deploy` for a human-approved demo deployment |
| [`.github/workflows/loadtest.yml`](../.github/workflows/loadtest.yml) | Push to `main` and manual `workflow_dispatch` | The headline demo gate: resolve the deployed resources, run `azure/load-testing@v1`, upload `loadTestResults`, then apply the robust pass/fail guard in §8.6a |

> **Private repo cost note.** This customer repo is private (`san360/contoso-bank-loadtest`). GitHub Actions still works on the free tier for private repositories: the account gets **2,000 included minutes/month** on GitHub-hosted runners. Public repositories get unlimited free standard hosted-runner minutes, so making the demo public later is an option if minutes become the constraint.

### The headline workflow

`loadtest.yml` uses the official action inputs confirmed from the current Azure Load Testing action code and README: `loadTestConfigFile`, `loadTestResource`, `resourceGroup`, `secrets`, and `env`.

```yaml
- name: Run Azure Load Test
  uses: azure/load-testing@v1
  with:
    loadTestConfigFile: ${{ steps.selected.outputs.config_file }}
    loadTestResource: ${{ steps.azure.outputs.load_test_resource }}
    resourceGroup: ${{ steps.azure.outputs.resource_group }}
    outputVariableName: loadTestRun
    secrets: |
      [
        {
          "name": "API_KEY",
          "value": "${{ secrets.DEMO_API_KEY }}"
        }
      ]
    env: |
      [
        {
          "name": "BASE_URL",
          "value": "${{ steps.azure.outputs.api_base_url }}"
        }
      ]
```

Notes to call out:
- `azure/login@v2` uses OIDC. There is no stored service-principal secret.
- `azure/load-testing@v1` creates/updates the selected test, starts a run, waits for completion, and downloads results under `$GITHUB_WORKSPACE/loadTest`; the workflow publishes that folder as the `loadTestResults-*` artifact.
- The default run is `contoso-bank-pr-gate` (`loadtests/config/pr-gate.yaml`), the short JMeter regression gate.
- Manual runs can pass `test_id`. The workflow maps the shipped test IDs to their YAML files and also supports `contoso-bank-secrets-gh`, which reuses the secrets JMeter script but supplies `API_KEY` from GitHub Actions instead of Key Vault.

### Secrets demo — why GitHub Secrets are the green path

The repo intentionally keeps two secret stories:

| Test | Purpose | Expected teaching point |
|---|---|---|
| `contoso-bank-secrets-kv` (`loadtests/config/secrets-demo.yaml`) | YAML contains a Key Vault secret URI | Governance cautionary tale: it validates at create time, but in this subscription the Key Vault data plane is private-only, so run-time resolution can fail |
| `contoso-bank-secrets-gh` (`loadtest.yml` override) | Same JMeter script, but the workflow passes `API_KEY` through the action's `secrets:` input from `${{ secrets.DEMO_API_KEY }}` | The CI/CD path that runs green because it bypasses the Key Vault data plane entirely |

This is not a workaround to hide from governance; it is the demo-friendly path through a governed subscription. The customer's Azure Policy forces Key Vault `publicNetworkAccess = Disabled`. Azure Load Testing accepts the Key Vault reference when the test is created, but the load engines cannot reach that data plane at run time unless there is a private-network path. A GitHub Actions secret is injected into the test run by the pipeline action, so no Key Vault data-plane call is needed during the run.

### The live demo script (this is the memorable bit)

The workflow in this repo runs unattended on **push to `main`** and can be started manually with `workflow_dispatch`. That is deliberate for this subscription: shared Terraform state is not solved yet, so PR-time ephemeral deployments are a later maturity step.

1. Merge or push a small change that introduces a **deliberate performance regression** in the Contoso Bank API — e.g. remove the cache, or add an artificial delay:
   ```
   Commit / PR title: "Add fraud-check enrichment to transfer endpoint"
   ```
   *Make it look like a plausible, well-intentioned feature — not obviously a bug. That's the whole point: it passes code review.*
2. Show the merge/push workflow running. Functional checks **pass** ✅.
3. The **Load test gate** workflow goes red ❌ before the change is promoted further.
4. Open the workflow log → the failed criterion is printed.
5. Open the `loadTestResults-*` artifact or click through to the Azure Load Testing run in the portal.
6. The release is blocked until the regression is accepted or fixed.
7. Push a fix commit → the next `main` run goes green → release can continue.

> **The line to land:** *"That regression passed code review and passed every unit test. The only thing that caught it before release was a load test running automatically after merge. That is the difference between finding it now, for free, and finding it at month-end with your payments queue backing up."*

---

## 8.4 One-time OIDC setup for GitHub Actions

Use an Entra application with federated credentials. Do **not** create a client secret.

The examples below assume:

```bash
SUBSCRIPTION_ID=<subscription-id>
TENANT_ID=<tenant-id>
REPO=san360/contoso-bank-loadtest
APP_NAME=github-contoso-bank-loadtest-cicd
AZURE_ENV_NAME=loadtest
AZURE_LOCATION=swedencentral
DEPLOY_ENVIRONMENT=contoso-demo
```

Create the app registration and service principal:

```bash
APP_ID=$(az ad app create \
  --display-name "$APP_NAME" \
  --query appId -o tsv)

APP_OBJECT_ID=$(az ad app show \
  --id "$APP_ID" \
  --query id -o tsv)

SP_OBJECT_ID=$(az ad sp create \
  --id "$APP_ID" \
  --query id -o tsv)
```

Create a branch-based federated credential for `main` (used by `loadtest.yml` push runs and manual runs started from `main`):

```bash
az ad app federated-credential create \
  --id "$APP_OBJECT_ID" \
  --parameters '{
    "name": "github-main",
    "issuer": "https://token.actions.githubusercontent.com",
    "subject": "repo:san360/contoso-bank-loadtest:ref:refs/heads/main",
    "description": "GitHub Actions from main branch",
    "audiences": ["api://AzureADTokenExchange"]
  }'
```

Create an environment-based federated credential for the protected deployment job (`deploy.yml` uses `environment: contoso-demo`):

```bash
az ad app federated-credential create \
  --id "$APP_OBJECT_ID" \
  --parameters '{
    "name": "github-contoso-demo-environment",
    "issuer": "https://token.actions.githubusercontent.com",
    "subject": "repo:san360/contoso-bank-loadtest:environment:contoso-demo",
    "description": "GitHub Actions deployment environment contoso-demo",
    "audiences": ["api://AzureADTokenExchange"]
  }'
```

Assign roles. Use subscription scope for a clean demo deployment, or narrow to the resource group after the first human-owned deployment exists.

```bash
SCOPE="/subscriptions/$SUBSCRIPTION_ID"

az role assignment create \
  --assignee-object-id "$SP_OBJECT_ID" \
  --assignee-principal-type ServicePrincipal \
  --role Contributor \
  --scope "$SCOPE"

az role assignment create \
  --assignee-object-id "$SP_OBJECT_ID" \
  --assignee-principal-type ServicePrincipal \
  --role "Load Test Contributor" \
  --scope "$SCOPE"

# Only needed if this identity creates or rotates the demo Key Vault secret.
az role assignment create \
  --assignee-object-id "$SP_OBJECT_ID" \
  --assignee-principal-type ServicePrincipal \
  --role "Key Vault Secrets Officer" \
  --scope "$SCOPE"
```

> If the deployment identity must create role assignments from scratch, it also needs role-assignment authority at the same scope (for example Owner, User Access Administrator, or Role Based Access Control Administrator). For the customer demo, prefer pre-created RBAC where possible and keep the pipeline identity's standing permissions small.

Configure GitHub **Secrets and variables → Actions**:

| Name | Type | Used by | Value |
|---|---|---|---|
| `AZURE_CLIENT_ID` | Secret | `deploy.yml`, `loadtest.yml` | `$APP_ID` |
| `AZURE_TENANT_ID` | Secret | `deploy.yml`, `loadtest.yml` | `$TENANT_ID` |
| `AZURE_SUBSCRIPTION_ID` | Secret | `deploy.yml`, `loadtest.yml` | `$SUBSCRIPTION_ID` |
| `DEMO_API_KEY` | Secret | `deploy.yml`, `loadtest.yml` | Fake demo API key supplied to Terraform and to `azure/load-testing@v1` |
| `AZURE_ENV_NAME` | Variable | `deploy.yml`, `loadtest.yml` | azd environment name, e.g. `loadtest` |
| `AZURE_LOCATION` | Variable | `deploy.yml` | Azure region, e.g. `swedencentral` |

No `LOAD_TEST_RESOURCE`, `LOAD_TEST_RESOURCE_GROUP`, or `API_HOSTNAME` variables are required. `loadtest.yml` discovers them from the `azd-env-name` and `azd-service-name=api` tags.

---

## 8.5 Terraform state and the deploy workflow — read this before enabling unattended deploys

Remote Terraform state is **currently impossible in this subscription**, and that is architectural rather than a repo bug.

Three Azure Policy `modify` effects silently override requested values. The commands return HTTP 200 / exit code 0, and the resources are not reported non-compliant:

1. **Key Vault `publicNetworkAccess` is forced to `Disabled`.** That is why the Key Vault-referenced secret test is a governance cautionary tale.
2. **Storage `allowSharedKeyAccess` is forced to `false`.** This causes `KeyBasedAuthenticationNotPermitted` and defeats the classic bootstrap pattern of enabling keys, creating the state container, then disabling keys again.
3. **Storage `publicNetworkAccess` is forced to `Disabled`.** This was proven directly: `az storage account update --public-network-access Enabled` exits successfully, and a following `az storage account show` still returns `Disabled`.

Consequence: an `azurerm` Terraform backend is unreachable from a workstation **and** from a GitHub-hosted runner. The repo therefore runs on **local state**:

| File | Meaning |
|---|---|
| `infra/providers.tf` | The `backend "azurerm" {}` block is commented out |
| `infra/provider.conf.json` | Contains only `{}` because azd requires the file to exist |
| `infra/provider.conf.json.example` | Holds the real remote-state backend template |
| `tools/bootstrap_tfstate.ps1` | Works until the private-only state endpoint blocks access |

### Blast radius in CI/CD

A GitHub-hosted runner starts with an empty filesystem. With local state, `terraform apply` has no memory of resources already created by a presenter laptop or a previous runner. It would try to create the stack again, collide with existing names and role assignments, or leave a second partial environment. That is why `deploy.yml` is **manual-only** and protected by a GitHub environment in this repo.

### Remediation options

| Option | What it means | Demo recommendation |
|---|---|---|
| **A — Private endpoint + network-joined runner** | Put the state storage account behind a private endpoint and run CI from a self-hosted GitHub runner, Azure DevOps managed pool, or VNet-integrated container job that can reach it | Correct enterprise architecture |
| **B — Policy exemption for the state storage account** | Allow public network access and Entra ID auth for just the Terraform state account | Operationally simple, but needs governance approval |
| **C — Keep deploy manual; run load tests unattended** | Humans own `azd provision/deploy` for the demo environment; `loadtest.yml` runs automatically because it needs no Terraform state | **Pragmatic default for this customer demo** |

`loadtest.yml` is therefore the workflow to make unattended. `deploy.yml` exists so the setup is documented and repeatable, not because this subscription can safely do shared-state deployment from a GitHub-hosted runner today.

---

## 8.6a The gate guard — three outcomes, not two

**Raised in review, and it is a genuine bug in most people's pipelines.**

Almost every load-testing gate ever written looks like this:

```bash
if [ "$RESULT" != "PASSED" ]; then exit 1; fi     # ← subtly broken
```

That's wrong in one direction and dangerously incomplete in another:

| Outcome | Naive gate says | Should say |
|---|---|---|
| `DONE` + `PASSED` | pass ✅ | pass ✅ |
| `DONE` + `FAILED` | fail ✅ | fail — **real regression** ✅ |
| `DONE` + `NOT_APPLICABLE`, no criteria | fail, with a confusing message | pass as **informational only** for the two named teaching artefacts |
| `DONE` + `NOT_APPLICABLE`, criteria exist | maybe pass, maybe fail, usually ambiguous | fail — **pass/fail evaluation did not settle** |
| `FAILED` / `VALIDATION_FAILURE` | fail, indistinguishable from a regression | fail — **the test broke, not necessarily the app** |
| `CANCELLED` (AutoStop fired) | fail, indistinguishable | fail — **run aborted, no conclusion** |

The distinction is not pedantry. A developer who sees "performance gate failed" when the *test harness* broke will start ignoring the gate — and then the gate is worthless.

### The guard implemented in `loadtest.yml`

```bash
read -r status result < <(az load test-run show \
  --load-test-resource "$LOAD_TEST_RESOURCE" \
  --resource-group "$RESOURCE_GROUP" \
  --test-run-id "$RUN_ID" \
  --query "[status, testResult]" \
  -o tsv | tr '\n' ' ')

case "$status" in
  DONE) ;;
  CANCELLED)
    echo "::error title=Load test aborted::AutoStop fired or the run was cancelled."
    exit 1 ;;
  *)
    echo "::error title=Load test infrastructure failure::status=$status."
    exit 1 ;;
esac

for attempt in 1 2 3 4 5 6; do
  [ "$result" = "NOT_APPLICABLE" ] || break
  sleep 10
  result=$(az load test-run show \
    --load-test-resource "$LOAD_TEST_RESOURCE" \
    --resource-group "$RESOURCE_GROUP" \
    --test-run-id "$RUN_ID" \
    --query "testResult" -o tsv)
done

if [ "$result" = "NOT_APPLICABLE" ]; then
  criteria_count=$(az load test show \
    --test-id "$TEST_ID" \
    --load-test-resource "$LOAD_TEST_RESOURCE" \
    --resource-group "$RESOURCE_GROUP" \
    --query "length(keys(passFailCriteria.passFailMetrics || \`{}\`))" \
    -o tsv)

  if [ "${criteria_count:-0}" -eq 0 ]; then
    echo "::notice title=Criteria-less teaching artefact::$TEST_ID is informational only."
    exit 0
  fi

  echo "::error title=Pass/fail evaluation unresolved::$TEST_ID defines $criteria_count criteria."
  exit 1
fi

if [ "$result" != "PASSED" ]; then
  echo "::error title=Performance regression::Fail criteria breached."
  exit 1
fi
```

> **Why the retry loop exists.** `status` and `testResult` are populated independently. Reading both from the first response where `status == DONE` is a race: `testResult` can still read `NOT_APPLICABLE` for several seconds on a test that *does* define criteria and *did* fail. This was observed empirically while building this kit — a run that finally reported `FAILED` at `avg 3588 ms` against a `> 500 ms` criterion read `NOT_APPLICABLE` moments earlier. Because `NOT_APPLICABLE` is overloaded, the guard re-polls, then settles the ambiguity against the static test definition, which cannot race.

### Shift it further left — catch it before the test even runs

Even better than failing after spending VUH: refuse to run a criteria-less test unless it is one of the two intentional teaching files.

```bash
for file in loadtests/config/*.yaml; do
  case "$(basename "$file")" in
    url-basic.yaml|url-false-green.yaml) continue ;;
  esac

  grep -Eq '^[[:space:]]*failureCriteria[[:space:]]*:' "$file" || {
    echo "::error file=$file::Missing failureCriteria"; exit 1; }
done
```

That is exactly what `infra-ci.yml` enforces. The allowlist is deliberately small and named: `url-basic.yaml` for the opening no-code demo, and `url-false-green.yaml` for the false-green cautionary tale.

> **Say this out loud:** *"This is a tiny check that makes an entire class of silent failure structurally impossible, while still preserving the two teaching artefacts where criteria-less behaviour is the point."*

See §7.2 for the status taxonomy this enforces, §7.9 for abort criteria, and doc [`06-demo-create-run-inspect.md`](06-demo-create-run-inspect.md) §6.4a for the live demo of the failure mode.

---

## 8.7 Azure DevOps

Many FSI customers are on Azure DevOps — cover this even if you demo GitHub.

```yaml
trigger:
  branches:
    include: [ main ]

pool:
  vmImage: ubuntu-latest

steps:
  - task: AzureLoadTest@1
    inputs:
      azureSubscription: 'contoso-azure-service-connection'
      loadTestConfigFile: 'loadtests/config/pr-gate.yaml'
      loadTestResource: '$(loadTestResource)'
      resourceGroup: '$(loadTestResourceGroup)'

  - publish: $(System.DefaultWorkingDirectory)/loadTest
    artifact: loadTestResults
    condition: always()
```

Points:
- Task is **`AzureLoadTest@1`**, available from the Azure DevOps Marketplace ("Azure Load Testing" by Microsoft).
- Authenticate with workload identity federation on the ARM service connection where possible; avoid client secrets for the same reason as GitHub Actions.
- Wire the result into a **release gate** on your pipeline environment approvals — that's how it becomes a formal release control, which maps neatly onto FSI change-management processes.

---

## 8.8 Where in the pipeline?

```mermaid
graph LR
    A["Pull request"] --> B["Build + unit tests"]
    B --> C["Deploy to ephemeral/staging"]
    C --> D["Smoke load test<br/>1 engine · 2-3 min<br/>strict criteria"]
    D -->|fail| X["❌ Block merge"]
    D -->|pass| E["Merge to main"]
    E --> F["Deploy to test env"]
    F --> G["Regression load test<br/>4 engines · 15-30 min<br/>vs baseline"]
    G -->|fail| Y["❌ Block release"]
    G -->|pass| H["Release candidate"]
    H --> I["Scheduled: soak + capacity test<br/>high scale · hours"]
    I --> J["Production release<br/>+ evidence pack"]
```

**Practical guidance to give them:**
- Test against an environment that is **shaped like production**, even if it's smaller. A test against a dev SKU proves nothing about capacity — but it still catches N+1 queries and lock contention, which is worth a lot.
- Use **ephemeral environments** for PR gates where possible, but only if Terraform state is solved first.
- Keep PR gates **short and strict**; keep nightly runs **long and broad**.
- Parameterise scale via `env` so the *same script* serves the PR gate and the capacity test — only the YAML differs.

---

## 8.9 Scripting it directly (CLI / REST)

For teams with a bespoke pipeline or a non-GitHub/ADO toolchain:

```powershell
az extension add --name load

az load test create `
  --load-test-resource $ltResource --resource-group $rg `
  --test-id contoso-bank-pr-gate `
  --load-test-config-file loadtests/config/pr-gate.yaml

az load test-run create `
  --load-test-resource $ltResource --resource-group $rg `
  --test-id contoso-bank-pr-gate `
  --test-run-id "pr-$env:BUILD_ID" `
  --description "PR $env:PR_NUMBER"

az load test-run download-files `
  --load-test-resource $ltResource --resource-group $rg `
  --test-run-id "pr-$env:BUILD_ID" --path ./results --result --log
```

Also available: the **REST API** and language SDKs. The `Azure-Samples/azure-load-testing-samples` repo has .NET SDK examples that create a URL-based quick test and a JMeter test programmatically, including fail criteria and server metrics — useful if the customer wants to build a self-service internal portal on top.

---

## 8.10 Operational practices to recommend

- **Version your baselines.** Store the accepted p95/RPS numbers alongside the code, and change them via pull request. Performance targets should require approval to weaken.
- **Tag test runs with the commit SHA** (`loadTestRunDescription`) so a result is traceable to a build. This matters for audit.
- **Fail loudly, but with a documented override path.** An engineering lead must be able to accept a regression with a recorded justification — otherwise teams will just delete the gate.
- **Notify on failure** via notification rules and action groups (Teams / email / ServiceNow).
- **Watch pipeline cost.** Every gated run consumes VUH and GitHub Actions minutes — keep PR gates to one engine and a few minutes (doc 12).
- **Don't gate on flaky criteria.** If a threshold fails intermittently, it's too tight or the environment is too noisy. Fix that immediately or the whole gate loses credibility.

---

## Reference links

- [Configure load tests in CI/CD](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/how-to-configure-load-test-cicd)
- [Load test YAML configuration reference](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/reference-test-config-yaml)
- [GitHub Action — Azure/load-testing](https://github.com/Azure/load-testing)
- [Azure Login with OpenID Connect](https://learn.microsoft.com/en-us/azure/developer/github/connect-from-azure-openid-connect)
- [Azure Developer CLI authentication](https://github.com/Azure/azure-dev/blob/main/cli/azd/docs/authentication.md)
- [az load CLI reference](https://learn.microsoft.com/en-us/cli/azure/load)
- [Define test fail criteria](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/how-to-define-test-criteria)
- [Azure-Samples/azure-load-testing-samples (SDK examples)](https://github.com/Azure-Samples/azure-load-testing-samples)
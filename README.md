# Azure Load Testing — Customer Demo Kit

A complete, runnable demo kit for presenting **Azure Load Testing** to a financial services customer.

It contains a documented session agenda, a purpose-built banking API with deliberately planted (and instantly removable) performance bottlenecks, eight load test definitions across four artefact styles, infrastructure as code, and a CI/CD pipeline.

---

## What's in here

| Folder | Contents |
|---|---|
| [`docs/`](./docs) | The session agenda and presenter runbook — 27 numbered documents (00–26) |
| [`src/`](./src) | **Contoso Bank API** — the system under test |
| [`loadtests/`](./loadtests) | JMeter, Locust, URL-based and cURL-based tests, plus YAML configs and test data. [`config-private/`](./loadtests/config-private) holds the VNet-injected definition, kept out of the auto-provisioned set because it needs an environment-specific subnet ID |
| [`infra/`](./infra) | Terraform IaC used by `azd up` |
| [`.github/workflows/`](./.github/workflows) | GitHub Actions performance gate (OIDC, no stored secrets) |
| [`.gitlab-ci.yml`](./.gitlab-ci.yml) + [`infra/gitlab-demo/`](./infra/gitlab-demo) | Optional, isolated **GitLab CE VM demo** with managed SSD, stateless ACA front door and ACA runner: real feature MR, measured red gate, scripted batching fix, green gate and exact tested-artifact promotion. [Setup, architecture, protection, costs and cleanup](./infra/gitlab-demo/README.md) |
| [`tools/`](./tools) | `azd up` load-test provisioning hooks, [`run_loadtest.ps1`/`.sh`](./tools) one-command test runners, [`provision_private_loadtest.ps1`](./tools/provision_private_loadtest.ps1) VNet-injected test with subnet pre-flight, [`compare_runs.ps1`](./tools/compare_runs.ps1) before/after run comparison, [`e2e-test.ps1`](./tools/e2e-test.ps1) end-to-end validation, hostname retargeting, VUH costing, and the harness that proves the planted bottlenecks actually bite |
| [`tools/cli_demo/`](./tools/cli_demo) | Local **Azure CLI lab**: preview/execute create/run/inspect commands, client/engine/server metrics, downloadable logs/results/metric evidence and step scripts. No additional Python packages |
| [`.github/skills/`](./.github/skills) | Reusable **JMX-to-Locust migration** and **downloaded artifact analysis** agent skills, with standard-library helpers |

---

## Quick start

```bash
azd auth login
azd up
```

Roughly 5–8 minutes. Provisions Log Analytics, Application Insights, an App Service plan, the API, the Azure Load Testing resource, and the Monitoring Reader role assignment — then deploys the app and creates/updates every load test definition in [`loadtests/config/`](./loadtests/config).

Then verify:

```bash
curl https://$SERVICE_API_HOSTNAME/health
curl https://$SERVICE_API_HOSTNAME/admin/config    # should report "bottlenecked"
```

Run the first test. The definitions already exist because the `azd up` post-deploy hook created them — running one is a separate, explicit step because it spends virtual user hours:

```powershell
# See what is deployed (free, spends no VUH)
pwsh -File tools/run_loadtest.ps1 -List

# Start the cheap gate test, wait for it, print the verdict and the statistics
pwsh -File tools/run_loadtest.ps1 -TestId contoso-bank-pr-gate -RunId demo-run-before

# Or run batch-eligible definitions, cheapest first (opt-in SQL metrics demos excluded)
pwsh -File tools/run_loadtest.ps1 -All
```

```bash
# Same thing on macOS/Linux
tools/run_loadtest.sh --list
tools/run_loadtest.sh --test-id contoso-bank-pr-gate --run-id demo-run-before
tools/run_loadtest.sh --all
```

The runner resolves the Azure Load Testing resource from the azd environment, polls until the run finishes, **waits for the pass/fail verdict to settle** (measured at ~97 seconds after `DONE` — reading it earlier reports a false green, see [doc 07 §7.2.4](./docs/07-analyzing-results-and-bottlenecks.md)), prints the breached criteria and the headline percentiles, and exits non-zero on anything short of proof of a pass. Add `-DownloadResults` / `--download` for the CSV/JTL and the JMeter HTML report, `-Preset` / `--preset` to flip the app between `bottlenecked` and `optimized` before the run, `-All` / `--all` to run every deployed definition sequentially, or `-NoWait` / `--no-wait` to fire and forget. Compare any two runs afterwards with [`tools/compare_runs.ps1`](./tools/compare_runs.ps1). The raw `az load test-run create` path still works and is documented in [doc 17](./docs/17-portal-free-operations-and-observability.md).

---

## Interactive CLI demo

Requires **Python 3.11+**, **Azure CLI 2.66+**, and an existing Azure Load Testing
resource. No `azd up`, `pip install`, npm install, or frontend build is needed.

```powershell
# Run from the repository root.
az login --tenant "<tenant-guid-or-domain>"
az account set --subscription "<subscription-guid>"
az extension add --name load
python -m tools.cli_demo
```

Open **http://127.0.0.1:8769** and enter the tenant, subscription ID, resource group,
Load Testing resource name, and authorized target URL. Preview each command,
then execute **Create the small demo test**, **Start a load test run**, **Watch
selected run**, and **Collect analysis bundle**. Starting a run requires explicit
confirmation.

The create preview generates a copy of
[`smoke.yaml`](./loadtests/cli-demo/smoke.yaml) with the form's **Test ID**, then
shows it beside the matching `az load test create --load-test-config-file ...`
command. The repository template stays unchanged. Copy the YAML, copy the
PowerShell/Bash command, or download the step script for a terminal demo.

`az account set` changes the current CLI profile. For a dedicated demo profile,
follow the `AZURE_CONFIG_DIR` setup in [doc 26](./docs/26-cli-demo-app-and-skills.md#different-tenants-and-cli-subscription-context).
If port 8769 is occupied, use `python -m tools.cli_demo --port 8766` and open
**http://127.0.0.1:8766**. Use the URL printed by the server; no source-code edits
are needed to change the port.

Press **Ctrl+C** in the server terminal to stop the local UI; this does **not**
cancel Azure runs. Restart with the same `python -m tools.cli_demo` command.
Browser settings and downloaded evidence in `.loadtest-demo/` remain local.

The opt-in
[`loadtests/cli-demo/`](./loadtests/cli-demo) test uses one engine, five users and a
60-second GET health check; starting a run still incurs Azure charges. The app is
localhost-only and separate from the deployed API.

See [doc 26](./docs/26-cli-demo-app-and-skills.md) for startup/RBAC, a presenter
flow, what **app components** mean, the distinction between client and server
metric commands, artifact downloads and both agent skills.

---

## Optional: real Azure SQL database metrics

This **separate, opt-in demo** creates a new **Basic Azure SQL database (5 DTU,
2 GB)** and a new JMeter test in an **existing** Load Testing resource. It does not
change the banking API's simulated database, existing tests, `azd up`, CI test
selection, or the normal run-all catalog. Nothing is provisioned or run unless
you invoke the commands below.

The two existing batch runners exclude **only** `sql-metrics-*` IDs from
`-All`/`--all`; their existing definitions, explicit named execution and CI
selection are unchanged. Keeping assets outside `loadtests/config/` alone is not
enough because those batch runners otherwise discover every live ALT definition.

The new test performs real, committed SQL updates and a bounded 512-row aggregate:
**one engine, two users, 120 seconds, one-second pacing, at most 240 transactions**.
It attaches the **database** (not just its logical server) to the ALT dashboard
with CPU, DTU, data IO, log IO, workers, sessions, session count and successful
connection metrics. Warm caches may legitimately yield zero data IO; this is a
metrics demonstration, not a promise to saturate the database.

Requires Python 3.11+, Azure CLI and Terraform 1.9+. No Python packages, local
Java runtime, SQL client or ODBC installation are needed. The helper downloads the
Microsoft JDBC **13.6.1.jre11** driver from Maven Central and verifies a pinned
SHA-256; it uploads the JAR and Groovy scripts only to this new test.
On Windows ARM64, use the x64 Terraform binary under emulation: AzureRM 5.10
does not publish a Windows ARM64 provider.

Select an authorized existing ALT ARM resource ID and its subscription/directory
explicitly. The helper discovers that resource's region and identity, checks Basic
availability and registered providers, and never changes shared CLI defaults.
The operator needs resource creation/deletion in the new group, permission to
assign **Monitoring Reader on the new database only**, **Load Test Contributor**
on the existing ALT resource, and subnet join/network permissions in the new VNet.
Review inherited Azure Policies first; the helper does not bypass them or register
providers automatically.

```powershell
# Run from the repository root. Replace these with your authorized environment.
$State = ".loadtest-demo\sql-metrics\my-sql-demo"
$Subscription = "<subscription-guid>"
$Tenant = "<tenant-guid>"
$Alt = "/subscriptions/$Subscription/resourceGroups/<existing-rg>/providers/Microsoft.LoadTestService/loadTests/<existing-alt>"

# Read-only Azure preflight; writes only local context, tfvars and current SQL cost.
python -m tools.sql_metrics_demo prepare --state-dir $State --name my-sql-demo `
  --subscription $Subscription --tenant $Tenant --load-test-resource-id $Alt

# Separate Terraform state; refuses to adopt an existing group or change old resources.
python -m tools.sql_metrics_demo provision --state-dir $State --accept-cost

# Upload this new definition only. SQL initialization happens inside its one engine.
python -m tools.sql_metrics_demo setup --state-dir $State

# Exactly one paid run per instance. Repeating this command refuses another start.
python -m tools.sql_metrics_demo run --state-dir $State --accept-cost

# Finish/re-read evidence after an interrupted collection, WITHOUT starting another run.
python -m tools.sql_metrics_demo inspect --state-dir $State

# Deletes only this generated test and Terraform-owned demo resources. Keeps local evidence.
python -m tools.sql_metrics_demo cleanup --state-dir $State --confirm-name my-sql-demo
```

For a diagnosed, **settled FAILED** run only, an operator can explicitly authorize
**one additional bounded validation run** with
`run --state-dir $State --retry-failed-run "<exact-failed-run-id>" --accept-cost`.
The helper first preserves its evidence under `attempts\<failed-run-id>\`, then
reserves a single retry. There are no automatic retries, retries of ambiguous
starts, or further retries; each additional start spends the same estimated
$0.25 plus temporary networking. `inspect` never starts load.

`--terraform` accepts an absolute executable path. Keep the state folder:
it is the inventory needed for safe cleanup. Context, state and evidence live under
the already-ignored `.loadtest-demo\`; do not commit them. A concurrent operation
is refused using `operation.lock`; after a crashed process, remove that **one**
lock file only after checking that its recorded PID is no longer running.

**Authentication/networking:** SQL is Entra-only. The bootstrap operator becomes
administrator of the **new logical server only**. The existing ALT system identity
gets a contained database user with only `CONNECT` and `EXECUTE` on one stored
procedure; it gets no table, DDL, server-admin or application permissions.
Engine and Metrics reference identities are explicitly configured separately.
The helper reads only that known managed identity's public client ID from Graph:
**SQL's contained-user SID uses the client/application ID; Azure RBAC uses the
principal/object ID**. It never assigns directory-reading roles to SQL or ALT.
A short-lived Entra SQL token for the operator goes directly into the **new run's
protected secret input** to seed and verify from inside its one engine. It is
never put in the test definition, command arguments, environment variables,
source, local JSON/archives or logs. The workload itself authenticates using
IMDS and the restricted database user, not the operator token. Bootstrap variables
are cleared after use; after the run the helper requests removal of its protected
secret and reports whether the service accepted removal. If terminal-run secret
editing is unsupported, the token expires at the timestamp in `run-manifest.json`.

SQL public access is **disabled**. A dedicated private endpoint subnet, one
Private Link endpoint, an isolated private DNS zone and a link to **only the new
VNet** provide connectivity. No workstation firewall rule or VPN is needed, and
inherited public-access restrictions are respected. There is **no** "allow all
Azure services" rule, shared VNet peering, shared DNS-zone edit, NAT gateway or
new web-service host. Cleanup owns the private endpoint and DNS resources too.

**Evidence:** open the generated test in the existing ALT resource, select its
run, and compare **SQLTransaction** client results with server-side SQL charts.
`run.json` contains the settled client verdict/statistics; `run-components.json`
and `run-server-metrics-config.json` show configuration, **not measured values**.
`sql-metrics.json`/`.csv` contain Azure Monitor values, and
`verified-evidence.json` is written only after all eight metrics have numeric
samples in **complete one-minute buckets entirely inside the run**, positive
SQL utilization/sessions, and a matching SQL run-token/committed-work count/balance
delta. Each accepted bucket must also overlap at least **30 seconds of actual
successful SQLTransaction sample timestamps**; provisioning/idle-only buckets do
not qualify. The database proof is one deliberately non-secret structured `SQLVerify`
response message in the raw request-result CSV, downloaded in memory with strict
archive bounds; raw archives/signed artifact
URLs/bootstrap secret fields are not saved. Artifact and telemetry polling are each
bounded to five minutes and never rerun load.
Missing/idle metrics, failed assertions, missing criteria, wrong resources and an
unsettled verdict fail closed. An interrupted start is persisted before the API
request; use `inspect`, not a retry/new run. No live verification is implied merely
by creating an app component.

**Costs:** retail checked **20 October 2026, Sweden Central, USD**:
Basic is **$0.161/day**, about **$4.90 per 730-hour month** or **$0.00671/hour
equivalent** (5 DTU and 2 GB included, LRS backups, no paid monitoring export).
The required private endpoint adds approximately **$0.01/hour = $7.30/month**;
the isolated private DNS zone adds approximately **$0.50/month**, plus tiny data
and DNS-query charges. The infrastructure subtotal is **$0.417/day, or $12.70 per
730-hour month**, not $4.90 for the whole demo.

**Inherited security matters:** the validated subscription already has Standard
Microsoft Defender for SQL, and the new server inherited **Enabled** protection.
Its current Standard Node retail meter is **$15/server/month**. With that
protection, the **total ongoing estimate is $0.911/day, or $27.70 per 730-hour
month**. The helper reads the selected subscription's security pricing plan and
includes this charge before provisioning; it never disables protection or changes
subscription security. Other subscriptions/agreements can differ.
SQL **does not auto-pause**: database, Private Link, DNS and applicable Defender
charges continue while idle until cleanup.
`prepare`/`provision` query the selected region's actual Basic retail meter and
refuse an unavailable tier rather than silently buying a larger one.
Using the repository calculator's **$0.15/VUH** assumption and **10-user,
10-minute per-engine billing floors**, the two-minute run is about **$0.25**,
not its $0.01 raw-VUH cost. VNet injection also adds temporary public-IP/load-balancer
charges (allow about **$0.03/hour plus traffic**, usually a few cents for the run).
Plan roughly **$0.30-$0.35 for one short demonstration and prompt cleanup**;
agreement rates, billing rounding, tax and existing infrastructure are excluded.
This is an estimate, not a spending cap. No scheduled/repeated runs or monthly
commitment is added to the existing ALT resource.

Local regression coverage: `python -m unittest discover -s tests -p "test_sql_metrics*.py"`.

References: [SQL retail price API](https://prices.azure.com/api/retail/prices),
[ALT pricing](https://azure.microsoft.com/pricing/details/load-testing/),
[supported SQL metrics](https://learn.microsoft.com/azure/azure-monitor/reference/supported-metrics/microsoft-sql-servers-databases-metrics),
[server-side metrics](https://learn.microsoft.com/azure/app-testing/load-testing/how-to-monitor-server-side-metrics),
[managed-identity endpoint authentication](https://learn.microsoft.com/azure/app-testing/load-testing/how-to-test-secured-endpoints#authenticate-with-a-managed-identity),
[JDBC access-token connection properties](https://learn.microsoft.com/sql/connect/jdbc/setting-the-connection-properties#accessToken),
[SQL service-principal SID/client-ID rules](https://learn.microsoft.com/sql/t-sql/statements/create-user-transact-sql#examples),
and [VNet injection/temporary network costs](https://learn.microsoft.com/azure/app-testing/load-testing/how-to-test-private-endpoint).
Private networking references:
[SQL Private Link](https://learn.microsoft.com/azure/azure-sql/database/private-endpoint-overview),
[Private Link pricing](https://azure.microsoft.com/pricing/details/private-link/),
[private DNS pricing](https://azure.microsoft.com/pricing/details/dns/).
[Defender for Cloud pricing](https://azure.microsoft.com/pricing/details/defender-for-cloud/)
applies in addition when inherited protection is enabled.

---

## The demo in one paragraph

The Contoso Bank API ships **broken on purpose**. Six realistic performance defects — a missing cache, an undersized connection pool, a global lock on the payment path, an N+1 query, sequential fraud checks, and a CPU-bound statement generator — are planted in the app and can be switched off with a single HTTP call.

So the session runs: **test it → watch it fall over → diagnose it from the dashboard → fix it live → test again → put both runs on one chart.** That arc is repeatable, takes about fifteen minutes, and cannot fail on the day.

```bash
# The reveal
curl -X POST https://$SERVICE_API_HOSTNAME/admin/config \
  -H "Content-Type: application/json" \
  -d '{"preset":"optimized"}'
```

Measured improvement (see [`tools/validate_bottlenecks.py`](./tools/validate_bottlenecks.py)):

| Endpoint | Before p95 | After p95 | Gain |
|---|---|---|---|
| Balance enquiry | 1844 ms | 516 ms | **3.6x** |
| Transaction history | 16531 ms | 453 ms | **36.5x** |
| Payment transfer | 4625 ms | 812 ms | **5.7x** |
| Statement generation | 625 ms | 328 ms | **1.9x** |

---

## Nine test definitions, four demo flavours

The kit deliberately shows the same scenario several ways, because "how do we get started?" has different answers depending on who's asking. `azd up` creates all YAML definitions automatically.

| Definition | Artefact | Audience / point | Time |
|---|---|---|---|
| **URL basic** | [`loadtests/config/url-basic.yaml`](./loadtests/config/url-basic.yaml) + URL JSON | Opening no-code demo: one endpoint, no script | 2 min |
| **URL smoke** | [`loadtests/config/url-smoke.yaml`](./loadtests/config/url-smoke.yaml) + URL JSON | No-script multi-request smoke test with criteria | 3 min |
| **URL cURL** | [`loadtests/config/url-curl.yaml`](./loadtests/config/url-curl.yaml) + cURL JSON | Bring existing runbook/Postman commands — six of them, including a correlated create-then-read | 3 min |
| **URL correlated** | [`loadtests/config/url-correlated.yaml`](./loadtests/config/url-correlated.yaml) + URL JSON | **The response-variable teaching test**: a URL request extracts the transfer id, then a URL *and* a cURL request both read it back ([doc 13.6](./docs/13-advanced-scenarios.md)) | 2 min |
| **URL false green** | [`loadtests/config/url-false-green.yaml`](./loadtests/config/url-false-green.yaml) + URL JSON | Criteria-less 404 test that proves "Completed" can still mean 100% errors | 3 min |
| **PR gate** | [`loadtests/config/pr-gate.yaml`](./loadtests/config/pr-gate.yaml) + JMeter | Cheap shift-left regression gate | 5 min |
| **Peak load** | [`loadtests/config/peak-load.yaml`](./loadtests/config/peak-load.yaml) + JMeter | Headline 1,000-VU release/capacity test | 15 min run |
| **Peak load (Locust)** | [`loadtests/config/peak-load-locust.yaml`](./loadtests/config/peak-load-locust.yaml) + Locust | Developer/Copilot-friendly equivalent | 15 min run |
| **Key Vault secrets** | [`loadtests/config/secrets-demo.yaml`](./loadtests/config/secrets-demo.yaml) + JMeter | Managed identity secret-resolution demo | 1 min run |

---

## Documentation

Start with [`docs/00-agenda-and-runbook.md`](./docs/00-agenda-and-runbook.md).

| # | Document |
|---|---|
| 00 | [Agenda and presenter runbook](./docs/00-agenda-and-runbook.md) |
| 01 | [Introduction to load testing](./docs/01-introduction-to-load-testing.md) |
| 02 | [Why load testing *as a service*](./docs/02-why-load-testing-as-a-service.md) |
| 03 | [Architecture — how it works](./docs/03-architecture-how-it-works.md) |
| 04 | [Portal feature walkthrough](./docs/04-portal-feature-walkthrough.md) |
| 05 | [Deploying and operating as code — Terraform + Azure CLI](./docs/05-deployment-and-operations-as-code.md) |
| 06 | [Demo — create, run and inspect a test](./docs/06-demo-create-run-inspect.md) |
| 07 | [Analysing results and finding bottlenecks](./docs/07-analyzing-results-and-bottlenecks.md) |
| 08 | [CI/CD integration](./docs/08-cicd-integration.md) |
| 09 | [VS Code extension](./docs/09-vscode-extension.md) |
| 10 | [GitHub Copilot integration](./docs/10-github-copilot-integration.md) |
| 11 | [Security, compliance and networking](./docs/11-security-compliance-network.md) |
| 12 | [Pricing and cost governance](./docs/12-pricing-and-cost-governance.md) |
| 13 | [Advanced scenarios](./docs/13-advanced-scenarios.md) |
| 14 | [Adoption roadmap and next steps](./docs/14-adoption-roadmap-next-steps.md) |
| 15 | [The demo application — Contoso Bank](./docs/15-demo-application-contoso-bank.md) |
| 16 | [Reference repos, links and facts to verify](./docs/16-reference-repos-and-links.md) |
| 17 | [Portal-free operations and LGTM/Grafana integration](./docs/17-portal-free-operations-and-observability.md) |
| 18 | [**Deploy and first run — hands-on walkthrough**](./docs/18-deploy-and-first-run.md) |
| 19 | [Recording walkthrough and test catalog](./docs/19-recording-and-test-catalog.md) |
| 20 | [Private network integration (VNet injection) — research](./docs/20-private-network-integration.md) |
| 21 | [**Solution architecture — this repository, end to end**](./docs/21-solution-architecture.md) |
| 22 | [Multiple runs and comparison](./docs/22-multiple-runs-and-comparison.md) |
| 23 | [VNet-injected test case — private network plan](./docs/23-vnet-injected-test-case.md) |
| 24 | [Secrets from Key Vault — audit and hardening plan](./docs/24-secrets-from-key-vault.md) |
| 25 | [Simulating JWT user identity](./docs/25-simulating-jwt-user-identity.md) |
| 26 | [**Interactive Azure CLI demo, app components, downloads and agent skills**](./docs/26-cli-demo-app-and-skills.md) |

Sections 02, 07, 11, 12, 13, 14, 17, 18 and 19 were added to the original agenda — see doc 00 for the rationale.

> 🚀 **Deploying this for the first time? Start at [doc 18](./docs/18-deploy-and-first-run.md).** It takes you from `azd up` to a completed load test with a visible bottleneck, and lists the snags to expect.

### Changed after customer review

| Feedback | Where it landed |
|---|---|
| Terraform (internally recommended) and Azure CLI instead of Bicep | Doc 05, plus real HCL in [`infra/`](./infra) |
| Result inspection without portal access; LGTM/Grafana dashboards | **Doc 17** |
| Practical cost calculation examples | Doc 12 §12.4 + [`tools/vuh_calculator.py`](./tools/vuh_calculator.py) |
| Error vs Failed vs Completed; criteria-less runs look successful | Doc 07 §7.2, demo in doc 06 §6.4a, gate guard in doc 08 §8.6a |
| Abort criteria alongside fail criteria | Doc 07 §7.9 |

---

## Before the customer session

1. Run `azd up` and confirm the app responds. **Full step-by-step: [doc 18](./docs/18-deploy-and-first-run.md).** The post-deploy hook retargets the artefacts and creates/updates the Azure Load Testing definitions automatically.
2. If you changed the hostname by hand, run `python tools/set_target_host.py --from-azd` — this is now what the hook does for you, but it remains useful as a manual repair tool.
3. **Run `pwsh -File tools/e2e-test.ps1 -SkipLoadTests`** (~5 min, no VUH spend) — proves the deployment, RBAC, every endpoint, and that the planted bottlenecks actually manifest. Then run the full `pwsh -File tools/e2e-test.ps1` (~35 min, ~$3) at least once before you present: it exercises all four result-status outcomes including the false-green and AutoStop scenarios.
4. Run `tools/validate_bottlenecks.py` against the **deployed** app and re-baseline the failure criteria in the YAML configs — absolute numbers differ between local and App Service.
5. Do one full dry run of the before/after arc end to end.
6. Check the "⚠️ Verify before the session" table in [doc 16](./docs/16-reference-repos-and-links.md) — a few product details (VS Code extension Marketplace ID, current pricing) move fast and should be re-confirmed.
7. Reset to the bottlenecked preset so you open on the problem.
8. Run `terraform init && terraform validate && terraform fmt -check` in [`infra/`](./infra); the HCL has not yet been applied against a live subscription.
9. Run `python tools/vuh_calculator.py --preset all` and sanity-check the figures against the customer's actual rate card before quoting cost.
10. Confirm whether the customer's Grafana/LGTM stack is network-reachable from an Azure VNet — this decides which integration path doc 17 §17.4 recommends. Ask *before* the session.

---

## Local development

```powershell
cd src
python -m venv .venv
.\.venv\Scripts\pip install -r requirements-dev.txt
.\.venv\Scripts\python -m uvicorn main:app --reload --port 8000
```

Note the two requirements files: several dependencies have no prebuilt Windows ARM64 wheels. See [doc 15.7](./docs/15-demo-application-contoso-bank.md).

---

## Disclaimer

The Contoso Bank API is demo software. Its admin endpoint is deliberately unauthenticated and its performance defects are deliberately planted. Do not use any part of it as a reference implementation for a production banking system.

# 16 — Reference Repositories & Links

> Verified during research on 2026-08-07. Items that could not be confirmed are explicitly flagged — **check anything marked ⚠️ before quoting it to the customer.**

---

## 16.1 Official Microsoft sample repositories

| Repo | What it contains | Demo use |
|---|---|---|
| [`Azure-Samples/azure-load-testing-samples`](https://github.com/Azure-Samples/azure-load-testing-samples) | The official samples collection. Includes **Power BI results integration**, and **.NET SDK samples** for creating URL-based quick tests and JMeter tests programmatically (with fail criteria and server metrics) | Show programmatic/SDK creation; the Power BI dashboard is a strong exec-level trend visual for FSI |
| [`Azure-Samples/nodejs-appsvc-cosmosdb-bottleneck`](https://github.com/Azure-Samples/nodejs-appsvc-cosmosdb-bottleneck) | The app used in the official *"identify performance bottlenecks"* tutorial. Node.js on App Service + Cosmos DB, with a deliberately bottlenecked write endpoint. Includes a deployment script and a JMeter script | **Fastest possible fallback demo** (~5–8 min deploy). Keep this in your back pocket in case the Contoso Bank app has a problem |
| [`Azure-Samples/load-testing-restricted-public-access`](https://github.com/Azure-Samples/load-testing-restricted-public-access) | Multiple scenario architectures: public, firewall-restricted, and **private endpoints via VNet injection**. Ships ARM/Bicep/Terraform, bash deployment scripts, **and both Azure DevOps and GitHub Actions pipelines** | **The answer to the #1 FSI objection.** Point the network/security team at this repo directly |

---

## 16.2 CI/CD integration

| Resource | Notes |
|---|---|
| [`Azure/load-testing` (GitHub Action)](https://github.com/Azure/load-testing) | Official action, `azure/load-testing@v1`. Runs on Linux/Windows/macOS runners. **Versions below 1.1 are deprecated** — use the `v1` tag or pin a version |
| **Azure DevOps task `AzureLoadTest@1`** | "Azure Load Testing" by Microsoft, in the Azure DevOps Marketplace. Uses an ARM service connection |
| [CI/CD how-to guide](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/how-to-configure-load-test-cicd) | Covers both platforms, plus authentication and RBAC requirements |
| [`az load` CLI reference](https://learn.microsoft.com/en-us/cli/azure/load) | `az extension add --name load` |

---

## 16.3 Infrastructure as code

| Resource | Notes |
|---|---|
| [AVM Bicep — `avm/res/load-test-service/load-test`](https://github.com/Azure/bicep-registry-modules/tree/main/avm/res/load-test-service/load-test) | **Recommended.** Azure Verified Module with managed identity, RBAC, diagnostics, CMK, tags, locks. Reference as `br/public:avm/res/load-test-service/load-test:<version>` |
| [AVM Terraform — `terraform-azurerm-avm-res-loadtestservice-loadtest`](https://github.com/Azure/terraform-azurerm-avm-res-loadtestservice-loadtest) | ⚠️ **Pre-1.0.0** at time of research. Usable, but flag the pre-release status to Terraform-first customers |
| [ARM template reference](https://learn.microsoft.com/en-us/azure/templates/microsoft.loadtestservice/loadtests) | Resource type `Microsoft.LoadTestService/loadTests` — check current API version |
| ⚠️ Azure Quickstart Templates | A load-testing-specific quickstart path could not be confirmed. Search [Azure template gallery](https://azure.microsoft.com/resources/templates/?term=load+testing) if needed |

---

## 16.4 Tooling

| Resource | Notes |
|---|---|
| [VS Code extension download](https://aka.ms/malt-vscode/get) | ⚠️ The Marketplace extension ID could not be independently confirmed during research, and some capability also surfaces via the **GitHub Copilot for Azure** extension. **Install and rehearse before the session** |
| [VS Code quickstart](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/quickstart-create-run-load-tests-from-visual-studio-code) | The authoritative walkthrough for the extension's capabilities |
| [`microsoft/mcp` — Azure MCP Server](https://github.com/microsoft/mcp) | Supersedes the archived `Azure/azure-mcp`. VS Code extension `ms-azuretools.vscode-azure-mcp-server`. ⚠️ **Load-testing-specific MCP tools not confirmed** — generic ARM operations work. Rehearse before demoing |
| [Apache JMeter](https://jmeter.apache.org/) | Service runs JMeter 5.6.3 on JDK 21 |
| [Locust](https://docs.locust.io/) | Service runs Locust 2.33.2 on Python 3.9.19 |

---

## 16.5 Alternative demo target applications

Only relevant if you choose not to use the purpose-built Contoso Bank app in this repo. Ranked by suitability for this customer.

| Repo | Deploy | FSI fit | Verdict |
|---|---|---|---|
| [`Azure-Samples/nodejs-appsvc-cosmosdb-bottleneck`](https://github.com/Azure-Samples/nodejs-appsvc-cosmosdb-bottleneck) | ~5–8 min, PowerShell script | ⭐⭐⭐⭐ | **Best fallback.** Intentional, reproducible bottleneck; used by the official tutorial |
| [`Azure-Samples/eShopOnAzure`](https://github.com/Azure-Samples/eShopOnAzure) | ~10–20 min, `azd up` | ⭐⭐⭐ | .NET Aspire microservices with ordering/payment APIs — conceptually close to a payments platform. Longer deploy, more moving parts |
| [`Azure-Samples/todo-csharp-sql`](https://github.com/Azure-Samples/todo-csharp-sql) | ~8–12 min, `azd up` | ⭐⭐⭐ | Simple C# API + Azure SQL + App Insights. Very reliable, but "todo items" undercuts the finance narrative |
| [`Azure-Samples/todo-python-mongo`](https://github.com/Azure-Samples/todo-python-mongo) | ~8–12 min, `azd up` | ⭐⭐ | Python FastAPI variant |
| [`Azure-Samples/azure-search-openai-demo`](https://github.com/Azure-Samples/azure-search-openai-demo) | ~15–25 min, `azd up`, needs OpenAI quota | ⭐⭐ | Only if the customer's interest is "load testing AI endpoints". Slow deploy, quota risk |
| [`microsoft/eShopOnContainers`](https://github.com/microsoft/eShopOnContainers) | 20–40+ min | ⭐ | **Avoid** — aging (.NET 5 era), no `azd`, too much setup for a live demo |
| `Azure-Samples/contoso-real-estate` | — | ❌ | **Archived. Do not use.** |

> **Note:** no banking/fintech-themed sample app exists under the Microsoft/Azure orgs — which is exactly why this repo builds one (see [doc 15](15-demo-application-contoso-bank.md)).

---

## 16.6 Core documentation

**Start here**
- [Azure Load Testing documentation home](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/)
- [What is Azure Load Testing?](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/overview-what-is-azure-load-testing)
- [Key concepts](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/concept-load-testing-concepts)

**Creating tests**
- [Quickstart: URL-based test](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/quickstart-create-and-run-load-test)
- [JMeter-based test](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/how-to-create-and-run-load-test-with-jmeter-script)
- [Locust-based test](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/quickstart-create-run-load-test-with-locust)
- [Parameterize load tests](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/how-to-parameterize-load-tests)
- [YAML config reference](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/reference-test-config-yaml)

**Running & analysing**
- [Configure for high-scale load](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/how-to-high-scale-load)
- [Monitor server-side metrics](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/how-to-monitor-server-side-metrics)
- [Supported Azure resource types](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/resource-supported-azure-resource-types)
- [Define test fail criteria](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/how-to-define-test-criteria)
- [Compare multiple test runs](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/how-to-compare-multiple-test-runs)
- [Export test results](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/how-to-export-test-results)
- [Analyze results with actionable insights (AI)](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/how-to-analyze-test-results-using-actionable-insights)
- [Diagnose failing load tests](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/how-to-diagnose-failing-load-test)
- [Tutorial: identify performance bottlenecks](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/tutorial-identify-bottlenecks-azure-portal)

**Security & networking**
- [VNet injection concepts](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/concept-azure-load-testing-vnet-injection)
- [Test private endpoints](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/how-to-test-private-endpoint)
- [Test secured endpoints](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/how-to-test-secured-endpoints)
- [Customer-managed keys](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/how-to-configure-customer-managed-keys)
- [Managed identity](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/how-to-use-a-managed-identity)
- [Assign roles (RBAC)](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/how-to-assign-roles)

**Scenario guidance**
- [Load testing Azure App Service](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/concept-load-test-app-service)
- [Load testing Azure Functions](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/how-to-create-load-test-function-app)

**Operations & commercial**
- [Limits, quotas and capacity](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/resource-limits-quotas-capacity)
- [Pricing](https://azure.microsoft.com/en-us/pricing/details/app-testing/)
- [Azure Pricing Calculator](https://azure.microsoft.com/pricing/calculator/)
- [Microsoft Service Trust Portal](https://servicetrust.microsoft.com/)
- [Azure compliance documentation](https://learn.microsoft.com/en-us/azure/compliance/)

**Context & best practice**
- [Well-Architected Framework — Performance testing](https://learn.microsoft.com/en-us/azure/well-architected/performance-efficiency/performance-test)
- [Well-Architected Framework — Reliability testing strategy](https://learn.microsoft.com/en-us/azure/well-architected/reliability/testing-strategy)
- [Azure Chaos Studio](https://learn.microsoft.com/en-us/azure/chaos-studio/)

---

## 16.7 ⚠️ Verify before the session

These could not be fully confirmed during research. **Check each one during rehearsal** and follow up in writing on anything still open:

| Item | Why it matters | How to check |
|---|---|---|
| **Free monthly VUH allotment** | ⚠️ **Sources actively conflict** (0 vs 50 VUH). Procurement will ask, and a wrong number is worse than "I'll confirm" | [Live pricing page](https://azure.microsoft.com/en-us/pricing/details/app-testing/) |
| **Current $/VUH pricing** | Doc 12 uses $0.15 (≤10k VUH/mo) and $0.06 (>10k). Confirm, and ask for their EA rate | [Live pricing page](https://azure.microsoft.com/en-us/pricing/details/app-testing/) |
| **Terraform AVM module published on the Registry** | Doc 05 §5.3.5. Could **not** be confirmed as published; pre-1.0. If they mandate AVM modules this may be a real blocker | [Terraform Registry](https://registry.terraform.io/search/modules?q=loadtestservice) |
| **`az load` extension preview status** | Doc 17 §17.2. Disclose it before they find it. May need a tooling-governance exception | `az extension list-available --query "[?name=='load']"` |
| **`testResult` value for an auto-stopped run** | Doc 07 §7.9 leaves this deliberately blank. Don't assert it | Run a test with `autoStop.errorPercentage: 1` against a failing endpoint and read both fields |
| **Complete `testResult` enum** | `PASSED` / `FAILED` / `NOT_APPLICABLE` confirmed; exhaustiveness not verified from the raw spec | [Data-plane REST reference](https://learn.microsoft.com/en-us/rest/api/loadtesting/dataplane/load-test-run/get-test-run) |
| **Prometheus remote-write / OTLP from a JMeter Backend Listener** | Doc 17 path A. **InfluxDB is confirmed; Mimir is not.** Scope as a POC spike, don't promise it | [Backend listener samples](https://github.com/Azure-Samples/azure-load-testing-samples/tree/main/jmeter-backend-listeners) |
| **Whether their Grafana is reachable from an Azure VNet** | Determines if doc 17 path A needs VNet injection, firewall changes and a change request | Ask them — put it on the pre-POC slide (§17.5) |
| **VS Code extension marketplace ID / packaging** | Doc 09 demo depends on it | Install from <https://aka.ms/malt-vscode/get> and rehearse |
| **Azure MCP Server load-testing-specific tools** | Doc 10 surface 3 | Install and inspect the available tools |
| **GA/preview status of individual features** | Don't over-claim to a regulated customer | Azure portal + [Azure updates](https://azure.microsoft.com/updates/?query=load+testing) |
| **Compliance certifications for this specific service** | FSI gating question | [Service Trust Portal](https://servicetrust.microsoft.com/) |
| **SLA** | Contractual question | [Microsoft SLA for Online Services](https://www.microsoft.com/licensing/docs/view/Service-Level-Agreements-SLA-for-Online-Services) |
| **Private endpoint on the load testing resource itself** (vs. on the tested app) | Security architecture question | Current networking docs |
| **Current ARM API version** for `Microsoft.LoadTestService/loadTests` | Bicep appendix walkthrough | [Template reference](https://learn.microsoft.com/en-us/azure/templates/microsoft.loadtestservice/loadtests) |

### Confirmed during research — safe to assert

These *were* verified and can be stated with confidence:

| Fact | Detail |
|---|---|
| **`status` and `testResult` are separate fields** | A run with no fail criteria and 100% failing requests ends `status=DONE`, `testResult=NOT_APPLICABLE` |
| **Terraform resource exists** | `azurerm_load_test`, `hashicorp/azurerm` >= 3.70.0 |
| **Terraform cannot manage test definitions** | Resource container only — test plans and criteria are data-plane |
| **Full CLI lifecycle exists** | `az load test create` / `test file upload` / `test app-component add` / `test-run create` / `show` / `stop` / `download-files` / `metrics list` |
| **ALT client-side metrics are NOT in Azure Monitor** | A Grafana Azure Monitor data source will not show load test results |
| **Diagnostic settings emit `OperationLogs` only** | Audit trail, not performance data |
| **JMeter Backend Listeners are supported** | InfluxDB listener confirmed via `Azure-Samples` |
| **AutoStop defaults** | `errorPercentage: 90`, `timeWindow: 60` (seconds); disable with `autoStop: disable`; auto-stopped run ends `status=CANCELLED` |
| **Minimum billing rule (from 2026-03-01)** | `max(actual VUs, 10 per engine)` and `max(duration, 10 minutes)` |

> **Rule for the session:** if you are not certain, say *"let me confirm that and come back to you in writing."* With a financial services customer, one confident wrong answer costs more credibility than ten honest deferrals.

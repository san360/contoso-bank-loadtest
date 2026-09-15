# 04 — Service Feature Overview (Portal Walkthrough)

> **Section goal:** A guided tour of the service surface so the audience knows what exists *before* you run the scripted demo. Keep it moving — this is a tour, not a deep dive. Every feature you show here should map to a customer problem you named in doc 01 §4.

**Duration:** 20 minutes · **Format:** Live Azure portal · **Audience:** All

> **Presenter tip:** Have a **completed test run with rich results already open in a second browser tab.** Live-created tests are empty and unimpressive. Show the finished article first ("here's where we're going"), then build up to it.

> ### 🔑 Frame this before you start — it defuses the biggest objection
>
> This customer **will not have portal access in higher environments**. Say so first, in your own words, before someone in the room says it for you:
>
> *"I'm going to walk the portal because it's the fastest way to show you what the service can do. But I want to be clear up front: the portal is a **learning surface, not an operating surface**. Everything I show you here has a CLI equivalent, and in your test and production subscriptions you'd drive all of it from a pipeline. I'll prove that in section 17."*
>
> This turns a 20-minute segment that would otherwise feel irrelevant into a product tour they can relax into. **As you walk each feature, drop the CLI equivalent in passing** — the full mapping is in doc [`17-portal-free-operations-and-observability.md`](17-portal-free-operations-and-observability.md) §17.2.1.

---

## 4.1 Finding the service

- Portal → search **"Azure Load Testing"** (now surfaced under the **Azure App Testing** umbrella alongside Playwright Workspaces).
- Show the resource blade: region, pricing/billing info, identity, networking, encryption, diagnostics.
- **Point out the region** and tie it back to data residency (doc 11).

---

## 4.2 Resource-level features to show

Walk the left-hand navigation of the Load Testing resource. Call out each of these:

| Blade | What to say |
|---|---|
| **Tests** | The catalogue of reusable test definitions. Filter, search, and see last-run status per test. |
| **Test runs** | Every execution, with status and pass/fail outcome. **This is your audit trail.** |
| **Identity** | System-assigned and user-assigned managed identity — used for Key Vault access, server-metric access, and simulating MI auth flows. |
| **Networking** | VNet injection configuration for reaching private endpoints. |
| **Encryption** | Customer-managed keys (Key Vault RSA key). Note: must be set at resource creation. |
| **Access control (IAM)** | Built-in roles: **Load Test Owner / Contributor / Reader**. Show them explicitly — separation of duties matters to FSI. |
| **Diagnostic settings** | Ship resource logs to Log Analytics / Event Hub / Storage. |
| **Alerts / notifications** | Notification rules that fire an action group on test-run events. |
| **Quotas / usage** | Where to check and request engine and VUH quota increases. |

---

## 4.3 Creating a test — show all three entry points

The key message: **there are three doors into the same service, for three different personas.**

### (a) URL-based test — "the no-script door"
- Portal → Create → **URL-based test**.
- **Basic mode:** paste an endpoint URL, choose a VU target, click Run. Literally that.
- **Advanced mode:** show that you can add
  - multiple requests defined in JSON, or **paste a cURL command** (developers love this),
  - HTTP verbs, custom headers, query params, request bodies,
  - a **load pattern** — Linear, Step, or Spike,
  - ramp-up time, duration, and engine instance count.
- **Say out loud:** *"No JMeter. No script. No infrastructure. This is a QA analyst or a product owner running a real 1,000-user test."*

### (b) JMeter test — "the reuse-your-IP door"
- Portal → Create → upload an existing `.jmx` file.
- Show the additional file types you can attach: CSV data, user `.properties` files, JMeter plugin JARs, ZIP artefacts.
- **Say out loud:** *"If your teams already have JMeter scripts, they run here unchanged. Your existing investment is not wasted — it's just no longer your problem to host."*

### (c) Locust test — "the Python door"
- Upload a `locustfile.py`, plus optional `requirements.txt` and `.conf`/`.ini`/`.toml` config.
- **Say out loud:** *"For teams who find JMeter's XML painful, tests are just Python. And this is the format GitHub Copilot generates best — we'll show that later."*

> Note the boundary honestly: **JMeter and Locust are the supported frameworks.** Other tools (k6, Gatling, NeoLoad) are not.

---

## 4.4 The test configuration tabs

Open an existing JMeter-based test and walk the tabs. This is the "feature overview" core.

### Test plan
Script + supporting files. Mention the **`splitAllCSVs`** option: with 10 engines and a 10,000-row customer CSV, each engine gets a distinct 1,000 rows — no duplicate logins, no artificially warm caches. *This is a detail that signals maturity to a performance engineer.*

> ### ⚠️ The portal's request editor is not a reliable view of a code-created URL test
>
> Every URL test in this repo is created from a requests JSON file by the CLI. Opening **Edit → Test plan** on one can show **fewer requests than the test actually contains — including none at all**. The test is fine; the editor is not showing you the truth.
>
> **Two measured cases (2026-09-15), same resource, same day:**
>
> | Test | Requests in the stored JSON | Requests the portal editor listed |
> |---|---:|---|
> | `contoso-bank-url-basic` | 1 | **0** — red "Add at least one request to the test plan" |
> | `contoso-bank-url-correlated` | 3 | **2** — `GetTransferById` silently missing |
>
> In the second case the missing row was the URL-type request whose endpoint contains a variable (`/api/transfers/${transferId}`). The cURL request using the *same* variable rendered fine, because its command is an opaque string to the editor. So the editor appears to drop rows it cannot map onto its own UI model, silently and without any warning.
>
> **The tests themselves are complete.** For `contoso-bank-url-correlated`, verified:
>
> | Check | Result |
> |---|---|
> | Stored `URL_TEST_CONFIG` | **3** requests, `VALIDATION_SUCCESS` |
> | Generated `url_test.jmx` | **3** samplers: `CreateTransfer`, `GetTransferById`, `GetTransferByIdCurl` |
> | Run `correlated-fixed-153140` | **3** labels executed: 1,540 × `201`, 1,520 × `200`, 1,520 × `200`, 0% errors |
>
> **Consequences for the session:**
> - Demo URL tests by **running** them and showing the dashboard — never by opening **Edit**.
> - Treat **Apply/Save** in that editor as destructive: saving a plan the editor has already dropped rows from risks writing that truncated view back. If anyone clicks it, restore with `pwsh -File tools/provision_loadtests.ps1`.
> - Verify from the API, not the blade: `az load test show --test-id <id> --query "inputArtifacts.urlTestConfigFileInfo.validationStatus"`, then check the request count in the run's results.
> - To edit requests, change the JSON in [`loadtests/url-tests/`](../loadtests/url-tests) and re-run the provisioning hook.
>
> **Request cap — the portal and the docs disagree.** The portal editor states *"You can add up to 15 requests in a test"*, while Microsoft Learn still says five. ⚠️ Re-confirm before quoting either number to a customer; if you need the limit to be load-bearing in an argument, test it.

### Parameters
| Type | Use |
|---|---|
| **Environment variables** | Non-sensitive config — base URL, test data size, feature flags |
| **Secrets** | A **Key Vault secret URI**, resolved at runtime via managed identity. In JMeter, read it into a User Defined Variables element with the `GetSecret` function and reference that variable elsewhere; in Locust, exposed as an env var |
| **Certificates** | A Key Vault certificate for **mTLS / client-certificate authentication** — very common in banking APIs |

**Say out loud:** *"Credentials are never in the script and never in Git. They're referenced from Key Vault and resolved by managed identity at run time."*

### Load
- Engine instance count (slider).
- Show the calculated total VUs updating live.
- For URL-based tests, the load pattern and ramp-up settings.

### Test criteria
- Add a criterion, e.g. `avg(response_time_ms) > 300`, `percentage(error) > 5`, `p95(response_time_ms) > 800`.
- Show that criteria can be **test-level** (all requests) or **request-level** (a specific named sampler — e.g. only the `POST /transfers` call).
- Show **server-side criteria** too — e.g. fail the test if App Service CPU exceeds 80%.
- Up to 50 criteria per test; where several apply to one metric, the lowest threshold wins.

**Say out loud:** *"This is the line between 'a load test' and 'a quality gate'. Without criteria, someone has to look at a chart and form an opinion. With criteria, the pipeline decides."*

### Monitoring / App Components
- Add the App Service, Application Insights, SQL Database, Cosmos DB — whatever is in the path.
- Show the auto-selected metrics per resource, and that you can add/remove metrics.
- Note the breadth of supported resource types — relevant FSI ones include **Azure SQL Database / Managed Instance / Elastic Pool, Cosmos DB, API Management, AKS, Functions, Service Bus, Event Hubs, Key Vault, Application Gateway, Front Door, Redis-adjacent services, Storage, VM Scale Sets**.

**Say out loud:** *"This is the bit a self-hosted JMeter grid can't give you without a second integration project."*

---

## 4.5 The test run dashboard — the money shot

Open a completed run with interesting results.

Point out, in this order:
1. **Pass / fail banner** at the top — the criteria verdict.
2. **Client-side charts:** virtual users over time, response time (with percentile selector), requests per second, error percentage.
3. **The percentile selector** — switch from average to p95/p99 and let the audience watch the "acceptable" line become unacceptable. *This is the most reliably persuasive 5 seconds in the whole demo.*
4. **Per-request breakdown** — which named sampler is the slow one.
5. **Server-side metrics** — the App Components panel, correlated on the same time axis.
6. **Engine health** — CPU/memory of the load generators, proving the test itself was valid.
7. **AI summary and insights** — click **Generate insights**; it correlates client and server data to explain latency spikes, throughput drops and backend bottlenecks. Add the honest caveat that AI insights should be validated against your own telemetry.

---

## 4.6 Results, comparison and export

| Feature | What to show |
|---|---|
| **Compare runs** | Select 2+ runs → overlay them. This is how you demonstrate a regression or prove an optimisation worked. |
| **Download results** | Apache JMeter CSV/JTL format (one CSV per engine) + downloadable JMeter HTML dashboard report. |
| **Engine logs** | For diagnosing a failing script (as opposed to a failing app). |
| **Retention** | Client metrics 365 days · server metrics 90 days · results & logs 6 months. |

⚠️ **Limitation to state honestly:** results file download is not available for runs with **more than 45 engine instances** or **longer than 3 hours** — for those, stream results via the JMeter Backend Listener / Locust event hooks or copy from a storage account.

---

## 4.7 AutoStop — the cost and safety guardrail

- Automatically aborts a run when the error percentage exceeds a threshold within a rolling time window.
- Defaults: error percentage **90%**, time window **60 seconds**. Can be disabled.
- **Two reasons this matters:** (1) it stops you burning VUH on a test that's failing anyway because of a typo'd URL; (2) it stops a runaway test hammering a shared environment.

---

## 4.8 Automation surfaces (preview of docs 05 and 08)

Close the tour by showing that everything you just clicked is also API-driven:

- **Azure CLI** — `az load` extension (create resources, tests, runs).
- **GitHub Actions** — `azure/load-testing@v1`.
- **Azure Pipelines** — `AzureLoadTest@1` task.
- **REST API and SDKs** (including .NET).
- **ARM / Bicep / Terraform** — resource type `Microsoft.LoadTestService/loadTests`.
- **YAML test config file** — the test definition itself lives in your repo (doc 08).

**Say out loud:** *"Nothing you've seen requires the portal. The portal is for exploring; the YAML file and the pipeline are for operating."*

---

## 4.9 Quick feature checklist (leave-behind slide)

- ✅ URL-based (no-script), JMeter (`.jmx`) and Locust (`.py`) tests — all GA
- ✅ Managed, elastic engine fleet — no infrastructure to run
- ✅ Load patterns: linear, step, spike; configurable ramp-up and duration
- ✅ Parameters, Key Vault secrets, client certificates, CSV data with auto-split
- ✅ JMeter plugins and user properties supported
- ✅ Client + server-side metrics correlated (Azure Monitor / App Insights / Container Insights)
- ✅ Pass/fail criteria on client *and* server metrics — up to 50 per test
- ✅ AutoStop guardrail
- ✅ Run comparison and trend analysis
- ✅ AI-generated actionable insights
- ✅ CI/CD: GitHub Actions + Azure DevOps
- ✅ CLI, REST API, SDK, Bicep/Terraform
- ✅ VNet injection for private/on-prem endpoints; multi-region load generation
- ✅ Managed identity, Azure RBAC, customer-managed keys, in-region data residency
- ✅ VS Code extension with GitHub Copilot script generation

---

## Reference links

- [Create a URL-based load test](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/quickstart-create-and-run-load-test)
- [Create a JMeter-based load test](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/how-to-create-and-run-load-test-with-jmeter-script)
- [Create a Locust-based load test](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/quickstart-create-run-load-test-with-locust)
- [Parameterize load tests](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/how-to-parameterize-load-tests)
- [Define test fail criteria](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/how-to-define-test-criteria)
- [Monitor server-side metrics](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/how-to-monitor-server-side-metrics)
- [Compare multiple test runs](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/how-to-compare-multiple-test-runs)
- [Export test results](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/how-to-export-test-results)

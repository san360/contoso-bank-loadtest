# 06 — Demo: Create, Run and Inspect a Test

> **Section goal:** The centrepiece. The audience sees a real test go from nothing to results. Run it in **three escalating flavours** so each persona in the room sees their own entry point.
>
> **Target application:** Contoso Bank API — see [`15-demo-application-contoso-bank.md`](15-demo-application-contoso-bank.md).

**Duration:** 30 minutes · **Format:** Live · **Audience:** All

---

## 6.0 Setup (before the audience arrives)

- [ ] `azd up` completed; note the API base URL
- [ ] `GET {baseUrl}/health` returns 200
- [ ] `POST {baseUrl}/admin/reset` works (resets demo state between runs)
- [ ] Load testing resource created in the **same region** as the app
- [ ] A **previous successful run** exists so you can demo comparison
- [ ] `az login` done; `az extension add --name load` installed
- [ ] Terminal font size increased; portal zoom ≥ 125%

**Reset between runs:**
```powershell
curl -X POST "$env:API_BASE_URL/admin/reset"
```

---

## 6.1 Flavour A — UI / no-code (≈6 minutes)

**Persona:** QA analyst, product owner, anyone who has never written a test script.
**Message:** *"Time to first load test is minutes, not weeks."*

### Steps
1. Portal → Azure Load Testing resource → **Create** → **URL-based test**.
2. **Basic tab:** paste `https://<app>.azurewebsites.net/api/accounts/ACC-1001/balance`.
3. Set **virtual users = 50**. Leave everything else default.
4. Click **Review + create** → **Create**. The test starts immediately.
5. While engines provision (~1–2 min), narrate the architecture from doc 03: *"Right now Microsoft is provisioning managed load generation engines in this region. You are not paying for a VM. When the test ends they're destroyed."*
6. Watch the live dashboard populate: VUs ramping, response time, RPS, errors.

### Then show Advanced mode
Create a second URL-based test with **Advanced settings enabled** and show:
- Adding **multiple requests** (balance → transactions → transfer) — including the **paste-a-cURL-command** option.
- Custom headers (e.g. `Authorization`, `X-Correlation-Id`).
- **Load pattern** selection: Linear / Step / **Spike**.
- Ramp-up time, duration and engine instance count.
- Adding **pass/fail criteria** in the UI.

> **Line to land:** *"That was fifty concurrent users against a production-shaped API, with zero code, zero infrastructure and zero JMeter knowledge. Now let's give the engineers the full toolkit."*

---

## 6.2 Flavour B — Code (≈12 minutes)

**Persona:** Performance engineer.
**Message:** *"Your existing JMeter IP works unchanged, and your tests live in Git."*

### 6.2.1 Upload an existing JMeter script

1. Show [`../loadtests/jmeter/contoso-bank.jmx`](../loadtests/jmeter/contoso-bank.jmx) briefly in VS Code (or JMeter GUI if you have it) — point out that it's an ordinary, unmodified JMeter test plan.
2. Portal → Create test → **Upload a JMeter script**.
3. Attach the supporting files:
   - `accounts.csv` — test data (customer/account identifiers)
   - `user.properties` — JMeter properties
4. **Enable "Split CSV evenly between test engines"** and explain why: with 4 engines and 4,000 accounts, each engine drives a distinct 1,000 — no duplicate logins, no unrealistically warm cache.
5. **Parameters tab:** add
   - env var `BASE_URL` → the app URL
   - secret `API_KEY` → a **Key Vault secret URI**, and show the `GetSecret` usage in the script.
   *Say: "The credential is not in the script, not in Git, and not in the test definition. It's resolved at runtime by managed identity."*
6. **Load tab:** set engine instances to **4**; show the total VU calculation (250 threads × 4 = 1,000).
7. **Test criteria tab:** add
   - `avg(response_time_ms) > 300`
   - `percentage(error) > 5`
   - request-level: `POST /transfers` → `p95(response_time_ms) > 800`
   - a **server-side** criterion: App Service CPU `> 80%`
8. **Monitoring tab:** add App Components — the **App Service**, the **App Service Plan** and **Application Insights**.
9. **Run it.**

### 6.2.2 The same test as code — `az load` CLI

Switch to the terminal and show that everything above is expressible as a file in the repo.

`loadtests/config/peak-load.yaml`:
```yaml
version: v0.1
testId: contoso-bank-peak-load
displayName: Contoso Bank - Peak Hour Transfers
description: Simulates month-end payment surge
testType: JMX
testPlan: ../jmeter/contoso-bank.jmx
engineInstances: 4
configurationFiles:
  - ../data/accounts.csv
splitAllCSVs: true
properties:
  userPropertyFile: ../jmeter/user.properties
env:
  - name: BASE_URL
    value: contoso-bank-api.azurewebsites.net
secrets:
  - name: API_KEY
    value: https://kv-contoso-demo.vault.azure.net/secrets/demo-api-key
failureCriteria:
  clientMetrics:
    - avg(response_time_ms) > 300
    - percentage(error) > 5
    - GetBalance: p95(response_time_ms) > 500
autoStop:
  errorPercentage: 90
  timeWindow: 60
appComponents:
  - resourceId: /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Web/sites/<app>
    kind: web
```

```powershell
az extension add --name load

# `azd up` has already run this for every file in loadtests/config.
# Show it as the mechanics behind the hook, or re-run it after editing YAML.
az load test create `
  --load-test-resource alt-contoso-demo `
  --resource-group rg-contoso-loadtest `
  --test-id contoso-bank-peak-load `
  --load-test-config-file loadtests/config/peak-load.yaml

az load test-run create `
  --load-test-resource alt-contoso-demo `
  --resource-group rg-contoso-loadtest `
  --test-id contoso-bank-peak-load `
  --test-run-id "run-$(Get-Date -Format yyyyMMdd-HHmmss)"
```

> **Shortcut for the live session:** `pwsh -File tools/run_loadtest.ps1 -TestId contoso-bank-peak-load` (or `tools/run_loadtest.sh --test-id ...`) wraps the two commands above, waits for the run, and prints the verdict, the breached criteria and the percentiles without leaving the terminal. `-List` shows every deployed definition and spends nothing.

**Say out loud:** *"That YAML file is the entire test — script reference, data, secrets, scale, thresholds and monitoring. It's reviewed in a pull request like any other change. In ten minutes we'll run exactly this file from a pipeline."*

### 6.2.3 (Optional) Locust — the Python door
Show [`../loadtests/locust/locustfile.py`](../loadtests/locust/locustfile.py) — a readable ~40-line Python file expressing the same banking journey. Note this is the format the VS Code extension and Copilot generate (docs 09, 10).

---

## 6.3 Flavour C — Inspect the results (≈12 minutes)

**This is the most important part of the whole demo.** Do not rush it. Anyone can start a test; the value is in what you learn.

### Walk the dashboard in this order

1. **Pass/fail banner.** Start with the verdict. *"The test failed — and nobody had to interpret a chart to know that."*
2. **Virtual users chart.** Confirms the load actually ramped as designed.
3. **Response time chart.** Now do the percentile move:
   - Show the **average** — looks acceptable.
   - Switch to **p95**, then **p99** — watch the line jump.
   - *"Your average customer is fine. Your busiest corporate client is not. Averages are how performance problems hide."*
4. **Requests per second.** Point out where throughput **plateaus** — that's the system's real capacity ceiling, and it's the number to take to a capacity planning meeting.
5. **Errors.** Show where the error rate starts climbing, and correlate it with the RPS plateau.
6. **Per-request breakdown.** Show that `GET /balance` is fine while `POST /transfers` is the offender. *"The test tells you which endpoint, not just 'the app is slow'."*
7. **Engine health.** CPU/memory well under 75% → *"the load generators were healthy, so this is your application, not our tool."*
8. **Server-side metrics (App Components).** The payoff — App Service CPU pegged, or thread/connection saturation, aligned on the same time axis as the latency spike.

### Compare runs
- Select the current run and the earlier baseline run → **Compare**.
- Show the overlay. *"This is how you prove an optimisation worked, and how you catch a regression before a customer does."*

### AI insights
- Click **Generate insights**.
- Read the summary out; drill into **View detailed insights** for evidence and recommended next steps.
- Add the honest caveat: AI-generated insights may not always be accurate — validate against your own telemetry.

### Export
- Download the results (JMeter CSV/JTL, one per engine) and the JMeter HTML dashboard report.
- *"Your evidence pack for the audit or the release board is a download, not a manual write-up."*

---

## 6.4 The "prove the value" moment (do not skip this)

This is the sequence that converts the demo from *interesting* to *funded*. It's the reason the demo app has a togglable bottleneck.

1. **Run the test with the bottleneck enabled.** It fails. p95 is terrible, throughput plateaus, App Service CPU is pegged.
2. **Show the AI insights and server-side metrics identifying the cause.**
3. **Fix it live** — flip the feature flags. The fastest option is the built-in preset:
   ```bash
   curl -X POST "$API_BASE_URL/admin/config" \
     -H "Content-Type: application/json" -d '{"preset": "optimized"}'
   ```
   Or change one knob at a time to make the causal link explicit:
   ```bash
   curl -X POST "$API_BASE_URL/admin/config" \
     -H "Content-Type: application/json" \
     -d '{"enable_cache": true, "db_pool_size": 64, "enable_transfer_lock": false}'
   ```
   > ⚠️ **Field names are snake_case** (`enable_cache`, not `enableCache`). The API returns 422 on unknown fields — verify with `GET /admin/config` after posting.
4. **Re-run the same test, unchanged.**
5. **Compare the two runs side by side.** Same script, same load, dramatically different result.

> **The line to land:** *"That is the entire value proposition in ninety seconds. We found a defect that only exists under concurrency, we understood it from server-side telemetry, we fixed it, and we proved the fix — before a single customer saw it. Doing that on every pull request is what section 08 is about."*

---

## 6.4a The false-green moment (the strongest 3 minutes in the deck)

**Raised in review, and it deserves its own slot.** This is the demo that makes a room go quiet.

### The setup

Use the checked-in URL test [`../loadtests/config/url-false-green.yaml`](../loadtests/config/url-false-green.yaml):

- **Test id:** `contoso-bank-url-false-green`
- **Type:** URL test, because the moment needs one static known-bad request, not a correlated JMeter journey. URL tests are capped at five requests; this one uses only one.
- **Endpoint:** `/api/false-green-404`, a deliberately nonexistent API route that returns HTTP 404.
- **Load profile:** 1 engine, 10 virtual users, 10s ramp, 60s duration. The profile lives in [`../loadtests/url-tests/contoso-bank-false-green.json`](../loadtests/url-tests/contoso-bank-false-green.json), not in the YAML.
- **No `failureCriteria`:** deliberately omitted.
- **AutoStop:** disabled, because the usual 90% error threshold would cancel the run and produce `status: CANCELLED`, which ruins the point.

Sanity-check the target before spending VUH:

```powershell
curl.exe -i https://app-loadtest-27d2e5.azurewebsites.net/api/false-green-404
# HTTP/1.1 404 Not Found
# {"detail":"Not Found"}
```

Create or refresh the test definition:

```powershell
$RG = "rg-loadtest"
$ALT_RESOURCE = "alt-loadtest-27d2e5"
$SUBSCRIPTION_ID = "1da52b57-468a-4dc6-ba36-1255cc1f6e8e"
$TEST_ID = "contoso-bank-url-false-green"

az load test create `
  --load-test-resource $ALT_RESOURCE `
  --resource-group $RG `
  --subscription $SUBSCRIPTION_ID `
  --test-id $TEST_ID `
  --load-test-config-file loadtests\config\url-false-green.yaml

# If a previous rehearsal already created it, use the same arguments with:
# az load test update ...
```

### The reveal, in three beats

1. **Run it. Let it finish.** Show the run list: **✅ Completed.** Say nothing yet. Let someone in the room nod.
   ```powershell
   $RUN_ID = "false-green-$(Get-Date -Format yyyyMMdd-HHmmss)"

   az load test-run create `
     --load-test-resource $ALT_RESOURCE `
     --resource-group $RG `
     --subscription $SUBSCRIPTION_ID `
     --test-id $TEST_ID `
     --test-run-id $RUN_ID
   ```
2. **Now open the dashboard.** **100% of requests failed.** Every single request in this run hit the 404 route.
3. **Show both fields together:**
   ```powershell
   az load test-run show `
     --load-test-resource $ALT_RESOURCE `
     --resource-group $RG `
     --subscription $SUBSCRIPTION_ID `
     --test-run-id $RUN_ID `
     --query "{status:status, testResult:testResult}" `
     -o table

   # Status    TestResult
   # --------  --------------
   # DONE      NOT_APPLICABLE
   ```

   ⚠️ **Hard-won warning:** `testResult` is **not settled at the instant `status`
   first becomes `DONE`**. In this environment it briefly read
   `NOT_APPLICABLE` before resolving to `PASSED` / `FAILED` on tests that really
   did have criteria. Wait a few seconds and re-query. For this demo,
   `NOT_APPLICABLE` is the real final value because the YAML genuinely has no
   `failureCriteria`.

#### Verified numbers from an actual run

This is not a thought experiment. Run `false-green-proof-20260830-154506` of this
exact test definition produced:

| Field | Value |
|---|---|
| `status` | `DONE` |
| `testResult` | `NOT_APPLICABLE` |
| Total requests | **12,254** |
| Errors | **12,254** |
| Error rate | **100.00%** |
| Duration | 3m 31s wall clock |
| Estimated VUH | 2 |

Every request failed. The run is green. If you want to reproduce the error rate
yourself rather than reading it off the dashboard, note that
`az load test-run show` does **not** return `testRunStatistics` in the current
extension — pull the metrics instead:

```powershell
az load test-run metrics list `
  --load-test-resource $ALT_RESOURCE --resource-group $RG `
  --test-run-id $RUN_ID `
  --metric-namespace LoadTestRunMetrics --metric-name Errors --aggregation Total -o json

# Compare against --metric-name TotalRequests --aggregation Count
```

> **The line to land:** *"That test is green. Every request failed. If this were running in your pipeline tonight, it would have passed, and you'd have shipped a completely broken flow. The status field was never telling you your app was healthy — it was telling you the load generator did its job."*

### Then fix it, in front of them

Add one line:

```yaml
failureCriteria:
  clientMetrics:
    - percentage(error) > 5
```

Re-run. **Failed** — `status: DONE`, `testResult: FAILED`. The test now works as a test.

> *"One line. That's the difference between a pipeline that protects you and a pipeline that lies to you."*

### Why this lands so hard with this audience

- It is a **process** failure, not a product limitation — so it doesn't sound like you're disparaging the product.
- It maps directly onto a control-failure narrative that a bank's risk function understands immediately.
- It gives them an **action for Monday**: audit every existing load test for missing criteria.
- It sets up doc [`08-cicd-integration.md`](08-cicd-integration.md), where the pipeline guard makes it structurally impossible.

No app reset is needed afterwards; this version proves the point by targeting a
known-bad URL rather than changing application configuration.

See §7.2 for the full status taxonomy and §7.9 for why `autoStop` had to be disabled here.

---

## 6.5 Troubleshooting cheat sheet (for you, not the slide)

| Symptom | Likely cause | Fix |
|---|---|---|
| Test fails instantly, 100% errors | Wrong URL, or auth failing | Check `BASE_URL` env var; run a 1-VU smoke test first |
| Server-side metrics panel empty | App Components not configured, **or** the load testing identity lacks metric read permission | Add App Components; assign **Monitoring Reader** to the load test resource identity |
| Engine health CPU > 75% | Too many threads per engine | Reduce threads to ≤250, increase engine instances |
| Test stopped early | AutoStop triggered by high error rate | Expected behaviour — investigate the errors, or adjust AutoStop |
| Results download unavailable | >45 engines or >3 hours duration | Use Backend Listener / storage account export |
| `GetSecret` returns nothing locally | It only resolves inside Azure Load Testing | Use a local fallback property when running in the JMeter GUI |
| Quota error on run start | Engine/concurrency quota | Check quota blade; raise a free support request ahead of time |

---

## Reference links

- [Quickstart: create and run a load test](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/quickstart-create-and-run-load-test)
- [Tutorial: identify performance bottlenecks](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/tutorial-identify-bottlenecks-azure-portal)
- [Parameterize load tests](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/how-to-parameterize-load-tests)
- [Define test fail criteria](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/how-to-define-test-criteria)
- [Diagnose failing load tests](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/how-to-diagnose-failing-load-test)
- [az load CLI reference](https://learn.microsoft.com/en-us/cli/azure/load)

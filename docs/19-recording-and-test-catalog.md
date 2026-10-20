# 19 — Recording Walkthrough & Test Catalog

> **Section goal:** Show the browser-recorder path honestly, then anchor the customer in the checked-in tests this kit actually provisions. Recording is a fast on-ramp; the catalog is the source of truth for repeatable demos.
>
> **Product naming:** Azure Load Testing now sits under the **Azure App Testing** umbrella. The browser extension is the **Azure App Testing** extension for Microsoft Edge or Google Chrome.

**Duration:** 20–25 minutes · **Format:** Browser walkthrough + code reference · **Audience:** QA, engineering, platform, SRE

---

## 19.1 Browser recording walkthrough

Azure Load Testing can create a JMeter load test from a recorded browser journey by using the **Azure App Testing** browser extension. Microsoft Learn describes the flow as: open the Azure Load Testing resource, select **Record**, use the extension to capture the journey, review AI-suggested labels / think times / correlations / parameters, configure load, and either run the test or download the generated JMeter script for further work. The cited Learn flow is JMeter-based; do not present HAR export as the supported path unless you have verified it in the installed extension build during rehearsal.

> **Manual step.** The recording itself must be performed by a human in Microsoft Edge or Google Chrome. Do not promise this as a scriptable CI step; treat it as an authoring workflow.

### 19.1.1 Prerequisites

- A deployed Contoso Bank UI, for example `https://<app>.azurewebsites.net/` or `https://$env:SERVICE_API_HOSTNAME/`.
- An Azure Load Testing resource, for this deployed demo: `alt-loadtest-27d2e5` in `rg-loadtest`.
- Azure permissions to create and run tests in that resource.
- The **Azure App Testing** browser extension:
  - Microsoft Edge: <https://aka.ms/AppTesting/Browser-Ext>
  - Google Chrome: <https://aka.ms/AppTesting/Browser-Ext-Chrome>

Microsoft's documented launch path is **Azure portal → Azure Load Testing resource → Overview → Record**. If the extension is not installed, the portal redirects you to the marketplace page for the extension.

### 19.1.2 Start the recording

1. Open the [Azure portal](https://portal.azure.com).
2. Go to the Azure Load Testing resource.
3. On the **Overview** page, select **Record**.
4. The **Azure App Testing** extension opens in a new browser tab.
5. Sign in if prompted.
6. Select the Azure subscription and the Azure Load Testing resource.
7. Enter the Contoso Bank UI URL: `https://<SERVICE_API_HOSTNAME>/`.
8. Select **Start Recording**.
9. The extension opens a new browser window. Perform the user journey there.

### 19.1.3 Exact Contoso Bank journey to record

Use one clean scenario. Do not click the demo-mode buttons during this recording.

1. **Load accounts**
   - On the Contoso Bank page, select **Load accounts**.
   - Leave the default selected account unless you are deliberately demonstrating parameterisation.
2. **View balance**
   - Select **View balance**.
3. **View transactions with page size 50**
   - In **Rows**, enter `50`.
   - Select **View transactions**.
4. **Make a transfer and check its status**
   - Confirm **Transfer from** and **Transfer to** are different accounts.
   - Leave **Amount** as `25.00` or enter a small positive amount.
   - Leave **Reference** as `Browser recorder demo` or enter a unique reference.
   - Select **Make transfer and check status**. The UI performs the correlated two-step flow: `POST /api/transfers`, then `GET /api/transfers/{transaction_id}`.
5. **Generate a statement**
   - Select **Generate statement**.

Return to the extension tab and select **Stop Recording**.

### 19.1.4 Review, export and create the test

1. Select **Review and create test**.
2. Filter domains so the test only keeps the Contoso Bank host. Remove unrelated browser, portal, identity-provider or extension traffic.
3. Review AI-suggested request labels. Rename vague labels to the names the rest of this kit uses: `LoadAccounts`, `GetBalance`, `GetTransactions`, `CreateTransfer`, `GetTransferStatus`, `GenerateStatement`.
4. Review the suggested **think times**. Keep realistic pauses; remove only accidental long delays caused by presenter narration.
5. Review **correlations** and **parameters** before trusting the script. See §19.1.5.
6. In **Load details**, set the intended virtual users, test duration and ramp-up time.
7. Either select **Run test now** for a quick portal demo, or use the advanced test options to configure the same details available for a JMeter test.
8. Download the generated JMeter script (`.jmx`) if you want to keep, diff and harden the artefact in Git.
9. To create a durable scripted test later, use **Azure Load Testing resource → Tests → + Create → Upload scripts**, upload the `.jmx` and supporting files, then configure parameters, load, criteria and monitoring.

### 19.1.5 Post-processing that actually matters

A raw browser recording replays what one user happened to do once. That is not yet a meaningful load test.

| Post-processing step | What to do for Contoso Bank | Why it matters |
|---|---|---|
| **Correlate dynamic values** | Extract `transaction_id` from the `POST /api/transfers` response with JSONPath `$.transaction_id`, store it as `transferId`, and use it in `GET /api/transfers/${transferId}`. If you choose account IDs from the account-list response, extract those too. | Without correlation, the replay uses one hardcoded transfer id. It can go falsely green, fail for the wrong reason, or stop testing the real business flow. |
| **Parameterise inputs** | Move `BASE_URL`, account IDs, page size, amount and reference values into environment variables, properties or CSV data. Use `loadtests/data/accounts.csv` when the script becomes a real JMeter test. | One account hit by every virtual user mostly tests caching and locking artefacts, not realistic banking traffic. |
| **Remove static-asset noise** | Delete or filter out `/static/app.js`, `/static/styles.css`, favicon and browser-extension requests unless the lesson is page-load behaviour. Keep the API calls for this demo. | Static assets hide the API signals and make the per-request breakdown less useful. |
| **Add think time** | Keep short pauses between account list, balance, transaction history, transfer and statement steps. | Zero think time models a bot, not a customer. It can create an accidental denial-of-service profile. |
| **Add failure criteria** | At minimum add `percentage(error) > 5` and a p95 response-time criterion for the critical requests. | No criteria means `testResult = NOT_APPLICABLE`; a run can look successful even when every request failed. See docs [`06`](06-demo-create-run-inspect.md#64a-the-false-green-moment-the-strongest-3-minutes-in-the-deck) and [`07`](07-analyzing-results-and-bottlenecks.md#72-step-1--read-the-run-status-correctly-then-validate-the-test). |

> **The line to land:** *"Recording captures the journey. Engineering turns it into a test."*

### 19.1.6 When recording makes sense — and when it does not

| Use recording when... | Prefer hand-written JMeter / Locust when... |
|---|---|
| A non-coder needs a first load test in minutes. | The service is API-first and the contract is already clear. |
| The journey is complex, authenticated, or hard to reconstruct from memory. | The test must live in Git, be easy to review, and be parameterised from day one. |
| You need to discover the real browser sequence before rewriting it cleanly. | You need deterministic data, explicit correlations and stable request names. |

For Contoso Bank, recording is a strong **first step** and a poor **final artefact**. The checked-in JMeter and Locust tests are clearer, diff-able and parameterised from the start.

---

## 19.2 Test catalog — what this kit provisions

`tools/provision_loadtests.ps1` is the post-deploy hook. It retargets every load-test artefact to the deployed App Service hostname, creates or updates every YAML file under `loadtests/config`, then attaches App Components and 15 server-side metrics to the three diagnostic tests.

Use these variables for the run commands below:

```powershell
$RG = "rg-loadtest"
$ALT_RESOURCE = "alt-loadtest-27d2e5"
$SUBSCRIPTION_ID = "1da52b57-468a-4dc6-ba36-1255cc1f6e8e"

function Invoke-ContosoLoadTest {
  param(
    [Parameter(Mandatory = $true)][string]$TestId,
    [Parameter(Mandatory = $true)][string]$RunIdPrefix
  )

  $runId = "$RunIdPrefix-$(Get-Date -Format yyyyMMdd-HHmmss)"
  az load test-run create `
    --load-test-resource $ALT_RESOURCE `
    --resource-group $RG `
    --subscription $SUBSCRIPTION_ID `
    --test-id $TestId `
    --test-run-id $runId
}
```

| Test ID / display name | Flavour | Source file(s) | Demonstrates / teaching moment | Failure criteria | Load profile | App Components + server metrics | Command |
|---|---|---|---|---|---|---|---|
| `contoso-bank-url-basic`<br/>Contoso Bank - URL Basic No-Code Demo | URL-based | `loadtests/config/url-basic.yaml`<br/>`loadtests/url-tests/contoso-bank-basic.json` | Opening no-code demo: paste one endpoint, set 50 users, see results quickly. | **No — intentional.** This mirrors doc 06 §6.1's "leave defaults" no-code path and sets up the criteria-less warning in doc 07 §7.2. | 1 engine; 50 VUs; 30s ramp; 180s duration. | No. | `Invoke-ContosoLoadTest -TestId contoso-bank-url-basic -RunIdPrefix url-basic` |
| `contoso-bank-url-smoke`<br/>Contoso Bank - URL Smoke | URL-based | `loadtests/config/url-smoke.yaml`<br/>`loadtests/url-tests/contoso-bank-smoke.json` | No-script multi-request smoke test with a visible pass/fail threshold. Shows the URL-test ramp before scripts. | Yes: average response time and error percentage. | 1 engine; 50 VUs; 30s ramp; 180s duration. | No. | `Invoke-ContosoLoadTest -TestId contoso-bank-url-smoke -RunIdPrefix url-smoke` |
| `contoso-bank-url-curl`<br/>Contoso Bank - URL cURL | cURL-based URL test | `loadtests/config/url-curl.yaml`<br/>`loadtests/url-tests/contoso-bank-curl.json` | Turns existing runbook / Postman / browser "Copy as cURL" commands into a load test without teaching JMeter first. | Yes: average response time and error percentage. | 1 engine; 50 VUs; 30s ramp; 180s duration. | No. | `Invoke-ContosoLoadTest -TestId contoso-bank-url-curl -RunIdPrefix url-curl` |
| `contoso-bank-url-correlated`<br/>Contoso Bank - URL Correlated (response variables) | URL-based | `loadtests/config/url-correlated.yaml`<br/>`loadtests/url-tests/contoso-bank-correlated.json` | Response variables: POST a transfer, extract `transaction_id` with a JSONExtractor, read it back as `${transferId}` from both a URL and a cURL request. Proves a no-code test can do dependent, dynamic journeys — see doc 13 §13.6. | Yes: error percentage and average response time. A broken extraction requests the literal `${transferId}`, 404s, and fails the run — so a green run proves correlation worked. | 1 engine; 20 VUs; 20s ramp; 120s duration. | No. | `Invoke-ContosoLoadTest -TestId contoso-bank-url-correlated -RunIdPrefix url-correlated` |
| `contoso-bank-url-false-green`<br/>Contoso Bank - URL False Green | URL-based | `loadtests/config/url-false-green.yaml`<br/>`loadtests/url-tests/contoso-bank-false-green.json` | The false-green proof: one guaranteed 404 route, AutoStop disabled, no criteria. Doc 06 §6.4a records a real run with **12,254 requests / 12,254 errors / 100.00% error rate** while the run still ended `DONE` / `NOT_APPLICABLE`. | **No — intentional.** Do not add criteria; the whole point is doc 06 §6.4a and doc 07 §7.2's warning that criteria-less runs can look green. | 1 engine; 10 VUs; 10s ramp; 60s duration. | No. | `Invoke-ContosoLoadTest -TestId contoso-bank-url-false-green -RunIdPrefix false-green` |
| `contoso-bank-url-csv`<br/>Contoso Bank - URL CSV Input Data | URL-based | `loadtests/config/url-csv.yaml`<br/>`loadtests/url-tests/contoso-bank-csv.json`<br/>`loadtests/data/url-accounts.csv` | Answers the portal's "Input data files" box: a `csvDataSetConfigList` binds CSV columns to `${accountId}`, `${payeeAccount}`, `${segment}` and `${pageSize}`, so each iteration uses a different account instead of replaying one hardcoded row. Note the headerless CSV — the generated JMX sets `ignoreFirstLine=false`, so a header row would be read as data. **Verified**: run `url-csv-20260930-101228` produced all 20 distinct `accountId` values (44–46 samples each) and 11 distinct `page_size` values, with 0 errors. | Yes: error percentage and average response time. A stray header row or missing CSV produces 404s and fails the run, so a green run proves the binding worked. | 1 engine; 25 VUs; 30s ramp; 180s duration. | No. | `Invoke-ContosoLoadTest -TestId contoso-bank-url-csv -RunIdPrefix url-csv` |
| `contoso-bank-pr-gate`<br/>Contoso Bank - PR Gate | JMeter | `loadtests/config/pr-gate.yaml`<br/>`loadtests/jmeter/contoso-bank.jmx`<br/>`loadtests/jmeter/user.properties`<br/>`loadtests/data/accounts.csv` | Cheap shift-left regression gate. It asks whether the change is slower than baseline, not where absolute capacity ends. | Yes: aggregate average/error percentage plus per-sampler p95 for `GetBalance` and `CreateTransfer`; AutoStop enabled. | 1 engine; 25 VUs; 30s ramp; 180s duration. | **Yes — diagnostic.** The hook attaches 3 App Components and 15 server metrics. | `Invoke-ContosoLoadTest -TestId contoso-bank-pr-gate -RunIdPrefix pr-gate` |
| `contoso-bank-peak-load`<br/>Contoso Bank - Peak Hour Transfers | JMeter | `loadtests/config/peak-load.yaml`<br/>`loadtests/jmeter/contoso-bank.jmx`<br/>`loadtests/jmeter/user.properties`<br/>`loadtests/data/accounts.csv` | Flagship month-end surge: run bottlenecked, optimise live, rerun unchanged, compare. Demonstrates CSV splitting, parameterised page size 50, transfer correlation and request-level criteria. | Yes: aggregate average/error percentage plus p95 for balance, transactions, transfer and statement; AutoStop enabled. | 4 engines; 250 VUs/engine = 1,000 VUs; 120s ramp; 900s duration. | **Yes — diagnostic.** The hook attaches 3 App Components and 15 server metrics. | `Invoke-ContosoLoadTest -TestId contoso-bank-peak-load -RunIdPrefix peak-load` |
| `contoso-bank-peak-load-locust`<br/>Contoso Bank - Peak Hour Transfers (Locust) | Locust | `loadtests/config/peak-load-locust.yaml`<br/>`loadtests/locust/locustfile.py`<br/>`loadtests/locust/locust.conf` | Same business journey and dashboard as the JMeter peak test, written in Python. Useful for developer/Copilot-friendly authoring. | Yes: aggregate average/error percentage plus p95 for `GetBalance` and `CreateTransfer`; AutoStop enabled. | 4 engines; 250 VUs/engine = 1,000 VUs; spawn rate 5/engine; 900s duration. | **Yes — diagnostic.** The hook attaches 3 App Components and 15 server metrics. | `Invoke-ContosoLoadTest -TestId contoso-bank-peak-load-locust -RunIdPrefix peak-locust` |
| `contoso-bank-secrets-kv`<br/>Contoso Bank - Key Vault Secrets | JMeter | `loadtests/config/secrets-demo.yaml`<br/>`loadtests/jmeter/contoso-bank-secrets.jmx`<br/>`loadtests/jmeter/user.properties` | Managed identity and Key Vault secret-resolution demo. Load is deliberately tiny; the lesson is secret handling and RBAC, not throughput. | Yes: average/error percentage plus p95 for the two secret-header samplers; AutoStop enabled. | 1 engine; 5 VUs; 15s ramp; 60s duration. | No. | `Invoke-ContosoLoadTest -TestId contoso-bank-secrets-kv -RunIdPrefix secrets-kv` |
| `contoso-bank-secrets-kv-locust`<br/>Contoso Bank - Key Vault Secrets (Locust) | Locust | `loadtests/config/secrets-demo-locust.yaml`<br/>`loadtests/locust/secrets_locustfile.py`<br/>`loadtests/locust/secrets.conf` | Locust migration of the Key Vault demo: injected `API_KEY`, one resolution check per user, then ordered balance and health requests. Unresolved secrets stop the user before HTTP traffic. | Same five criteria as the JMX, including zero tolerated `SecretResolutionCheck` failures; AutoStop enabled. | 1 engine; 5 VUs total; spawn rate 1/3 user/s; 15s ramp window included in 60s duration. | No. | `Invoke-ContosoLoadTest -TestId contoso-bank-secrets-kv-locust -RunIdPrefix secrets-kv-locust` |

### 19.2.1 Authoring note — URL tests need `body` on every request

Every `requestType: URL` request in the JSON files above carries `"body": null` and `"requestBodyFormat": null`, including the GETs. This is not cosmetic. Omit them and the **portal's Edit → Test plan** tab silently drops that request from the list — the test still runs correctly, but the editor lies about its contents, and pressing **Apply** there would persist the truncated list. `requestType: CURL` requests must **not** have these keys. Full diagnosis and evidence: [doc 13 §13.6.8](13-advanced-scenarios.md).

### 19.2.2 Diagnostic monitoring set

The diagnostic tests are:

- `contoso-bank-peak-load`
- `contoso-bank-peak-load-locust`
- `contoso-bank-pr-gate`

For each, `tools/provision_loadtests.ps1` attaches three App Components:

1. App Service (`SERVICE_API_RESOURCE_ID`)
2. Application Insights (`APPLICATIONINSIGHTS_RESOURCE_ID`)
3. App Service Plan (`APP_SERVICE_PLAN_RESOURCE_ID`)

It then adds 15 focused metrics: App Service Plan CPU, memory and HTTP queue length; App Service response time, request count, 5xx count, working set and instance count; and Application Insights request duration/count/failed/rate plus queue, process CPU and process private bytes.

---

## Reference links

- [Quickstart: Create and run a load test from a recording using AI](https://learn.microsoft.com/azure/app-testing/load-testing/quickstart-create-run-load-tests-from-recording)
- [Create and manage tests in Azure Load Testing](https://learn.microsoft.com/azure/app-testing/load-testing/how-to-create-manage-test)

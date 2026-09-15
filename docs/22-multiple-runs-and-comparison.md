# 22 — Multiple Runs and Comparison

> **Section goal:** Answer the customer feedback directly: one test run is a screenshot; multiple runs are evidence. This section shows the repeatable before/after arc, how to name runs so they line up in the portal and CLI, and how to compare the same figures without opening the portal.

**Duration:** 20 minutes · **Format:** Live terminal + portal overlay · **Audience:** Engineering, SRE, release governance

---

## 22.1 The answer to the feedback

The feedback was:

> "Multiple runs — to be able to compare the results and show the difference, we need to run multiple test runs to be able to show the information."

That is exactly the right ask. A single run can tell you *what happened once*. It cannot prove an optimisation worked, and it cannot prove a regression was introduced. The demo arc should therefore be:

| Step | Run state | Purpose |
|---|---|---|
| 1 | **Baseline / before** | Capture the current behaviour under a known load profile |
| 2 | **Change** | Flip the Contoso Bank app from `bottlenecked` to `optimized`, or deploy a code/configuration change |
| 3 | **Re-run the same test definition** | Keep the load shape unchanged so the comparison is fair |
| 4 | **Compare** | Show latency, error percentage and throughput side by side |
| 5 | **Keep the run ids** | Treat them as the audit trail for the decision |

**Say out loud:** *"The first run tells us we have a problem. The second run tells us whether the change fixed it. The comparison is the evidence."*

Azure Load Testing creates a **test run** every time a test is started, and the run carries a copy of the configuration used for that execution. Existing runs do not automatically inherit later edits to the test definition; new settings apply to new runs. In this kit, use that property deliberately: never overwrite the story by reusing vague ids such as `demo1` and `demo2`.

Treat run ids as immutable audit records in the demo process: one observation, one id. The CLI can rerun an existing run when explicitly asked, but that is for reproducing the original settings, not for before/after proof.

> **Presenter rule:** use a fresh, meaningful run id for every run you intend to discuss.

---

## 22.2 Run id naming convention

Run ids are what the portal, CLI and release notes all point back to. Make them sortable, pairable and short enough for Azure Load Testing's limit.

> **Hard limit verified against the live resource:** `testRunId` must be **2-50 characters**. A 51-character id (`contoso-bank-url-false-green-runall-20260915-102506`) was rejected by `az load test-run create` with `InvalidRequest`. That kind of avoidable failure costs you the run slot in a live demo, so design the naming convention around the budget.

The timestamp `yyyyMMdd-HHmmss` costs 15 characters, or **16** once you include the separator before it. That leaves **34 characters** for everything else. Do not use the full long test id plus a long purpose token; `contoso-bank-peak-load-locust-before-20260915-110500` is already too long. Use short test tokens and short purpose tokens instead.

| Pattern | Example | Use it when |
|---|---|---|
| `<token>-<purpose>-<yyyyMMdd-HHmmss>` | `prgate-before-20260915-110500`<br/>`prgate-after-20260915-110500` | Keeping a before/after pair together |
| `<token>-<preset>-<yyyyMMdd-HHmmss>` | `peak-bneck-20260915-110500`<br/>`peak-opt-20260915-110500` | Showing preset differences |
| `<token>-base-<yyyyMMdd-HHmmss>` | `peakloc-base-20260915-110500` | Marking a release or capacity baseline |
| `<token>-b<build-number>` | `prgate-b1842` | CI/CD traceability when the workflow run id is already the timestamp |

Recommended tokens for this kit:

| Test id | Token | Why |
|---|---|---|
| `contoso-bank-pr-gate` | `prgate` | Cheap before/after proof |
| `contoso-bank-peak-load` | `peak` | Headline JMeter run |
| `contoso-bank-peak-load-locust` | `peakloc` | Headline Locust run without overflowing |
| `contoso-bank-url-smoke` | `urlsmoke` | No-script smoke comparison |
| `contoso-bank-url-false-green` | `urlfg` | Teaching artefact only |

This repo's runner scripts support explicit run ids:

```powershell
pwsh -File tools\run_loadtest.ps1 `
  -TestId contoso-bank-pr-gate `
  -RunId prgate-before-20260915-110500
```

```bash
tools/run_loadtest.sh \
  --test-id contoso-bank-pr-gate \
  --run-id prgate-before-20260915-110500
```

If you omit `-RunId` / `--run-id`, the runner generates `<testId>-yyyyMMdd-HHmmss` and truncates the **test-id portion only** so the generated id always fits the 2-50 character limit. If you supply `-RunId` / `--run-id` yourself, the runner fails fast with an explanatory message when the id is outside 2-50 characters, instead of letting the Azure API reject it after the fact. For a customer-facing comparison, pass a short explicit id so the pair reads cleanly in screenshots and CLI output.

**Say out loud:** *"A run id is not just a label. It is the handle for the evidence pack."*

---

## 22.3 The before/after demo arc

Use `contoso-bank-pr-gate` for the live proof because it is cheap and fast. Use `contoso-bank-peak-load` only when you have budget and time for the headline 1,000-VU story.

### PowerShell

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$before = "prgate-before-$stamp"
$after = "prgate-after-$stamp"

# 1. Run the exact same test definition against the deliberately slow app.
pwsh -File tools\run_loadtest.ps1 `
  -TestId contoso-bank-pr-gate `
  -Preset bottlenecked `
  -RunId $before

# 2. Apply the fix live. The runner can do this with -Preset optimized,
#    but showing the HTTP call makes the demo app's control plane explicit.
$api = (azd env get-values | Select-String '^SERVICE_API_URI=').ToString().Split('=', 2)[1].Trim('"').TrimEnd('/')
Invoke-RestMethod -Method Post `
  -Uri "$api/admin/config" `
  -ContentType 'application/json' `
  -Body (@{ preset = 'optimized' } | ConvertTo-Json)

# 3. Re-run the same test definition. Only the application preset changed.
pwsh -File tools\run_loadtest.ps1 `
  -TestId contoso-bank-pr-gate `
  -RunId $after

# 4. Compare from the terminal.
pwsh -File tools\compare_runs.ps1 -RunIds $before,$after
```

Shortcut for step 2 + 3:

```powershell
pwsh -File tools\run_loadtest.ps1 `
  -TestId contoso-bank-pr-gate `
  -Preset optimized `
  -RunId $after
```

### Bash

```bash
stamp=$(date +%Y%m%d-%H%M%S)
before="prgate-before-$stamp"
after="prgate-after-$stamp"

# 1. Before: deliberately bottlenecked.
tools/run_loadtest.sh \
  --test-id contoso-bank-pr-gate \
  --preset bottlenecked \
  --run-id "$before"

# 2. Apply the fix live.
API_BASE_URL=$(azd env get-values | awk -F= '$1=="SERVICE_API_URI"{gsub(/^"|"$/, "", $2); print $2}')
curl -fsS -X POST "${API_BASE_URL%/}/admin/config" \
  -H "Content-Type: application/json" \
  -d '{"preset":"optimized"}'

# 3. After: same test definition, optimised app.
tools/run_loadtest.sh \
  --test-id contoso-bank-pr-gate \
  --run-id "$after"

# 4. If you are on PowerShell-capable runner, use the comparison helper:
pwsh -File tools/compare_runs.ps1 -RunIds "$before","$after"
```

**Say out loud:** *"The load file did not change. The engines did not change. The only thing that changed was the application behaviour. That is why the comparison is meaningful."*

---

## 22.4 Portal comparison

For the visual reveal:

1. Azure Portal → Azure Load Testing resource → **Tests**.
2. Select the test, for example `contoso-bank-pr-gate`.
3. Select the two run ids from §22.3.
4. Select **Compare**.
5. Show the overlays for:
   - response time p95 and p99,
   - error percentage,
   - throughput,
   - server-side metrics if App Components are attached.

Verified on Microsoft Learn: Azure Load Testing lets you compare **up to 10** test runs in the dashboard. Those runs can be from the same test or another test. A run needs to be in the *Passed*, *Failed*, *Completed* or *Stopped* state to compare it. The Trends tab for a test shows the 10 most recent runs for total requests, response time, error percentage and throughput.

> **Presenter caution:** the portal overlay is persuasive, but it is still only fair if the runs used the same load shape. Do not compare `contoso-bank-pr-gate` with `contoso-bank-peak-load` and call it an optimisation.

---

## 22.5 Portal-free comparison with the CLI

The portal-free path is the same evidence without the browser.

| Need | CLI command |
|---|---|
| List recent runs for a test | `az load test-run list --test-id ...` |
| Read one run's outcome and statistics | `az load test-run show --test-run-id ...` |
| Read time-series metrics | `az load test-run metrics list --metric-namespace LoadTestRunMetrics ...` |
| Download CSV/JTL, logs and report | `az load test-run download-files --result --log --report ...` |

```powershell
$RG = $env:AZURE_RESOURCE_GROUP
$LT = $env:LOAD_TEST_NAME
$TEST_ID = "contoso-bank-pr-gate"

az load test-run list `
  --load-test-resource $LT `
  --resource-group $RG `
  --test-id $TEST_ID `
  --query "[].{runId:testRunId,status:status,result:testResult,started:startDateTime}" `
  -o table

az load test-run show `
  --load-test-resource $LT `
  --resource-group $RG `
  --test-run-id $before `
  --query "{status:status,testResult:testResult,total:testRunStatistics.Total,criteria:passFailCriteria.passFailMetrics}" `
  -o json

az load test-run metrics list `
  --load-test-resource $LT `
  --resource-group $RG `
  --test-run-id $before `
  --metric-namespace LoadTestRunMetrics `
  --metric-name ResponseTime `
  -o json

az load test-run download-files `
  --load-test-resource $LT `
  --resource-group $RG `
  --test-run-id $before `
  --path ".loadtest-results\$before" `
  --result --log --report --force
```

### 22.5.1 Working PowerShell delta table

This snippet extracts `p95`, `p99`, `errorPct` and `throughput` from `testRunStatistics.Total` for two existing runs and prints deltas against the first run. It takes the **pessimistic reading of both verdict signals**, because neither one alone is trustworthy immediately after a run finishes — see [doc 07 §7.2.4](07-analyzing-results-and-bottlenecks.md).

```powershell
$RG = $env:AZURE_RESOURCE_GROUP
$LT = $env:LOAD_TEST_NAME
$RunIds = @("prgate-before-20260915-110500", "prgate-after-20260915-110500")

function Get-Verdict($run) {
  # Measured: for ~97s after status reaches DONE the criteria carry no result at
  # all and testResult reads NOT_APPLICABLE. Reading either one early is a false
  # green, so anything short of positive proof is reported as UNSETTLED.
  $criteria = $run.passFailCriteria.passFailMetrics
  $items = @()
  if ($criteria) { $items = @($criteria.PSObject.Properties | ForEach-Object { $_.Value }) }
  if ($items.Count -eq 0) { return "NOT_APPLICABLE" }
  if ($run.status -ne "DONE") { return "NO_VERDICT" }
  if (@($items | Where-Object { $_.result -eq "failed" }).Count -gt 0 -or $run.testResult -eq "FAILED") { return "FAILED" }
  if ($run.testResult -eq "PASSED") { return "PASSED" }
  return "UNSETTLED"
}

$rows = foreach ($id in $RunIds) {
  $run = az load test-run show `
    --load-test-resource $LT `
    --resource-group $RG `
    --test-run-id $id `
    -o json | ConvertFrom-Json

  $total = $run.testRunStatistics.Total
  [pscustomobject]@{
    runId      = $id
    verdict    = Get-Verdict $run
    p95        = [double]$total.pct2ResTime
    p99        = [double]$total.pct3ResTime
    errorPct   = [double]$total.errorPct
    throughput = [double]$total.throughput
  }
}

$base = $rows[0]
$comparison = foreach ($metric in "p95", "p99", "errorPct", "throughput") {
  $after = $rows[1].$metric
  $delta = $after - $base.$metric
  $pct = if ($base.$metric -eq 0) { "n/a" } else { "{0:n1}%" -f (($delta / $base.$metric) * 100) }
  [pscustomobject]@{
    metric   = $metric
    before   = $base.$metric
    after    = $after
    delta    = "{0:n2}" -f $delta
    change   = $pct
  }
}

$comparison | Format-Table -AutoSize
```

### 22.5.2 Working Bash + jq delta table

```bash
RG="$AZURE_RESOURCE_GROUP"
LT="$LOAD_TEST_NAME"
before="prgate-before-20260915-110500"
after="prgate-after-20260915-110500"

before_json=$(az load test-run show \
  --load-test-resource "$LT" \
  --resource-group "$RG" \
  --test-run-id "$before" -o json)

after_json=$(az load test-run show \
  --load-test-resource "$LT" \
  --resource-group "$RG" \
  --test-run-id "$after" -o json)

jq -nr --argjson before "$before_json" --argjson after "$after_json" '
  def verdict($r):
    ($r.passFailCriteria.passFailMetrics // {}) as $m
    | ($m | to_entries) as $c
    | if ($c | length) == 0 then "NOT_APPLICABLE"
      elif $r.status != "DONE" then "NO_VERDICT"
      elif (any($c[]; .value.result == "failed") or $r.testResult == "FAILED") then "FAILED"
      elif $r.testResult == "PASSED" then "PASSED"
      else "UNSETTLED" end;
  def pct($b; $a): if $b == 0 then "n/a" else (((($a - $b) / $b) * 100) | tostring) + "%" end;
  ["run", "verdict", "p95", "p99", "errorPct", "throughput"],
  ["before", verdict($before), $before.testRunStatistics.Total.pct2ResTime, $before.testRunStatistics.Total.pct3ResTime, $before.testRunStatistics.Total.errorPct, $before.testRunStatistics.Total.throughput],
  ["after", verdict($after), $after.testRunStatistics.Total.pct2ResTime, $after.testRunStatistics.Total.pct3ResTime, $after.testRunStatistics.Total.errorPct, $after.testRunStatistics.Total.throughput],
  [],
  ["metric", "before", "after", "delta", "change"],
  ("pct2ResTime,pct3ResTime,errorPct,throughput" | split(",")[] as $m |
    ($before.testRunStatistics.Total[$m] | tonumber) as $b |
    ($after.testRunStatistics.Total[$m] | tonumber) as $a |
    [$m, $b, $a, ($a - $b), pct($b; $a)])
  | @tsv'
```

For day-to-day use, prefer the checked-in helper:

```powershell
pwsh -File tools\compare_runs.ps1 -RunIds $before,$after
pwsh -File tools\compare_runs.ps1 -TestId contoso-bank-pr-gate -Last 2
```

It resolves `LOAD_TEST_NAME`, `AZURE_RESOURCE_GROUP` and `AZURE_SUBSCRIPTION_ID` the same way as `tools\run_loadtest.ps1`: parameters first, then environment variables, then `azd env get-values`.

---

## 22.6 Trend and regression over time

The same comparison pattern becomes more valuable when it is automatic:

| Layer | In this repo | What it proves |
|---|---|---|
| Local rehearsal | `tools\run_loadtest.ps1 -TestId contoso-bank-pr-gate` | The presenter's environment is ready |
| CI gate | `.github\workflows\loadtest.yml` | The same definition can run from GitHub Actions with OIDC and no stored Azure password |
| Run history | Azure Load Testing resource → test → runs | The service keeps the historical runs under the test |
| Evidence pack | `loadTest` folder in GitHub Actions, or `az load test-run download-files` | CSV/JTL and report artefacts for audit/release boards |

The checked-in workflow runs on pushes to `main` that touch `src/**`, `loadtests/**` or the workflow itself, and can also be triggered manually with a chosen `test_id`. If the customer wants *every pull request* or *every branch push*, widen the `on:` block; keep the test id stable so trends remain meaningful.

Retention verified on Microsoft Learn:

| Data | Retention | Practical meaning |
|---|---:|---|
| Server-side metrics | 90 days | App Component charts are short-lived compared with run history |
| Client-side metrics | 365 days | The dashboard/trends view can show the performance history for a year |
| Test results | 6 months | Download CSV/JTL artefacts before they age out |
| Test log files | 6 months | Download worker logs when they matter for an incident or audit |

**Say out loud:** *"The pipeline does not replace the demo. It industrialises the demo. Same test, every change, with a run id the release board can inspect."*

---

## 22.7 Pitfalls that invalidate a comparison

Use this table as the presenter checklist before showing the overlay.

| Pitfall | Why it invalidates the comparison | What to do in this repo |
|---|---|---|
| Different engine counts | More engines changes load distribution, CSV splitting and load-generator capacity | Compare `contoso-bank-pr-gate` with `contoso-bank-pr-gate`, not with `contoso-bank-peak-load` |
| Different duration or ramp-up | Percentiles and throughput settle differently on a 3-minute gate and a 15-minute peak run | Keep the same YAML and only change the app preset |
| Cold start / worker warm-up | App Service may be slower while the worker, dependencies and caches warm up | Run a warm-up smoke or discard the first rehearsal run before capturing the baseline |
| Autoscale changed capacity mid-comparison | The result may be "more workers", not "better code" | `infra\main.tf` ships an autoscale rule, but it is **disabled**. If you enable it for the autoscale chapter, label that comparison as a capacity demo |
| Noisy neighbours / regional variance | Shared platform noise can move p95/p99 between runs | Run pairs close together and repeat if the numbers are marginal |
| Different fail criteria | A verdict change might be a rule change, not an application change | Use `passFailCriteria.passFailMetrics` from each run and call out criteria changes explicitly |
| Comparing `status` instead of verdict | `DONE` means the test infrastructure completed; it does not mean the app met the SLO | Use the doc 07 §7.2.4 rule: wait for the verdict to settle, then take the pessimistic reading of `testResult` and `passFailMetrics[*].result` |
| **Reading the verdict too early** | Measured at **97 seconds**: at first `DONE` the criteria carry no `result` at all and `testResult` is `NOT_APPLICABLE`. Four runs in the 2026-09-15 batch were reported as PASSED when they had actually breached their criteria | Never compare or gate on an unsettled run. The runner, CI gate and `compare_runs.ps1` now wait and report `UNSETTLED` rather than a pass |
| Criteria-less runs | A 100% error run can still look "Completed" if no fail criteria exist | Use `url-false-green` only as the teaching artefact; never as release evidence |
| Different data distribution | Reusing the same accounts can warm caches unrealistically | Keep `splitAllCSVs: true` on JMeter tests with multiple engines |

**Say out loud:** *"Comparison is a scientific method moment. If two things changed, the chart is interesting but not proof."*

---

## 22.8 Reference links

- [Compare load test runs in Azure Load Testing](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/how-to-compare-multiple-test-runs) — verified: compare up to 10 runs, compare eligible states, Trends tab uses the 10 most recent runs.
- [Azure CLI `az load test-run`](https://learn.microsoft.com/en-us/cli/azure/load/test-run?view=azure-cli-latest) — verified: `create`, `list`, `show`, `download-files`; `download-files` supports `--input`, `--log`, `--result`, `--report`.
- [Azure CLI `az load test-run metrics`](https://learn.microsoft.com/en-us/cli/azure/load/test-run/metrics?view=azure-cli-latest) — verified: `metrics list`, `LoadTestRunMetrics`, `EngineHealthMetrics`.
- [Export load test results](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/how-to-export-test-results) — verified: CSV result files per engine, HTML report download, GitHub Actions `loadTest` artefact folder.
- [Service limits for Azure Load Testing](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/resource-limits-quotas-capacity) — verified: data retention for server-side metrics, client-side metrics, test results and logs.
- [Azure Load Testing concepts](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/concept-load-testing-concepts) — verified: a test run represents one execution, carries a copy of the configuration, and existing runs do not inherit later test-definition changes.

No product claims in this document are intentionally left unverified. Re-check the Learn links before a customer session if the session is months after this document was written.

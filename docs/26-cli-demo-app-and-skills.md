# 26 - Azure CLI demo app and reusable agent skills

The local **CLI lab** is a presenter UI for real Azure CLI commands against an
**existing** Azure Load Testing resource. It previews, copies, downloads and
executes a fixed set of commands; it is not a simulated terminal or a replacement
for the CLI. No new infrastructure is required.

It is deliberately separate from the deployed Contoso Bank API. Never add a
cloud-credential command runner to the public banking app.

## Start the app

Requirements: Python 3.11+, Azure CLI 2.66+, the `load` extension, and an existing
Azure Load Testing resource. The web app and both skill helpers use only the
Python standard library; there is no npm build or pip install.

For a fresh checkout, run this from the directory where you keep repositories:

```powershell
git clone https://github.com/san360/contoso-bank-loadtest.git
Set-Location contoso-bank-loadtest
```

If you already have the repository, use its root instead. Sign into the intended
directory, explicitly select the subscription, install the extension, and start:

```powershell
az login --tenant "<tenant-guid-or-domain>"
az account set --subscription "<subscription-guid>"
az extension add --name load
python -m tools.cli_demo
```

Use `fdpo.onmicrosoft.com` as the tenant argument for the fdpo demo directory.
`az account set` changes the active CLI profile; see the isolated-profile option
below if other terminals use that profile.

Open **http://127.0.0.1:8765** in a browser. If that port is occupied:

```powershell
python -m tools.cli_demo --port 8766
```

On macOS/Linux, use `cd contoso-bank-loadtest` instead of `Set-Location` and
`python3` if needed. The app itself needs no additional Python packages or Node.js.

Enter the subscription ID, resource group and existing Load Testing resource.
Alternatively, preview and execute **Check signed-in account**, **Find
subscriptions** and **Find Load Testing resources**, then choose a discovered
resource. Discovery is read-only and does not change the CLI's default subscription.
Every resource command includes an explicit `--subscription`; load commands also
use the fully scoped Load Testing ARM ID.

For the existing demo deployment, enter these form values:

| Field | Value |
| --- | --- |
| Tenant / directory | `fdpo.onmicrosoft.com` |
| Subscription ID | The subscription GUID you selected with `az account set` |
| Resource group | `rg-loadtest` |
| Load Testing resource | `alt-loadtest-27d2e5` |
| Authorized target URL | `https://app-loadtest-27d2e5.azurewebsites.net/health` |

Replace the example deployment values when using another environment. Keep the
default `cli-demo-smoke` test ID for this small scenario, and use **New ID** before
each new execution. Follow the [presenter flow](#suggested-presenter-flow) to
create, optionally attach monitoring, run, inspect and download the evidence.

Optional initial settings:

```powershell
$env:AZURE_SUBSCRIPTION_ID = "<subscription-guid>"
$env:AZURE_TENANT_ID = "<tenant-guid>"
$env:AZURE_RESOURCE_GROUP_NAME = "<resource-group>"
$env:AZURE_LOAD_TEST_RESOURCE_NAME = "<load-testing-resource>"
$env:SERVICE_API_HOSTNAME = "<your-app>.azurewebsites.net"
python -m tools.cli_demo
```

These values are suggestions for the configuration form, not credentials. Form
settings are remembered in browser local storage. The CLI uses its existing Entra
login; the app does not ask for or persist access tokens, passwords or client
secrets. Interactive login and extension installation happen in a terminal, not
inside the app.

### Stopping and restarting

Press **Ctrl+C** in the terminal running the server to stop the local app. This
does not stop a cloud load test. Use **Stop selected run** before shutdown if you
intend to cancel that execution.

To restart, open a terminal at the checkout root and run:

```powershell
python -m tools.cli_demo
```

Repeat `--port <port>` or `--data-dir <directory>` if you used those options.
Configuration is remembered for the same browser origin/port. Downloaded
artifacts remain in `.loadtest-demo\artifacts\` by default and are available through
**Show saved downloads** after restart; restarting never deletes them.

If using a dedicated CLI profile, set `AZURE_CONFIG_DIR` again in a new terminal
before starting the app. Reapply optional form-default environment variables if
needed, or use the browser's saved settings. An expired CLI login must be renewed
in the terminal; the web app does not perform interactive login.

### Different tenants and CLI subscription context

The form includes a tenant GUID or directory such as `fdpo.onmicrosoft.com` for
login guidance. Selecting a subscription does not authenticate a new directory:
first sign into it using `az login --tenant <tenant-or-directory>`.

**Load extension 2.1.0 has an important cross-subscription pitfall:** its data-plane
client resolves credentials from the active/default CLI subscription, even when
the command contains `--subscription`. The app checks the active context before
every load command and refuses a mismatch. A fully scoped resource ID also makes
the extension fail closed if another terminal changes context between that check
and execution.

**Check signed-in account** shows the actual active subscription/tenant.
**Select CLI subscription** explicitly runs `az account set --subscription ...`
and requires confirmation because it changes the profile used by other
terminals. There are no hidden account switches.

For a demo isolated from other terminals, start a separately authenticated CLI
profile instead:

```powershell
$env:AZURE_CONFIG_DIR = Join-Path $HOME ".azure-loadtest-demo"
az login --tenant fdpo.onmicrosoft.com
az account set --subscription "<subscription-guid>"
az extension add --name load
python -m tools.cli_demo
```

The app honors `AZURE_CONFIG_DIR`. Azure CLI manages that profile's credential
cache; the app does not copy credentials between profiles. Use that same profile
when running downloaded step scripts. Do not globally switch a shared profile
without authorization.

### Required access

| Operation | Access |
| --- | --- |
| Discover/resolve Azure resources | Management-plane read access, commonly **Reader**, scoped to the relevant resource group/subscription |
| Inspect existing tests/runs | **Load Test Reader**, or a stronger Load Testing data-plane role |
| Create/update definitions, attach monitoring configuration, start/stop runs | **Load Test Contributor** on the Load Testing resource |
| Query server metric values | Azure Monitor metrics read access, commonly **Monitoring Reader**, on the application resources |

Management-plane Contributor/Owner is not a substitute for checking Load Testing
data-plane access. If server-metric failure criteria are enabled, the Load Testing
resource's configured managed identity also needs the appropriate metrics read
permissions on those application resources. The UI does not create identities or
assign roles. It changes CLI subscription context only through the separately
confirmed **Select CLI subscription** step.

## Suggested presenter flow

### 1. Create a test definition

Use a distinct test ID such as `cli-demo-smoke`. Set an authorized target URL, for
example the deployed bank's `/health` endpoint. Preview **Create the small demo
test** to show the actual **Azure Load Testing YAML** above the **CLI shell**
preview. Explain the YAML first, then the `az load test create
--load-test-config-file ...` command that reads it. **Copy YAML** copies the
generated file; the CLI preview, **Copy** and **Download step script** use the same
creation command in PowerShell or Bash. Confirm the target, then execute.

Each create preview writes a separate `smoke.yaml` with `testId` taken from the
form's **Test ID**, plus a copy of `smoke.jmx` beside it. The files live in the
local artifact directory (by default,
`.loadtest-demo/artifacts/<preview-id>/`). This prepares local inputs only;
previewing does not execute Azure CLI or create anything in Azure. The generated
YAML shown and copied in the UI is the exact file used by the command and
downloaded step scripts. Separate previews do not overwrite each other's inputs.
After changing the Test ID, preview **Create** again before executing it.

The opt-in files are outside `loadtests/config/` so existing `azd up` provisioning
does not change. These repository templates are never rewritten by the UI:

- [`loadtests/cli-demo/smoke.yaml`](../loadtests/cli-demo/smoke.yaml)
- [`loadtests/cli-demo/smoke.jmx`](../loadtests/cli-demo/smoke.jmx)

The test uses **one engine, five users, ten-second ramp-up, 60-second scheduled
duration, one-second per-user pacing**, and one GET request. A 200 response is
asserted; error percentage and average response time have fail criteria.
`example.invalid` in the YAML is an intentionally unusable placeholder; the UI
supplies the actual host, scheme, port and path with `--env`. The CLI's
`--test-id` matches the selected test ID written into the generated YAML.
The target arguments override the YAML's placeholder values. Users, ramp-up, duration and pacing
are defined in the referenced JMX, not by `engineInstances` alone.

The YAML uses the [official Azure Load Testing configuration format](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/reference-test-config-yaml),
not a local schema. The same creation step can be run directly from the
repository root in PowerShell (replace the placeholders and select the matching
CLI subscription first):

```powershell
az load test create `
  --subscription "<subscription-guid>" `
  --resource-group "<resource-group>" `
  --load-test-resource "/subscriptions/<subscription-guid>/resourceGroups/<resource-group>/providers/Microsoft.LoadTestService/loadTests/<load-testing-resource>" `
  --test-id "cli-demo-smoke" `
  --load-test-config-file ".\loadtests\cli-demo\smoke.yaml" `
  --env "DEMO_HOST=<authorized-hostname>" DEMO_PROTOCOL=https DEMO_PORT=443 DEMO_PATH=/health
```

Creating a definition does not start traffic. If the selected ID already exists,
`create` can report a conflict; choose a fresh ID and preview again to create a
separate definition. This step does not automatically switch to `update`.
The workload is intentionally fixed-size; the UI is not a capacity-test designer.

### 2. Add application monitoring before the run

On **Server metrics**, paste the ARM ID of an existing application resource.
**Inspect application resource** resolves its actual name/type/kind. Preview and
execute **Attach component to test**.

Next, **Discover server metrics**, choose a metric definition and its supported
aggregation, then **Attach server metric to test**. Metric IDs are taken from
discovery, not fabricated.

For App Service, `CpuTime` is **CPU seconds**, commonly aggregated with `Total`;
it is not CPU utilization percentage. A utilization metric may live on the App
Service **plan**, a different app-component resource. Always inspect metric
definitions instead of assuming the unit, name or aggregation.

This step changes only the selected test's monitoring configuration. It does not
change the application, provision Azure resources or grant permissions.

### 3. Start and inspect an execution

Inspect the definition before running it. Generate a fresh run ID, preview
**Start a load test run**, explain `--no-wait`, confirm the charge/traffic warning,
then execute.

**The selected definition controls the actual workload and target.** Editing the
URL form does not change an already-created definition until **Create** is
executed again. Selecting an existing, larger test can run that larger workload.

**Watch selected run** repeatedly executes `az load test-run show`. It reads
execution status, aggregate result and individual fail criteria. Criteria can
settle after execution reaches `DONE`; the UI shows **SETTLING**, **NO VERDICT**,
**FAILED** or **PASSED** rather than treating every completed run as green.
In particular, `DONE` plus `NOT_APPLICABLE` with configured, unevaluated criteria
continues polling as **SETTLING**; the same aggregate field without any configured
criteria is **NO VERDICT**.

**Stop watching** only stops local polling. **Stop selected run** executes the
separate Azure stop command and requires confirmation. Closing the browser/server
does **not** cancel a cloud run. Local watch is bounded to 45 minutes.

Even this small run incurs real Azure charges, including any minimum billing
rules. It is not free just because it runs for one minute.

### 4. Show client, engine and server values

On **Client & engine**, use **Load runs** beside the left-hand run selector to
preview/list executions for the selected test, then choose a run. Preview and
execute **Get all client metrics** to read the run metadata, discover definitions,
and fetch the complete `LoadTestRunMetrics` namespace without a metric-name filter.
The app checks that the run belongs to the configured test ID.

The tab displays a separate named chart/card for every available client metric.
For the included JMX demo these are **Virtual Users**, **Response Time**, **Total
Requests**, and **Errors**. Metrics with empty series remain visible as **No
datapoints returned**, not invented zeros. Each populated card shows the latest
bucket value and its unit, with expandable datapoints/dimensions. Values use
CLI/service-default aggregations and one-minute buckets; a latest bucket value is
not a whole-run total.

The overview has direct **JSON/CSV download** links. JSON preserves named empty
series; CSV contains all returned datapoints with their metric names and units.
Changing the selected test/run clears the previous overview, and an in-flight
response cannot overwrite results for a newly selected scope.

**Get one client metric** still fetches a named metric such as `ResponseTime`,
while **Get engine health** remains separate. Both keep the existing single-series
chart/table and JSON/CSV exports.

On **Server metrics**, use the start/end timestamps populated by **Inspect run**
to fetch application values over the same window. Discovery shows valid metric
names and aggregations for that resource.

| Question | Command family | Meaning |
| --- | --- | --- |
| What latency/errors/traffic did simulated users see? | `az load test-run metrics list --metric-namespace LoadTestRunMetrics` | Client-side run metrics |
| Was the load generator itself saturated? | `az load test-run metrics list --metric-namespace EngineHealthMetrics` | Generator CPU/memory; **not server CPU/memory** |
| What resources are monitored for this run? | `az load test-run app-component list` | App-component configuration |
| Which server metrics are configured? | `az load test-run server-metric list` | Metric configuration; **not measured values** |
| What was the application resource doing? | `az monitor metrics list --resource <app-component-arm-id>` | Server-side metric values from Azure Monitor |

The `az load test-run metrics` CLI accepts client/engine namespaces, not an
invented `ServerMetrics` namespace. Client load-test measurements do not become
Azure Monitor metrics on the Load Testing resource.

### 5. Download evidence

Use **Download engine logs**, **Download request results** or **Download JMeter
HTML report**. Downloads are the service's original archives. Reports may not
exist for every engine type or run state; missing artifacts and CLI errors remain
visible. The app does not execute or inline artifact HTML.

**Collect analysis bundle** obtains:

- Run metadata with status, criteria and statistics.
- Engine logs and raw result archives.
- The configured named client metric and generator-health metric, as JSON/CSV.
- Server metric values **only if** a server resource/metric and valid run time
  window are configured; the preview explicitly says whether these are included.
- A manifest with command provenance, errors and warnings.

Collection refuses a non-terminal run. A failed step leaves a clearly marked
**partial** bundle rather than concealing missing evidence.
Azure can refine run timestamps during final result processing. If a collection
would query server metrics using a stale window, it stops and asks for a fresh
inspection/preview rather than silently changing the previewed command.

Every execution has a separate persistent folder:

```text
.loadtest-demo/artifacts/<job-id>/
  manifest.json
  run.json
  client-metrics.json
  client-metrics.csv
  engine-metrics.json
  engine-metrics.csv
  server-metrics.json             # when requested/configured
  server-metrics.csv
  files/                         # original log/result archives
```

Use the individual download links or **Download this evidence bundle (.zip)**.
**Show saved downloads** also finds previous local output after an app restart
(the latest 40 jobs). CLI-error output and downloaded JSON are redacted for common
credentials/SAS patterns; **original log/result archives are not sanitized**.
The whole `.loadtest-demo/` directory is gitignored. Treat it as sensitive local
evidence and delete only the specific folders you no longer need.

The shell selector switches between PowerShell and Bash previews. **Download step
script** saves the exact frozen preview, not a hidden SDK operation or a full
unattended orchestration. Review the target/cost and execute lifecycle steps in
order, waiting for a terminal run before downloading. For an existing definition,
the original [`tools/run_loadtest.ps1`](../tools/run_loadtest.ps1) and `.sh` runner
remain available for an end-to-end terminal workflow.

## What does `az load test-run app-component list` mean?

An **app component** is an Azure resource involved in the system under test whose
server-side metrics you want to correlate with load-test results: for example an
App Service, its hosting plan, a SQL database or Application Insights resource.

It is **not** a JMeter sampler, a tested URL, a virtual user, a load-test engine or
a list of installed software libraries. Listing it shows resource configuration
such as ARM ID, name and resource type. It does not return latency, CPU samples,
application exceptions or request logs.

```powershell
$subscription = "<subscription-guid>"
$rg = "<resource-group>"
$loadResource = "<existing-load-testing-resource>"
$loadResourceId = "/subscriptions/$subscription/resourceGroups/$rg/providers/Microsoft.LoadTestService/loadTests/$loadResource"
$testId = "cli-demo-smoke"
$runId = "<completed-run-id>"
$scope = @("--subscription", $subscription, "--resource-group", $rg,
           "--load-test-resource", $loadResourceId)

# Run-specific resource configuration, not metric values:
az load test-run app-component list @scope --test-run-id $runId
az load test-run server-metric list @scope --test-run-id $runId

# Definitions: changes here apply to future runs:
az load test app-component list @scope --test-id $testId
```

A run can have no configured components and still execute successfully. Depending
on the extension/service version, listing an unconfigured run may return an empty
result or `AppComponentNotFound` / `ServerMetricsConfigNotFound`. That is missing
monitoring configuration, not proof the application was healthy or broken.

To make a repeatable demonstration, configure the **definition before the next
run**, not just a historical run's metadata:

```powershell
$appId = "<existing-App-Service-ARM-ID>"
$app = az resource show --ids $appId --subscription $subscription `
  --query "{id:id,name:name,type:type,kind:kind}" --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw "Could not resolve the application resource." }

az load test app-component add @scope --test-id $testId `
  --app-component-id $app.id --app-component-name $app.name `
  --app-component-type $app.type --app-component-kind $app.kind
if ($LASTEXITCODE -ne 0) { throw "Could not attach the application component." }

$metric = az monitor metrics list-definitions --resource $appId `
  --subscription $subscription --query "[?name.value=='CpuTime'] | [0]" `
  --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $null -eq $metric) { throw "Discover a supported metric first." }

az load test server-metric add @scope --test-id $testId `
  --app-component-id $app.id --app-component-type $app.type `
  --metric-id $metric.id --metric-name $metric.name.value `
  --metric-namespace $metric.namespace --aggregation $metric.primaryAggregationType
if ($LASTEXITCODE -ne 0) { throw "Could not configure the server metric." }
```

Then, after executing the next run:

```powershell
$run = az load test-run show @scope --test-run-id $runId --output json |
  ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $run.endDateTime) { throw "Inspect a finished run first." }

# All available client metrics, keyed by metric name:
az load test-run metrics list @scope --test-run-id $runId `
  --metric-namespace LoadTestRunMetrics --interval PT1M

# Or fetch only a named client metric:
az load test-run metrics list @scope --test-run-id $runId `
  --metric-namespace LoadTestRunMetrics --metric-name ResponseTime --interval PT1M

az monitor metrics list --subscription $subscription --resource $appId `
  --metrics $metric.name.value --aggregation $metric.primaryAggregationType `
  --namespace $metric.namespace --interval PT1M `
  --start-time $run.startDateTime --end-time $run.endDateTime
```

Direct Azure Monitor queries use the caller's access. They can return metrics even
if that resource was not attached to the Load Testing definition. Metric
retention, supported aggregations and ingestion delay still apply.

## Skill: JMX to Locust

[`jmx-to-locust`](../.github/skills/jmx-to-locust/SKILL.md) instructs an agent to
inventory the actual JMX, preserve execution-tree scope, request names,
correlation, assertions, CSV behavior, sessions, pacing and distributed load
shape, then produce and verify a distinct migration.

Its bundled inspector is directly runnable without Locust:

```powershell
python .github\skills\jmx-to-locust\scripts\inspect_jmx.py `
  loadtests\jmeter\contoso-bank.jmx `
  --output .loadtest-demo\migration\inventory.json
```

Example agent request:

> Use the jmx-to-locust skill to convert loadtests/jmeter/contoso-bank.jmx into
> a new Locust test. Preserve correlation, assertions, CSV data and load shape.
> Document and verify parity; do not overwrite the existing Locust demo.

The existing Locust example intentionally differs from the JMX in data selection,
session lifetime, task dispatch and parts of the transfer flow. It must not be
presented as an exact automatic conversion. Creating this skill does not silently
replace either existing test.

## Skill: analyze downloaded artifacts

[`analyze-load-test-artifacts`](../.github/skills/analyze-load-test-artifacts/SKILL.md)
guides an agent through scoped downloads, deterministic local triage and
evidence-based synthesis. Findings distinguish engine failures, connectivity,
application errors, assertion/correlation failures, performance breaches and
unproven bottleneck hypotheses.

The helper accepts an evidence folder or downloaded ZIP:

```powershell
python .github\skills\analyze-load-test-artifacts\scripts\analyze_artifacts.py `
  .loadtest-demo\artifacts\<job-id> --format markdown `
  --output .loadtest-demo\analysis\summary.md
```

Use `--format json` for machine-readable evidence. It reads JMeter CSV/XML,
Locust final statistics/failures, logs, metric exports and run metadata; preserves
archive-member provenance; bounds decompression; excludes duplicate content and
overlapping Locust aggregate/history rows; and flags missing/malformed data.
Exit code 2 means the report is partial or the input could not be analyzed.

Example agent request:

> Use analyze-load-test-artifacts on the downloaded evidence folder. Summarize
> issues with file/line evidence, quantified impact and confidence. Separate
> observations from root-cause hypotheses and flag missing server/application
> telemetry. Do not send raw artifacts to an external service.

The UI prepares the evidence and a copyable prompt. It does not embed an LLM or
automatically upload artifacts to an agent service.

## Local safety and maintenance

- The server binds only to `127.0.0.1`. **Do not tunnel, reverse-proxy or deploy
  it publicly**; it executes commands with the local user's Azure permissions.
- Host/origin/session/CSRF checks protect the local control surface. Commands are
  argument arrays with `shell=False`; the Windows MSI launcher is invoked through
  Azure CLI's bundled Python rather than `cmd.exe`.
- Previews are immutable and single-use. Writes and run/stop operations require
  confirmation, as does explicit CLI-context selection. Errors, missing
  datapoints and partial artifacts are visible.
  Non-idempotent operations are never automatically retried.
- Downloads are attachment-only and confined to generated job folders. Metric
  CSV exports neutralize formula prefixes. Archive contents are never executed.
- Never treat an engine log as server telemetry, an execution status as a
  performance verdict, or a time-series mean as a request percentile.

Contributor checks, without Azure traffic:

```powershell
python -m unittest discover -s tools\cli_demo -p "test_*.py"
node --check tools\cli_demo\static\app.js
```

## References

- https://learn.microsoft.com/cli/azure/load/test
- https://learn.microsoft.com/cli/azure/load/test-run
- https://learn.microsoft.com/cli/azure/load/test-run/metrics
- https://learn.microsoft.com/cli/azure/load/test-run/app-component
- https://learn.microsoft.com/cli/azure/load/test-run/server-metric
- https://learn.microsoft.com/cli/azure/monitor/metrics
- https://learn.microsoft.com/azure/app-testing/load-testing/how-to-monitor-server-side-metrics

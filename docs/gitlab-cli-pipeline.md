# Four-stage Azure Load Testing CLI demo

In GitLab, select **Build > Pipelines > Run pipeline**, choose protected `main`,
and run. Open the four boxes from left to right:

```text
deploy_load_test -> run_load_test -> view_results_metrics -> view_logs
```

| Stage / job | What to say to the audience | Main visible CLI command |
|---|---|---|
| `deploy_load_test` | "Deploy the load-test definition from the YAML and URL-request JSON in the repo." | `az load test update --load-test-config-file ...` |
| `run_load_test` | "Run that test and wait for a measured pass/fail result." | `az load test-run create` followed by `az load test-run show` |
| `view_results_metrics` | "Read the request statistics, client metrics and actual server metrics as TSV." | `az load test-run metrics list` and `az monitor metrics list` |
| `view_logs` | "Download the result/report/engine logs and inspect correlated application logs." | `az load test-run download-files --result --log --report` and `az monitor log-analytics query` |

The root [`.gitlab-ci.yml`](../.gitlab-ci.yml) is the presenter script. Every
Azure operation is visible there; none of these four jobs invokes Python.
The shared setup only signs in with managed identity in each job's isolated
CLI profile. The complex feature/MR path is separate in
[`.gitlab/advanced.yml`](../.gitlab/advanced.yml) and does not run in this demo.

This tests the **already-prepared staging application**. It does not redeploy,
reset or promote the application. The test ID, 40-row request, 12 users,
90-second duration and p95/error thresholds stay unchanged. The existing ALT
resource/test, staging slot, shared plan, seven monitoring definitions and reader
roles are prerequisites; use `create` once at onboarding for a new test, not
an implicit create-on-error fallback. The presenter pipeline updates that
prepared test and reuses its correct slot bindings.

One `run-id.txt` artifact carries the exact `cli-<pipeline-id>` through all four
jobs; result/log readers never ask for "latest." They run with `when: always`
and retain artifacts even when the measured run fails. A failed gate remains
nonzero: the viewers do not turn a failed load test into a successful pipeline.
An unavailable metric query is visible as a failed viewing job; empty series
remain `no samples`, and asynchronous app-log availability is labelled explicitly.

## CI/CD workflow and trigger scenarios

This demo deliberately separates **feature validation (CI)**, **production
promotion (CD)** and **testing an already-prepared app**. Its current rules are
restricted to trusted protected branches; they are not generic CI for every
branch, issue or fork.

| Scenario / event | What actually triggers today | Jobs / effect |
|---|---|---|
| Create a feature request or GitLab issue | Nothing automatically | A request describes work; it does not generate code, open an MR or start a load test. |
| Push a commit to a branch without an open eligible MR | Push pipelines are filtered | No pipeline, including a push to `main`. |
| Open a same-project MR from protected `demo-merchant-enrichment` to `main` | GitLab `merge_request_event` | `functional_tests` -> `candidate_artifact` -> `performance_gate`. |
| Push another commit to that open MR's source branch | A new MR pipeline for the new source SHA | Repeat functional checks, build a new exact-SHA ZIP and load-test that candidate in staging. A previous green SHA is not approval for new code. |
| MR targets another branch, uses an arbitrary feature-branch name, or comes from a fork | Not configured / filtered | No privileged deployment/load jobs. Do not broaden these rules for untrusted code. |
| Performance gate fails | The MR pipeline fails | Merge is blocked; retain results/logs. No production swap. A code fix on the same eligible MR triggers fresh CI. |
| Merge a green MR into `main` | The merge's ordinary push event is filtered | **Merge alone does not deploy production.** This also allows CI/documentation-only changes without a swap. |
| External demo controller explicitly requests promotion after verified green + fast-forward merge | API pipeline on `main` with `DEMO_PROMOTE_SHA`, `DEMO_GATE_JOB_ID` and `DEMO_MR_IID` | Only `promote_tested_slot`; revalidate the current merged SHA/latest green gate and ALT pass, then swap the exact tested staging slot without rebuilding. |
| Select **Run pipeline** on protected `main` in GitLab | `web` pipeline, with `DEMO_PROMOTE_SHA` empty | The four presenter stages above. No application deploy, reset or promotion. |
| An authorized caller requests a basic run using the API | `api` pipeline on `main`, `DEMO_CLI_WALKTHROUGH=true`, `DEMO_PROMOTE_SHA` empty | The same four-stage pipeline. API invocation remains an API event; it does not pretend to be a UI/web event. |
| A schedule, tag, external trigger-token event, or automatic issue webhook | Not configured / filtered | A future extension would need reviewed workflow/job rules and credential/runner protections; it is not active in this deployment. |

The two API modes are mutually exclusive: walkthrough needs an empty
`DEMO_PROMOTE_SHA`; promotion requires that SHA and rejects
`DEMO_CLI_WALKTHROUGH=true`. Calling the API without the required mode/inputs
does not create a usable deployment pipeline. A basic-run request is:

```http
POST /api/v4/projects/1/pipeline
```
```json
{
  "ref": "main",
  "variables": [{"key": "DEMO_CLI_WALKTHROUGH", "value": "true"}]
}
```

Use an authorized project token kept outside source/logs; the example deliberately
contains no authentication header or credential. In the GitLab UI no API flag is
needed: choose `main` and leave promotion inputs empty.

### Feature request through merge and optional release

```text
Feature request / issue (no automatic pipeline)
  -> trusted developer implements a change on the fixed protected source branch
  -> opens or updates its same-project MR to main
  -> functional_tests
  -> candidate_artifact: ZIP + source SHA / producing pipeline / content hash
  -> performance_gate: one locked staging deploy -> exact-SHA probe -> reset/warm
       -> native az test upsert/run -> settled criteria -> TSV metrics/logs
       -> fail: merge blocked; fix code and repeat the MR pipeline
       -> pass: current source is eligible for fast-forward merge
  -> merge to main (no automatic production deployment)
  -> optional explicit controller API promotion request
       -> verify the exact merged/tested source and fresh ALT pass
       -> swap tested staging to production; no rebuild
```

The optional controller is the transparent, prewritten merchant/category
red-to-green demonstration, **not an issue-triggered autonomous coding agent**.
It creates/updates an MR, verifies the measured red before applying its batching
patch, and separately requests promotion after green and merge. For a manually
managed feature MR, green/merge does not secretly start that controller.
The existing completed MR1 and CI-only MR2 are retained examples; do not reset
or replay them merely to inspect this workflow.

The definition and run jobs each use `resource_group: contoso-gitlab-demo-staging`;
that is **not a pipeline-wide lock between jobs**. Before creation, the run job
holds that shared lock while it rechecks the definition, downloads/compares the
actual uploaded URL profile, verifies the monitored slot/plan bindings and probes
the prepared application's version. It keeps the lock through the measurement,
settled verdict and final version probe. If something changed between definition
and run, it fails rather than silently testing a different profile. The viewer
jobs are read-only and use the immutable run ID, captured source SHA and time
window; they do not need the staging lock.

The advanced MR `performance_gate` still holds its original contiguous lock
from candidate deployment through measurement/evidence, and promotion uses the
same lock. The unchanged load gate requires p95 <= 500 ms and errors <= 1%,
with settled numeric criteria; DONE/NOT_APPLICABLE alone cannot pass.
JSON evidence and result/log/report downloads remain job artifacts on failure
as well as success. Optional asynchronous server/log telemetry is explicitly
labelled when unavailable, never fabricated.

Open each stage's log to follow the native commands; shared JSON artifacts carry
the handoff between stages. No Python Azure gate/deployment wrapper or global
shell tracing is used. Native `test-run create` waits for completion; a short
bounded read loop only waits for numeric aggregate/criterion evidence to settle.
`loadtests/gitlab-demo/verdict.jq` is a small pure JSON filter, not an Azure
wrapper. Functional checks exercise it against actual historical red data,
NOT_APPLICABLE, cancelled, criteria-less and contradictory examples.

## Optional advanced feature/MR path

The separately included `performance_gate` remains the exact-artifact MR regression path.
Its Azure operations are also direct native CLI. It additionally demonstrates:

1. `az login --identity`, `az account set` and `az account show`.
2. `az webapp deploy` of the exact SHA-stamped ZIP to **staging**.
3. Checked `az load test list`, then `az load test create` or `update` from
   the unchanged version-controlled URL-test YAML.
4. `az load test app-component add/list`, discovered Azure Monitor definitions,
   and seven native `az load test server-metric add/list` definitions.
5. `az load test-run create` and bounded `az load test-run show` polling.
6. Safe numeric verdict/criterion/client-statistic output. DONE is not sufficient:
   every measured criterion and aggregate must settle; NOT_APPLICABLE never passes.
7. `az load test-run download-files --result --log --report`, with a bounded,
   sanitized **engine-log excerpt printed in the console**.
8. `az load test-run metrics list` for real client time series.
9. `az monitor metrics list` for actual staging metrics and **shared S1-plan**
   CPU/memory/queue samples, plus `az monitor log-analytics query` for
   source-SHA-correlated request logs. Unavailable/empty asynchronous telemetry
   is explicitly labelled, never replaced with invented samples.

`AZURE_CONFIG_DIR` is unique to each job. Selecting a default subscription is
safe **inside that isolated profile only** and avoids load 2.1's multi-subscription
resolver bug. Resource commands still supply `--subscription`. Never run an
equivalent `az account set` in the shared presenter workstation profile.

In the advanced MR path only, Python remains for application/tests, exact artifact/functional/contract hashes,
private SUT reset/warm-up, GitLab MR/provenance checks, strict local JSON verdict
interpretation and console redaction. Those helpers make **no Azure calls**.
One contiguous resource-group lock covers deployment through evidence collection;
the shared promotion lock guards a visible native `az webapp deployment slot swap`
only after fresh native ALT evidence and exact tested-source checks.

The optional external feature/MR controller is unchanged. Historical MR1
pipeline2's 911-sample, 1250ms-p95/0%-error run is retained and projected into
`tests/fixtures/gitlab-alt-historical-red.json` to prove the strict red parser.
It is historical evidence, not a newly replayed regression. A CI-only validation
MR keeps the existing batched feature and production artifact; it must not
automatically reset or promote just to demonstrate the new YAML.

Job artifacts (`when: always`, 30 days) retain safe run/verdict/summary JSON,
component/definition inventories, client/server metrics, correlated logs,
downloaded results/engine logs/report and the exact candidate ZIP/manifest.
Secrets, access tokens, SAS response URLs and raw authorization headers are not
printed. Runner prerequisites are pinned in its Dockerfile: jq, unzip, load 2.1.0
and log-analytics 1.0.0b3. Its explicit test environment also pins PyYAML 6.0.3.

### Readable TSV in the console

The results stage and advanced gate project saved JSON into headered TSV with small, local
`jq -r --arg view ... -f loadtests/gitlab-demo/metrics-tsv.jq` commands.
No extra Azure query or load run is needed to change the presentation:
the complete original JSON remains the gate input and downloadable artifact.
Request columns are `request`, `samples`, `p95_ms`, `error_percent` and
`throughput_rps`. Client time series label timestamps and `ms`/`count`/`users`;
`TotalRequests` is a bucket count, **not RPS**, and `ResponseTime` points are
not relabelled as p95. Server rows preserve the reported units and aggregation,
with explicit `staging_slot` versus `shared_plan` scope (plan CPU includes
production). Definition tables are labelled definitions, not measured samples.
Real zero samples remain `0`; null buckets are omitted and empty series say
`no samples` with `N/A`, never fabricated zeros. Console row limits apply
only after the headered projection, not to the JSON artifacts.

Reference: [Microsoft's Azure Load Testing CLI CI/CD guide](https://learn.microsoft.com/azure/app-testing/load-testing/how-to-configure-load-test-cicd).

## Historical single-job TSV run (2026-10-20, before the four-stage revision)

The requested fresh run is
[pipeline10](https://gitlab-clean.greenpond-7c00b83b.swedencentral.azurecontainerapps.io/root/contoso-bank/-/pipelines/10),
with exactly one successful
[`cli_walkthrough` job24](https://gitlab-clean.greenpond-7c00b83b.swedencentral.azurecontainerapps.io/root/contoso-bank/-/jobs/24).
It used API mode on protected `main` source
`ca0d8e72f6fc55602dce6051ddcf0d28d8ead919`; ALT run `cli-10-24` passed both
unchanged criteria. The actual console includes:

```tsv
request	samples	p95_ms	error_percent	throughput_rps
GetTransactions	6879	243	0	70.91752577319588
Total	6879	243	0	70.91752577319588
```

The job also printed 30 real client metric points, 19 staging and 15 shared-plan
non-null metric points, two monitored components, seven metric definitions,
a sanitized engine-log excerpt and 100 source-correlated application log rows.
All TSV headers and request values were checked against the fresh JSON artifacts.
Use the job's **log** for the readable CLI/TSV story and **Browse artifacts** for
`demo-evidence/run.json`, `verdict.json`, metric/log JSON and downloaded
result/log/report archives; the full JSON was not replaced by the tables.

This run did **not** deploy/reset the application or promote anything.
Staging remained source `af3663bb04ee4ca9c1a93fb1370bac1c1427293b`
from producing pipeline8 before and after the test. Production remained source
`39ab1f106620b71cda89ec31b48c3f692c7f5042` from pipeline3. Pipeline code SHA,
deployed staging SHA and production SHA are intentionally distinct here.

## Historical single-job execution (2026-10-19)

[CI-only MR2](https://gitlab-clean.greenpond-7c00b83b.swedencentral.azurecontainerapps.io/root/contoso-bank/-/merge_requests/2)
was fast-forward merged only after the advanced native-CLI
[pipeline8](https://gitlab-clean.greenpond-7c00b83b.swedencentral.azurecontainerapps.io/root/contoso-bank/-/pipelines/8)
passed: run `gl-8-22-af3663bb`, 4,999 samples, p95 **360 ms**, errors **0%**.
Its source/functional/load-contract evidence remains compatible with the
optional external controller.

The primary, actual API-mode
[pipeline9](https://gitlab-clean.greenpond-7c00b83b.swedencentral.azurecontainerapps.io/root/contoso-bank/-/pipelines/9)
ran exactly one
[`cli_walkthrough` job23](https://gitlab-clean.greenpond-7c00b83b.swedencentral.azurecontainerapps.io/root/contoso-bank/-/jobs/23):
run `cli-9-23`, **5,336 samples, p95 310 ms, errors 0%**. Its console contains
actual sanitized engine log lines, **30** real client metric points, component/definition
inventories, **19** non-null staging metric points, **15** shared-plan points and
**100** source-correlated application log rows. The trace has zero Python
commands and no application deployment/reset/slot swap.

Both probes read deployed staging source
`af3663bb04ee4ca9c1a93fb1370bac1c1427293b`, producing pipeline8, unchanged
before/after the basic walkthrough. Production remains the historical tested
`39ab1f106620b71cda89ec31b48c3f692c7f5042` artifact from pipeline3.
No old scenario/reset/promotion was rerun. Pipelines5-7 remain transparently
retained setup failures before any measured ALT run (test dependency and native
CLI argument/type validation), not new performance regressions.

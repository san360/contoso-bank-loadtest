# GitLab: Azure Load Testing with visible Azure CLI commands

**Start here:** in GitLab, select **Build > Pipelines > Run pipeline**, choose
protected `main`, and run. The single **`cli_walkthrough`** job has **zero Python
commands**: only Azure CLI, Bash, jq, curl, unzip and sed. It targets the already
prepared staging app, with no application redeploy/reset/promotion. API validation
uses `DEMO_CLI_WALKTHROUGH=true`; that mode is mutually exclusive with promotion.
Correlation uses the actual staging `/version` SHA, not the pipeline commit.
Prerequisite: the test/SUT is already prepared with the actual staging slot,
shared plan and seven monitoring definitions from demo onboarding. The advanced
MR path validates these and can add supported plan metrics. The walkthrough
verifies they survive upsert; a first-ever test creation
does not magically create those bindings and will fail the prerequisite check
until monitoring is prepared.
The existing ALT system-assigned monitoring identity is retained and shown.
Load 2.1's `--metrics-reference-id None` validator rejects that documented
sentinel, so the walkthrough does not send it or use a hidden REST workaround.
Its add-component validator also parses a slot ID as parent `Microsoft.Web/sites`
and rejects the correct `Microsoft.Web/sites/slots` type. The existing correctly
prepared slot/metric bindings are therefore reused and verified with native
list commands. Missing slot bindings fail loudly; production is never attached
as a substitute. Actual slot samples still come from native Azure Monitor CLI.

Open [`.gitlab-ci.yml`](../.gitlab-ci.yml), then the `cli_walkthrough` log.
Azure operations are ordinary `az` commands in separate script entries, with
safe explicit labels inside the few loops. No Python Azure gate/deployment
wrapper or global shell tracing is used.

The walkthrough demonstrates native identity login, version-controlled test
create/update, binding readback, real run/poll/strict jq verdict, downloads and
sanitized engine log excerpt, client/server samples and correlated KQL logs.
`loadtests/gitlab-demo/verdict.jq` is a small pure JSON filter, not an Azure
wrapper. Functional checks exercise it against actual historical red data,
NOT_APPLICABLE, cancelled, criteria-less and contradictory examples.

**Advanced:** `performance_gate` remains the exact-artifact MR regression path.
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

Reference: [Microsoft's Azure Load Testing CLI CI/CD guide](https://learn.microsoft.com/azure/app-testing/load-testing/how-to-configure-load-test-cicd).

## Verified live execution (2026-10-19)

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

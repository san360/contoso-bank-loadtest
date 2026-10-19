# GitLab: Azure Load Testing with visible Azure CLI commands

Open [`.gitlab-ci.yml`](../.gitlab-ci.yml), then the `performance_gate` job log.
Azure operations are ordinary `az` commands in separate script entries, with
safe explicit labels inside the few loops. No Python Azure gate/deployment
wrapper or global shell tracing is used.

The job demonstrates, in order:

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

Python remains for application/tests, exact artifact/functional/contract hashes,
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
and log-analytics 1.0.0b3.

Reference: [Microsoft's Azure Load Testing CLI CI/CD guide](https://learn.microsoft.com/azure/app-testing/load-testing/how-to-configure-load-test-cicd).

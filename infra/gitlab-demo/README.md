# Experimental GitLab + Azure Load Testing demo

This **optional, ACA-only proof of concept** is separate from the default `azd`
stack in `infra/`. It creates a new resource group, never reuses the original
application, and does not provision VMs, Grafana, or LGTM.

**Not a supported production GitLab architecture.** GitLab advises against Azure
Files and does not support NFS repository storage. The user explicitly accepted
this experimental hosting choice. A successful demo, restart, or backup trial
does **not** establish corruption safety, HA, vendor support, or a production SLA.
Use only disposable demo code and synthetic data.

## What it demonstrates

Run the external controller once. It creates an actual feature commit and GitLab
merge request on `demo-merchant-enrichment`. The feature adds merchant/category
metadata to transaction history using one simulated database lookup per row.
Functional tests pass, but a real Azure Load Testing run breaches the p95 gate.
GitLab blocks merge. Only after that exact settled, p95-only failure does the
controller commit the prewritten batching implementation. The feature output,
application settings, load profile and criteria stay unchanged. The green MR is
fast-forward merged, and a second locked pipeline swaps the **tested slot** into
this demo's production slot without rebuilding it.

This is **scripted remediation, not an autonomous AI coding claim**. The two
implementations are visible in `tools/gitlab_demo/recipes/`.

The default API still has its original runtime-toggle demo behavior. Its new
enrichment seam is a no-op until the feature commit. `/version` exposes embedded
artifact source SHA and pipeline ID. The isolated deployment additionally
requires a secret header for `/admin/*`; public read-only load requests do not
carry credentials.

## Topology and security boundaries

| Resource | Configuration |
|---|---|
| Resource group | New `rg-contoso-gitlab-demo`; exact tag `SecurityControl=Ignore`, plus owner/project/purpose |
| GitLab CE | Pinned 19.4.2 image, ACA Consumption, 3.75 vCPU/7.5 GiB main plus 0.25 vCPU/0.5 GiB init (4/8 total), one replica, Single revision mode |
| Persistent storage | Classic Premium LRS Azure Files NFS share, 100 GiB; separate config/data/log directories |
| Network | Dedicated VNet/subnet; storage firewall allows that subnet; no public SSH |
| Public GitLab | ACA's DNS name and TLS termination; HTTP redirect, local password/PAT authentication, signups disabled before readiness |
| Runner | Separate ACA shell-executor container, 1 vCPU/2 GiB; global concurrency 2; project-specific protected runner |
| Runner tools | Runner 19.4.0, Python 3.12.12, Azure CLI 2.89.0, load extension 2.1.0 |
| System under test | New Linux S1 App Service and `staging` slot; ZIP/Oryx deployment, one worker |
| Load generator | New Azure Load Testing resource; one URL-test engine, read-only transactions |
| Azure credentials | Runner user-assigned managed identity; Website Contributor on this app, Load Test Contributor on ALT, AcrPull on ACR, Monitoring Reader on staging/plan and Log Analytics Reader on this workspace |
| ALT metrics identity | ALT's own system-assigned identity, Monitoring Reader scoped to the actual staging slot and shared S1 plan |

No privileged containers, Docker socket, DinD, GitLab SAML/OIDC integration, or
long-lived Azure client secret is used. GitLab UI credentials are not Azure ARM
credentials: ARM/ALT/Log Analytics still require Microsoft Entra tokens, obtained
here through the runner's managed identity.

The tag is a requested policy control input, **not a blanket policy exemption**.
Inspect effective policy and deployment results. Do not modify subscription
policies or assume the tag bypasses every deny.

ACA supports NFS only with a custom VNet and cannot encrypt this NFS mount in
transit. `https_traffic_only_enabled=false` is deliberate for the VNet-restricted
NFS account; shared-key access and anonymous public objects are disabled. Do not
copy this exception into unrelated storage.

Single revision mode and min/max one replica reduce, but do not eliminate,
overlap/multiwriter risks. **Stop GitLab before an image/revision update**, preserve
the share, and then start the new revision. Do not use rolling GitLab upgrades
or scale this proof of concept out.

For maintenance, switch the app to Multiple mode, explicitly deactivate **all**
active revisions, and verify there are no active replicas before applying the
new image. Keep private `gitlab_revision_mode="Multiple"` during this stopped
rollout; only after the new revision is genuinely healthy set it back to
`"Single"` and apply again. Restoring Single before the replacement is healthy
can reactivate an old "latest ready" revision, even one whose app never worked.
Do not leave the final demo in Multiple mode. The CLI does not have
an `az containerapp stop` command. Use `az containerapp revision set-mode --mode
multiple` followed by `az containerapp revision deactivate --revision <exact-name>`,
with the selected app/resource group/subscription on both commands. Do not
deactivate unrelated apps or discard their revisions.

## Presenter prerequisites

Use an explicitly selected subscription/tenant with permission to create this
isolated stack and its resource-scoped role assignments. Required providers:
Microsoft.App, Microsoft.Network, Microsoft.Storage, Microsoft.ContainerRegistry,
Microsoft.ManagedIdentity, Microsoft.Web, Microsoft.OperationalInsights and
Microsoft.LoadTestService.

Use Terraform 1.9 or newer (less than 2), the checked-in AzureRM 5.10.0 lockfile,
Git, Python 3.11 or newer, Azure CLI 2.89.0 and load extension 2.1.0. The optional
noninteractive ACA admin helper uses the pinned
`tools/gitlab_demo/requirements-admin.txt`. The runner image contains the test
dependencies; presenters running local tests use `src/requirements-dev.txt`.

Do not run `az account set`, change global defaults, or log out other sessions.
Every resource command below supplies `--subscription`.

Check regional availability/quota before apply. Sweden Central supports the
selected services; capacity and policy can still change. During the live
provisioning trial, ACA VNet setup reported the missing
`Microsoft.Network/AllowBringYourOwnPublicIpAddress` feature even though no
customer BYO-IP input was configured. One normal registration of that exact
Azure-reported prerequisite succeeded, followed by provider propagation and
reconciling the existing failed environment. This is a documented AFEC operation,
not a policy change:

```powershell
az feature show --namespace Microsoft.Network --name AllowBringYourOwnPublicIpAddress --subscription $subscription
# Only if the deployment reports this exact prerequisite, with authorization:
az feature register --namespace Microsoft.Network --name AllowBringYourOwnPublicIpAddress --subscription $subscription
# Continue only after Registered; Pending requires external approval, not retries.
az provider register --namespace Microsoft.Network --subscription $subscription --wait
```

## Provision once, outside CI

Choose an **owner-only private directory outside the repository**. It holds
credentials, local Terraform state/plans, resource outputs and controller reports.
Do not use a shared folder, commit it, or upload it as CI artifacts.

```powershell
$private = 'C:\secure\contoso-gitlab-demo'
$subscription = '<subscription-id>'
$tenant = '<tenant-id>'

python -m tools.gitlab_demo.bootstrap init --private-dir $private `
  --subscription $subscription --tenant $tenant --location swedencentral

terraform -chdir=infra\gitlab-demo init "-backend-config=path=$private\terraform.tfstate"
terraform -chdir=infra\gitlab-demo fmt -check
terraform -chdir=infra\gitlab-demo validate
terraform -chdir=infra\gitlab-demo plan "-var-file=$private\demo.tfvars.json" "-out=$private\foundation.tfplan"
terraform -chdir=infra\gitlab-demo apply "$private\foundation.tfplan"
terraform -chdir=infra\gitlab-demo output -json demo |
  Set-Content "$private\resources.json" -Encoding utf8
```

`bootstrap init` generates cryptographically random, distinct secrets and secures
the directory with owner/SYSTEM ACLs on Windows or mode 0700 on Linux. Never print
the credential JSON to a terminal or paste it into chat.

Build the small wrapper images using local Docker if available; otherwise ACR
remote builds require no local Docker daemon. Build contexts contain no secrets:

```powershell
$resources = Get-Content "$private\resources.json" -Raw | ConvertFrom-Json
Push-Location infra\gitlab-demo\images\gitlab
az acr build --registry $resources.registry --image gitlab:19.4.2-demo-v3 `
  --platform linux/amd64 --file Dockerfile --subscription $subscription .
Pop-Location
Push-Location infra\gitlab-demo\images\runner
az acr build --registry $resources.registry --image runner:19.4.0-demo-v2 `
  --platform linux/amd64 --file Dockerfile --subscription $subscription .
Pop-Location
```

Wait for **both builds to succeed**. The image builds validate Ruby/bash syntax,
normalize Linux line endings, and verify the runner tools/dependencies.
The GitLab image also checks its pinned base image's actual
`/assets/init-container` boot command, supported post-reconfigure hook and required tools. The custom entrypoint
executes that vendor command as PID 1; it does not assume the removed
`/assets/wrapper` path or replace vendor signal handling.
nginx listens on its standard port 80 behind ACA TLS termination; port 8080
belongs to GitLab's loopback Puma backend and must not be used for this ingress.
Local-auth/token bootstrap runs after the vendor reconfiguration, with its
completion marker written by the root hook rather than the unprivileged Rails
process. Existing bootstrap tokens are reused, never silently reissued.
Resolve each tag's digest with `az acr repository show --image ... --query digest`
and set private `gitlab_image_tag`/`runner_image_tag` values to
`gitlab@sha256:...` and `runner@sha256:...`. Set `deploy_gitlab=true` in the private
variables file, leaving `deploy_runner=false`. Validate, plan and apply again.
Do not put secret `-var` values in process arguments.

```powershell
python -m tools.gitlab_demo.verify --private-dir $private
```

This waits for **the actual public HTTPS sign-in form and a real root local-password session**.
It does not treat an assigned hostname or successful ARM creation as app health.
GitLab's monitoring endpoints can remain restricted to localhost; do not open
their IP allowlist just to make an external health check green.
The root login password is in the owner-only credential file, not this repo.

Commit the implementation before seeding, then:

```powershell
python -m tools.gitlab_demo.bootstrap seed --private-dir $private
```

The bootstrap creates one private project, pushes the exact committed source,
protects `main` and the fixed demo branch, enables **Pipelines must succeed** and
protected MR resources, creates a 30-day project bot token and project-specific
protected runner, and configures protected CI variables. The short-lived root
bootstrap PAT is then revoked. It does **not** push to GitHub or create a GitHub PR.
Partial bootstrap progress stays in private JSON so a retry can resume it.
Runner creation uses the temporary local-admin PAT; the project bot is used
only for project-scoped repository, pipeline and variable operations.
The fixed demo branch has no slash because this ACA proxy normalizes encoded
slashes in URL path parameters. File reads use documented tree/blob/commit
queries, not encoded-slash file endpoints or double-encoding workarounds.

The bootstrap sets `deploy_runner=true` in the saved private variables. Validate,
plan and apply to start the runner. Then run `verify` again to establish the
runner is online and the repository can be read. Keep the root password and
project token private; both GitLab URL and project URL are in `gitlab.json`.

The project is intentionally restricted to trusted same-project protected
branches. Do not use these privileged shell jobs for untrusted forks or arbitrary
customer code. Both branches, the actor's Maintainer role, and
`protect_merge_request_pipelines=true` are necessary for protected MR variables
and the protected runner to be available.

## Prove persistence and recovery before presenting

Do not call this deployment ready based on mounts appearing in ARM. After seed:

1. Record the project `main` SHA and repository file hash, verify local login,
   and inspect the running container's mount types for all three GitLab paths.
2. Create a small marker under `/var/opt/gitlab`, flush/fsync it, and record its
   hash plus the hash of `/etc/gitlab/gitlab-secrets.json` (never the file content).
3. Create application and **separate configuration** backups using
   `/opt/demo/backup.sh`. Both live on the persistent share; restrict access.
4. Restart the existing GitLab ACA revision, wait for HTTPS, and prove the same
   repository SHA, marker hash, secrets hash, project token and local login survive.
5. With no CI jobs or users mutating the lab, trial restoration of that exact
   backup on this disposable instance. Stop Puma/Sidekiq, run the supported
   `GITLAB_ASSUME_YES=1 gitlab-backup restore BACKUP=<id>` command, restart the
   services, and verify repository/database/login/runner state again.

The optional `python -m tools.gitlab_demo.aca_exec --private-dir ... --script ...`
uses Azure-authorized, noninteractive ACA exec. It needs no public SSH port and
keeps the Azure exec token in memory. Use `--output <private-file>` for verbose
administration output; never embed credentials in the supplied script.
Run these commands serially. If the exec gateway returns 429, honor its
`Retry-After` value instead of repeatedly opening sessions or changing identity.

`gitlab-backup` excludes `gitlab.rb`, TLS material and `gitlab-secrets.json`.
`gitlab-ctl backup-etc` is **not optional**. Restoring application data without the
original secrets can make encrypted application data unreadable. Copy tested
application/config backups off this single share for a real recovery strategy.
A same-share backup is not disaster recovery. Restore only to the same GitLab
version/edition, preserve configuration secrets, and do not overwrite another
instance. This proof of concept has no automatic scheduled backup or HA.

## Launch the customer sequence

Run this on the presenter machine, **not as a GitLab CI job**:

```powershell
python -m tools.gitlab_demo.controller run --private-dir $private
```

The controller holds an exclusive local lock and runs exactly one regression/fix
pair. It never takes a runner slot. It stops on infrastructure/authentication/
functional failures, no criteria, unsettled results, cancellation, source drift,
wrong failures, or missing artifacts; it does not auto-retry arbitrary failures.

If an operator repairs a **pre-red setup** failure on the same MR, an explicit
`controller resume --private-dir $private --source-sha <repaired-naive-SHA>`
continues that original MR. It creates no new scenario, reruns no job itself,
archives the stopped report, checks the unchanged target base and exact naive
feature, and still requires a real settled p95-only failure before batching.
It cannot resume past a measured-red phase or accept a prematurely batched
feature. Setup uses a scoped test lookup: existing definitions are updated,
only a real HTTP 404 is created, and authorization/network failures stop loudly.
App-component and seven server-metric bindings are configured/read back after
each update; no existing test/run history is deleted.

The fixed gate is one engine, 12 VUs, 10-second ramp, 90-second duration, a
40-row read-only page, p95 response time no higher than 500 ms and errors no
higher than 1%. It does not exercise the original JMeter property/environment
mapping or teaching scripts. `tools/loadtest_gate.py` is the shared, tested
fail-closed parser used by CI, promotion, and the external controller.

`performance_gate` holds **one resource-group lock for its entire**
deploy/SHA probe/reset/warm/load/download interval. Splitting those phases into
separate jobs with the same lock would allow candidate interleaving. Promotion
uses the same lock, checks the successful **latest** MR pipeline and exact source,
re-reads settled ALT evidence, verifies `main` did not drift, and checks the
staging version before swapping. Fast-forward-only merge preserves the tested
source SHA. There is no Terraform apply per MR, preset flip, SLO weakening,
`allow_failure`, or success-shaped error fallback.

GitLab artifacts use `when: always`, retained for 30 days, and include source
SHA, ZIP hash, GitLab pipeline/job IDs, ALT run ID/time window, run JSON snapshots,
criteria, p95/errors, unchanged load-contract/functional-output hashes and
downloaded engine results/logs/report. `demo-run.json` in the private presenter
directory records the before/after pair and resulting promotion URL.
Snapshots keep the server's measurement fields but exclude SAS-bearing artifact
URLs and credential fields. Explicit job/script timeouts leave time to upload
failure evidence rather than relying on GitLab's ineffective default timeout.

For a new presentation, explicitly reset **only this demo**:

```powershell
python -m tools.gitlab_demo.controller reset --private-dir $private
python -m tools.gitlab_demo.controller run --private-dir $private
```

Reset refuses open MRs and active pipelines, commits the no-feature repository
baseline and deletes/reprotects the fixed source branch. It does not force-push
main, discard evidence, or roll back the feature as the green fix. Production
continues serving the previous tested artifact until the new green promotion.
If a prior attempt stopped, inspect its evidence and close that stopped demo MR
before reset. Never delete controller locks without verifying their process
has exited.

The shell runner explicitly forwards its tool paths and ACA's local managed
identity endpoint/header into the job environment: `su --login` otherwise drops
them. It does not forward the runner authentication token. The local identity
header stays in the root-only runner configuration, not project variables or
artifacts; trusted jobs use it to obtain short-lived Azure tokens.

## Bounded recovery environment

If the original environment fails during platform provisioning, do not loop
PATCH calls or delete its data. After fixing the exact prerequisite, the private
`use_clean_environment=true` option provisions **one** explicitly named recovery
target: `cae-gitlab-demo-clean`, subnet `container-apps-clean` (`10.74.2.0/23`) and
app `gitlab-clean`. It preserves the original environment/subnet/NFS binding and
share, adds only the new subnet to the storage firewall, and uses the same pinned
images, credentials and scoped identities. Never enable it to perform a rolling
update of a healthy GitLab instance.

An original Failed app cannot be imported by AzureRM while `listSecrets` returns
`ResourceNotProvisioned`. Its ID and the inactive `aca-platform-probe` diagnostic
job are retained explicitly as read-only inventory in state/outputs instead of
deleting or pretending to manage them. Review the recovery plan: it must have
**zero destroys**. An assigned recovery hostname is not a healthy service.
If the clean target also cannot allocate, stop cloud retries and report the
actual blocker and retained resources. No VM fallback or policy changes.

Retained environment networking can have charges. The manual diagnostic job has
no schedule and no active compute after its bounded execution. Before eventual
full lab destruction, explicitly review and remove the exact retained failed
app/diagnostic job IDs reported by Terraform; they are read-only inventory, so
Terraform does not delete them. Resource-group deletion intentionally refuses
unmanaged leftovers rather than silently deleting them.

## Reading results, server metrics and logs

The client proof is available through `az load test-run show`, `metrics list`,
`download-files`, and GitLab job artifacts. ALT client p95/RPS/errors and worker
logs are **ALT data-plane results**, not native Log Analytics tables. ALT
`OperationLogs` diagnostics are API audit events, not client performance series.

The test now attaches the **actual staging slot**, not the production parent,
and the shared S1 plan. The service accepted the `Microsoft.Web/sites/slots`
component and seven metric definitions in the live configuration trial.
Slot metrics are Requests/HTTP 5xx totals, average HTTP response time (seconds)
and average memory working set (bytes). Plan CPU/memory percentages and HTTP
queue length are explicitly **shared with production**, not candidate-isolated.
These are diagnostic definitions, not additional gate thresholds.

Each locked gate configures and reads back the components/definitions before
starting the run. Artifacts include `app-components.json`,
`server-metric-definitions.json`, their per-run counterparts,
`client-metric-series.json`, `server-metrics-staging.json`,
`server-metrics-shared_plan.json`, `staging-console-logs.json` and
`monitoring-summary.json`. Server samples are queried from Azure Monitor for the
real run's time window and bordering one-minute buckets. A definition does not
prove any time-series samples exist; zero is a real sample, null/empty is not.
Pending ingestion and unavailable/forbidden telemetry are reported explicitly.
The p95/error verdict remains independent from telemetry ingestion.

The optional stack enables request JSON logging with source SHA, producing
pipeline ID, correlation ID, method/path/status and elapsed time; it never logs
authentication headers or request bodies. `LOG_REQUESTS` stays **off by default**
for the original API/demo. Staging and production console/HTTP diagnostics route
to the **existing** workspace's `AppServiceConsoleLogs`/`AppServiceHTTPLogs`
tables. No Application Insights, Grafana, LGTM or new workspace is added.
Log ingestion is asynchronous and tables may not exist until the first records.

### Shared-profile CLI limitation

With CLI 2.89/load 2.1, the native load data-client factory resolves credentials
from the profile's default subscription even when `--subscription` is supplied.
Another process changing a shared default can therefore cause a misleading
`ResourceGroupNotFound` or a null endpoint for an existing resource. Do not edit
installed CLI files, copy caches, switch the shared default, or put bearer tokens
in `az rest --headers` process arguments.

The external controller and this read-only helper discover the endpoint through
explicit-subscription ARM, acquire an explicit-subscription/audience token, and
keep its Authorization header only in process memory:

```powershell
python -m tools.gitlab_demo.azure_http run --private-dir $private --run-id '<run-id>'
python -m tools.gitlab_demo.azure_http components --private-dir $private
python -m tools.gitlab_demo.azure_http metric-definitions --private-dir $private
```

The runner has its own per-job `AZURE_CONFIG_DIR`, one managed-identity
subscription, and records/validates the selected subscription/directory before
using native `az load`. Tokens and SAS-bearing artifact URLs are excluded from
the saved read-only snapshots. After changing Terraform resource outputs for an
existing lab, `python -m tools.gitlab_demo.bootstrap configure --private-dir
$private` refreshes protected CI variables without creating tokens/projects.

Grafana's Azure Monitor data source is optional for KQL/server telemetry, and
does not automatically expose ALT run results. See the separately researched
observability documentation for exporter and alternative Log Analytics paths.

## Cost exposure, stopping and cleanup

Leave cloud services running only as long as this demo is needed. Public USD
Sweden Central ACA active retail meters checked on 2026-10-19 are
0.000024 per vCPU-second and 0.000003 per GiB-second. At continuously active
5 vCPU/10 GiB allocation, ACA alone is about **$0.54/hour or $394/month (730h)**,
before free grants, requests, networking, storage, App Service, ACR, log ingestion
and ACR builds. Idle billing can be lower; background GitLab activity means you
must not budget on scale-to-zero or free idle compute.

The Linux S1 plan, 100 GiB provisioned Premium Files share and Basic ACR have
separate standing charges even if the web apps or containers stop. One 90-second
ALT run is 0.30 raw VUH; the repo's documented 10-minute/10-user engine floor
makes this 12-user run 2 billed VUH. At the calculator's assumed $0.15/VUH, the
pair is about $0.60, before free allowances or negotiated rates. Recheck current
ALT terms/rates; this is an estimate, not a bill or total lab budget.

To pause, first stop the external controller and ensure no CI jobs are active,
then stop GitLab and runner Container Apps and the app/slot using their **exact
resource names**. Keep the NFS share and private state/configuration backups.
Stopping apps is not full cleanup and does not stop plan/storage/registry charges.
Do not scale GitLab out as a cost workaround.

To remove the lab, retain/download evidence and backups first. Review
`terraform plan -destroy` with the **same private backend and variables**, verify
it contains only this lab's resources, then explicitly apply that reviewed
destroy plan. Do not delete a resource group to recover from an error, destroy
unrelated azd state, unregister shared subscription features, or run broad
wildcard deletion. The requested work leaves services running; cleanup is a
separate, deliberate presenter action.

## References

- [GitLab requirements and Azure Files warning](https://docs.gitlab.com/install/requirements/)
- [GitLab NFS limitations](https://docs.gitlab.com/administration/nfs/)
- [GitLab memory-constrained settings](https://docs.gitlab.com/omnibus/settings/memory_constrained_envs/)
- [GitLab backup and restore](https://docs.gitlab.com/administration/backup_restore/)
- [Protected MR resources](https://docs.gitlab.com/ci/pipelines/merge_request_pipelines/#control-access-to-protected-variables-and-runners)
- [Azure Container Apps storage and NFS restrictions](https://learn.microsoft.com/azure/container-apps/storage-mounts)
- [Azure feature registration](https://learn.microsoft.com/azure/azure-resource-manager/management/preview-features)
- [Azure Load Testing YAML contract](https://learn.microsoft.com/azure/app-testing/load-testing/reference-test-config-yaml)
- [Azure retail prices API](https://learn.microsoft.com/rest/api/cost-management/retail-prices/azure-retail-prices)

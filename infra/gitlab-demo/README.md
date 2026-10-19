# GitLab VM + Azure Load Testing demo

This optional stack is separate from the default `azd` stack in `infra/`.
The selected architecture is **official GitLab CE on a private Linux VM with
managed SSD storage**, with the existing ACA endpoint becoming a small stateless
nginx TLS front door. The ACA runner, its managed identity, App Service, ALT,
workspace, public hostname and completed demo history are retained.
It does not provision Grafana or LGTM.

**Migration verified (2026-10-19):** the current completed project was restored,
survived a controlled VM reboot with Docker automatic startup and no runtime NFS,
and is live through the original HTTPS hostname. Root local login, runner online,
HTTPS clone, original marker/secrets hashes, MR/pipeline history, ZIP checksum
and protected-variable decryption passed. An actual deallocation request was
denied with `ScopeLocked`; the VM remained running. The editable as-built diagram is
[`docs/architecture/gitlab-vm-demo.drawio`](../../docs/architecture/gitlab-vm-demo.drawio).

This is a single-node synthetic-data demo, **not HA or a production SLA**.
The former ACA-only/NFS hosting was an explicitly accepted experiment. Its
bounded rolling-restart test exposed overlapping replicas and failed repository
readiness; controlled cold-boot recovery also timed out. That is a real PoC
limitation, not diagnosed proof of corruption. Original share/backups and
failure evidence remain preserved; no further stateful ACA startup is required.

## VM migration and protection

`vm.tf` adds a regular D4as_v5 VM (4 vCPU/16 GiB), a 128 GiB Premium managed SSD,
private subnet `10.74.4.0/24`, private IP `10.74.4.4` and scoped NAT egress.
The VM has **no public IP or inbound SSH**. NAT's public IP is outbound-only.
Run Command is the administration path until the final protection lock.
The VM uses the unmodified, pinned official CE image through
`vm-compose.yaml`; live config/data/logs bind to the ext4 disk at `/srv/gitlab`.
The NFS share is only a read-only migration source, never VM repository storage.

The cutover sequence is foundation validate/plan/apply, freeze every old
stateful ACA revision and verify zero actual replicas, restore the CURRENT
post-promotion application/config-secret backup to the same CE version, verify
main/MR/pipeline/ZIP/crypto state privately, then replace the existing ACA
template with official nginx. The proxy has 0.25 vCPU/0.5 GiB, no GitLab
password/bootstrap secrets, no NFS volume/init container, and forwards private
HTTP while preserving the public Host and forwarded HTTPS scheme. Keep Multiple
maintenance mode until nginx is healthy/latest-ready, then return to Single.
Existing runner/controller/MR/job URLs do not change.

The data disk has `CanNotDelete` protection. The user explicitly selected a
**VM-scoped ReadOnly lock**, applied only after admin, backup, controlled reboot
and public migration verification. It blocks Azure control-plane power and VM
management writes, including Run Command; authorized maintenance requires
explicit unlock. It cannot prevent guest-OS shutdown, Azure platform failure,
or an authorized administrator removing the lock. Never place it on the whole
resource group/subscription: CI must still deploy App Service and invoke ALT.

Post-migration **code cleanup** removes the obsolete stateful ACA image,
bootstrap/readiness hooks and WebSocket/NFS recovery adapters. Normal CI/ALT/
controller code and the stateless proxy/runner remain. Original infrastructure
declarations, NFS data, backup archives, MR history and evidence are retained.

## What it demonstrates

The Azure path is now **CLI-first**: start with **Run pipeline on protected main**
and the zero-Python `cli_walkthrough` job. It exercises the prepared staging app
without redeploy/reset/promotion. The advanced MR `performance_gate` also shows
direct managed-identity login, candidate
deployment, load-test upsert/run/read/download, component/metric definitions,
client/server metrics and correlated Log Analytics queries. Engine logs are
shown as a bounded sanitized console excerpt, not just hidden in artifacts.
See [the CLI presenter walkthrough](../../docs/gitlab-cli-pipeline.md).

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

## Architecture and trust boundaries

| Resource | Configuration |
|---|---|
| Resource group | New `rg-contoso-gitlab-demo`; exact tag `SecurityControl=Ignore`, plus owner/project/purpose |
| GitLab CE | Unmodified pinned 19.4.2 official image, regular private D4as_v5 VM, 4 vCPU/16 GiB; constrained demo sizing, not a production reference architecture |
| Persistent storage | 128 GiB Premium managed SSD, ext4, stable UUID at `/srv/gitlab`; Docker `RequiresMountsFor` plus UUID/type/mountpoint guard fails closed |
| Network | Private VM `10.74.4.4`; only ACA subnets reach HTTP 80; no VM public IP/SSH; explicit NAT on the VM subnet |
| Public front door | Official nginx, ACA Consumption 0.25 vCPU/0.5 GiB, one replica/Single mode, no NFS/init/GitLab secrets |
| Retained legacy storage | Original 100 GiB Premium Azure Files share and exact migration backups; not active GitLab storage |
| Public GitLab | ACA's DNS name and TLS termination; HTTP redirect, local password/PAT authentication, signups disabled before readiness |
| Runner | Separate ACA shell-executor container, 1 vCPU/2 GiB; global concurrency 2; project-specific protected runner |
| Runner tools | Runner 19.4.0, Python 3.12.12, Azure CLI 2.89.0, load 2.1.0, log-analytics 1.0.0b3, pinned jq/unzip |
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

The retained NFS account has the earlier, explicitly accepted VNet-restricted
NFS transport configuration. Its resource declarations stay in Terraform so code
cleanup cannot schedule deletion of original backups. Never restart an old
stateful ACA revision: only the stateless nginx revision should be active.

## Presenter prerequisites

Use an explicitly selected subscription/tenant with permission to create this
isolated stack and its resource-scoped role assignments. Required providers:
Microsoft.App, Microsoft.Network, Microsoft.Storage, Microsoft.ContainerRegistry,
Microsoft.ManagedIdentity, Microsoft.Web, Microsoft.OperationalInsights and
Microsoft.LoadTestService and Microsoft.Compute.

Use Terraform 1.9 or newer (less than 2), the checked-in AzureRM 5.10.0 lockfile,
Git, Python 3.11 or newer, Azure CLI 2.89.0, load extension 2.1.0 and `ssh-keygen`.
The VM uses pinned Ubuntu 24.04 image `24.04.202610160`; Docker 29.1.3 and Compose
2.40.3 were verified. VM Run Command uses standard Azure CLI, not an SSH/WebSocket
adapter. The runner image contains the test
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

`bootstrap init` secures the directory with owner/SYSTEM ACLs on Windows or mode
0700 on Linux, generates the app admin secret/proposed root password and a
provisioning SSH key, and selects the VM foundation. The SSH key is not an inbound
access path: the VM has no public IP/SSH rule. Never print credential JSON.

```powershell
$resources = Get-Content "$private\resources.json" -Raw | ConvertFrom-Json
Push-Location infra\gitlab-demo\images\runner
az acr build --registry $resources.registry --image runner:19.4.0-demo-v4 `
  --platform linux/amd64 --file Dockerfile --subscription $subscription .
Pop-Location
az acr import --name $resources.registry --source docker.io/library/nginx:stable-alpine `
  --image nginx-proxy:stable-alpine --subscription $subscription
```

Only the tool-pinned runner image is built; nginx is an unmodified official
image mirror. Resolve/pin their ACR digests in the private variables. GitLab
itself uses the exact official CE digest in `vm-compose.yaml`, with its real OCI
`CMD /assets/init-container` and no custom entrypoint.

On the prepared VM, install `vm-compose.yaml` and `vm-gitlab.rb` using scoped
Run Command (`tools.gitlab_demo.vm_exec.execute` keeps verbose output private).
The config binds to `/srv/gitlab/config/gitlab.rb`; Compose resides in
`/opt/gitlab-demo/compose.yaml`, with an owner-only `.env` containing the public
`GITLAB_EXTERNAL_URL`. Start the official image only after the UUID mount guard
passes. For a migration, `vm-restore.sh` additionally requires explicit frozen
source proof, matching archive/secret/marker hashes, same-version backup and a
fresh, not-already-started restore. It never deletes the old share.

For a new instance, follow GitLab's official first-sign-in procedure, disable
public signups before enabling ingress, set/save the real root password and
create a temporary admin PAT. Store that actual PAT in the private
`gitlab_bootstrap_token` field; the tool does not fabricate a working PAT.
Set `deploy_gitlab=true` only after the VM's real readiness succeeds, then
validate/plan/apply the stateless proxy. Do not put secret `-var`, tokens or
Authorization headers in process arguments.

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

## Persistence, backups and maintenance

The completed VM trial recorded a changed host boot ID, identical disk UUID,
automatic Docker/GitLab startup without a manual start, zero runtime NFS mounts,
unchanged original marker/secrets fingerprints, same main/MR/pipeline/ZIP state
and decryptable protected variables. Public local login and runner online were
verified again after the actual `ScopeLocked` deallocation denial.

Before authorized maintenance, deliberately remove the **VM-only** ReadOnly lock;
Run Command and VM power changes cannot work while it is present. Never unlock
the whole resource group or change subscription defaults. Quiesce Puma/Sidekiq,
run the vendor `gitlab-backup create STRATEGY=copy` and
`gitlab-ctl backup-etc`, and preserve CURRENT completed history. Separate
configuration/secrets backup is mandatory; restore only the same CE version.
Do not restore an earlier demo snapshot over new MRs/pipelines.

Current VM archives are
`/srv/gitlab/data/backups/1792435003_2026_10_19_19.4.2_gitlab_backup.tar`
and `/srv/gitlab/config/config_backup/gitlab_config_1792435009_2026_10_19.tar`.
Checksum-verified off-VM copies are on the retained share at
`stgitlabeeed02.file.core.windows.net:/stgitlabeeed02/gitlab/vm-migration-backups/20261019T183649Z`.
The original post-promotion migration archives remain untouched too.
This is an off-VM copy in the same environment, **not isolated/offsite DR**;
there is no scheduled backup or HA claim. Restore the lock only after health,
history/crypto and persistence checks succeed.

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
feature. Setup uses a checked native `az load test list` inventory: existing
definitions are updated and only a successfully confirmed absence is created.
Authorization/network/list failures stop loudly rather than being treated as absence.
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

## Verified deployed scenario (2026-10-19)

The private project is
[root/contoso-bank](https://gitlab-clean.greenpond-7c00b83b.swedencentral.azurecontainerapps.io/root/contoso-bank).
[MR 1](https://gitlab-clean.greenpond-7c00b83b.swedencentral.azurecontainerapps.io/root/contoso-bank/-/merge_requests/1)
is merged and retained, with its source branch and all pipeline evidence.
Do not launch another controller or reset it just to inspect these results.

| Actual candidate | Pipeline | ALT run | Samples | p95 | Errors | Result |
|---|---|---|---:|---:|---:|---|
| Naive per-row enrichment | [2](https://gitlab-clean.greenpond-7c00b83b.swedencentral.azurecontainerapps.io/root/contoso-bank/-/pipelines/2) | `gl-2-6-d8288fea` | 911 | 1,250 ms | 0% | Only p95 criterion failed; merge blocked |
| Batched enrichment | [3](https://gitlab-clean.greenpond-7c00b83b.swedencentral.azurecontainerapps.io/root/contoso-bank/-/pipelines/3) | `gl-3-9-39ab1f10` | 6,573 | 253 ms | 0% | Both unchanged criteria passed |

The complete load/settings contract and functional-output SHA256 hashes match
between the runs; p95 improved 4.94x. Pipeline 1 remains as a transparently
classified **infrastructure failure before load**, not a measured performance
regression. It exposed an existing-definition create/update mismatch; the
explicit pre-red repair resumed the same MR without changing the naive feature,
load profile or thresholds.

[Promotion pipeline 4](https://gitlab-clean.greenpond-7c00b83b.swedencentral.azurecontainerapps.io/root/contoso-bank/-/pipelines/4)
swapped the exact tested slot without rebuilding. The
[production version](https://app-gitlab-demo-eeed02.azurewebsites.net/version)
was verified as source `39ab1f106620b71cda89ec31b48c3f692c7f5042`,
producing pipeline `3`, feature `merchant-category-v1`. The tested ZIP SHA256 is
`9bcccd4fe7fa2bae0b2e869ee8b7334601692c8759966796e6a86ab6c6e81203`.

Both runs have **actual samples**, not just monitoring definitions: red collected
20 non-null staging points, 15 shared-plan points and 100 source-correlated
console log rows; green collected 16, 12 and 100 respectively. Plan CPU/memory
remain shared with production. The `performance_gate` job's always-uploaded
`demo-evidence/` and `dist/` artifacts hold the measurements, logs and exact ZIP.
Artifacts expire after 30 days; retain them deliberately before then.

## Retained legacy inventory

The historical `use_clean_environment=true` selection keeps the original
environment/subnet/NFS binding and the later `cae-gitlab-demo-clean` environment
and `container-apps-clean` subnet (`10.74.2.0/23`) in state. `gitlab-clean` now
runs only the stateless proxy. The original share/archives and failed resources
remain explicitly inventoried and billable; code cleanup does not remove them.

An original Failed app cannot be imported by AzureRM while `listSecrets` returns
`ResourceNotProvisioned`. Its ID and the inactive `aca-platform-probe` diagnostic
job are retained explicitly as read-only inventory in state/outputs instead of
deleting or pretending to manage them. The final migration/cleanup plan must have
**zero destroys**. No old stateful ACA revision is reactivated.

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
subscription, and visibly selects the target with `az account set` **only inside
that job's isolated profile**, then records/validates subscription/directory before
using native `az load`. Tokens and SAS-bearing artifact URLs are excluded from
the saved read-only snapshots. After changing Terraform resource outputs for an
existing lab, `python -m tools.gitlab_demo.bootstrap configure --private-dir
$private` refreshes protected CI variables without creating tokens/projects.

Grafana's Azure Monitor data source is optional for KQL/server telemetry, and
does not automatically expose ALT run results. See the separately researched
observability documentation for exporter and alternative Log Analytics paths.

## Cost exposure, stopping and cleanup

The user requested always-on VM protection. Sweden Central Linux D4as_v5 retail
checked on 2026-10-19 is **$0.184/hour, about $134/month (730h)** for VM compute.
The 0.25/0.5 proxy plus 1/2 runner total 1.25 vCPU/2.5 GiB; at the checked ACA
active meters (0.000024/vCPU-second, 0.000003/GiB-second), their continuous-active
allocation is about **$0.135/hour or $99/month** before grants/idle billing.
NAT Gateway hourly/data processing, its Standard egress IP, the 64 GiB OS and
128 GiB data disks, App Service, ACR, log ingestion and builds are additional.
These are partial retail estimates, not a bill or total budget.

The Linux S1 plan, 100 GiB provisioned Premium Files share and Basic ACR have
separate standing charges even if the web apps or containers stop. One 90-second
ALT run is 0.30 raw VUH; the repo's documented 10-minute/10-user engine floor
makes this 12-user run 2 billed VUH. At the calculator's assumed $0.15/VUH, the
pair is about $0.60, before free allowances or negotiated rates. Recheck current
ALT terms/rates; this is an estimate, not a bill or total lab budget.

Stopping only the proxy does not stop VM/NAT/disk/plan charges. A later,
separately authorized pause needs an intentional VM-only unlock and controlled
GitLab shutdown; do not defeat the requested protection as part of code cleanup.
Keep the original share and private state/configuration backups. Do not scale
GitLab out or reactivate old ACA writers as a cost workaround.

To remove the lab, retain/download evidence and backups first. Review
`terraform plan -destroy` with the **same private backend and variables**, verify
it contains only this lab's resources, then explicitly apply that reviewed
destroy plan. Do not delete a resource group to recover from an error, destroy
unrelated azd state, unregister shared subscription features, or run broad
wildcard deletion. VM/data-disk locks also require deliberate authorized removal.
This work leaves the protected services and retained archives running/preserved;
cloud cleanup is a separate presenter decision.

## References

- [GitLab requirements and Azure Files warning](https://docs.gitlab.com/install/requirements/)
- [GitLab NFS limitations](https://docs.gitlab.com/administration/nfs/)
- [GitLab memory-constrained settings](https://docs.gitlab.com/omnibus/settings/memory_constrained_envs/)
- [GitLab backup and restore](https://docs.gitlab.com/administration/backup_restore/)
- [Official GitLab Docker installation](https://docs.gitlab.com/install/docker/)
- [Protected MR resources](https://docs.gitlab.com/ci/pipelines/merge_request_pipelines/#control-access-to-protected-variables-and-runners)
- [Azure Container Apps storage and NFS restrictions](https://learn.microsoft.com/azure/container-apps/storage-mounts)
- [Azure feature registration](https://learn.microsoft.com/azure/azure-resource-manager/management/preview-features)
- [Azure resource locks and their limits](https://learn.microsoft.com/azure/azure-resource-manager/management/lock-resources)
- [Azure Load Testing YAML contract](https://learn.microsoft.com/azure/app-testing/load-testing/reference-test-config-yaml)
- [Azure retail prices API](https://learn.microsoft.com/rest/api/cost-management/retail-prices/azure-retail-prices)

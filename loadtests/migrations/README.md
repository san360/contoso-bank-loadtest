# JMeter-to-Locust migrations

Two independent entry points migrate the supplied plans. Neither original JMX,
the original CSV/properties/YAML inputs, nor the existing Locust demo was changed.
JMX inspection parsed XML as data; no embedded BeanShell, Groovy, or secret
function was executed.

## Inputs and approved decisions

| Migration | Source | Azure baseline |
| --- | --- | --- |
| Retail | [contoso-bank.jmx](../jmeter/contoso-bank.jmx), [accounts.csv](../data/accounts.csv), [user.properties](../jmeter/user.properties) | [peak-load.yaml](../config/peak-load.yaml) |
| Secrets | [contoso-bank-secrets.jmx](../jmeter/contoso-bank-secrets.jmx), [user.properties](../jmeter/user.properties) | [secrets-demo.yaml](../config/secrets-demo.yaml) |

The user explicitly selected:

- **Literal retail load**, not the comments' intended peak profile. Retail
  `__P(...)` reads JMeter properties, not the similarly named YAML environment
  variables. Its effective profile is 50 users per engine, a 60-second ramp,
  300-second duration, page size 20, and 500 ms base think time.
- **Literal CSV first-row skipping**, including on headerless Azure shards.
  This deliberately retains the source's potential loss of a real first account.
- **Guarded failure paths**: missing/unusable transfer correlation fails
  `CreateTransfer` and skips its dependent read; an unresolved secret fails
  `SecretResolutionCheck` and stops that user without sending HTTP.
- **Bounded shutdown grace**: 75 seconds for retail and 45 seconds for secrets.
  New samplers stop at the duration deadline; a still-active request may be
  aborted after this additional grace. Unlike JMeter's normal in-flight
  completion behavior, this is not an unlimited wait.
- The referenced but unattached `hc.parameters` is the stock JMeter file,
  containing no active overrides.

## Generated files

| Files | Purpose |
| --- | --- |
| [retail_locustfile.py](retail_locustfile.py) | CSV-driven retail execution, independent shared percentage controllers, assertions and correlation |
| [secrets_locustfile.py](secrets_locustfile.py) | Ordered secrets journey and once-per-user secret check |
| [jmeter_runtime.py](jmeter_runtime.py) | Scheduler deadline, session setup, explicit startup failures and cookie rejection |
| [retail.local.conf](retail.local.conf), [secrets.local.conf](secrets.local.conf) | Local-only, loopback default profiles |
| [retail.azure.yaml](retail.azure.yaml), [retail.azure.conf](retail.azure.conf) | Separate retail Azure configuration; new test ID |
| [secrets.azure.yaml](secrets.azure.yaml), [secrets.azure.conf](secrets.azure.conf) | Separate secrets Azure configuration; original Key Vault URI reference and a new test ID |
| [requirements.txt](requirements.txt), [requirements-test.txt](requirements-test.txt) | Pinned local Locust runtime and validation dependencies |
| [Retail inventory](retail-banking/source-inventory.json), [secrets inventory](secrets-demo/source-inventory.json) | Scoped JMX inventories with original SHA-256 hashes; no credential/header/body export |
| [migration_support.py](tests/migration_support.py) | Loopback HTTP fixture, synthetic accounts and ephemeral generated credentials |
| [test_migration_behavior.py](tests/test_migration_behavior.py) | Source inventory, request-trace and behavior checks |
| [test_migration_profiles.py](tests/test_migration_profiles.py) | Native ramp dispatcher, YAML paths, aggregate load and configuration checks |
| [test_migration_smoke.py](tests/test_migration_smoke.py) | Bounded one-user Locust CLI runs and negative-path exit-code checks |

The original CSV is referenced, not copied into the migration. Test fixtures use
synthetic account records and generate credentials in memory.

## Run locally

Use Python 3.11 or newer. On Windows ARM64, use an **x64 Python interpreter**:
Locust's native dependencies may not have ARM64 wheels. With `python` pointing
to the selected interpreter, start from the repository root:

```powershell
Set-Location loadtests\migrations
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-test.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_migration_*.py' -v
```

The test suite starts and checks its own HTTP fixture on an ephemeral
`127.0.0.1` port, then stops it. It does not require a deployed application, an
Azure login, a real API key, or a separately running local server.

For an application already listening on an authorized local loopback endpoint:

```powershell
.\.venv\Scripts\python.exe -m locust --config retail.local.conf `
  --locustfile retail_locustfile.py --users 1 --spawn-rate 1 --run-time 10s
```

For the secrets entry point, first supply `API_KEY` through your local secret
mechanism, then run:

```powershell
.\.venv\Scripts\python.exe -m locust --config secrets.local.conf `
  --locustfile secrets_locustfile.py --users 1 --spawn-rate 1 --run-time 5s
```

Both local configuration files default to `http://127.0.0.1:8000`. They contain
the literal shared-property profile, not a one-user profile; the example CLI
overrides make the manual runs small and bounded.

Migration environment settings:

- `PAGE_SIZE`: retail `pageSize` property; defaults to 20.
- `THINK_TIME_MS`: retail `thinkTimeMs` property; defaults to 500. The independent
  0-499 ms jitter remains even when the base is set to zero.
- `ACCOUNTS_CSV`: optional explicit local CSV path. Invalid paths fail startup.
  Without an override, the script uses an uploaded adjacent CSV or the original
  repository CSV in the known local layout.
- `ACCOUNT_ID`: secrets `accountId` property; defaults to `ACC-1001`.
- `API_KEY`: resolved once per user, never logged as plaintext.
- Standard Locust CLI/configuration settings replace the JMeter load properties.
  An explicit host wins over the `BASE_URL` hostname fallback.

## Load profiles and aggregate engine validation

| Profile | Engines | Peak users per engine | Aggregate peak users | Ramp | Scheduled duration |
| --- | ---: | ---: | ---: | ---: | ---: |
| Retail local, attached properties | 1 | 50 | 50 | 60 s | 300 s |
| Secrets local, no Azure environment overrides | 1 | 50 | 50 | 60 s | 300 s |
| Retail Azure, literal source | 4 | 50 | **200** | 60 s | 300 s |
| Secrets Azure, effective Groovy environment values | 1 | 5 | **5** | 15 s | 60 s |

The secrets JMX gives its Azure environment values precedence over the shared
properties for users/ramp/duration. Without those overrides, the attached
properties resolve to the 50/60/300 local profile.

Current official Azure documentation specifies **aggregate** Locust users and
spawn rate, even though each engine runs a `LocalRunner`. Therefore retail
configures `LOCUST_USERS=200` and overall `LOCUST_SPAWN_RATE=200/60`, not 50
aggregate users and not 200 users per engine. Engine-local values under the
documented distribution contract are 50 users and 50/60 users per second.
Secrets configures 5 users and 5/15 users per second overall.

The tests drive the actual pinned Locust `UsersDispatcher` with time fast-forwarded:
retail starts one user per engine every 1.2 seconds; secrets starts one every
3 seconds. The first user starts at zero, so final ideal starts are 58.8 seconds
and 12 seconds, respectively. Retail aggregate checkpoints are 4 users at zero,
104 at 30 seconds, and 200 by 60 seconds. Deadline and partial-journey shutdown
checks are separate from ramp checks.

`LOCUST_RUN_TIME` values are integer seconds. Configuration files contain only
options recognized by Locust 2.46.4; no module-global percentile setting is
mistaken for a CLI option.

## Behavior parity

| Source behavior | Destination implementation | Verification / intentional gap |
| --- | --- | --- |
| Scoped hashTree execution, one scheduled thread group per plan, infinite iterations, no start delay | One ordered task per entry point; native users/spawn-rate/run-time; deadline checked before every sampler | Source inventories; native dispatcher and shutdown tests |
| Four independent global percentage controllers, evaluated each iteration in source order | Engine-shared rounding counters for 60/20/15/5; not weighted random tasks; an iteration can select zero or multiple controllers | 1,000 decisions match the JMeter formula exactly: 600/200/150/50 selected journeys |
| Stable retail sampler ordering and names | `GetBalance`, `GetTransactions`, `CreateTransfer`, `GetTransferStatus`, `GenerateStatement`, each with explicit `name=` | 100 fixture iterations produce the reference trace: 60/20/15/15/5 requests, 115 total |
| Four CSV variables, UTF-8, comma delimiter, quotedData=false, shared across users, advancing every iteration, recycling | One engine-shared cursor; literal quotes; unconditional first-row skip on load/recycle; row changes even on empty controller iterations | Actual source CSV checked without duplication; synthetic sharing, quoting, EOF and headerless-shard cases |
| Raw transfer JSON, fixed destination, inclusive random range, thread number and global counter | Original raw JSON formatting and types; destination unchanged; amount 500-25000; `LOADTEST-<thread>-<engine-counter>` | Exact recorded request body, both amount boundaries, multiple-user counter lifetimes |
| First JSONPath match `$.transaction_id`, default `NOT_FOUND` | Extract actual response value; no invented/stale ID; missing/invalid value fails creation and skips dependent HTTP | String/numeric ID, missing/null/invalid JSON/wrong-type cases; approved guard replaces `/NOT_FOUND` |
| Exact response-code assertions; continue on sample error | Explicit `catch_response` assertions and original custom messages; other selected samplers continue | Expected-status failures and CLI nonzero-exit checks; even a failed create status follows a real usable ID, preserving continue-on-error |
| No explicit status assertion on `GetTransferStatus` | Normal HTTP success/failure classification, not a new 200-only assertion | 204/302 accepted, 404 failed |
| Retail global headers, per-user cookies cleared each iteration, same-user=false | Original headers, isolated sessions, cookie reset and closed connection pools each iteration; keepalive within an iteration | Recorded headers, cookie reset/isolation, connection reuse, redirects and timeouts |
| No CookieManager in the secrets JMX | Reject response cookies, including redirect-hop cookies, rather than inheriting Locust's normal session persistence | Secrets redirect and multi-iteration traces contain no cookies |
| UniformRandomTimer at thread-group scope | Delay before **every executed sampler**, including both transfer requests: 500-999 ms by default; no added task wait | Bounds, real measured pre-request delays, 115 timer calls for the 100-iteration trace |
| No timers in the secrets JMX | No invented pacing; zero inter-task wait | Ordered local traces and unpaced bounded CLI run |
| OnceOnlyController plus known JSR223 secret check; secret captured per thread | One `JSR223` request event named `SecretResolutionCheck` per user; capture environment secret once; preserve safe length/fingerprint logging | Per-user count, frozen header lifetime, Java trim/UTF-16 length, unresolved-secret failure and no-HTTP guard |
| HTTPS default, redirects, keepalive, no retries, separate connect/read timeouts | Explicit host override for loopback; redirects enabled; zero-retry session; 10/60 s retail and 10/30 s secrets timeouts | Fixture traces, request arguments and adapter checks; TLS implementation differences noted below |
| Scheduled stop | Stop new samplers at deadline; permit an in-flight sample to finish within configured grace | Timer/dependent-read shutdown tests; user-approved 75/45 s cap differs from unlimited completion |

## Azure configuration and remaining limits

- Both YAMLs use new test IDs and `testType: Locust`. Relative script,
  configuration, helper and CSV paths exist. Azure's flat uploaded-file layout
  is exercised locally with isolated copied code and synthetic CSV data.
- All six retail and five secrets client-metric criteria, and the 90%/60-second
  auto-stop settings, are preserved from their respective source YAMLs.
  `SecretResolutionCheck` retains its sampler-specific `percentage(error) > 0`.
- Retail retains `splitAllCSVs: true`. Azure does not preserve CSV headers:
  **unconditional skipping can discard a real row from a shard**. This is an
  explicitly selected source behavior, not a recommendation. No claim is made
  about live service shard boundaries.
- The active `API_KEY` Key Vault URI is unchanged, without fetching or embedding
  its value. The commented-out retail secret example is not enabled.
- Neither supplied baseline has active subnet/public-IP restrictions, identity
  overrides, certificates or app components, so none were invented. Existing
  resource defaults and permissions must still be appropriate before a future
  authorized Azure execution.
- The repository's provisioning hook attaches monitoring to its old test IDs,
  not these new IDs. Environment-specific app components/server metrics, or any
  networking/identity settings applied outside the supplied YAMLs, must be
  exported or reattached separately before using the new tests in Azure. The
  hook and original test definitions were not changed.
- Source `DEMO_LOG_SECRET=true` plaintext logging is intentionally unsupported
  and explicitly rejected. The supplied false setting remains supported.
- Upstream Locust master/worker or multi-process mode is explicitly rejected;
  independent workers would otherwise change shared CSV/controller semantics.
  Azure's documented per-engine `LocalRunner` mode is the intended cloud runtime.
- Empty/malformed CSVs fail explicitly rather than relying on JMeter's stale
  variable or EOF-marker behavior for invalid data. The supplied valid CSV and
  recycling policy are covered.
- Java versus Python TLS trust stores, TLS session resumption, transport headers,
  random-number sequences, startup overhead and real scheduler jitter are not
  byte-for-byte identical. No identical throughput, latency or percentile
  performance claim is made from matching concurrency.
- The known BeanShell/Groovy/GetSecret/JSR223 behaviors in these inputs were
  translated manually. This is not an arbitrary embedded-code converter.
- **Azure validation is offline only**: YAML parsing, paths, applicable schema
  fields, criteria equality, documented aggregate-load math and local flat-file
  execution were checked. No Azure test was created, deployed, service-validated
  or run; the current managed engine version and live Key Vault/RBAC/monitoring
  behavior were not queried.

## Validation results

Validated on 2026-10-20 with Python 3.11.9 x64, Locust 2.46.4 and PyYAML 6.0.3:

- **41 tests passed**: 30 behavior tests, 6 configuration/load-profile tests and
  5 CLI smoke tests. The final suite completed in approximately 19 seconds.
- Successful one-user CLI runs exercised every retail request and both secrets
  HTTP requests plus the synthetic secret-check metric.
- Negative CLI runs proved nonzero exit status for a missing secret, an expected
  HTTP assertion failure and an invalid CSV, with no HTTP for startup/secret
  failures.
- CLI assertions use final native JSON statistics, not a potentially stale
  periodic CSV snapshot. A scheduled end may legitimately leave one successful
  creation without its follow-up read; that boundary is checked explicitly.
- The 8 original JMX/CSV/properties/baseline-YAML/demo files matched the SHA-256
  baseline captured before generation.
- Python compilation and Pylance syntax checks passed. The selected editor
  interpreter was not changed; it lacks these Locust dependencies, so a
  dependency-complete editor type-check was not performed.
- All validation HTTP traffic stayed on loopback. Fixture processes, temporary
  CSVs, ephemeral credentials and temporary result files were cleaned up.

## Runtime references

- [Azure Locust quickstart and LocalRunner mode](https://learn.microsoft.com/azure/app-testing/load-testing/quickstart-create-run-load-test-with-locust)
- [Azure aggregate users and spawn rate](https://learn.microsoft.com/azure/app-testing/load-testing/how-to-high-scale-load)
- [Azure YAML reference](https://learn.microsoft.com/azure/app-testing/load-testing/reference-test-config-yaml)
- [Azure CSV splitting and header handling](https://learn.microsoft.com/azure/app-testing/load-testing/how-to-read-csv-data)
- [Azure Locust secrets as environment variables](https://learn.microsoft.com/azure/app-testing/load-testing/how-to-parameterize-load-tests)
- [Locust configuration](https://docs.locust.io/en/stable/configuration.html)
- [JMeter 5.6.3 percentage controller source](https://github.com/apache/jmeter/blob/rel/v5.6.3/src/components/src/main/java/org/apache/jmeter/control/ThroughputController.java)

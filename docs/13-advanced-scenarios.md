# 13 — Advanced Scenarios (NEW SECTION)

> **Section goal:** Material for the "yes, but our situation is more complicated" conversation. You will not present all of this — treat it as a **menu**. Pick the two or three that match what the customer raised in the discovery call, and keep the rest as leave-behind documentation.

**Duration:** flexible (5–20 minutes) · **Format:** Slides / whiteboard on demand · **Audience:** Architecture, performance engineering

---

## 13.1 Testing private and internal endpoints

**The scenario:** *"Our core banking APIs are on a private network. They have no public endpoint and never will."*

**The answer:** VNet injection — set `subnetId` and the engines run inside your VNet.

```yaml
subnetId: /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Network/virtualNetworks/<vnet>/subnets/<subnet>
publicIPDisabled: true
```

Design checklist to walk through with their network team:
- [ ] Dedicated subnet, sized for your maximum engine count (address space runs out surprisingly fast at 100+ engines)
- [ ] Subnet correctly delegated/configured per the docs
- [ ] NSG rules permitting egress to the target service
- [ ] Private DNS resolution working from that subnet (**the #1 cause of "it worked publicly but fails privately"**)
- [ ] If egress is force-tunnelled through a firewall/NVA, confirm the firewall is sized for test throughput — otherwise you are load testing your firewall
- [ ] `publicIPDisabled: true` if a fully private configuration is required
- [ ] Budget for the load balancer + public IP if it is not disabled

**Variant — public endpoint with IP allow-listing:** attach a **NAT Gateway** to the injected subnet so all engine egress uses a static, allow-listable IP. This works with App Gateway/WAF, App Service access restrictions and third-party firewalls.

**Variant — on-premises targets:** from the injected subnet, reach on-prem over **ExpressRoute or VPN**. This is how you load test a service fronting a mainframe.

---

## 13.2 Testing secured endpoints

**The scenario:** *"Every one of our APIs requires OAuth, and some require mutual TLS."*

| Auth type | Approach |
|---|---|
| **Bearer token / OAuth 2.0** | Acquire a token in a setup step of the script; store the client secret in **Key Vault** and reference it as a `secrets` entry |
| **Client certificate (mTLS)** | Upload the certificate to **Key Vault** and reference it in the `certificates` section of the config |
| **Managed identity** | The engine can use the load testing resource's managed identity to authenticate to Azure-protected endpoints |
| **API key / shared secret** | Key Vault secret, retrieved via `GetSecret` (JMeter) or an env var (Locust) |

**Token lifetime is the classic trap:** in a 2-hour soak test, a token acquired once at the start will expire mid-run and every request after that fails. Refresh tokens periodically inside the script. **Mention this — it's the kind of detail that tells a performance engineer you've actually done this.**

---

## 13.3 High-scale testing

**The scenario:** *"We need to prove we can handle market open — 50,000 concurrent users."*

Method:
1. **Compute the engine count.** JMeter: `total VUs = threads per engine × engines`, with threads per engine ≤ 250. So 50,000 VUs ≈ 200 engines.
2. **Check quotas well in advance.** Max 400 engines per run, 1,000 concurrent (quota-gated). Raise the request weeks ahead.
3. **Ramp up in stages.** Never jump straight to full scale — you'll learn nothing except that it broke.
4. **Watch engine health.** CPU/memory below 75% average, or the numbers are invalid.
5. **Estimate RPS:** `RPS ≈ VUs × (1 / response time in seconds)`. Six-figure RPS is achievable.
6. **Mind the results limitation:** for runs with >45 engines or >3 hours, direct results download is unavailable — use the JMeter **Backend Listener**, Locust event hooks, or copy from a storage account.
7. **Split your test data** (`splitAllCSVs: true`) so 200 engines don't all replay the same 100 accounts.
8. **Check the rest of the chain:** at this scale you may saturate your own firewall, DNS, Front Door, or a third-party dependency's rate limit before you saturate the app.

---

## 13.4 Multi-region load generation

**The scenario:** *"Our customers are across EMEA and APAC, and we run active-active. Testing from one region proves nothing about our global routing."*

Use `regionalLoadTestConfig` to distribute engine instances across multiple Azure regions in a single test.

Use it to validate:
- **Front Door / Traffic Manager** routing decisions under real load
- **Regional failover** — start a test, fail a region mid-run, watch traffic and latency shift
- Genuine **geographic latency** as experienced by customers, not just from your test region
- **Data replication lag** under write load in an active-active configuration

⚠️ **Compliance interaction:** engines will run in the regions you nominate. Confirm this against the data residency position from doc 11 before enabling it.

---

## 13.5 Data-driven and parameterised tests

**The scenario:** *"If all our virtual users hit the same account, we've tested our cache, not our database."* (Correct — and a very common flaw in in-house tests.)

Techniques:
- **CSV data files** with `splitAllCSVs: true` so each engine gets a distinct slice of the data.
- **ZIP artefacts** for large data sets (limits: max 50 MB compressed, 5 ZIPs per test, 1,000 files per ZIP, 1 GB uncompressed).
- **Environment variables** to parameterise base URL, duration, thread count — so the *same script* serves the PR gate, the nightly regression and the capacity test.
- **Key Vault secrets and certificates** for credentials.
- **JMeter user `.properties` files** (or Locust `.conf`/`.ini`/`.toml`) for tuning without editing the script.
- **JMeter plugins** — from jmeter-plugins.org or custom JARs — supported.

**Realistic data guidance for FSI:** use a representative distribution, not a uniform one. Real banking traffic is heavily skewed — a small number of accounts generate a large share of transactions, and balance enquiries vastly outnumber transfers. A uniform test overstates cache effectiveness and understates hot-partition risk.

---

## 13.6 Response variables — dynamic, dependent requests without a script

**The scenario:** *"Our journeys aren't a list of URLs. You create a payment, get an ID back, then poll that ID. A no-code tool can't do that."*

It can. **Response variables** extract a value from one response and inject it into a later request, using `${variable-name}`. This is the no-code equivalent of a JMeter post-processor, and it is what turns a URL-based test from a flat list of endpoints into an actual business journey.

### 13.6.1 Why it matters — the alternative is a test that measures nothing

Without correlation, a no-code test can only replay hardcoded values. Against this kit's API, a hardcoded transfer id returns **404 for every request**. The test still "runs", still reports throughput and percentiles, and still goes green if you forgot failure criteria — while measuring the 404 path at full speed. That is the false green of [doc 07 §7.2](07-analyzing-results-and-bottlenecks.md) wearing a different hat.

| Without response variables | With response variables |
|---|---|
| Static ids that rot the moment data changes | Ids generated by the run itself |
| Read path only, or writes nobody verifies | Write → read-back, the real journey |
| Hot-caches one record, hides partitioning problems | Touches a distinct record per iteration |
| Green run proves the error path is fast | Green run proves the journey works |

### 13.6.2 The three extractor types

| `extractorType` | `expression` is | Use it for |
|---|---|---|
| `JSONExtractor` | JSONPath, e.g. `$.transaction_id` | REST/JSON APIs — the default choice |
| `XPathExtractor` | XPath, e.g. `/note/body` | SOAP, XML payloads, HTML fragments |
| `RegularExpression` | a regex | Tokens embedded in HTML, `Set-Cookie` style values, anything not cleanly structured |

Each variable needs a unique `variableName`; refer to it later as `${variableName}`.

### 13.6.3 The worked example in this kit

[`loadtests/config/url-correlated.yaml`](../loadtests/config/url-correlated.yaml) + [`loadtests/url-tests/contoso-bank-correlated.json`](../loadtests/url-tests/contoso-bank-correlated.json) implement the real banking flow, with no script at all:

```jsonc
{
  "requestName": "CreateTransfer",
  "requestType": "URL",
  "method": "POST",
  "endpoint": "https://<host>/api/transfers",
  "body": "{\"from_account\":\"ACC-1001\",\"to_account\":\"ACC-2002\",\"amount_minor\":1,...}",
  "requestBodyFormat": "JSON",
  "responseVariables": [
    { "extractorType": "JSONExtractor",
      "expression": "$.transaction_id",
      "variableName": "transferId" }
  ]
},
{
  "requestName": "GetTransferById",
  "requestType": "URL",
  "method": "GET",
  "endpoint": "https://<host>/api/transfers/${transferId}"
},
{
  "requestName": "GetTransferByIdCurl",
  "requestType": "CURL",
  "curlCommand": "curl --request GET 'https://<host>/api/transfers/${transferId}' --header 'Accept: application/json'"
}
```

**Measured on run `correlated-fixed-153140` (2026-09-15), 20 VUs, 2 minutes:**

| Request | Samples | Status |
|---|---:|---|
| `CreateTransfer` | 1,540 | `201` |
| `GetTransferById` | 1,520 | `200` |
| `GetTransferByIdCurl` | 1,520 | `200` |

Verdict `PASSED`, 4,580 samples, **0% errors**, and the results CSV shows genuinely distinct ids being requested:

```text
https://<host>/api/transfers/TRF-D595658122FF
https://<host>/api/transfers/TRF-A5537603F8FD
https://<host>/api/transfers/TRF-E64CF4E5876A
```

Two things this proves beyond the headline: extraction works per iteration (not once per run), and **a variable extracted by a `URL` request is usable by a `CURL` request in the same scenario** — the two request styles share one variable scope.

### 13.6.4 Make the test self-validating

The config deliberately carries `percentage(error) > 5`. If the extraction ever breaks, the follow-up request fails and the run fails with it. **Never ship a correlated test without an error-percentage criterion** — correlation is precisely the thing that breaks silently when an API changes.

### 13.6.5 The failure mode to recognise — an empty variable is not an obvious error

When extraction yields nothing, the variable resolves to an empty string rather than raising. `GET /api/transfers/${transferId}` becomes `GET /api/transfers/`, and this is what the results actually looked like on the first attempt:

```text
GetTransferById-0   307    ← trailing-slash redirect
GetTransferById-1   301    ← redirect again
GetTransferById-2   405    ← Method Not Allowed: /api/transfers only accepts POST
```

A redirect chain ending in `405` is a very confusing way to be told "your JSONPath matched nothing". Recognise the shape: **sub-samples numbered `-0`, `-1`, `-2` on a request that should be a single call mean redirects**, and redirects on a correlated request usually mean an empty variable.

The root cause that time was upstream: `CreateTransfer` was returning `422 Insufficient funds` because earlier runs had drained `ACC-1001` to £9.77, so there was no `transaction_id` to extract. Hence two habits worth keeping:

- Transfer **trivial amounts** (`amount_minor: 1`) in correlated tests so balances survive repeated runs.
- Reset the demo data before a run that moves money: `pwsh -File tools/run_loadtest.ps1 -TestId contoso-bank-url-correlated -ResetData`.

### 13.6.6 The portal editor hides some of these requests — the test is still fine

Opening **Edit → Test plan** on this test in the portal lists only **two** of its three requests: `CreateTransfer` and `GetTransferByIdCurl`. `GetTransferById` — the URL-type request whose endpoint is `/api/transfers/${transferId}` — is silently absent.

Nothing is wrong with the test. Verified on 2026-09-15:

| Check | Result |
|---|---|
| Stored `URL_TEST_CONFIG` | 3 requests, `VALIDATION_SUCCESS` |
| Generated `url_test.jmx` | 3 samplers, including `GetTransferById` |
| Run `correlated-fixed-153140` | 3 labels executed, 1,520 × `200` on `GetTransferById` |

The pattern, as far as it has been measured: the editor renders a plain URL request and a cURL request (whose command is an opaque string to it), but drops a **URL request with a variable in the endpoint**. It gives no warning that it has done so.

So: **do not use the portal editor to review or edit a correlated test**, and never press **Apply** there — see [doc 04 §4.4](04-portal-feature-walkthrough.md). Review the JSON in source control instead, and prove behaviour by running the test and reading the per-request results.

### 13.6.7 When to use it — and when to reach for JMeter instead

**Use response variables when:**

- the journey is dependent — create → read, login → call, search → open result
- you want the write path covered without a hardcoded, rotting id
- the audience is a QA analyst or product owner who will not maintain a JMX file
- you need a correlated journey *today* and a scripted test is a week away

**Reach for JMeter or Locust when:**

- the journey needs more requests than a URL test allows (⚠️ the portal editor states **15**, Microsoft Learn still says **five** — verify before quoting a number)
- you need conditional logic, loops, or arithmetic between steps
- you need custom assertions beyond status codes, or protocol support beyond HTTP
- you need plugins, or bespoke think-time distributions

> **Correction to an earlier claim in this kit:** `url-smoke.yaml` and `url-curl.yaml` used to say the correlated transfer flow "needs the JMX or Locust flavour". That is wrong, and the measured run above disproves it. The real URL-based limits are the request cap and the absence of scripting logic — not correlation.

---



## 13.7 Soak and endurance testing

**The scenario:** *"We're fine for an hour, but we restart the service every night and nobody quite knows why."*

- Run moderate load for **4–24 hours** (24h is the documented maximum duration).
- Look for **gradual degradation** rather than an immediate failure:
  - memory growth → leak
  - connection count growth → connection leak
  - thread count growth → thread leak
  - disk usage growth → unrotated logs
  - response time creeping up → cache/index/GC pressure
- Configure **server-side metrics** — soak failures are almost always visible in server metrics long before client metrics move.
- ⚠️ Remember the >3-hour results download limitation — plan result streaming.

---

## 13.8 Spike testing

**The scenario:** *"A push notification goes to two million customers at 9am. What happens?"*

- URL-based tests support a **Spike** load pattern (`spikeMultiplier`, `spikeHoldTimeInSeconds`).
- For JMeter, model the spike in the thread group.
- What you're actually testing: **can the platform scale fast enough?** Autoscale takes minutes; a spike takes seconds.
- Watch for: cold starts, connection pool exhaustion, scale-out lag, cache stampede on a cold cache, and **retry storms** — a client retry policy can turn a brief spike into a sustained self-inflicted DDoS.
- **This is one of the most valuable tests for a bank** and one of the least commonly run.

---

## 13.9 Validating autoscale

**The scenario:** *"We have autoscale rules. Nobody has ever verified they work."*

A genuinely compelling live demo if you have time:
1. Configure autoscale on the app (App Service or Container Apps).
2. Add the app resource as an **App Component** so replica/instance count appears in the dashboard.
3. Run a **step or ramping** load test.
4. Watch, on one screen: VUs rising → response time rising → **instance count increasing** → response time recovering.

Questions this answers, all of which matter operationally:
- Do the rules trigger on the right metric?
- Is scale-out fast enough to matter, or does the incident end before the instances arrive?
- What's the response-time cost during the scale-out window?
- Does scale-**in** behave sensibly afterwards, or does it flap?
- What does it cost at peak? (Ties to doc 12.)

---

## 13.10 Testing the whole chain, not just the app

Encourage the customer to add App Components for **everything in the request path**, and to think about what else the test exercises:

- **API Management** — capacity %, and whether rate-limit policies fire as intended
- **Application Gateway / Front Door / WAF** — backend health, and whether the **WAF blocks your load test** (a classic first-run failure — the WAF sees a bot and is entirely correct to)
- **Service Bus / Event Hubs** — queue depth growth is the leading indicator that downstream processing can't keep up, even while the API still looks healthy
- **Azure SQL / Cosmos DB** — DTU/RU saturation, throttling, deadlocks, hot partitions
- **Redis / caching layer** — hit ratio under load, eviction rate
- **Third-party dependencies** — credit bureaux, KYC/AML providers, payment schemes. **Their rate limits are your capacity ceiling**, and you need their agreement before load testing through them

> **Important practical warning to give:** *"Confirm before you point a 20,000-user test at a shared pre-production environment or a third party's sandbox. Two things you don't want: an unplanned outage for another team, or a stiff email from a payment scheme."*

---

## 13.11 Chaos + load: resilience testing

For a mature customer, the natural next step: run a load test **while** injecting failure with **Azure Chaos Studio**.

- Load test running at steady state → kill an instance / add network latency / fail over the database → observe whether SLOs hold.
- This is precisely the "severe but plausible scenario" testing that operational resilience regulation asks for.
- Strong follow-up engagement topic — mention it in doc 14's roadmap rather than demoing it here.

---

## Reference links

- [Test private endpoints](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/how-to-test-private-endpoint)
- [Test secured endpoints](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/how-to-test-secured-endpoints)
- [Configure for high-scale load](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/how-to-high-scale-load)
- [VNet injection concepts](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/concept-azure-load-testing-vnet-injection)
- [Parameterize load tests](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/how-to-parameterize-load-tests)
- [Load test YAML configuration reference](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/reference-test-config-yaml)
- [Load testing Azure App Service](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/concept-load-test-app-service)
- [Load testing Azure Functions](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/how-to-create-load-test-function-app)
- [Azure Chaos Studio](https://learn.microsoft.com/en-us/azure/chaos-studio/)

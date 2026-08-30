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

## 13.6 Soak and endurance testing

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

## 13.7 Spike testing

**The scenario:** *"A push notification goes to two million customers at 9am. What happens?"*

- URL-based tests support a **Spike** load pattern (`spikeMultiplier`, `spikeHoldTimeInSeconds`).
- For JMeter, model the spike in the thread group.
- What you're actually testing: **can the platform scale fast enough?** Autoscale takes minutes; a spike takes seconds.
- Watch for: cold starts, connection pool exhaustion, scale-out lag, cache stampede on a cold cache, and **retry storms** — a client retry policy can turn a brief spike into a sustained self-inflicted DDoS.
- **This is one of the most valuable tests for a bank** and one of the least commonly run.

---

## 13.8 Validating autoscale

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

## 13.9 Testing the whole chain, not just the app

Encourage the customer to add App Components for **everything in the request path**, and to think about what else the test exercises:

- **API Management** — capacity %, and whether rate-limit policies fire as intended
- **Application Gateway / Front Door / WAF** — backend health, and whether the **WAF blocks your load test** (a classic first-run failure — the WAF sees a bot and is entirely correct to)
- **Service Bus / Event Hubs** — queue depth growth is the leading indicator that downstream processing can't keep up, even while the API still looks healthy
- **Azure SQL / Cosmos DB** — DTU/RU saturation, throttling, deadlocks, hot partitions
- **Redis / caching layer** — hit ratio under load, eviction rate
- **Third-party dependencies** — credit bureaux, KYC/AML providers, payment schemes. **Their rate limits are your capacity ceiling**, and you need their agreement before load testing through them

> **Important practical warning to give:** *"Confirm before you point a 20,000-user test at a shared pre-production environment or a third party's sandbox. Two things you don't want: an unplanned outage for another team, or a stiff email from a payment scheme."*

---

## 13.10 Chaos + load: resilience testing

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

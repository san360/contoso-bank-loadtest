# 02 — Why Load Testing *as a Service*? (NEW SECTION)

> **Section goal:** This is the section that answers your explicit requirement: *"The demo should also prove why we need load testing as a service altogether."*
> Make the build-vs-buy argument honestly and quantitatively. A financial services customer has almost certainly already built a JMeter grid at some point — so **do not pretend self-hosting is impossible.** Argue that it is *undifferentiated heavy lifting* that they are paying for twice: once in cloud spend, and again in engineer-years.

**Duration:** 15 minutes · **Format:** Slides + TCO table · **Audience:** Leadership + platform engineering

---

## 2.1 Frame it correctly: three ways to get load testing

| Option | What it is | Where it fails |
|---|---|---|
| **A — Do nothing** | Test functionally, hope for the best, fix in prod | Regulatory exposure, incident-driven engineering |
| **B — Self-host** | Build a JMeter/Locust/k6 grid on VMs, AKS or containers | Works, but you now operate a distributed system whose only job is testing another distributed system |
| **C — Managed service** | Azure Load Testing: you bring the script, Microsoft runs the fleet | Cost per VUH; requires an Azure footprint |

The honest position: **B is a legitimate engineering choice — but it is rarely a good use of a bank's scarce platform engineers.**

---

## 2.2 The hidden cost of self-hosted load testing

Walk through what a self-hosted grid actually requires. This slide should feel uncomfortably familiar to anyone who has built one.

**Infrastructure you must build and own:**
- Controller + worker (master/slave) topology, and the RMI/port configuration between them
- Autoscaling the worker pool, and — critically — **scaling it back down** so idle VMs don't burn budget
- Image lifecycle: JMeter version, JDK version, plugin JARs, OS patching, CVE remediation
- Networking: VNet, NSGs, egress IPs, firewall allow-listing, private DNS
- Distributing test artefacts (scripts, CSV data) to every worker, and **splitting CSV data** so workers don't replay identical rows
- Aggregating results from N workers into one coherent report
- Time synchronisation across workers, or your percentiles are wrong
- A results store, a dashboard, and retention/archival for audit
- Secrets handling so credentials aren't baked into `.jmx` files in Git

**Operational burden that never ends:**
- Someone is on the hook when the grid breaks the night before a release
- Capacity contention: two teams want 500 VUs at the same time
- "Is the test wrong or is the app slow?" — with self-hosting, the grid is always a suspect
- Cost attribution and showback across teams
- Certificate, credential and subscription-level policy drift

**The killer argument — the observability gap:**
> A self-hosted grid gives you **client-side numbers only**. Correlating a p99 spike with the database DTU, the App Service CPU, the Service Bus queue depth and the dependency failure rate is a *second* integration project. Azure Load Testing does that natively via App Components and Azure Monitor (doc 07).

---

## 2.3 Side-by-side comparison

Present this as the anchor slide of the section.

| Capability | Self-hosted JMeter grid | Azure Load Testing |
|---|---|---|
| **Time to first test** | Days to weeks (build the grid first) | **Minutes** — URL-based test needs no script at all |
| **Load generation infrastructure** | You provision, patch, scale, pay for idle | Fully managed; Microsoft-operated engines |
| **Scale-out model** | Manual worker orchestration | Set an engine-instance number; runs in parallel |
| **Max scale** | Whatever you've built (and tested) | Up to 400 engine instances per run, 1,000 concurrent (max, quota-gated) |
| **Idle cost** | You pay for VMs / node pools even between tests | **Zero** — billed on virtual user hours consumed |
| **JMeter/Locust runtime maintenance** | Yours (JMeter, JDK, Python, plugins, OS, CVEs) | Managed (JMeter 5.6.3/JDK 21, Locust 2.33.2/Python 3.9.19) |
| **Existing `.jmx` scripts** | Reusable | **Reusable — upload unchanged** |
| **No-script option for non-experts** | None | URL-based quick test |
| **Server-side metric correlation** | Custom integration project | Native — Azure Monitor / App Insights / Container Insights |
| **Pass/fail criteria as a quality gate** | Custom scripting | Built-in (client *and* server metrics), up to 50 criteria |
| **Run-over-run comparison / trends** | Build it yourself | Built-in comparison view |
| **CI/CD integration** | Custom scripting | Official GitHub Action + Azure DevOps task |
| **Secrets management** | Your problem | Key Vault + managed identity, first class |
| **Private/internal endpoint testing** | Yours to network | VNet injection (GA) |
| **Multi-region load origination** | Build a grid per region | `regionalLoadTestConfig` in one test |
| **Auto-stop on runaway errors** | Build it yourself | Built-in AutoStop |
| **RBAC & auditability** | DIY | Azure RBAC built-in roles + Azure Monitor diagnostics |
| **Encryption with customer-managed keys** | DIY | Supported (CMK via Key Vault) |
| **Data residency guarantee** | Your responsibility to design | Documented: data not stored/processed outside the deployment region |
| **AI-assisted result analysis** | None | Built-in actionable insights |
| **Who fixes it at 2am** | You | Microsoft |

---

## 2.4 The TCO conversation

Don't put fake numbers on a slide. Instead, put the **shape** of the cost on the slide and fill it in with the customer's own figures during the session. This is far more persuasive than a vendor-supplied ROI chart.

**Self-hosted annual cost model:**

```
  Compute for load generators (incl. idle time and over-provisioning)
+ Storage, networking, egress
+ Monitoring/observability tooling for the grid itself
+ Initial build effort            (engineer-days × loaded day rate)
+ Ongoing maintenance             (~0.2–0.5 FTE is a common real-world figure — ask them)
+ Upgrade/patch cycles            (JMeter, JDK, OS, plugins, CVE response)
+ Opportunity cost               (what those engineers could have built instead)
+ Incident cost of grid outages blocking releases
= Total cost of ownership
```

**Azure Load Testing annual cost model:**

```
  Virtual User Hours actually consumed  (see doc 12 for the billing formula)
+ (optional) VNet injection: load balancer + public IP + egress
= Total cost of ownership
```

**Live exercise to run in the room:**
1. Ask: *"How many engineer-days went into building your current performance test capability?"*
2. Ask: *"How much of an FTE keeps it running today?"*
3. Ask: *"How many hours per month do your load generators actually generate load?"* — the answer is usually **under 20**, against infrastructure that is paid for 730 hours a month.
4. Put both models side by side.

> **The line to land:** *"You are running a 24×7 platform to do a job that happens for 12 hours a month. And the engineers maintaining it are the same people you need for your modernisation programme."*

---

## 2.5 The counter-arguments (be ready — they will come)

Credibility comes from engaging with these rather than deflecting.

| Customer objection | Honest response |
|---|---|
| *"We've already built the grid; it's a sunk cost."* | Sunk cost is sunk. The question is the **next** 3 years of maintenance, not the last 3. Also ask what the grid *can't* do today (server-side correlation, CI/CD gating, private endpoint testing at scale). |
| *"We need k6 / Gatling / NeoLoad — not JMeter."* | Azure Load Testing supports **JMeter and Locust** only. If a team is standardised on another tool, be straight about it: either they migrate the scripts, or that team stays self-hosted. Locust (Python) is often an easy target for teams that dislike JMeter's XML. |
| *"Data sovereignty — we can't send test data outside our region."* | Documented: the service doesn't store or process customer data outside the region the instance is deployed in. Combine with CMK and VNet injection (doc 11). |
| *"Our APIs aren't reachable from the internet."* | VNet injection places engines **inside your VNet** — reaching private endpoints, internal load balancers and on-prem over ExpressRoute (doc 13). |
| *"We're multi-cloud / have on-prem workloads."* | The service generates load against **any** reachable HTTP endpoint, regardless of where the app is hosted. The target does not have to be in Azure. |
| *"Per-VUH pricing could get expensive at our scale."* | True at extreme continuous scale — so model it. Note the **AutoStop** and quota controls that prevent runaway spend, and that most regression-gate tests are small and short. Compare against the *fully loaded* self-host cost, not just VM cost. |
| *"We'd be locked in."* | Your `.jmx` and `locustfile.py` scripts are portable, industry-standard artefacts. The lock-in surface is the config YAML and the dashboards, not your test IP. |
| *"Can it really generate enough load?"* | Scale is horizontal across engine instances; documented guidance shows six-figure RPS is achievable. Size it with them (doc 03). |

---

## 2.6 How the demo *proves* this (not just asserts it)

Tie each demo moment back to this section — this is what makes the argument land rather than sound like a pitch.

| Demo moment | What it proves |
|---|---|
| Creating a URL-based test with **no script and no infrastructure**, running in ~3 minutes | Time-to-first-test collapses from weeks to minutes |
| Changing engine instances from 1 → 10 with a slider and re-running | Elastic scale with **no capacity planning and no idle cost** |
| The dashboard showing client metrics *and* App Service CPU *and* Application Insights side by side | The observability gap a self-hosted grid leaves open |
| A failing pass/fail criterion turning a PR check red | Load testing becomes a **gate**, not a project |
| Copilot generating a working Locust script from an `.http` file | The "we don't know JMeter" objection disappears |
| AI insights explaining *why* p95 spiked | Analysis effort — usually the biggest hidden cost — is reduced |
| Deleting the whole thing at the end | **No residual cost.** Try that with a VM fleet. |

> **Closing line for the section:** *"Nobody in this room gets promoted for running a JMeter cluster. They get promoted for shipping a payments platform that doesn't fall over. Let's spend your engineers on the second thing."*

---

## Reference links

- [What is Azure Load Testing?](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/overview-what-is-azure-load-testing)
- [Key concepts — test engines and scaling](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/concept-load-testing-concepts)
- [Configure for high-scale load](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/how-to-high-scale-load)
- [Azure Load Testing pricing](https://azure.microsoft.com/en-us/pricing/details/app-testing/)
- [Service limits and quotas](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/resource-limits-quotas-capacity)

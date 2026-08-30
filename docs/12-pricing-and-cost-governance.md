# 12 — Pricing & Cost Governance (NEW SECTION)

> **Section goal:** Explain the billing model clearly enough that the customer can model their own costs, and show the controls that prevent surprise bills. Procurement *will* ask, and if you can't answer, the deal stalls in finance rather than in engineering.
>
> ⚠️ **Do not quote per-VUH prices from these notes.** Rates vary by region and change over time. Open the [live pricing page](https://azure.microsoft.com/en-us/pricing/details/app-testing/) and the [Azure Pricing Calculator](https://azure.microsoft.com/pricing/calculator/) in the session.

**Duration:** 10 minutes · **Format:** Slides + live pricing page · **Audience:** Engineering leadership, FinOps, procurement

---

## 12.1 The billing model in one line

> **You are billed for Virtual User Hours (VUH) consumed. You are not billed for the test engine VMs, and you are not billed when no test is running.**

```
VUH = (virtual users) × (test duration in hours) × (engine instances)
```

**Indicative list rates** (verify on the live pricing page before quoting):

| Monthly VUH | Rate |
|---|---|
| First 10,000 VUH | **$0.15 / VUH** |
| Above 10,000 VUH | **$0.06 / VUH** |

> **The tier break matters more than it looks.** Above 10,000 VUH/month the rate drops by 60%. High-scale testing gets *cheaper per unit* — the exact opposite of the self-hosted model, where scale is where costs explode.

> ⚠️ **Free monthly allotment: do not quote a figure.** Public sources conflict on whether there is one and what it is. Check the live pricing page on the day, or say *"I'll confirm that in writing."* Guessing here is the kind of small error that costs you the room.

Contrast this explicitly with self-hosting, where you pay for VMs 730 hours a month to do maybe 12 hours of actual work.

---

## 12.2 Minimum billing rule — know this before you're asked

A **minimum per-test-run charge** applies (effective 1 March 2026):

1. A minimum of **10 virtual users per engine** is billed for the test run duration.
2. If the run is shorter than 10 minutes, a minimum of **10 VUs × 10 minutes × engine count** is billed.

Documented worked examples:

| Scenario | Actual VUH | Minimum VUH | **Charged** |
|---|---|---|---|
| 5 VUs × 1 engine × 3 hours | 15 | 30 | **30** |
| 5 VUs × 1 engine × 5 minutes | 0.83 | 1.67 | **1.67** |
| 500 VUs × 2 engines × 5 minutes | 83.33 | 3.33 | **83.33** (actual exceeds minimum) |

**Practical implication to give them:** very small, very short tests are billed at the floor. So don't design your PR gate as fifty separate 30-second micro-tests — consolidate into one short test. This is a genuinely useful piece of advice and shows you understand the model.

> ⚠️ Verify the **current free monthly VUH allotment** on the live pricing page — sources conflict, so confirm rather than assert. See §12.1.

> **Tie this to AutoStop.** The minimum-billing floor makes *many tiny runs* expensive; AutoStop (§7.9) makes *one runaway run* cheap. Together they define the cost-efficient shape of a test suite: few tests, right-sized, with a circuit breaker.

---

## 12.3 Other costs to disclose

Be complete here; an unmentioned cost discovered later damages trust more than the cost itself.

| Cost | When it applies |
|---|---|
| **Virtual user hours** | Always — the primary charge |
| **VNet injection: Azure Load Balancer + public IP** | Only when `subnetId` is used; deployed into *your* subscription and billed separately |
| **Network egress** | Standard Azure egress rates may apply to test traffic |
| **The system under test** | The environment you're testing costs money to run, and **autoscaling under load costs more** — this is often the larger number. Say so |
| **Application Insights / Log Analytics ingestion** | Server-side telemetry generated during a high-load test can be significant. Worth a sampling conversation |

> **Honest framing:** *"The load test itself is usually not the expensive part. Scaling your application to survive the test is. That's a good thing — you're discovering the cost of your capacity target in a controlled way, before you commit to it in production."*

---

## 12.4 Cost modelling — worked examples using *this repo's* configs

> **Presenter note:** every number below is reproducible live with the calculator in this repo. That is the point — you are not showing a slide, you are showing arithmetic the customer can re-run with their own rate.
>
> ```bash
> python tools/vuh_calculator.py --preset all
> python tools/vuh_calculator.py --vus 500 --engines 2 --minutes 20 --runs-per-month 30
> python tools/vuh_calculator.py --preset all --price 0.12 --currency "£"   # their EA rate
> ```

**Rates used below:** `$0.15/VUH` for the first 10,000 VUH per month, `$0.06/VUH` above that.
⚠️ **Verify before quoting** — and ask for their EA/CSP rate rather than using list price.

### Scenario A — PR performance gate (`loadtests/config/pr-gate.yaml`)

| | |
|---|---|
| Configuration | 25 VUs × 1 engine × 3 min |
| Frequency | ~40 PRs/week = 160 runs/month |
| **Raw** VUH per run | `25 × (3/60) = ` **1.25** |
| **Billed** VUH per run | `25 × (10/60) = ` **4.17** ⚠️ *minimum duration applied* |
| VUH per month | `4.17 × 160 = ` **667** |
| **Cost per month** | `667 × $0.15 = ` **≈ $100** |

> **The teaching moment is the 3.3× uplift.** The raw figure is 1.25 VUH; you are billed 4.17 because of the 10-minute floor. Nobody discovers this until the first invoice. Showing it *before* they ask is the single most trust-building thing in this section.

*Message: continuous performance validation on every single pull request costs about the same as one engineer's coffee budget.*

### Scenario B — Nightly regression suite (`loadtests/config/peak-load.yaml`)

| | |
|---|---|
| Configuration | 1,000 VUs (4 engines × 250 threads) × 15 min |
| Frequency | 20 working nights/month |
| Raw = billed VUH per run | `1,000 × (15/60) = ` **250** *(no floor — well above both minimums)* |
| VUH per month | `250 × 20 = ` **5,000** |
| **Cost per month** | `5,000 × $0.15 = ` **$750** |
| **Cost per year** | **$9,000** |

*Message: a full 1,000-user regression suite every working night, with zero infrastructure to own, for less than the annual cost of a single mid-range VM fleet.*

### Scenario C — Quarterly peak capacity test

| | |
|---|---|
| Configuration | 20,000 VUs × 80 engines × 2 hours |
| Frequency | 4× per year |
| VUH per run | `20,000 × 2 = ` **40,000** |
| Cost per run (tiered) | `10,000 × $0.15 + 30,000 × $0.06 = $1,500 + $1,800 = ` **$3,300** |
| **Cost per year** | `4 × $3,300 = ` **$13,200** |

> **Note the tier break doing real work here** — above 10,000 VUH/month the rate drops by 60%. High-scale testing is disproportionately cheap, which is exactly the opposite of the self-hosted model.

*Message: this is the workload you'd otherwise build and maintain a 100-VM fleet for — and that fleet would sit idle for the other 8,758 hours of the year.*

### Scenario D — What today's session costs

| | |
|---|---|
| Configuration | ~5 runs × 100 VUs × 1 engine × 5 min |
| VUH total | ≈ **42** |
| **Total cost** | **≈ $6** |

> **Use this.** *"Everything you've watched me do this morning — every test run, every dashboard — cost about six dollars. The barrier to starting is not the money."*

---

## 12.4a The annual TCO comparison — the slide procurement actually wants

All three routine scenarios combined (**A + B + C**):

| | Azure Load Testing | Self-hosted JMeter grid |
|---|---|---|
| Load generation compute | **$0** (included in VUH) | ~8 × D4s_v5 @ 730 h/mo ≈ **$14,000/yr** (idle 97% of the time) |
| Virtual user hours | **≈ $23,400/yr** (A+B+C) | n/a |
| Orchestration / controller VM | $0 | ≈ **$1,800/yr** |
| Storage, networking, load balancer | Minimal | ≈ **$1,200/yr** |
| Results store & dashboards | Included | InfluxDB/Grafana to build and run — ≈ **$2,400/yr** |
| Patching, upgrades, JMeter version drift | $0 | **0.25–0.5 FTE** ≈ **$30,000–60,000/yr** |
| Scaling to 20,000 users | Configuration change | Procurement exercise, weeks of lead time |
| Geo-distributed load | Built in | Multi-region fleet to build and maintain |
| **Indicative total** | **≈ $23,400/yr** | **≈ $50,000–80,000/yr** |

> **The honest framing:** *"The licence cost isn't the interesting number. The interesting number is the quarter of an engineer you get back, and the six weeks of lead time you don't spend when someone asks for a 20,000-user test."*

**Three caveats to state, because they will be checked:**
1. The FTE figure is the largest and most arguable line — ask them what they'd estimate rather than asserting it.
2. Both columns exclude the cost of the **system under test**, which is often larger than either (see §12.3).
3. The $23,400 is the simple sum of scenarios A+B+C. Because the tier break is applied to *combined* monthly usage, the real figure is somewhat **lower** — around $21,000. Erring high in your own favour is the right way round.

---

## 12.5 Cost controls

Show these as a defence-in-depth stack — it's the answer to "how do we stop a junior engineer accidentally spending £50,000?"

| Control | Layer | Effect |
|---|---|---|
| **AutoStop** | Test | Aborts a run when the error rate exceeds a threshold in a rolling window (default 90% / 60s). Stops you paying for a test that's failing because of a typo'd URL |
| **Engine instance quota** | Subscription | Hard cap on the maximum load anyone can generate |
| **Concurrent run quota** | Subscription | Caps parallel spend |
| **Max test duration limit** | Subscription | Prevents a forgotten 24-hour soak |
| **RBAC** | Resource | Only Load Test Contributors/Owners can run tests at all |
| **Azure Budgets + cost alerts** | Resource group / subscription | Financial alerting and (optionally) automated action |
| **Tagging** | Resource | Cost centre / team showback and chargeback |
| **Pipeline design** | Practice | Short strict PR gates; long tests on a schedule, not on every commit |
| **Resource lock** | Resource | Prevents accidental deletion of audit evidence |

---

## 12.6 Quotas — plan ahead

Defaults vary by subscription type; maxima are documented. Raising a quota is **free** via an Azure support request.

| Dimension | Default | Maximum |
|---|---|---|
| Engine instances per test run | 1–400 (varies) | 400 |
| Concurrent engine instances | 5–400 (varies) | 1,000 |
| Concurrent test runs | 5–50 (varies) | 1,000 |
| Test duration | 3–24 hours (varies) | 24 hours |

> **Advice worth giving:** *"If you're planning a 50,000-user market-open simulation for a go-live in six weeks, raise the quota request now. Discovering the cap at 2am on test night is a bad way to find out."*

---

## 12.7 The TCO slide (ties back to doc 02)

| | Self-hosted grid | Azure Load Testing |
|---|---|---|
| **Load generation infrastructure** | VMs/nodes, sized for peak, paid 24×7 | £0 — no VM charge |
| **Idle cost** | Full cost between tests | **Zero** |
| **Build effort** | Engineer-weeks, up front | ~Zero (create a resource) |
| **Maintenance** | Ongoing FTE fraction, forever | Zero |
| **Upgrade/patch cycle** | Yours (JMeter, JDK, Python, OS, CVEs) | Microsoft's |
| **Observability integration** | A project | Included |
| **CI/CD integration** | A project | Included |
| **Scaling to a one-off peak test** | Provision a fleet, then decommission it | Change a number in a YAML file |
| **Cost model** | Fixed / capex-like | **Consumption — pay for what you use** |

> **Closing line:** *"The question isn't 'how much does Azure Load Testing cost'. It's 'how much are you already spending to have a worse version of it'."*

---

## Reference links

- [Azure Load Testing pricing](https://azure.microsoft.com/en-us/pricing/details/app-testing/)
- [Azure Pricing Calculator](https://azure.microsoft.com/pricing/calculator/)
- [Service limits, quotas and capacity](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/resource-limits-quotas-capacity)
- [Azure Cost Management + Billing](https://learn.microsoft.com/en-us/azure/cost-management-billing/)

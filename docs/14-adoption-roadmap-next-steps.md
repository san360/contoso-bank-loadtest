# 14 — Adoption Roadmap & Next Steps (NEW SECTION)

> **Section goal:** Never end a demo without answering *"what happens Monday?"*. A demo that impresses but produces no next action is a wasted day. Leave the room with an owner, a date and a scope.

**Duration:** 10 minutes · **Format:** Discussion, whiteboard · **Audience:** All — but especially whoever holds the budget

---

## 14.1 Maturity model — ask them to place themselves

Put this on screen and ask the room to self-assess. It's more effective than telling them where they are.

| Level | Description | Typical symptoms |
|---|---|---|
| **0 — Reactive** | No load testing. Performance problems are found in production. | "We scale up when it gets slow." Incident-driven capacity decisions. |
| **1 — Project-based** | A specialist team runs a test before major releases. | Weeks of lead time. A PDF report nobody reads. Only for "big" releases. |
| **2 — Scheduled** | Automated tests run nightly/weekly in a test environment. | Results exist but aren't gating anything. Regressions are noticed late. |
| **3 — Gated** | Load tests run in CI/CD with pass/fail criteria. Regressions block merges. | Performance is a shared engineering responsibility. |
| **4 — Continuous & resilient** | Production-shaped, continuous validation. Autoscale and failover validated under load. Chaos + load combined. | Performance and resilience are proven, continuously, with evidence. |

Most financial services organisations are at **level 1**, believe they're at level 2, and need to reach **level 3** to satisfy operational resilience expectations.

---

## 14.2 A 90-day adoption plan

Present this as a concrete, low-risk proposal — not a transformation programme.

### Days 1–30: Prove it on one service
**Goal:** one real service under automated load test, with a documented baseline.

- [ ] Pick **one** important business service — high volume, well understood, ideally with a known performance concern. *Do not pick the hardest one.*
- [ ] Provision the Load Testing resource via **Terraform** into the correct landing zone (doc 05)
- [ ] Configure RBAC, diagnostics, tagging; decide on CMK and VNet injection with the security team (doc 11)
- [ ] Build the first test — start with a **URL-based test**, then move to JMeter/Locust for the real journey
- [ ] Configure **App Components** for server-side metrics (doc 07)
- [ ] Run it, establish and document a **baseline**: p95, p99, RPS ceiling, error rate
- [ ] Present findings to the service owner

**Success criterion:** *"We know the actual capacity of this service, with evidence, and we have at least one actionable finding."*

### Days 31–60: Automate it
**Goal:** the gate exists and is enforced.

- [ ] Move the test definition to **YAML in the repo** (doc 08)
- [ ] Add the **GitHub Actions / Azure DevOps** step to the pipeline
- [ ] Define **pass/fail criteria** from the baseline — agree them with the service owner
- [ ] Add branch protection so the check is required
- [ ] Add a **nightly regression run** with broader coverage
- [ ] Set up notification rules and result archival to long-term storage
- [ ] Deliberately introduce a regression to **prove the gate works** — then remove it

**Success criterion:** *"A performance regression cannot reach main without someone explicitly accepting it."*

### Days 61–90: Scale the practice
**Goal:** it's a platform capability, not a one-team experiment.

- [ ] Onboard 3–5 more services using the first one as a template
- [ ] Publish an internal **reference pattern**: repo layout, YAML template, criteria guidance, naming standards
- [ ] Enable **VS Code extension + Copilot** for developer self-service (docs 09, 10)
- [ ] Run one **high-scale capacity test** for a known peak event (month-end, campaign, market open)
- [ ] Establish a **trend dashboard** for leadership (portal comparison, or the Power BI sample from `azure-load-testing-samples`)
- [ ] Define the **rules of engagement**: which environments, who approves, what change windows
- [ ] Review cost and set budgets/quotas (doc 12)

**Success criterion:** *"A new team can add a performance gate to their service in a day, following an internal template."*

---

## 14.3 Who needs to be involved

| Role | What you need from them | When |
|---|---|---|
| **Platform / cloud engineering** | Landing zone placement, Terraform module, pipeline templates | Day 1 |
| **Security architecture** | Sign-off on CMK, RBAC model, data residency | Days 1–15 — **start early, this is the long pole** |
| **Network engineering** | Subnet for VNet injection, DNS, firewall sizing | Days 1–15 if private endpoints are in scope |
| **Service owner / product** | Which service, what "good" means, agreement on thresholds | Days 1–30 |
| **Performance engineering / QA** | Test scenarios, realistic traffic mix, test data | Throughout |
| **Development team** | Fixing what you find, owning the gate | Days 30+ |
| **FinOps** | Budgets, tagging, showback | Day 30 |
| **Risk / operational resilience** | Mapping tests to impact tolerances and evidence requirements | Day 60 |

> **Practical advice:** get security and network engaged in **week one**. In every bank, the technology works long before the approvals do.

---

## 14.4 Common adoption pitfalls (learn from others' mistakes)

| Pitfall | Avoid it by |
|---|---|
| Testing an environment nothing like production | Right-shape it, or be explicit that you're testing for *defects* not *capacity* |
| Unrealistic traffic mix (all users doing the same thing) | Model the real distribution; use split CSV data |
| No think time — accidental DoS | Add realistic pauses between user actions |
| Thresholds set so tight the gate is always red | Set from a measured baseline, not from a wish. Fix flaky criteria immediately |
| Nobody owns the results | Assign an owner per service; findings become backlog items with a sprint |
| Gate quietly disabled after it blocks a release | Provide a documented, recorded override path with an accountable approver |
| Testing only the happy path | Include error paths, timeouts and downstream failures |
| Ignoring third-party rate limits | Agree load testing with dependency providers before you start |
| Load testing shared environments unannounced | Publish and enforce rules of engagement |
| Treating it as a QA activity | Performance is a **design** concern; engage architects, not just testers |

---

## 14.5 Success metrics to agree now

Agree how you'll judge this in 90 days — before you start.

- Number of services with an automated performance gate
- Number of performance regressions **caught pre-production** (this is the headline number for leadership)
- Reduction in performance-related production incidents
- Mean time to identify a performance bottleneck (before vs after)
- Time from "we need a load test" to "we have results" (was: weeks; target: hours)
- Documented capacity headroom for the top N business services
- Evidence artefacts available for audit/regulatory reporting

---

## 14.6 Concrete asks to close the session with

Pick two or three; don't present a wish list.

1. **Named pilot service** and a service owner who agrees to it.
2. **A workshop date** (half day) with platform + security + network to agree landing zone, RBAC, CMK and networking design.
3. **Access to a subscription** for the pilot, with quota checked.
4. **A hands-on enablement session** for the pilot team (2–3 hours, using this demo repo as the teaching asset).
5. **A follow-up date** to review baseline findings — 30 days out.

> **Close with a question, not a summary:** *"Which service would you pick, and who owns it?"* If they can answer that in the room, the engagement has begun.

---

## 14.7 Follow-up material to send

Send within 24 hours while the demo is still fresh:

- [ ] This documentation set + the demo repository
- [ ] Recording of the session (if permitted)
- [ ] Links from [`16-reference-repos-and-links.md`](16-reference-repos-and-links.md)
- [ ] Written answers to anything you flagged as "let me confirm" — **especially pricing, SLA and compliance certifications**. Never guess at these live; always follow up in writing
- [ ] A one-page proposed pilot scope, with the 90-day plan tailored to their named service
- [ ] Trust Center / Service Trust Portal links for the security team

---

## 14.8 Longer-term horizon (the next conversation)

Seeds to plant for follow-on engagement:

- **Chaos engineering + load** — Azure Chaos Studio during a load test, to evidence operational resilience under severe-but-plausible scenarios (doc 13.11)
- **Performance budgets in architecture review** — every new service declares its SLO and ships with a load test from day one
- **Production-shaped continuous validation** — synthetic load in production during low-traffic windows
- **Capacity forecasting** — feeding load test results into capacity planning and cloud spend forecasting
- **Extending to the full Azure App Testing story** — Playwright Workspaces for end-to-end UI testing alongside load testing

---

## Reference links

- [Azure Load Testing documentation home](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/)
- [Well-Architected Framework — Performance Efficiency](https://learn.microsoft.com/en-us/azure/well-architected/performance-efficiency/)
- [Well-Architected Framework — Reliability testing strategy](https://learn.microsoft.com/en-us/azure/well-architected/reliability/testing-strategy)
- [Azure Chaos Studio](https://learn.microsoft.com/en-us/azure/chaos-studio/)
- [Microsoft Service Trust Portal](https://servicetrust.microsoft.com/)

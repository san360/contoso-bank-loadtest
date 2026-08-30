# Azure Load Testing — Customer Demo Agenda & Runbook

> **Audience:** Financial services customer (engineering leadership, platform/DevOps, SRE, QA/performance engineering, security & compliance).
> **Goal:** Prove that load testing is a *first-class engineering discipline*, and that Azure Load Testing is the fastest, safest and cheapest way to operate it — without building and running your own JMeter grid.

---

## 1. Agenda mapping

The agenda below reflects **your revised list plus the review feedback** received after the first draft. Docs 02, 07, 11, 12, 13, 14, **17**, 18 and 19 were added because the original agenda didn't cover them.

| # | Agenda item | Doc in this repo | Status |
|---|---|---|---|
| 1 | Introduction to load testing | [`01-introduction-to-load-testing.md`](01-introduction-to-load-testing.md) | Kept |
| 2 | ALT overview & key use cases | [`02-why-load-testing-as-a-service.md`](02-why-load-testing-as-a-service.md) | **NEW** — build-vs-buy / TCO vs self-hosted JMeter. Your explicit ask: *"prove why we need load testing as a service"* |
| 3 | How it works — architecture & core components | [`03-architecture-how-it-works.md`](03-architecture-how-it-works.md) | Kept, expanded |
| 4 | Service feature overview & portal walkthrough | [`04-portal-feature-walkthrough.md`](04-portal-feature-walkthrough.md) | **Reframed** — portal as *learning* surface, not operating surface |
| 5 | Test creation options: URL-based, JMeter, Locust | [`06-demo-create-run-inspect.md`](06-demo-create-run-inspect.md) | Promoted to its own agenda item |
| 6 | **Deploying & operating as code — Terraform + Azure CLI** | [`05-deployment-and-operations-as-code.md`](05-deployment-and-operations-as-code.md) | **REWRITTEN** — Terraform-first after the Bicep-to-Terraform migration |
| 7 | Demo: create, run & inspect a load test | [`06-demo-create-run-inspect.md`](06-demo-create-run-inspect.md) | **Moved before CI/CD**; ➕ the *false-green* demo (§6.4a) |
| 8 | Analyse results: dashboard, bottlenecks, fail **and abort** criteria | [`07-analyzing-results-and-bottlenecks.md`](07-analyzing-results-and-bottlenecks.md) | **NEW** section, now ➕ run status taxonomy (§7.2) and AutoStop (§7.9) |
| 9 | **Portal-free operations & LGTM/Grafana integration** | [`17-portal-free-operations-and-observability.md`](17-portal-free-operations-and-observability.md) | **NEW** — direct response to review feedback |
| 10 | Integration with CI/CD pipelines | [`08-cicd-integration.md`](08-cicd-integration.md) | Kept; ➕ the three-outcome gate guard (§8.6a) |
| 11 | Visual Studio Code extension | [`09-vscode-extension.md`](09-vscode-extension.md) | Kept |
| 12 | GitHub Copilot — script authoring **and migration** | [`10-github-copilot-integration.md`](10-github-copilot-integration.md) | Kept, expanded (3 Copilot surfaces) |
| 13 | Pricing & cost governance | [`12-pricing-and-cost-governance.md`](12-pricing-and-cost-governance.md) | **NEW**; ➕ worked examples + `tools/vuh_calculator.py` |
| — | Security, compliance & network isolation | [`11-security-compliance-network.md`](11-security-compliance-network.md) | **NEW** — *mandatory* for a regulated customer |
| — | Advanced scenarios | [`13-advanced-scenarios.md`](13-advanced-scenarios.md) | **NEW** — private endpoints, OAuth/mTLS, high scale, multi-region |
| — | Adoption roadmap & next steps | [`14-adoption-roadmap-next-steps.md`](14-adoption-roadmap-next-steps.md) | **NEW** — never end without a "what happens Monday" slide |
| — | Demo application spec | [`15-demo-application-contoso-bank.md`](15-demo-application-contoso-bank.md) | Reference |
| — | Reference repos, links & **facts to verify** | [`16-reference-repos-and-links.md`](16-reference-repos-and-links.md) | Reference |
| — | Recording walkthrough & test catalog | [`19-recording-and-test-catalog.md`](19-recording-and-test-catalog.md) | Reference |

### What changed after review — the five deltas

| Feedback | Where it landed |
|---|---|
| *Replace Bicep with Terraform (recommended internally) and Azure CLI (portal not permitted in higher environments)* | Doc 05 rewritten Terraform-first; real HCL in [`../infra`](../infra); Azure CLI cookbook in doc 17 |
| *Result inspection without portal access; LGTM/Grafana dashboards* | **New doc 17.** Note: ALT client-side metrics are **not** in Azure Monitor — the section is honest about which paths actually work |
| *Practical cost calculation examples* | Doc 12 §12.4 fully worked from this repo's own configs, plus [`../tools/vuh_calculator.py`](../tools/vuh_calculator.py) |
| *Differentiate Error vs Failed vs Completed; a criteria-less run looks successful* | Doc 07 §7.2 (`status` vs `testResult`, the `NOT_APPLICABLE` trap), the live demo in doc 06 §6.4a, and the pipeline guard in doc 08 §8.6a |
| *Cover abort criteria alongside fail criteria* | Doc 07 §7.9 |

### Why move the demo earlier
CI/CD integration is much more persuasive *after* the audience has seen a test run and a dashboard. Recommended flow: **see it work → understand the results → then automate it.**

### ⚠️ Product naming note
As of 2025/2026 Azure Load Testing sits under the **Azure App Testing** umbrella (alongside Playwright Workspaces). Docs have moved from `learn.microsoft.com/azure/load-testing/` → `learn.microsoft.com/azure/app-testing/load-testing/`. The ARM resource type is unchanged: `Microsoft.LoadTestService/loadTests`. Use current naming in front of the customer.

---

## 2. Recommended session flow (half day, ~3h45 with breaks)

| Time | Section | Format | Doc |
|---|---|---|---|
| 00:00 – 00:20 | Introduction to load testing — types, terminology, where it fits in SDLC | Slides | 01 |
| 00:20 – 00:35 | **Why load testing *as a service*** — the self-hosted JMeter tax | Slides + TCO table | 02 |
| 00:35 – 00:55 | How it works — architecture, engines, scale model | Slides + whiteboard | 03 |
| 00:55 – 01:15 | Service feature overview — portal walkthrough *(learning surface)* | Live portal | 04 |
| 01:15 – 01:25 | ☕ Break | | |
| 01:25 – 01:55 | **Demo 1: create, run & inspect a test** (Contoso Bank API) | Live | 06 |
| 01:55 – 02:00 | 🚨 **Demo 2: the false green** — a 100%-failing test that reports Completed | Live | 06 §6.4a |
| 02:00 – 02:25 | **Demo 3: find the bottleneck** — server-side metrics + AI insights; status taxonomy; fail vs abort criteria | Live | 07 |
| 02:25 – 02:45 | **Deploying & operating as code** — Terraform walkthrough + `az load` | Code + terminal | 05 |
| 02:45 – 03:05 | **Portal-free operations & LGTM/Grafana** | Live terminal + slides | 17 |
| 03:05 – 03:25 | **Demo 4: CI/CD gate** — GitHub Actions blocks a bad PR, and rejects a criteria-less one | Live | 08 |
| 03:25 – 03:40 | **Demo 5: VS Code + GitHub Copilot** — generate a Locust test from an API spec | Live | 09, 10 |
| 03:40 – 03:55 | Security, compliance & network isolation for FSI | Slides | 11 |
| 03:55 – 04:05 | Pricing & cost governance — worked examples, live calculator | Slides + terminal | 12 |
| 04:05 – 04:15 | Roadmap & next steps | Discussion | 14 |

**If you only have 60 minutes:** 02 (why), 06 + §6.4a (demo + false green), 07 (bottleneck), 17 (portal-free), 08 (CI/CD). Drop the rest.

**If the audience is platform/SRE-heavy:** lead with 05 and 17, and treat the portal walkthrough as a two-minute aside.

---

## 3. The demo flavours and ready-made tests

The demos are deliberately layered so the customer sees **the same test getting progressively more automated**. Same app, same scenario, four entry points, and every version-controlled YAML test definition that `azd up` now creates automatically:

| # | Flavour | Persona | Time to value shown | Doc |
|---|---|---|---|---|
| **A** | **UI / no-code** — URL-based quick test in the portal, zero scripting | QA analyst, product owner, anyone | ~3 minutes from resource creation to running test | 06 |
| **B** | **Code** — JMeter `.jmx` + Locust `locustfile.py` + `config.yaml`, run via `az load` CLI. **No portal at any point** | Performance engineer, platform engineer | Reuse existing JMeter IP; version-controlled tests; works in locked-down subscriptions | 06, 17 |
| **C** | **Pipeline** — GitHub Actions with pass/fail gates on a PR, plus a pre-flight that rejects criteria-less tests | Platform / DevOps engineer | Performance regression blocked before merge | 08 |
| **D** | **GitHub Copilot** — VS Code extension generates a Locust script from an `.http` file / Postman collection; portal AI insights explain the failure | Every developer | From "no test" to "running test" in one prompt | 09, 10 |

> **The narrative arc:** *Anyone can start (A) → engineers get full power, with or without the portal (B) → it runs itself on every change (C) → AI removes the last bit of friction (D).*

---

## 4. The demo application — Contoso Bank API

A purpose-built financial-services API lives in [`../src`](../src) with IaC in [`../infra`](../infra). See [`15-demo-application-contoso-bank.md`](15-demo-application-contoso-bank.md).

Why purpose-built rather than an off-the-shelf sample:
- **Domain resonance** — endpoints are `/accounts`, `/balance`, `/transfers`, `/statements`, not "todo items".
- **Deliberate, controllable bottlenecks** — we can *turn a bottleneck on and off with a feature flag*, so the "before / after fix" story is repeatable and never fails live.
- **Deterministic** — no dependency on Azure OpenAI quota, Cosmos RU throttling luck, or a 20-minute deploy.
- **One-command deploy** — `azd up`.

---

## 5. Pre-demo checklist (do this the day before)

- [ ] `azd up` completed; app URL responds to `GET /health`
- [ ] Azure Load Testing resource created in the **same region** as the app
- [ ] App Components configured on the test (App Service + Application Insights) — this is what makes Demo 2 work
- [ ] Baseline test run completed and **saved**, so you have something to compare against live
- [ ] A second "bad" run recorded in case live scaling misbehaves — **always have a recorded fallback**
- [ ] GitHub repo forked with `AZURE_CREDENTIALS` / OIDC federated credential configured
- [ ] VS Code: Azure Load Testing extension + GitHub Copilot signed in; Python + Locust installed locally
- [ ] Portal AI insights: run "Generate insights" once beforehand (first generation can take a moment)
- [ ] Browser: signed into the correct tenant, unnecessary tabs closed, zoom at 125%+
- [ ] Check your **VUH quota** and concurrent engine quota in the demo subscription (see doc 12)

---

## 6. Known live-demo risks & mitigations

| Risk | Mitigation |
|---|---|
| Test takes too long to start (engine provisioning) | Start the long test *before* the break; narrate architecture while it provisions |
| Autoscale doesn't kick in during the demo window | Pre-configure aggressive scale rules; have a recorded run ready |
| VUH quota exhausted mid-demo | Check quota beforehand; keep engine counts modest (2–5) for live runs |
| Portal UI changed since rehearsal | Rehearse within 48h of the session |
| Customer asks about a feature you're unsure of | Use doc 16 links; commit to a follow-up rather than guessing |

# 10 — Integration with GitHub Copilot

> **Section goal:** Show that AI removes the two most expensive parts of a performance testing practice — **writing the tests** and **interpreting the results**. There are three distinct Copilot surfaces here; present them as a chain, not as three unrelated features.

**Duration:** 10 minutes (combined with doc 09) · **Format:** Live · **Audience:** Developers, engineering leadership

---

## 10.1 The three Copilot surfaces

| # | Surface | Where | What it does | Maturity |
|---|---|---|---|---|
| **1** | **Copilot in VS Code (via the Load Testing extension)** | VS Code | Generates and refines Locust scripts from `.http` files, Postman/Insomnia collections, or a URL | Documented, demo-ready (doc 09) |
| **2** | **AI actionable insights** | Azure portal, test run dashboard | Correlates client + server metrics and explains latency spikes, throughput drops and backend bottlenecks | Documented, demo-ready |
| **3** | **Copilot + Azure MCP Server** | VS Code / any MCP-capable agent | Natural-language interaction with Azure resources — including load testing resources — via the Model Context Protocol | ⚠️ Verify current tool coverage before demoing |

```mermaid
graph LR
    A["I have an API<br/>but no load test"] -->|Copilot in VS Code| B["A working Locust script"]
    B -->|Azure Load Testing| C["A test run with results"]
    C -->|AI actionable insights| D["An explanation of<br/>what broke and why"]
    D -->|Copilot in the IDE| E["A code fix"]
    E -->|CI/CD gate| F["Proof the fix worked,<br/>enforced forever"]
```

**The narrative:** *"AI closes the loop. From 'we have no test' to 'we have an enforced performance gate' without a specialist in the chain."*

---

## 10.2 Surface 1 — Script generation in VS Code

Fully covered in [doc 09](09-vscode-extension.md). The headline points for this section:

- Generate a Locust script from a **Postman collection** the API team already maintains — no new artefact to create and keep in sync.
- Refine in natural language: *"randomize the payloads"*, *"add think time"*, *"weight balance checks 10× higher than transfers"*, *"add a debug mode"*.
- **Copilot is doing the tedious part** — request sequencing, correlation of IDs between calls, parameterisation, realistic data generation. That's genuinely most of the effort in authoring a load test.

### Extra prompts worth demonstrating (finance flavoured)
- *"Add a task that simulates a corporate customer downloading a 12-month statement — this should be 5% of traffic."*
- *"Extract the transaction ID from the transfer response and use it in a follow-up status check."*
- *"Add assertions that the transfer response returns HTTP 201 and contains a `transactionId`."*
- *"Read account numbers from accounts.csv instead of hard-coding them."*

> Correlation and data-driven parameterisation are exactly the things that make people give up on load testing. Showing Copilot handle them is the point of this demo.

---

## 10.3 Surface 2 — AI actionable insights in the portal

This is the one that lands with engineering leadership, because **analysis effort is the hidden cost that kills performance testing programmes**.

### Demo steps
1. Open a **failed** test run from the Contoso Bank demo (the one with the bottleneck enabled).
2. Scroll to **AI summary and insights** → click **Generate insights** (first visit only).
3. Read the summary aloud.
4. Click **View detailed insights**:
   - what went wrong,
   - the supporting evidence (which metrics, at which time),
   - recommended next steps.
5. Cross-check it against the server-side metrics you looked at manually in doc 07 — *"it found in seconds what we walked through in five minutes."*

### Be honest about the caveat
The documentation itself states that AI-generated insights might not always be accurate and should be validated against your own telemetry. **Say this out loud.** With a financial services audience, volunteering the limitation buys more credibility than the feature does.

### Make it work well
- Insights are **much better when App Components are configured** — without server-side metrics the AI only sees half the picture. This is a practical adoption tip, not just a demo tip.

> **Positioning line:** *"This does not replace your performance engineer. It means a developer with no performance background gets a credible first hypothesis in ten seconds instead of escalating to a specialist queue. That is the difference between a finding that gets fixed and a report that gets filed."*

---

## 10.4 Surface 3 — Copilot + Azure MCP Server

**Repo:** <https://github.com/microsoft/mcp> (the `Azure.Mcp.Server` component; supersedes the archived `Azure/azure-mcp`)
**VS Code extension ID:** `ms-azuretools.vscode-azure-mcp-server`
**Install options:** `npx @azure/mcp@latest`, `dnx Azure.Mcp`, or `uvx msmcp-azure`. Works with VS Code, Visual Studio, IntelliJ, Eclipse and other MCP-capable agents.

The Azure MCP Server connects AI agents to Azure services over the Model Context Protocol, letting Copilot query and operate your Azure estate in natural language.

### ⚠️ Verify before you demo this
Dedicated, named load-testing tools were **not confirmed** in the MCP server's documented tool list at the time of writing. Generic Azure Resource Manager operations (listing resources, reading properties) do work.

**Recommended approach:**
- **Rehearse it.** If load-testing-specific tools are present in your installed version, demo them — it's a strong moment.
- If not, demo the generic ARM capability (*"list my load testing resources and show the most recent test runs"*) and be clear that this is resource-management-level, not test-authoring-level.
- **Do not promise capability you haven't seen work.** With this audience, one over-claim costs you the room.

### Prompts to try in rehearsal
- *"List the Azure Load Testing resources in my subscription."*
- *"Show me the resource group and region for the load testing resource named alt-contoso-demo."*
- *"What Application Insights resources exist in rg-contoso-loadtest?"*

### The broader story (safe to make regardless)
Even without load-testing-specific MCP tools, the message stands:
> *"The direction of travel is that your engineers describe intent — 'load test the transfer endpoint at 2,000 users and tell me if it breaches our SLO' — and the tooling assembles the test, runs it, and explains the result. The VS Code extension and portal insights are that pattern shipping today."*

---

## 10.5 Copilot beyond the tooling — the fix loop

Worth 60 seconds, because it completes the story and it's the part with the clearest developer ROI:

1. Load test fails; AI insights say the bottleneck is repeated database queries on the transfer path.
2. In VS Code, ask Copilot: *"This endpoint queries the account balance on every request. Add a short-lived in-memory cache with a 30-second TTL and make it configurable."*
3. Copilot writes the fix.
4. Push → the **CI/CD performance gate** (doc 08) re-runs the same test → green.

> **The line to land:** *"AI wrote the test, AI explained the failure, AI wrote the fix, and an automated gate proved it. The human decided what 'good' means — the p95 threshold — and that's exactly where a human should be spending their time."*

---

## 10.6 Governance questions this audience will ask

Be ready for these; a financial services customer will not let AI features pass unchallenged.

| Question | Suggested response |
|---|---|
| *"Does our code/test data leave our environment?"* | Copilot's data handling is governed by your GitHub Copilot Business/Enterprise agreement and settings. Direct them to their existing Copilot governance position — don't improvise. |
| *"Can we trust AI-generated tests?"* | Treat them as a first draft under human pull-request review, exactly like AI-generated application code. The generated script is plain, readable Python. |
| *"Can we trust AI insights?"* | No — trust the evidence it points at. The docs say to validate against your own telemetry, and the insight always cites the metrics behind it. |
| *"Is AI making changes to our Azure resources?"* | In the demo, no. MCP-based agents act with the permissions you grant — apply the same RBAC and least-privilege discipline as any other identity. |
| *"Is this deterministic enough for a regulated change process?"* | The *test* is deterministic — it's a versioned script and a versioned YAML config in Git. Only the authoring assistance is AI. Make this distinction clearly; it usually resolves the concern. |

---

## Reference links

- [Analyze test results using actionable insights](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/how-to-analyze-test-results-using-actionable-insights)
- [Create and run load tests from Visual Studio Code](https://learn.microsoft.com/en-us/azure/app-testing/load-testing/quickstart-create-run-load-tests-from-visual-studio-code)
- [Azure MCP Server (microsoft/mcp)](https://github.com/microsoft/mcp)
- [GitHub Copilot documentation](https://docs.github.com/copilot)
- [GitHub Copilot Trust Center](https://copilot.github.trust.page/)

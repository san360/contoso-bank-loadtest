"use strict";

const $ = (id) => document.getElementById(id);
const storageKey = "azure-load-cli-demo-config-v1";
let defaults = {};
let config = {};
let actions = [];
let token = "";
let plan = null;
let busy = false;
let watching = false;
let watchVersion = 0;
let lastEvidenceJob = null;
let resources = [];
let runs = [];
let serverDefinitions = [];

function notify(message, error = false) {
  const element = $(error ? "error" : "notice");
  element.textContent = message;
  element.hidden = !message;
}

function handle(action) {
  return () => Promise.resolve().then(action).catch((error) => notify(error.message, true));
}

async function api(path, body) {
  const options = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Demo-Token": token },
    body: JSON.stringify(body),
  };
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
  return data;
}

function freshRunId() {
  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d+Z$/, "").replace("T", "-");
  return `cli-${stamp}-${crypto.getRandomValues(new Uint16Array(1))[0].toString(16)}`;
}

function save() {
  localStorage.setItem(storageKey, JSON.stringify(config));
}

function runKey(value = config) {
  return JSON.stringify([value.subscription, value.resourceGroup, value.loadTestResource, value.testId, value.runId]);
}

function fillInputs() {
  document.querySelectorAll("[data-config]").forEach((input) => {
    input.value = config[input.dataset.config] || "";
  });
  $("component-description").textContent = config.serverResourceName
    ? `${config.serverResourceName} | ${config.serverResourceType} | ${config.serverResourceKind || "no kind"}`
    : "Inspect this resource to resolve its name/type before attaching it to the test.";
  const tenant = config.tenant && /^[A-Za-z0-9.-]+$/.test(config.tenant) ? ` --tenant ${config.tenant}` : "";
  $("login-instructions").textContent = `az login${tenant}\naz extension add --name load`;
}

function updateExecute() {
  $("execute").disabled = busy || !plan || plan.executed || (Boolean(plan.confirmation) && !$("confirmation").checked);
}

function invalidate() {
  plan = null;
  $("script-download").hidden = true;
  $("copy-command").disabled = true;
  $("confirmation-row").hidden = true;
  $("command-label").textContent = "Configuration changed - preview a step again";
  $("run-summary").hidden = true;
  $("metric-panel").hidden = true;
  $("client-overview").hidden = true;
  updateExecute();
}

function stopWatching() {
  watching = false;
  watchVersion += 1;
  $("watch-run").textContent = "Watch selected run";
}

function switchTab(name) {
  document.querySelectorAll(".tab").forEach((tab) => {
    const active = tab.dataset.tab === name;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  document.querySelectorAll(".stage").forEach((stage) => {
    stage.hidden = stage.id !== `tab-${name}`;
  });
}

function renderCommand() {
  if (!plan) return;
  const shell = $("shell").value;
  $("command").textContent = plan.commands.map((item) => item[shell]).join("\n\n");
  $("script-download").href = `/api/plans/${plan.id}/script?shell=${shell}`;
}

async function previewAction(action, duringWatch = false) {
  if (busy) throw new Error("Wait for the active command before previewing another.");
  if (!duringWatch) stopWatching();
  notify("", true);
  const scope = JSON.stringify(config);
  busy = true;
  updateExecute();
  let next;
  try {
    next = await api("/api/preview", { action, config });
    if (JSON.stringify(config) !== scope) {
      throw new Error("Configuration changed while preparing the preview. Preview this step again.");
    }
  } finally {
    busy = false;
    updateExecute();
  }
  plan = next;
  $("command-label").textContent = plan.label;
  $("command-description").textContent = plan.description;
  $("plan-note").textContent = plan.note || "";
  $("confirmation").checked = false;
  $("confirmation-row").hidden = !plan.confirmation;
  $("confirmation-text").textContent = plan.confirmation;
  $("script-download").hidden = false;
  $("copy-command").disabled = false;
  $("job-state").textContent = "PREVIEW";
  $("job-state").className = "pill";
  document.querySelectorAll("[data-action]").forEach((button) => {
    button.classList.toggle("selected", button.dataset.action === action);
  });
  renderCommand();
  updateExecute();
  return next;
}

function table(headers, rows) {
  const element = document.createElement("table");
  const head = document.createElement("thead");
  const headerRow = document.createElement("tr");
  headers.forEach((label) => {
    const cell = document.createElement("th");
    cell.textContent = label;
    headerRow.append(cell);
  });
  head.append(headerRow);
  const body = document.createElement("tbody");
  rows.forEach((values) => {
    const row = document.createElement("tr");
    values.forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = value === null || value === undefined ? "-" : String(value);
      row.append(cell);
    });
    body.append(row);
  });
  element.append(head, body);
  return element;
}

function arrayOf(data) {
  if (Array.isArray(data)) return data;
  if (data && Array.isArray(data.value)) return data.value;
  return [];
}

function options(select, items, placeholder, label, value) {
  select.replaceChildren(new Option(placeholder, ""));
  items.forEach((item, index) => select.add(new Option(label(item), value ? value(item) : String(index))));
}

function fillFromResult(job) {
  for (const entry of job.commands) {
    if (entry.artifact === "context.json" && job.state === "succeeded") {
      $("account-status").textContent = "CLI subscription explicitly selected. Check the signed-in account to inspect the active tenant.";
      continue;
    }
    const data = entry.data;
    if (!data) continue;
    if (entry.artifact === "subscriptions.json") {
      const list = $("subscription-list");
      list.replaceChildren();
      arrayOf(data).forEach((item) => list.append(new Option(item.name, item.id)));
      notify(`Found ${arrayOf(data).length} subscriptions. Select the subscription ID field to choose one.`);
    } else if (entry.artifact === "account.json") {
      $("account-status").textContent = `${data.name} | ${data.id} | tenant ${data.tenantId} | ${data.state}`;
      if (!config.subscription && data.id) config.subscription = data.id;
      if (config.subscription === data.id && !config.tenant) config.tenant = data.tenantId || "";
      if (config.subscription && config.subscription !== data.id) {
        notify("The active CLI subscription differs from the configured target. Load commands will be blocked until you explicitly select that subscription or use an isolated CLI profile.");
      }
    } else if (entry.artifact === "resources.json") {
      resources = arrayOf(data);
      options($("resource-picker"), resources, "Select an existing resource", (item) => `${item.name} (${item.resourceGroup})`);
      notify(`Found ${resources.length} Load Testing resources. Choose one on the left.`);
    } else if (entry.artifact === "runs.json") {
      runs = arrayOf(data);
      options($("run-picker"), runs, "Select an execution", (item) => `${item.testRunId} | ${item.status}`);
      notify(`Found ${runs.length} runs for this test.`);
    } else if (entry.artifact === "component.json") {
      config.serverResourceName = data.name || "";
      config.serverResourceType = data.type || "";
      config.serverResourceKind = data.kind || "";
    } else if (entry.artifact === "server-definitions.json") {
      serverDefinitions = arrayOf(data);
      options($("server-metric-picker"), serverDefinitions, "Select a metric definition",
        (item) => `${item.name.value} (${item.unit}; ${item.primaryAggregationType})`);
    } else if (entry.artifact === "client-definitions.json") {
      $("client-metric-list").replaceChildren();
      arrayOf(data).forEach((item) => {
        const name = typeof item === "string" ? item : (item.name?.value || item.name || item.metricName);
        if (name) $("client-metric-list").append(new Option(name, name));
      });
    } else if (entry.artifact === "run.json") {
      if (data.startDateTime) config.startTime = data.startDateTime;
      if (data.endDateTime) config.endTime = data.endDateTime;
      renderRun(data, entry.verdict);
    }
  }
  fillInputs();
  save();
}

function renderRun(run, verdict) {
  const panel = $("run-summary");
  panel.replaceChildren();
  const heading = document.createElement("div");
  heading.className = "section-heading";
  const title = document.createElement("h2");
  title.textContent = run.testRunId || config.runId;
  const status = document.createElement("span");
  status.className = `pill ${verdict?.kind || ""}`;
  status.textContent = verdict?.label || run.status || "UNKNOWN";
  heading.append(title, status);
  const explanation = document.createElement("p");
  explanation.className = "hint";
  explanation.textContent = verdict?.detail || "Read execution status and fail criteria together.";
  panel.append(heading, explanation);
  const statistics = Object.entries(run.testRunStatistics || {});
  if (statistics.length) {
    const wrapper = document.createElement("div");
    wrapper.className = "table-scroll";
    wrapper.append(table(["Request", "Samples", "Mean ms", "p95 ms", "p99 ms", "Error %", "Requests/s"],
      statistics.map(([name, item]) => [name, item.sampleCount, item.meanResTime, item.pct2ResTime,
        item.pct3ResTime, item.errorPct, typeof item.throughput === "number" ? item.throughput.toFixed(2) : "-"])));
    panel.append(wrapper);
  }
  panel.hidden = false;
}

function renderMetrics(rows) {
  $("metric-panel").hidden = false;
  $("metric-heading").textContent = rows[0]?.metric || "Metric time series";
  $("metric-count").textContent = `${rows.length} returned datapoints`;
  $("metric-table").replaceChildren(table(["Timestamp", "Namespace", "Metric", "Aggregation", "Value", "Unit"],
    rows.slice(0, 200).map((row) => [row.timestamp, row.namespace, row.metric, row.aggregation, row.value, row.unit])));
  const svg = $("metric-chart");
  drawMetricChart(svg, rows);
}

function firstMetricSeries(rows) {
  if (!rows.length) return [];
  const first = rows[0];
  return rows.filter((row) => row.metric === first.metric && row.dimensions === first.dimensions
    && row.aggregation === first.aggregation && row.value !== null && row.value !== ""
    && Number.isFinite(Number(row.value)) && Number.isFinite(Date.parse(row.timestamp)))
    .sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp));
}

function drawMetricChart(svg, rows) {
  svg.replaceChildren();
  const points = firstMetricSeries(rows);
  if (!points.length) return;
  const values = points.map((row) => Number(row.value));
  const low = Math.min(0, ...values);
  const high = Math.max(...values, low + 1);
  const times = points.map((row) => Date.parse(row.timestamp));
  const minTime = Math.min(...times);
  const timeRange = Math.max(1, Math.max(...times) - minTime);
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", points.map((point, index) => `${index ? "L" : "M"}${15 + ((times[index] - minTime) / timeRange) * 670},${125 - ((Number(point.value) - low) / (high - low)) * 110}`).join(" "));
  path.setAttribute("stroke", "#075bd8");
  path.setAttribute("stroke-width", "2.5");
  path.setAttribute("fill", "none");
  const marker = document.createElementNS("http://www.w3.org/2000/svg", "circle");
  marker.setAttribute("cx", String(15 + ((times.at(-1) - minTime) / timeRange) * 670));
  marker.setAttribute("cy", String(125 - ((values.at(-1) - low) / (high - low)) * 110));
  marker.setAttribute("r", "3");
  marker.setAttribute("fill", "#075bd8");
  svg.append(path, marker);
}

function prepareClientOverview(snapshot) {
  $("client-overview").hidden = false;
  $("client-metric-count").textContent = "LOADING";
  $("client-metric-scope").textContent = `${snapshot.loadTestResource} | Test: ${snapshot.testId} | Run: ${snapshot.runId}`;
  $("client-metric-status").textContent = "Fetching run metadata, metric definitions and all client metric values...";
  $("client-metric-grid").replaceChildren();
  $("client-metric-downloads").replaceChildren();
}

function renderClientOverview(job, snapshot) {
  prepareClientOverview(snapshot);
  if (job.state !== "succeeded") {
    $("client-metric-count").textContent = "UNAVAILABLE";
    $("client-metric-status").textContent = job.error || "The client metrics request did not complete.";
    return;
  }
  const definitionEntry = job.commands.find((entry) => entry.artifact === "client-definitions.json");
  const metricEntry = job.commands.find((entry) => entry.artifact === "all-client-metrics.json");
  const definitions = new Map();
  arrayOf(definitionEntry?.data).forEach((item) => {
    const name = typeof item === "string" ? item : (item.name?.value || item.name);
    if (typeof name === "string" && name) definitions.set(name, typeof item === "string" ? {} : item);
  });
  const payload = metricEntry?.data;
  if (payload && typeof payload === "object" && !Array.isArray(payload)) {
    Object.keys(payload).forEach((name) => { if (!definitions.has(name)) definitions.set(name, {}); });
  }
  $("client-metric-count").textContent = `${definitions.size} METRICS`;
  $("client-metric-status").textContent = definitions.size
    ? "All available client metrics for this run. Charts show the first returned series; expand details for every datapoint."
    : "No client metric definitions or series were returned for this run.";
  const rows = metricEntry?.metricRows || [];
  definitions.forEach((definition, name) => {
    const metricRows = rows.filter((row) => row.metric === name);
    const points = firstMetricSeries(metricRows);
    const latest = points.at(-1);
    const card = document.createElement("article");
    card.className = "client-metric-card";
    card.dataset.clientMetric = name;
    const heading = document.createElement("h3");
    heading.textContent = name.replace(/([a-z])([A-Z])/g, "$1 $2");
    const description = document.createElement("p");
    description.className = "hint";
    description.textContent = definition.description || name;
    const value = document.createElement("div");
    value.className = latest ? "metric-value" : "metric-empty";
    value.textContent = latest ? Number(latest.value).toLocaleString(undefined, { maximumFractionDigits: 3 })
      : "No datapoints returned";
    const unit = document.createElement("span");
    unit.className = "metric-unit";
    unit.textContent = latest?.unit || definition.unit || "";
    value.append(unit);
    const caption = document.createElement("p");
    caption.className = "hint";
    caption.textContent = latest ? `Latest bucket: ${latest.timestamp} | ${metricRows.length} datapoints`
      : "Missing data is not assumed to be zero. Check run state, ingestion delay and retention.";
    card.append(heading, description, value, caption);
    if (latest) {
      const chart = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      chart.classList.add("metric-sparkline");
      chart.setAttribute("viewBox", "0 0 700 140");
      chart.setAttribute("role", "img");
      chart.setAttribute("aria-label", `${name} first returned series over time`);
      drawMetricChart(chart, metricRows);
      card.append(chart);
    }
    if (metricRows.length) {
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = `View datapoints (${metricRows.length})`;
      const wrapper = document.createElement("div");
      wrapper.className = "table-scroll";
      wrapper.append(table(["Timestamp", "Value", "Unit", "Dimensions"],
        metricRows.slice(0, 200).map((row) => [row.timestamp, row.value, row.unit, row.dimensions || "Run-wide"])));
      details.append(summary, wrapper);
      if (metricRows.length > 200) {
        const note = document.createElement("p");
        note.className = "hint";
        note.textContent = "Showing the first 200 datapoints; the JSON/CSV downloads contain all returned datapoints.";
        details.append(note);
      }
      card.append(details);
    }
    $("client-metric-grid").append(card);
  });
  for (const name of ["all-client-metrics.json", "all-client-metrics.csv"]) {
    const file = job.files.find((item) => item.name === name);
    if (!file) continue;
    const link = document.createElement("a");
    link.className = "file-link";
    link.href = file.url;
    link.textContent = name.endsWith(".json") ? "Download all metrics JSON" : "Download all datapoints CSV";
    $("client-metric-downloads").append(link);
  }
}

function filesFor(job, target) {
  const wrapper = document.createElement("div");
  wrapper.className = "file-list";
  if (job.files.length) {
    const bundle = document.createElement("a");
    bundle.className = "file-link bundle-link";
    bundle.href = job.bundleUrl;
    bundle.textContent = job.state === "succeeded" ? "Download this evidence bundle (.zip)" : "Download partial evidence (.zip)";
    wrapper.append(bundle);
  }
  job.files.forEach((file) => {
    const link = document.createElement("a");
    link.className = "file-link";
    link.href = file.url;
    link.textContent = `${file.name} (${(file.bytes / 1024).toFixed(1)} KB)`;
    wrapper.append(link);
  });
  const location = document.createElement("p");
  location.className = "hint";
  location.textContent = `Local evidence folder: ${job.artifactDirectory}`;
  target.append(wrapper, location);
}

function renderJob(job) {
  $("job-state").textContent = job.state.toUpperCase();
  $("job-state").className = `pill ${job.state === "failed" ? "bad" : job.state === "succeeded" ? "good" : ""}`;
  const chunks = job.commands.map((entry) => [
    `$ ${entry.command}`,
    `Exit ${entry.exitCode} | ${entry.durationSeconds}s`,
    entry.stdout || "(no stdout)",
    entry.stderr ? `stderr:\n${entry.stderr}` : "",
  ].filter(Boolean).join("\n"));
  if (job.currentCommand) chunks.push(`Running: ${job.currentCommand}`);
  if (job.error) chunks.push(`ERROR: ${job.error}`);
  if (job.warnings.length) chunks.push(`WARNINGS:\n${job.warnings.join("\n")}`);
  $("output").textContent = chunks.join("\n\n") || "Queued...";
  $("downloads").replaceChildren();
  if (!["queued", "running"].includes(job.state)) filesFor(job, $("downloads"));
}

async function executePlan(duringWatch = false) {
  if (!plan || busy) return null;
  if (!duringWatch) stopWatching();
  const selected = plan;
  const scope = JSON.stringify(config);
  const selectedRun = runKey();
  busy = true;
  updateExecute();
  notify("", true);
  if (selected.action === "all-client-metrics") {
    prepareClientOverview(JSON.parse(scope));
    $("metric-panel").hidden = true;
  }
  try {
    const started = await api("/api/jobs", { planId: selected.id, confirmed: $("confirmation").checked });
    let job;
    do {
      job = await api(`/api/jobs/${started.id}`);
      renderJob(job);
      if (["queued", "running"].includes(job.state)) await new Promise((resolve) => setTimeout(resolve, 1000));
    } while (["queued", "running"].includes(job.state));
    if (job.action === "collect-evidence") lastEvidenceJob = { job, key: selectedRun };
    if (JSON.stringify(config) === scope) {
      if (job.action !== "all-client-metrics" || job.state === "succeeded") fillFromResult(job);
      if (job.action === "all-client-metrics") {
        renderClientOverview(job, JSON.parse(scope));
        if (!$("tab-metrics").hidden) $("client-overview").scrollIntoView({ behavior: "smooth", block: "start" });
      } else job.commands.forEach((entry) => { if (entry.metricRows) renderMetrics(entry.metricRows); });
    } else notify("These results belong to the previous configuration; automatic field and metric updates were skipped.");
    if (job.error) notify(job.error, true);
    if (job.warnings.length) notify(job.warnings.join(" "));
    return job;
  } finally {
    busy = false;
    if (plan?.id === selected.id) plan.executed = true;
    updateExecute();
  }
}

async function watchRun() {
  if (watching) {
    stopWatching();
    notify("Stopped local polling. The Azure run was not cancelled.");
    return;
  }
  if (busy) throw new Error("Wait for the active command before watching.");
  watching = true;
  const version = ++watchVersion;
  const deadline = Date.now() + 45 * 60 * 1000;
  $("watch-run").textContent = "Stop watching (run continues)";
  try {
    while (watching && version === watchVersion && Date.now() < deadline) {
      await previewAction("show-run", true);
      const job = await executePlan(true);
      if (!job || job.state === "failed") break;
      const run = job.commands.find((entry) => entry.artifact === "run.json");
      if (run?.verdict && run.verdict.kind !== "pending") break;
      await new Promise((resolve) => setTimeout(resolve, 10000));
    }
    if (Date.now() >= deadline) notify("Local watch timed out. The Azure run was not cancelled.");
  } finally {
    if (version === watchVersion) stopWatching();
  }
}

async function bootstrap() {
  const data = await api("/api/bootstrap");
  token = data.token;
  defaults = data.defaults;
  config = { ...defaults };
  try {
    const stored = JSON.parse(localStorage.getItem(storageKey) || "{}");
    if (!stored || Array.isArray(stored) || typeof stored !== "object") throw new Error("Invalid saved configuration.");
    for (const key of Object.keys(defaults)) {
      if (typeof stored[key] === "string") config[key] = stored[key];
    }
  } catch (error) {
    notify(`Saved browser configuration could not be loaded: ${error.message}`, true);
  }
  if (!config.runId) config.runId = freshRunId();
  actions = data.actions;
  actions.forEach((action) => {
    const button = document.createElement("button");
    button.dataset.action = action.id;
    if (action.group === "setup") {
      button.className = "text-button";
      button.textContent = action.label;
      $("setup-actions").append(button);
    } else {
      button.className = "action-card";
      const symbol = document.createElement("span");
      symbol.className = "action-symbol";
      symbol.textContent = action.confirmation ? "+" : ">";
      symbol.setAttribute("aria-hidden", "true");
      const content = document.createElement("span");
      const label = document.createElement("strong");
      label.textContent = action.label;
      const description = document.createElement("small");
      description.textContent = action.description;
      content.append(label, description);
      button.append(symbol, content);
      $(`actions-${action.group}`).append(button);
    }
    button.addEventListener("click", handle(() => previewAction(action.id)));
  });
  fillInputs();
  if (!data.cliInstalled) notify("Azure CLI is missing. Install it and the load extension before executing commands.", true);

  document.querySelectorAll("[data-config]").forEach((input) => {
    input.addEventListener("input", () => {
      stopWatching();
      config[input.dataset.config] = input.value.trim();
      if (input.dataset.config === "tenant") {
        const tenant = config.tenant && /^[A-Za-z0-9.-]+$/.test(config.tenant) ? ` --tenant ${config.tenant}` : "";
        $("login-instructions").textContent = `az login${tenant}\naz extension add --name load`;
      }
      if (["subscription", "resourceGroup", "loadTestResource", "testId", "runId"].includes(input.dataset.config)) {
        config.startTime = ""; config.endTime = "";
        $("start-time").value = ""; $("end-time").value = "";
        if (input.dataset.config !== "runId") {
          runs = [];
          options($("run-picker"), [], "List runs for the selected test/resource", () => "");
        }
      }
      if (input.dataset.config === "serverResourceId") {
        for (const key of ["serverResourceName", "serverResourceType", "serverResourceKind",
          "serverMetric", "serverMetricId", "serverNamespace"]) config[key] = "";
        serverDefinitions = [];
        options($("server-metric-picker"), [], "Discover server metrics first", () => "");
        $("component-description").textContent = "Inspect this resource again before attaching it.";
        $("server-metric").value = ""; $("server-namespace").value = "";
      }
      save();
      invalidate();
    });
  });
  document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => switchTab(tab.dataset.tab)));
  $("resource-picker").addEventListener("change", () => {
    const item = resources[Number($("resource-picker").value)];
    if ($("resource-picker").value === "" || !item) return;
    stopWatching();
    config.resourceGroup = item.resourceGroup;
    config.loadTestResource = item.name;
    config.startTime = ""; config.endTime = "";
    runs = [];
    options($("run-picker"), [], "List runs for this resource", () => "");
    save(); fillInputs(); invalidate();
  });
  $("run-picker").addEventListener("change", () => {
    const item = runs[Number($("run-picker").value)];
    if ($("run-picker").value === "" || !item) return;
    stopWatching();
    config.runId = item.testRunId;
    config.startTime = item.startDateTime || "";
    config.endTime = item.endDateTime || "";
    save(); fillInputs(); invalidate();
  });
  $("server-metric-picker").addEventListener("change", () => {
    const item = serverDefinitions[Number($("server-metric-picker").value)];
    if ($("server-metric-picker").value === "" || !item) return;
    config.serverMetric = item.name.value;
    config.serverMetricId = item.id;
    config.serverNamespace = item.namespace || config.serverResourceType;
    config.serverAggregation = item.primaryAggregationType;
    save(); fillInputs(); invalidate();
  });
  $("new-run").addEventListener("click", () => {
    stopWatching();
    config.runId = freshRunId(); config.startTime = ""; config.endTime = "";
    save(); fillInputs(); invalidate();
  });
  $("reset-config").addEventListener("click", () => {
    stopWatching();
    config = { ...defaults, runId: freshRunId() };
    resources = []; runs = []; serverDefinitions = [];
    options($("resource-picker"), [], "Find resources, then select one", () => "");
    options($("run-picker"), [], "List runs to select an execution", () => "");
    options($("server-metric-picker"), [], "Discover server metrics first", () => "");
    save(); fillInputs(); invalidate();
    notify("Browser configuration reset. Downloaded artifacts were not deleted.");
  });
  $("confirmation").addEventListener("change", updateExecute);
  $("load-run-list").addEventListener("click", handle(() => previewAction("list-runs")));
  $("shell").addEventListener("change", renderCommand);
  $("execute").addEventListener("click", handle(() => executePlan()));
  $("watch-run").addEventListener("click", handle(watchRun));
  $("copy-command").addEventListener("click", handle(async () => {
    await navigator.clipboard.writeText($("command").textContent);
    notify("Command copied.");
  }));
  $("copy-migration").addEventListener("click", handle(async () => {
    await navigator.clipboard.writeText("Use the jmx-to-locust skill to convert loadtests/jmeter/contoso-bank.jmx into a new Locust test. Inventory its behavior first, preserve correlation, data, assertions and load shape, document gaps, and verify parity. Do not overwrite the existing Locust demo.");
    notify("Migration prompt copied. Paste it into your coding agent.");
  }));
  $("copy-analysis").addEventListener("click", handle(async () => {
    let task;
    if (lastEvidenceJob?.key === runKey()) {
      task = `analyze the downloaded evidence in "${lastEvidenceJob.job.artifactDirectory}"`;
    } else {
      if (!config.subscription || !config.resourceGroup || !config.loadTestResource || !config.runId) {
        throw new Error("Configure the subscription, resource group, load resource and run ID first.");
      }
      task = `download and analyze evidence for run "${config.runId}" in Load Testing resource "${config.loadTestResource}", resource group "${config.resourceGroup}", subscription "${config.subscription}". Save downloads in an ignored local evidence directory`;
    }
    await navigator.clipboard.writeText(`Use the analyze-load-test-artifacts skill to ${task}. Summarize issues with file/line evidence, distinguish observations from hypotheses, and flag missing data. Do not send raw artifacts to an external service.`);
    notify("Analysis prompt copied. Paste it into your coding agent.");
  }));
  $("saved-downloads").addEventListener("click", handle(async () => {
    const jobs = await api("/api/artifacts");
    $("saved-files").replaceChildren();
    if (!jobs.length) $("saved-files").textContent = "No saved downloads yet.";
    jobs.forEach((job) => {
      const section = document.createElement("section");
      section.className = "saved-job";
      const heading = document.createElement("h3");
      heading.textContent = `${job.action} | ${job.createdAt} | ${job.state}`;
      section.append(heading);
      filesFor(job, section);
      $("saved-files").append(section);
    });
  }));
}

bootstrap().catch((error) => notify(`Could not initialize the local app: ${error.message}`, true));

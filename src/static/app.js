const state = {
  accounts: [],
  config: null,
  presetLegend: null
};

const $ = (id) => document.getElementById(id);

function formatMoney(value, currency) {
  return new Intl.NumberFormat("en-GB", { style: "currency", currency }).format(value);
}

function formatElapsed(ms) {
  return `${Math.round(ms).toLocaleString("en-GB")} ms`;
}

function elapsedClass(ms) {
  if (ms > 1000) return "slow";
  if (ms >= 500) return "medium";
  return "fast";
}

function modeFromConfig(config) {
  return config.enable_cache && !config.enable_transfer_lock ? "OPTIMIZED" : "BOTTLENECKED";
}

function formatConfigValue(value) {
  if (typeof value === "boolean") return value ? "true" : "false";
  if (value === null || value === undefined) return "Not set by preset";
  return String(value);
}

function setBusy(button, busy) {
  button.disabled = busy;
  button.dataset.originalText ||= button.textContent;
  button.textContent = busy ? "Working..." : button.dataset.originalText;
}

async function timedFetch(url, options = {}) {
  const started = performance.now();
  const response = await fetch(url, options);
  const elapsed = performance.now() - started;
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const message = data?.error || data?.detail || response.statusText;
    const error = new Error(message);
    error.elapsed = elapsed;
    error.status = response.status;
    error.data = data;
    throw error;
  }
  return { data, elapsed, status: response.status };
}

function renderShell(targetId, title, elapsed, body, data) {
  $(targetId).innerHTML = `
    <article class="result-card">
      <div class="result-title">
        <h3>${title}</h3>
        <span class="elapsed ${elapsedClass(elapsed)}">${formatElapsed(elapsed)}</span>
      </div>
      ${body}
      <details>
        <summary>Raw JSON</summary>
        <pre>${escapeHtml(JSON.stringify(data, null, 2))}</pre>
      </details>
    </article>`;
}

function renderError(targetId, title, error) {
  const elapsed = error.elapsed ?? 0;
  renderShell(
    targetId,
    title,
    elapsed,
    `<p class="error">Request failed${error.status ? ` (${error.status})` : ""}: ${escapeHtml(error.message)}</p>`,
    error.data || { error: error.message }
  );
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderPresetValueCell(item, preset) {
  const isSet = item.set_by_preset?.[preset];
  if (!isSet) {
    return `<span class="muted">Unchanged by preset</span>`;
  }

  const value = item.preset_values[preset];
  const isCurrent = state.config && state.config[item.setting] === value;
  const currentText = isCurrent ? ` <span class="current-marker">current</span>` : "";
  const currentClass = isCurrent ? " current-value" : "";
  return `<code class="${currentClass}">${escapeHtml(formatConfigValue(value))}</code>${currentText}`;
}

function renderModeLegend() {
  if (!state.presetLegend) return;

  const mode = state.config ? modeFromConfig(state.config) : "BOTTLENECKED";
  $("mode-legend-summary").textContent = `What does ${mode} mean?`;

  const rows = state.presetLegend.settings.map((item) => `
    <tr>
      <td><code>${escapeHtml(item.setting)}</code></td>
      <td>${escapeHtml(item.meaning)}</td>
      <td>${renderPresetValueCell(item, "bottlenecked")}</td>
      <td>${renderPresetValueCell(item, "optimized")}</td>
      <td>${escapeHtml(item.impact)}</td>
    </tr>`).join("");

  $("mode-legend-table").innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Setting</th>
          <th>Plain-English meaning</th>
          <th>Value in bottlenecked</th>
          <th>Value in optimized</th>
          <th>Endpoint / journey degraded and symptom under load</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function selectedAccountId() {
  return $("account-select").value;
}

function accountCurrency(accountId) {
  return state.accounts.find((account) => account.account_id === accountId)?.currency || "GBP";
}

function accountOption(account) {
  return `${account.account_id} — ${account.customer_name} (${account.currency})`;
}

function populateAccountSelects(accounts) {
  const options = accounts.map((account) => `<option value="${account.account_id}">${escapeHtml(accountOption(account))}</option>`).join("");
  $("account-select").innerHTML = options;
  $("from-account-select").innerHTML = options;
  $("to-account-select").innerHTML = options;
  if (accounts.length > 1) {
    $("to-account-select").selectedIndex = 1;
  }
}

function renderAccounts(result) {
  const rows = result.data.items.slice(0, 10).map((account) => `
    <tr>
      <td>${escapeHtml(account.account_id)}</td>
      <td>${escapeHtml(account.customer_name)}</td>
      <td>${escapeHtml(account.sort_code)}</td>
      <td>${formatMoney(account.balance_minor / 100, account.currency)}</td>
    </tr>`).join("");
  renderShell(
    "accounts-result",
    `Loaded ${result.data.items.length} of ${result.data.total} accounts`,
    result.elapsed,
    `<table>
      <thead><tr><th>Account</th><th>Customer</th><th>Sort code</th><th>Seed balance</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`,
    result.data
  );
}

async function loadAccounts() {
  const button = $("load-accounts-button");
  setBusy(button, true);
  try {
    const result = await timedFetch("/api/accounts?limit=50&offset=0");
    state.accounts = result.data.items;
    populateAccountSelects(state.accounts);
    renderAccounts(result);
  } catch (error) {
    renderError("accounts-result", "Account list", error);
  } finally {
    setBusy(button, false);
  }
}

async function viewBalance() {
  const button = $("view-balance-button");
  setBusy(button, true);
  try {
    const result = await timedFetch(`/api/accounts/${encodeURIComponent(selectedAccountId())}/balance`);
    renderShell(
      "balance-result",
      "Balance",
      result.elapsed,
      `<div class="summary-grid">
        <div class="metric"><span>Account</span><strong>${escapeHtml(result.data.account_id)}</strong></div>
        <div class="metric"><span>Customer</span><strong>${escapeHtml(result.data.customer_name)}</strong></div>
        <div class="metric"><span>Available</span><strong>${formatMoney(result.data.available_balance, result.data.currency)}</strong></div>
        <div class="metric"><span>Source</span><strong>${escapeHtml(result.data.source)}</strong></div>
      </div>`,
      result.data
    );
  } catch (error) {
    renderError("balance-result", "Balance", error);
  } finally {
    setBusy(button, false);
  }
}

async function viewTransactions() {
  const button = $("view-transactions-button");
  setBusy(button, true);
  try {
    const pageSize = Math.max(1, Math.min(50, Number($("page-size-input").value) || 20));
    $("page-size-input").value = pageSize;
    const result = await timedFetch(`/api/accounts/${encodeURIComponent(selectedAccountId())}/transactions?page_size=${pageSize}`);
    const rows = result.data.items.map((item) => `
      <tr>
        <td>${escapeHtml(item.booked_at.slice(0, 10))}</td>
        <td>${escapeHtml(item.description)}</td>
        <td>${formatMoney(item.amount_minor / 100, item.currency)}</td>
        <td>${escapeHtml(item.status)}</td>
      </tr>`).join("");
    renderShell(
      "transactions-result",
      `${result.data.count} transactions (${result.data.query_pattern})`,
      result.elapsed,
      `<table>
        <thead><tr><th>Date</th><th>Description</th><th>Amount</th><th>Status</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`,
      result.data
    );
  } catch (error) {
    renderError("transactions-result", "Transactions", error);
  } finally {
    setBusy(button, false);
  }
}

async function makeTransfer() {
  const button = $("make-transfer-button");
  setBusy(button, true);
  try {
    const amount = Number($("amount-input").value);
    const payload = {
      from_account: $("from-account-select").value,
      to_account: $("to-account-select").value,
      amount_minor: Math.round(amount * 100),
      currency: accountCurrency($("from-account-select").value),
      reference: $("reference-input").value
    };
    if (!payload.from_account || !payload.to_account || payload.from_account === payload.to_account) {
      throw new Error("Choose two different accounts before making a transfer.");
    }
    if (!Number.isFinite(amount) || payload.amount_minor <= 0) {
      throw new Error("Enter a positive transfer amount.");
    }

    const created = await timedFetch("/api/transfers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const transactionId = created.data.transaction_id;
    const fetched = await timedFetch(`/api/transfers/${encodeURIComponent(transactionId)}`);
    const body = `
      <div class="summary-grid">
        <div class="metric"><span>Created in</span><strong class="elapsed ${elapsedClass(created.elapsed)}">${formatElapsed(created.elapsed)}</strong></div>
        <div class="metric"><span>Status lookup in</span><strong class="elapsed ${elapsedClass(fetched.elapsed)}">${formatElapsed(fetched.elapsed)}</strong></div>
        <div class="metric"><span>Transaction id</span><strong>${escapeHtml(transactionId)}</strong></div>
        <div class="metric"><span>Status</span><strong>${escapeHtml(fetched.data.status)}</strong></div>
        <div class="metric"><span>Amount</span><strong>${formatMoney(fetched.data.amount, fetched.data.currency)}</strong></div>
        <div class="metric"><span>Reference</span><strong>${escapeHtml(fetched.data.reference || "None")}</strong></div>
      </div>`;
    renderShell(
      "transfer-result",
      "Transfer created and checked",
      created.elapsed + fetched.elapsed,
      body,
      { create: created.data, status: fetched.data }
    );
  } catch (error) {
    renderError("transfer-result", "Transfer", error);
  } finally {
    setBusy(button, false);
  }
}

async function generateStatement() {
  const button = $("generate-statement-button");
  setBusy(button, true);
  try {
    const result = await timedFetch(`/api/statements/${encodeURIComponent(selectedAccountId())}?months=3`);
    const summary = result.data.summary;
    const currency = accountCurrency(result.data.account_id);
    renderShell(
      "statement-result",
      "Three-month statement",
      result.elapsed,
      `<div class="summary-grid">
        <div class="metric"><span>Customer</span><strong>${escapeHtml(result.data.customer_name)}</strong></div>
        <div class="metric"><span>Closing balance</span><strong>${formatMoney(summary.closing_balance, currency)}</strong></div>
        <div class="metric"><span>Credits</span><strong>${formatMoney(summary.total_credits, currency)}</strong></div>
        <div class="metric"><span>Debits</span><strong>${formatMoney(summary.total_debits, currency)}</strong></div>
        <div class="metric"><span>Transactions</span><strong>${summary.transaction_count}</strong></div>
        <div class="metric"><span>Document hash</span><strong>${escapeHtml(result.data.document_hash)}</strong></div>
      </div>`,
      result.data
    );
  } catch (error) {
    renderError("statement-result", "Statement", error);
  } finally {
    setBusy(button, false);
  }
}

function updateModeBadge(config) {
  state.config = config;
  const mode = modeFromConfig(config);
  const badge = $("mode-badge");
  badge.textContent = mode;
  badge.className = `mode-badge ${mode.toLowerCase()}`;
  $("mode-subtitle").textContent = mode === "OPTIMIZED"
    ? "After-the-fix comparison: the same code with the planted defects switched off, no redeploy or restart."
    : "Demo starting state: six realistic performance defects are switched on so load tests expose them.";
  renderModeLegend();
}

async function loadPresetLegend() {
  const result = await timedFetch("/admin/presets");
  state.presetLegend = result.data;
  renderModeLegend();
}

async function loadInitialMode() {
  const result = await timedFetch("/admin/config");
  updateModeBadge(result.data.config);
}

async function refreshMode(targetId = "mode-result") {
  const result = await timedFetch("/admin/config");
  updateModeBadge(result.data.config);
  renderShell(
    targetId,
    "Mode refreshed",
    result.elapsed,
    `<div class="summary-grid">
      <div class="metric"><span>Mode</span><strong>${modeFromConfig(result.data.config)}</strong></div>
      <div class="metric"><span>Requests served</span><strong>${result.data.runtime.requests_served}</strong></div>
      <div class="metric"><span>DB queries</span><strong>${result.data.runtime.db_queries}</strong></div>
      <div class="metric"><span>Peak DB wait</span><strong>${result.data.runtime.db_peak_pool_wait_ms} ms</strong></div>
    </div>`,
    result.data
  );
  return result;
}

async function clickRefreshMode() {
  const button = $("refresh-mode-button");
  setBusy(button, true);
  try {
    await refreshMode();
  } catch (error) {
    renderError("mode-result", "Mode", error);
  } finally {
    setBusy(button, false);
  }
}

async function setMode(preset) {
  const button = preset === "optimized" ? $("set-optimized-button") : $("set-bottlenecked-button");
  setBusy(button, true);
  try {
    const postResult = await timedFetch("/admin/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ preset })
    });
    const getResult = await timedFetch("/admin/config");
    updateModeBadge(getResult.data.config);
    renderShell(
      "mode-result",
      `Set ${preset}`,
      postResult.elapsed + getResult.elapsed,
      `<div class="summary-grid">
        <div class="metric"><span>POST /admin/config</span><strong class="elapsed ${elapsedClass(postResult.elapsed)}">${formatElapsed(postResult.elapsed)}</strong></div>
        <div class="metric"><span>GET /admin/config</span><strong class="elapsed ${elapsedClass(getResult.elapsed)}">${formatElapsed(getResult.elapsed)}</strong></div>
        <div class="metric"><span>Mode</span><strong>${modeFromConfig(getResult.data.config)}</strong></div>
      </div>`,
      { setConfig: postResult.data, refreshedConfig: getResult.data }
    );
  } catch (error) {
    renderError("mode-result", `Set ${preset}`, error);
  } finally {
    setBusy(button, false);
  }
}

function wireEvents() {
  $("load-accounts-button").addEventListener("click", loadAccounts);
  $("view-balance-button").addEventListener("click", viewBalance);
  $("view-transactions-button").addEventListener("click", viewTransactions);
  $("make-transfer-button").addEventListener("click", makeTransfer);
  $("generate-statement-button").addEventListener("click", generateStatement);
  $("refresh-mode-button").addEventListener("click", clickRefreshMode);
  $("set-bottlenecked-button").addEventListener("click", () => setMode("bottlenecked"));
  $("set-optimized-button").addEventListener("click", () => setMode("optimized"));
}

wireEvents();
loadPresetLegend().catch((error) => {
  $("mode-legend-table").innerHTML = `<p class="error">Mode legend failed to load: ${escapeHtml(error.message)}</p>`;
});
loadInitialMode().catch((error) => {
  $("mode-subtitle").textContent = `Mode failed to load: ${error.message}`;
});
loadAccounts();

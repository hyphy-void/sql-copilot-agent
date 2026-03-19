const API_BASE = window.location.origin;
const MAX_SUGGESTIONS = 10;

let editor;
let activeSuggestions = [];
let activeSuggestionReasons = {};
let activeSuggestionContext = null;
let inlinePreview = null;
let refreshTimer = null;
let requestSeq = 0;
let activeProposal = null;
let activeProposalSummary = null;
let lastRetryAction = null;
let activeSuggestionStrategy = "rule_only";
let schemaOverviewItems = [];
let schemaColumnCache = {};
let schemaExpandedTables = new Set();
let schemaLoadingTables = new Set();
let schemaLoadErrors = {};

function getUseLlmEnabled() {
  const useLlmInput = document.getElementById("use-llm");
  return !!useLlmInput?.checked;
}

function setStatus(message, level = "info", retryAction = null) {
  const statusEl = document.getElementById("status");
  const retryBtn = document.getElementById("status-retry");
  if (!statusEl || !retryBtn) {
    return;
  }

  statusEl.textContent = message;
  statusEl.className = `status-${level}`;

  if (typeof retryAction === "function") {
    lastRetryAction = retryAction;
    retryBtn.classList.add("visible");
  } else {
    lastRetryAction = null;
    retryBtn.classList.remove("visible");
  }
}

function setButtonLoading(buttonId, isLoading, loadingText = "Loading...") {
  const button = document.getElementById(buttonId);
  if (!button) {
    return;
  }

  if (!button.dataset.defaultLabel) {
    button.dataset.defaultLabel = button.textContent || "";
  }

  if (isLoading) {
    button.disabled = true;
    button.dataset.loading = "1";
    button.textContent = loadingText;
  } else {
    button.disabled = false;
    button.dataset.loading = "0";
    button.textContent = button.dataset.defaultLabel;
  }
}

function getIsButtonLoading(buttonId) {
  const button = document.getElementById(buttonId);
  return !!button && button.dataset.loading === "1";
}

function switchPanel(target) {
  const suggestionPanel = document.getElementById("suggestions-panel");
  const chatPanel = document.getElementById("chat-panel");
  const suggestionTab = document.getElementById("tab-suggestions");
  const chatTab = document.getElementById("tab-chat");
  if (!suggestionPanel || !chatPanel || !suggestionTab || !chatTab) {
    return;
  }

  if (target === "chat") {
    suggestionPanel.classList.add("hidden");
    chatPanel.classList.remove("hidden");
    suggestionTab.classList.remove("active");
    chatTab.classList.add("active");
    suggestionTab.setAttribute("aria-selected", "false");
    chatTab.setAttribute("aria-selected", "true");
    return;
  }

  suggestionPanel.classList.remove("hidden");
  chatPanel.classList.add("hidden");
  suggestionTab.classList.add("active");
  chatTab.classList.remove("active");
  suggestionTab.setAttribute("aria-selected", "true");
  chatTab.setAttribute("aria-selected", "false");
}

function appendChatMessage(role, text) {
  const stream = document.getElementById("chat-stream");
  if (!stream) {
    return;
  }
  const item = document.createElement("div");
  item.className = `chat-msg ${role === "user" ? "user" : "assistant"}`;

  const roleEl = document.createElement("div");
  roleEl.className = "chat-role";
  roleEl.textContent = role === "user" ? "你 You" : "助手 Agent";

  const body = document.createElement("div");
  body.textContent = text;

  item.appendChild(roleEl);
  item.appendChild(body);
  stream.appendChild(item);
  stream.scrollTop = stream.scrollHeight;
}

function readApiError(payload, fallback) {
  if (!payload) {
    return fallback;
  }

  if (typeof payload.detail === "string" && payload.detail.trim()) {
    return payload.detail;
  }

  if (typeof payload.message === "string" && payload.message.trim()) {
    return payload.message;
  }

  return fallback;
}

function computeProposalSummary(proposal) {
  const operations = proposal?.operations || [];
  const allowedCount = operations.filter((item) => item.allowed).length;
  const blockedCount = operations.length - allowedCount;
  const failedPreflightChecks = getFailedPreflightChecks(proposal);

  if (failedPreflightChecks.length > 0) {
    return {
      allowed_count: allowedCount,
      blocked_count: blockedCount,
      next_action_hint:
        "存在失败的预检查项，请先处理后再审批执行 / Failed preflight checks detected. Resolve them before execution.",
    };
  }

  if (blockedCount > 0) {
    return {
      allowed_count: allowedCount,
      blocked_count: blockedCount,
      next_action_hint:
        "存在阻断语句，请先修改请求再重新生成提案 / Blocked operations detected. Revise your request first.",
    };
  }

  return {
    allowed_count: allowedCount,
    blocked_count: blockedCount,
    next_action_hint:
      "确认语句后输入 APPROVE 再执行 / Review statements, then type APPROVE before execution.",
  };
}

function getProposalPreflightChecks(proposal) {
  return Array.isArray(proposal?.preflight_checks) ? proposal.preflight_checks : [];
}

function getFailedPreflightChecks(proposal) {
  return getProposalPreflightChecks(proposal).filter(
    (item) => String(item?.status || "").toLowerCase() === "fail"
  );
}

function hasBlockingApprovalIssue(proposal) {
  return !!proposal && (!!proposal.has_blocking_risk || getFailedPreflightChecks(proposal).length > 0);
}

function buildProposalNotes(proposal) {
  const merged = [];
  const seen = new Set();

  const pushNote = (text) => {
    const value = String(text || "").trim();
    if (!value || seen.has(value)) {
      return;
    }
    seen.add(value);
    merged.push(value);
  };

  (proposal?.notes || []).forEach(pushNote);

  if (proposal?.impact_summary) {
    pushNote(`Impact: ${proposal.impact_summary}`);
  }

  const preflightChecks = getProposalPreflightChecks(proposal).filter((item) =>
    ["fail", "warning", "review"].includes(String(item?.status || "").toLowerCase())
  );
  preflightChecks.forEach((item) => {
    pushNote(
      `Preflight ${String(item?.name || "check")}: [${String(item?.status || "").toUpperCase()}] ${String(
        item?.detail || ""
      ).trim()}`
    );
  });

  return merged;
}

function resolvePlanSummary(summary, proposal) {
  const resolved = summary || computeProposalSummary(proposal);
  if (!proposal || !resolved) {
    return resolved;
  }

  if (getFailedPreflightChecks(proposal).length > 0) {
    return {
      ...resolved,
      next_action_hint:
        "存在失败的预检查项，请先处理后再审批执行 / Failed preflight checks detected. Resolve them before execution.",
    };
  }

  return resolved;
}

function renderPlanSummary(summary, proposal) {
  const allowedEl = document.getElementById("summary-allowed");
  const blockedEl = document.getElementById("summary-blocked");
  const nextEl = document.getElementById("proposal-next");
  if (!allowedEl || !blockedEl || !nextEl) {
    return;
  }

  if (!proposal) {
    allowedEl.textContent = "0";
    blockedEl.textContent = "0";
    nextEl.textContent = "等待提案 / Awaiting proposal.";
    return;
  }

  const resolved = resolvePlanSummary(summary, proposal);
  allowedEl.textContent = String(resolved.allowed_count || 0);
  blockedEl.textContent = String(resolved.blocked_count || 0);
  nextEl.textContent =
    resolved.next_action_hint ||
    "确认语句后输入 APPROVE 再执行 / Review statements, then type APPROVE before execution.";
}

function updateApproveAvailability() {
  const approveBtn = document.getElementById("proposal-approve");
  const rejectBtn = document.getElementById("proposal-reject");
  const confirmInput = document.getElementById("approve-confirm");
  if (!approveBtn || !rejectBtn || !confirmInput) {
    return;
  }

  if (!activeProposal) {
    approveBtn.disabled = true;
    rejectBtn.disabled = true;
    confirmInput.disabled = true;
    return;
  }

  const isPending = activeProposal.status === "PENDING";
  const approvalBlocked = hasBlockingApprovalIssue(activeProposal);
  const confirmed = (confirmInput.value || "").trim().toUpperCase() === "APPROVE";

  confirmInput.disabled = !isPending || approvalBlocked;
  approveBtn.disabled = !isPending || approvalBlocked || !confirmed;
  rejectBtn.disabled = !isPending;
}

function renderProposal(proposal, summary = null) {
  activeProposal = proposal || null;
  activeProposalSummary = summary || (proposal ? computeProposalSummary(proposal) : null);

  const meta = document.getElementById("proposal-meta");
  const risk = document.getElementById("proposal-risk");
  const notesEl = document.getElementById("proposal-notes");
  const ops = document.getElementById("proposal-ops");
  const confirmInput = document.getElementById("approve-confirm");
  if (!meta || !risk || !notesEl || !ops || !confirmInput) {
    return;
  }

  renderPlanSummary(activeProposalSummary, activeProposal);

  if (!proposal) {
    meta.textContent = "No proposal yet.";
    risk.textContent = "SAFE 安全";
    risk.className = "risk-badge safe";
    notesEl.innerHTML = "";
    ops.innerHTML = "";
    confirmInput.value = "";
    updateApproveAvailability();
    return;
  }

  const blocking = hasBlockingApprovalIssue(proposal);
  meta.textContent = `#${proposal.proposal_id} | ${proposal.status} | ${proposal.backend.toUpperCase()} (${proposal.source})`;
  risk.textContent = blocking ? "BLOCKED 已阻断" : "SAFE 安全";
  risk.className = `risk-badge ${blocking ? "blocked" : "safe"}`;

  notesEl.innerHTML = "";
  const notes = buildProposalNotes(proposal);
  notes.forEach((note) => {
    const item = document.createElement("li");
    item.textContent = note;
    notesEl.appendChild(item);
  });

  ops.innerHTML = "";
  (proposal.operations || []).forEach((operation) => {
    const item = document.createElement("li");
    const tag = operation.allowed ? "[SAFE]" : "[BLOCKED]";
    item.textContent = `${tag} ${operation.statement} (${operation.reason})`;
    ops.appendChild(item);
  });

  if (proposal.status !== "PENDING") {
    confirmInput.value = "";
  }

  updateApproveAvailability();
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(readApiError(payload, `Request failed (${response.status})`));
  }
  return payload;
}

async function createChatPlan(prompt, useLLM) {
  return fetchJson(`${API_BASE}/chat/plan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      prompt,
      use_llm: useLLM,
    }),
  });
}

async function getProposal(proposalId) {
  return fetchJson(`${API_BASE}/chat/proposals/${proposalId}`);
}

async function approveProposal(proposalId, approvalToken) {
  return fetchJson(`${API_BASE}/chat/proposals/${proposalId}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      approval_token: approvalToken,
      approver: "web-ui",
    }),
  });
}

async function rejectProposal(proposalId, reason) {
  return fetchJson(`${API_BASE}/chat/proposals/${proposalId}/reject`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason }),
  });
}

async function fetchSchemaOverview() {
  return fetchJson(`${API_BASE}/schema/overview`);
}

async function fetchSchemaColumns(table) {
  return fetchJson(`${API_BASE}/schema/columns/${encodeURIComponent(table)}`);
}

function normalizeSource(value) {
  if (value === "llm") {
    return "llm";
  }
  if (value === "join_infer") {
    return "join_infer";
  }
  if (value === "recovery") {
    return "recovery";
  }
  return "rule";
}

function prioritizeLlm(items) {
  if (!items || items.length === 0) {
    return [];
  }

  const priorities = {
    llm: 4,
    join_infer: 3,
    recovery: 2,
    rule: 1,
  };

  return [...items].sort((left, right) => {
    const leftPriority = priorities[left.source] || 0;
    const rightPriority = priorities[right.source] || 0;
    if (leftPriority !== rightPriority) {
      return rightPriority - leftPriority;
    }
    return (right.confidence || 0) - (left.confidence || 0);
  });
}

function formatConfidence(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return "--";
  }
  return `${Math.round(numeric * 100)}%`;
}

function humanizeReasonCode(reasonCode) {
  const code = String(reasonCode || "").trim();
  return code ? code.replaceAll("_", " ") : "context match";
}

function formatStrategyLabel(strategy) {
  const labels = {
    rule_only: "Rule Only",
    hybrid: "Hybrid",
    recovery: "Recovery",
    join_infer: "Join Infer",
  };
  return labels[strategy] || strategy || "Rule Only";
}

function normalizeSuggestionPayload(payload, defaultSource = "rule") {
  const structuredItems = Array.isArray(payload?.items) ? payload.items : [];
  const suggestions = (payload?.suggestions || []).slice(0, MAX_SUGGESTIONS);
  const sourceMap = payload?.debug?.suggestion_sources || {};
  const ruleSet = new Set(
    (payload?.debug?.rule_suggestions || []).map((item) => item.toLowerCase())
  );
  const llmSet = new Set(
    (payload?.debug?.llm_suggestions || []).map((item) => item.toLowerCase())
  );
  const reasonMap = payload?.debug?.suggestion_reasons || {};

  let items = structuredItems
    .slice(0, MAX_SUGGESTIONS)
    .map((item) => ({
      text: item.text,
      source: normalizeSource(item.source || defaultSource),
      confidence: Number(item.confidence) || 0,
      reasonCode: item.reason_code || "context_match",
      reason:
        item.reason ||
        reasonMap[item.text] ||
        "基于当前上下文推荐 / Suggested from current context.",
    }));

  if (items.length === 0) {
    items = suggestions.map((text) => {
      let source = defaultSource;

      if (sourceMap[text]) {
        source = normalizeSource(sourceMap[text]);
      } else {
        const key = text.toLowerCase();
        if (llmSet.has(key) && !ruleSet.has(key)) {
          source = "llm";
        } else if (ruleSet.has(key)) {
          source = "rule";
        }
      }

      return {
        text,
        source,
        confidence: source === "llm" ? 0.7 : 0.5,
        reasonCode: source === "llm" ? "semantic_prediction" : "context_match",
        reason:
          reasonMap[text] ||
          "基于当前上下文推荐 / Suggested from current context.",
      };
    });
  }

  return {
    items: prioritizeLlm(items),
    reasonMap,
    contextLabel:
      payload?.debug?.ui_context_label ||
      "通用补全 / General SQL suggestions",
    fallbackReason: payload?.debug?.fallback_reason || "",
    mode: payload?.mode || "rule_only",
    strategy: payload?.strategy || payload?.mode || "rule_only",
    strategyLabel: formatStrategyLabel(payload?.strategy || payload?.mode || "rule_only"),
  };
}

function updateContextLabel(label, strategyLabel = "Rule Only") {
  const contextLabelEl = document.getElementById("context-label");
  if (contextLabelEl) {
    contextLabelEl.textContent = `${label || "通用补全 / General SQL suggestions"} | Strategy: ${strategyLabel}`;
  }
}

function renderSuggestionList(items, reasonMap = {}) {
  const listEl = document.getElementById("suggestions");
  if (!listEl) {
    return;
  }

  listEl.innerHTML = "";
  activeSuggestions = items || [];
  activeSuggestionReasons = reasonMap || {};

  if (activeSuggestions.length === 0) {
    const item = document.createElement("li");
    item.textContent = "暂无建议 / No suggestions";
    listEl.appendChild(item);
    return;
  }

  activeSuggestions.forEach((suggestion, index) => {
    const item = document.createElement("li");
    item.className = "suggestion-item";
    if (index === 0) {
      item.classList.add("top");
    }
    item.dataset.index = String(index);

    const head = document.createElement("div");
    head.className = "suggestion-head";

    const left = document.createElement("div");
    left.className = "suggestion-left";

    const text = document.createElement("span");
    text.className = "suggestion-text";
    text.textContent = suggestion.text;

    const source = document.createElement("span");
    source.className = `source-badge ${suggestion.source}`;
    source.textContent = suggestion.source.toUpperCase();

    const confidence = document.createElement("span");
    confidence.className = "confidence-badge";
    confidence.textContent = formatConfidence(suggestion.confidence);

    left.appendChild(text);
    left.appendChild(source);
    left.appendChild(confidence);

    const shortcut = document.createElement("span");
    shortcut.className = "shortcut-key";
    shortcut.textContent = `Opt+Shift+${index + 1}`;

    head.appendChild(left);
    head.appendChild(shortcut);

    const reason = document.createElement("div");
    reason.className = "suggestion-reason";
    reason.textContent =
      suggestion.reason ||
      activeSuggestionReasons[suggestion.text] ||
      "基于当前上下文推荐 / Suggested from current context.";

    const meta = document.createElement("div");
    meta.className = "suggestion-meta";
    meta.textContent = `reason_code: ${humanizeReasonCode(
      suggestion.reasonCode
    )} | strategy: ${formatStrategyLabel(activeSuggestionStrategy)}`;

    item.appendChild(head);
    item.appendChild(reason);
    item.appendChild(meta);

    item.addEventListener("click", () => {
      insertSuggestionByIndex(index);
    });

    listEl.appendChild(item);
  });

  listEl.scrollTop = 0;
}

function formatSchemaTableCount(count) {
  return `${count} 张表 / ${count} tables`;
}

function formatSchemaKeyPreview(keyColumns) {
  const items = (keyColumns || []).filter(Boolean).slice(0, 3);
  return items.length > 0 ? items.join(", ") : "No highlighted columns";
}

function formatColumnDefault(value) {
  if (value === null || value === undefined || String(value).trim() === "") {
    return "-";
  }
  return String(value);
}

function createSchemaPill(text, kind = "") {
  const pill = document.createElement("span");
  pill.className = kind ? `schema-pill ${kind}` : "schema-pill";
  pill.textContent = text;
  return pill;
}

function renderSchemaColumns(columns) {
  const list = document.createElement("ul");
  list.className = "schema-columns";

  if (!columns || columns.length === 0) {
    const empty = document.createElement("div");
    empty.className = "schema-state";
    empty.textContent = "该表暂无列信息 / No column metadata available for this table.";
    return empty;
  }

  columns.forEach((column) => {
    const item = document.createElement("li");
    item.className = "schema-column-item";

    const top = document.createElement("div");
    top.className = "schema-column-top";

    const name = document.createElement("div");
    name.className = "schema-column-name";
    name.textContent = String(column?.name || "-");

    const type = document.createElement("div");
    type.className = "schema-column-type";
    type.textContent = String(column?.type || "UNKNOWN");

    top.appendChild(name);
    top.appendChild(type);

    const meta = document.createElement("div");
    meta.className = "schema-column-meta";
    if (column?.pk) {
      meta.appendChild(createSchemaPill("PK", "key"));
    }
    meta.appendChild(createSchemaPill(column?.notnull ? "NOT NULL" : "NULLABLE"));

    const defaultValue = document.createElement("div");
    defaultValue.className = "schema-default";
    defaultValue.textContent = `DEFAULT: ${formatColumnDefault(column?.default)}`;

    item.appendChild(top);
    item.appendChild(meta);
    item.appendChild(defaultValue);
    list.appendChild(item);
  });

  return list;
}

function renderSchemaRail() {
  const listEl = document.getElementById("schema-rail-list");
  const countEl = document.getElementById("schema-table-count");
  if (!listEl || !countEl) {
    return;
  }

  countEl.textContent = formatSchemaTableCount(schemaOverviewItems.length);
  listEl.innerHTML = "";

  if (!schemaOverviewItems || schemaOverviewItems.length === 0) {
    const empty = document.createElement("div");
    empty.className = "schema-empty";
    empty.textContent = "暂无 Schema 信息 / No schema metadata available yet.";
    listEl.appendChild(empty);
    return;
  }

  schemaOverviewItems.forEach((tableInfo) => {
    const tableName = String(tableInfo?.table || "");
    const details = document.createElement("details");
    details.className = "schema-card";
    details.open = schemaExpandedTables.has(tableName);

    const summary = document.createElement("summary");

    const head = document.createElement("div");
    head.className = "schema-card-head";

    const left = document.createElement("div");
    left.className = "schema-card-left";

    const title = document.createElement("div");
    title.className = "schema-table-name";
    title.textContent = tableName;

    const badges = document.createElement("div");
    badges.className = "schema-table-badges";
    badges.appendChild(createSchemaPill(`${Number(tableInfo?.column_count || 0)} cols`));

    (tableInfo?.key_columns || [])
      .filter(Boolean)
      .slice(0, 3)
      .forEach((columnName) => {
        badges.appendChild(createSchemaPill(String(columnName), "key"));
      });

    left.appendChild(title);
    left.appendChild(badges);

    const preview = document.createElement("div");
    preview.className = "schema-preview";
    preview.textContent = formatSchemaKeyPreview(tableInfo?.key_columns || []);

    head.appendChild(left);
    head.appendChild(preview);

    summary.appendChild(head);

    const body = document.createElement("div");
    body.className = "schema-card-body";

    if (!details.open) {
      const hint = document.createElement("div");
      hint.className = "schema-state";
      hint.textContent = "展开后按需加载列信息 / Expand to load column details on demand.";
      body.appendChild(hint);
    } else if (schemaLoadingTables.has(tableName)) {
      const loading = document.createElement("div");
      loading.className = "schema-state";
      loading.textContent = "正在加载列信息... / Loading column metadata...";
      body.appendChild(loading);
    } else if (schemaLoadErrors[tableName]) {
      const error = document.createElement("div");
      error.className = "schema-state error";
      error.textContent = `加载失败：${schemaLoadErrors[tableName]}`;

      const retry = document.createElement("button");
      retry.type = "button";
      retry.className = "ghost-btn compact-btn";
      retry.textContent = "重试 Retry";
      retry.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        ensureSchemaColumnsLoaded(tableName, { force: true, showStatus: true });
      });

      body.appendChild(error);
      body.appendChild(retry);
    } else if (schemaColumnCache[tableName]) {
      body.appendChild(renderSchemaColumns(schemaColumnCache[tableName]));
    } else {
      const placeholder = document.createElement("div");
      placeholder.className = "schema-state";
      placeholder.textContent = "准备加载列信息... / Preparing column metadata...";
      body.appendChild(placeholder);
    }

    details.appendChild(summary);
    details.appendChild(body);
    details.addEventListener("toggle", () => {
      if (details.open) {
        schemaExpandedTables.add(tableName);
        ensureSchemaColumnsLoaded(tableName);
      } else {
        schemaExpandedTables.delete(tableName);
      }
    });

    listEl.appendChild(details);
  });
}

async function ensureSchemaColumnsLoaded(table, { force = false, showStatus = false } = {}) {
  const tableName = String(table || "").trim();
  if (!tableName) {
    return;
  }

  if (!force && schemaColumnCache[tableName]) {
    return;
  }

  if (schemaLoadingTables.has(tableName)) {
    return;
  }

  delete schemaLoadErrors[tableName];
  schemaLoadingTables.add(tableName);
  renderSchemaRail();

  try {
    const payload = await fetchSchemaColumns(tableName);
    schemaColumnCache[tableName] = Array.isArray(payload?.columns) ? payload.columns : [];
    delete schemaLoadErrors[tableName];
  } catch (error) {
    schemaLoadErrors[tableName] = error.message;
    if (showStatus) {
      setStatus(
        `表 ${tableName} 列信息加载失败：${error.message}`,
        "error",
        () => ensureSchemaColumnsLoaded(tableName, { force: true, showStatus: true })
      );
    }
  } finally {
    schemaLoadingTables.delete(tableName);
    renderSchemaRail();
  }
}

async function refreshSchemaOverview(showStatus = false) {
  try {
    const payload = await fetchSchemaOverview();
    schemaOverviewItems = Array.isArray(payload?.tables) ? payload.tables : [];
    const availableTables = new Set(schemaOverviewItems.map((item) => String(item?.table || "")));
    schemaExpandedTables = new Set(
      [...schemaExpandedTables].filter((tableName) => availableTables.has(tableName))
    );
    schemaColumnCache = {};
    schemaLoadingTables = new Set();
    schemaLoadErrors = {};
    renderSchemaRail();
    schemaExpandedTables.forEach((tableName) => {
      void ensureSchemaColumnsLoaded(tableName);
    });
    if (showStatus) {
      setStatus("Schema 结构已更新 / Schema explorer refreshed.", "success");
    }
  } catch (error) {
    setStatus(
      `Schema 加载失败：${error.message}`,
      "error",
      () => handleSchemaRefresh()
    );
  }
}

async function handleSchemaRefresh() {
  if (getIsButtonLoading("schema-refresh")) {
    return;
  }

  setButtonLoading("schema-refresh", true, "刷新中 Refreshing...");
  try {
    await refreshSchemaOverview(true);
  } finally {
    setButtonLoading("schema-refresh", false);
  }
}

async function fetchSuggestions(sql, cursor, useLLM = true) {
  const response = await fetch(`${API_BASE}/autocomplete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      sql,
      cursor,
      max_suggestions: MAX_SUGGESTIONS,
      use_llm: useLLM,
    }),
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Request failed (${response.status}): ${text}`);
  }

  return response.json();
}

function getActiveContext() {
  if (!editor) {
    return null;
  }

  const model = editor.getModel();
  const position = editor.getPosition();
  if (!model || !position) {
    return null;
  }

  return {
    model,
    position,
    sql: model.getValue(),
    cursor: model.getOffsetAt(position),
    useLLM: getUseLlmEnabled(),
  };
}

function isSameContext(left, right) {
  return (
    !!left &&
    !!right &&
    left.sql === right.sql &&
    left.cursor === right.cursor &&
    !!left.useLLM === !!right.useLLM
  );
}

function extractTokenPrefix(sql, cursor) {
  const prefix = sql.slice(0, cursor);
  const match = prefix.match(/([A-Za-z_][A-Za-z0-9_.]*)$/);
  return match ? match[1] : "";
}

function inferClauseForGhost(sql, cursor) {
  const prefix = sql.slice(0, cursor).toUpperCase();
  const patterns = [
    ["HAVING", /\bHAVING\b/g],
    ["ORDER BY", /\bORDER\s+BY\b/g],
    ["GROUP BY", /\bGROUP\s+BY\b/g],
    ["ON", /\bON\b/g],
    ["WHERE", /\bWHERE\b/g],
    ["JOIN", /\bJOIN\b/g],
    ["FROM", /\bFROM\b/g],
    ["SELECT", /\bSELECT\b/g],
  ];

  let bestClause = "UNKNOWN";
  let bestIndex = -1;
  for (const [clause, pattern] of patterns) {
    const matches = [...prefix.matchAll(pattern)];
    if (!matches.length) {
      continue;
    }
    const index = matches[matches.length - 1].index ?? -1;
    if (index > bestIndex) {
      bestClause = clause;
      bestIndex = index;
    }
  }
  return bestClause;
}

function isConditionSuggestion(item) {
  const text = String(item?.text || "");
  const reasonCode = String(item?.reasonCode || "");
  return (
    /(=|!=|<>|>=|<=|>|<|\blike\b|\bin\b|\bis\b|\bbetween\b)/i.test(text) ||
    /(semantic_prediction|where|having|on)/i.test(reasonCode)
  );
}

function isFromJoinSuggestion(item) {
  const text = String(item?.text || "").trim();
  const source = String(item?.source || "");
  const reasonCode = String(item?.reasonCode || "");
  return (
    source === "join_infer" ||
    /^JOIN\b/i.test(text) ||
    (/^[A-Za-z_][A-Za-z0-9_]*$/.test(text) && /(table|join)/i.test(reasonCode))
  );
}

function isSelectSuggestion(item) {
  const text = String(item?.text || "").trim();
  const reasonCode = String(item?.reasonCode || "");
  return (
    text.includes(".") ||
    /\b(count|sum|avg|min|max)\(/i.test(text) ||
    /(select|column|group_by|order_by)/i.test(reasonCode)
  );
}

function isRepairSuggestion(item) {
  return /semantic[_\s]?repair/i.test(String(item?.reasonCode || ""));
}

function pickGhostSuggestion(context, items) {
  if (!context || !items?.length) {
    return null;
  }

  const llmItems = items.filter((item) => item.source === "llm");
  const repairItems = llmItems.filter(isRepairSuggestion);
  if (repairItems.length > 0) {
    return repairItems[0];
  }

  const tokenPrefix = extractTokenPrefix(context.sql, context.cursor);
  if (tokenPrefix && !isClauseKeyword(tokenPrefix)) {
    return llmItems[0] || items[0];
  }

  const clause = inferClauseForGhost(context.sql, context.cursor);
  if (clause === "WHERE" || clause === "ON" || clause === "HAVING") {
    return llmItems.find(isConditionSuggestion) || items.find(isConditionSuggestion) || null;
  }
  if (clause === "FROM" || clause === "JOIN") {
    return llmItems.find(isFromJoinSuggestion) || items.find(isFromJoinSuggestion) || null;
  }
  if (clause === "SELECT" || clause === "GROUP BY" || clause === "ORDER BY") {
    return llmItems.find(isSelectSuggestion) || items.find(isSelectSuggestion) || null;
  }

  return llmItems[0] || items[0] || null;
}

function isClauseKeyword(token) {
  const keywords = new Set([
    "SELECT",
    "FROM",
    "WHERE",
    "JOIN",
    "ON",
    "HAVING",
    "GROUP",
    "ORDER",
    "BY",
  ]);
  return keywords.has(String(token || "").toUpperCase());
}

function shouldAllowClauseGhostPreview(sql, cursor, tokenPrefix) {
  if (!tokenPrefix || !isClauseKeyword(tokenPrefix)) {
    return false;
  }

  const nextChar = sql[cursor] || "";
  return nextChar === "" || /\s/.test(nextChar);
}

function buildClauseGhostText(sql, cursor, suggestionText) {
  const prevChar = sql[cursor - 1] || "";
  const needsLeadingSpace = prevChar && !/\s|\(|,/.test(prevChar);
  return `${needsLeadingSpace ? " " : ""}${suggestionText}`;
}

function getReplacementRange(model, cursor, position, suggestion) {
  const sql = model.getValue();
  const tokenPrefix = extractTokenPrefix(sql, cursor);

  if (tokenPrefix && suggestion.toLowerCase().startsWith(tokenPrefix.toLowerCase())) {
    const startOffset = cursor - tokenPrefix.length;
    const startPos = model.getPositionAt(startOffset);
    return new monaco.Range(
      startPos.lineNumber,
      startPos.column,
      position.lineNumber,
      position.column
    );
  }

  return new monaco.Range(
    position.lineNumber,
    position.column,
    position.lineNumber,
    position.column
  );
}

function getSuggestionReplacementRange(context, suggestionItem) {
  const suggestionText = String(suggestionItem?.text || "");
  const tokenPrefix = extractTokenPrefix(context.sql, context.cursor);

  if (isRepairSuggestion(suggestionItem) && tokenPrefix && !isClauseKeyword(tokenPrefix)) {
    return getReplacementRange(context.model, context.cursor, context.position, tokenPrefix);
  }

  return getReplacementRange(context.model, context.cursor, context.position, suggestionText);
}

function clearInlinePreview() {
  inlinePreview = null;
  if (editor) {
    editor.trigger("inline", "editor.action.inlineSuggest.hide", {});
  }
}

function buildInlinePreview(context, suggestionItem) {
  const suggestionText = String(suggestionItem?.text || "");
  if (!context || !suggestionText) {
    return null;
  }

  const tokenPrefix = extractTokenPrefix(context.sql, context.cursor);
  const isRepair = isRepairSuggestion(suggestionItem);
  let insertText = "";
  let range = {
    startLineNumber: context.position.lineNumber,
    startColumn: context.position.column,
    endLineNumber: context.position.lineNumber,
    endColumn: context.position.column,
  };

  if (tokenPrefix && suggestionText.toLowerCase().startsWith(tokenPrefix.toLowerCase())) {
    insertText = suggestionText.slice(tokenPrefix.length);
  } else if (isRepair && tokenPrefix && !isClauseKeyword(tokenPrefix)) {
    insertText = suggestionText;
    const replacementRange = getSuggestionReplacementRange(context, suggestionItem);
    range = {
      startLineNumber: replacementRange.startLineNumber,
      startColumn: replacementRange.startColumn,
      endLineNumber: replacementRange.endLineNumber,
      endColumn: replacementRange.endColumn,
    };
  } else if (shouldAllowClauseGhostPreview(context.sql, context.cursor, tokenPrefix)) {
    insertText = buildClauseGhostText(context.sql, context.cursor, suggestionText);
  } else if (!tokenPrefix && /\s$/.test(context.sql.slice(0, context.cursor))) {
    insertText = suggestionText;
  }

  if (!insertText) {
    return null;
  }

  return {
    text: insertText,
    fullText: suggestionText,
    cursor: context.cursor,
    modelVersion: context.model.getVersionId(),
    range,
  };
}

function updateInlinePreview(context, items) {
  if (!context) {
    clearInlinePreview();
    return;
  }

  const preview = buildPreviewFromSuggestions(context, items || []);
  if (!preview) {
    clearInlinePreview();
    return;
  }

  inlinePreview = preview;

  editor.trigger("inline", "editor.action.inlineSuggest.trigger", {});
}

function applySuggestionsForContext(context, normalized, statusMessage = null, statusLevel = "info") {
  const activeContext = getActiveContext();
  if (!isSameContext(context, activeContext)) {
    return false;
  }

  activeSuggestionStrategy = normalized.strategy || "rule_only";
  activeSuggestionContext = {
    sql: activeContext.sql,
    cursor: activeContext.cursor,
    modelVersion: activeContext.model.getVersionId(),
    useLLM: !!activeContext.useLLM,
  };
  renderSuggestionList(normalized.items, normalized.reasonMap);
  updateContextLabel(normalized.contextLabel, normalized.strategyLabel);
  updateInlinePreview(activeContext, normalized.items);
  maybeTriggerSuggestWidget(activeContext);

  if (statusMessage) {
    setStatus(statusMessage, statusLevel);
  }

  return true;
}

function hasFreshSuggestionCache(context) {
  return (
    !!context &&
    !!activeSuggestionContext &&
    activeSuggestionContext.sql === context.sql &&
    activeSuggestionContext.cursor === context.cursor &&
    activeSuggestionContext.modelVersion === context.model.getVersionId() &&
    activeSuggestionContext.useLLM === !!context.useLLM &&
    activeSuggestions.length > 0
  );
}

function buildPreviewFromSuggestions(context, items) {
  if (!context || !items?.length) {
    return null;
  }

  const selectedSuggestion = pickGhostSuggestion(context, items);
  if (!selectedSuggestion?.text) {
    return null;
  }

  return buildInlinePreview(context, selectedSuggestion);
}

function buildCompletionItems(model, context, items) {
  return {
    suggestions: (items || []).map((item) => ({
      label: item.text,
      kind: monaco.languages.CompletionItemKind.Field,
      insertText: item.text,
      range: getSuggestionReplacementRange(context, item),
      detail: `${item.source.toUpperCase()} | ${formatConfidence(item.confidence)} | ${humanizeReasonCode(
        item.reasonCode
      )}`,
      documentation: item.reason || activeSuggestionReasons[item.text] || "Context aware suggestion",
    })),
  };
}

function maybeTriggerSuggestWidget(context) {
  if (!editor || !context || !hasFreshSuggestionCache(context)) {
    return;
  }

  window.setTimeout(() => {
    const latestContext = getActiveContext();
    if (!latestContext || !hasFreshSuggestionCache(latestContext)) {
      return;
    }

    editor.trigger("autocomplete", "editor.action.triggerSuggest", {});
    editor.trigger("inline", "editor.action.inlineSuggest.trigger", {});
  }, 0);
}

function getInlineRange() {
  if (!inlinePreview) {
    return null;
  }

  return new monaco.Range(
    inlinePreview.range.startLineNumber,
    inlinePreview.range.startColumn,
    inlinePreview.range.endLineNumber,
    inlinePreview.range.endColumn
  );
}

function acceptInlinePreviewWithTab() {
  if (!editor || !inlinePreview) {
    return false;
  }

  const context = getActiveContext();
  if (!context) {
    return false;
  }

  if (context.cursor !== inlinePreview.cursor || context.model.getVersionId() !== inlinePreview.modelVersion) {
    return false;
  }

  const range = getInlineRange();
  if (!range) {
    return false;
  }

  editor.executeEdits("inline-tab-accept", [
    {
      range,
      text: inlinePreview.text,
      forceMoveMarkers: true,
    },
  ]);

  clearInlinePreview();
  scheduleLiveRefresh(70);
  return true;
}

function acceptTopSuggestionWithTab() {
  if (!editor || !activeSuggestions.length) {
    return false;
  }

  const context = getActiveContext();
  if (!context || !hasFreshSuggestionCache(context)) {
    return false;
  }

  const suggestion = pickGhostSuggestion(context, activeSuggestions);
  if (!suggestion) {
    return false;
  }

  const range = getSuggestionReplacementRange(context, suggestion);
  let text = suggestion.text;

  editor.executeEdits("inline-tab-fallback-accept", [
    {
      range,
      text,
      forceMoveMarkers: true,
    },
  ]);

  clearInlinePreview();
  scheduleLiveRefresh(70);
  return true;
}

function insertSuggestionByIndex(index) {
  if (!editor || index < 0 || index >= activeSuggestions.length) {
    return;
  }

  const context = getActiveContext();
  if (!context) {
    return;
  }

  const suggestion = activeSuggestions[index];
  const range = getSuggestionReplacementRange(context, suggestion);

  editor.executeEdits("sidebar-suggestion-insert", [
    {
      range,
      text: suggestion.text,
      forceMoveMarkers: true,
    },
  ]);
  editor.focus();

  clearInlinePreview();
  setStatus(
    `已插入 #${index + 1} (${suggestion.source.toUpperCase()}) / Inserted suggestion #${index + 1}.`,
    "success"
  );
  scheduleLiveRefresh(70);
}

async function hydrateWithLlm(context, seq, label = "Live preview", showStatus = true) {
  try {
    const payload = await fetchSuggestions(context.sql, context.cursor, true);
    if (seq !== requestSeq) {
      return;
    }

    const normalized = normalizeSuggestionPayload(payload, "rule");
    const llmCount = normalized.items.filter((item) => item.source === "llm").length;
    const statusMessage =
      normalized.strategy === "hybrid"
        ? `${label}: ${normalized.strategyLabel}，已合并 ${llmCount} 条 LLM 建议 / Hybrid suggestions merged`
        : normalized.fallbackReason ||
          `${label}: 当前策略 ${normalized.strategyLabel} / Active strategy: ${normalized.strategyLabel}`;

    applySuggestionsForContext(
      context,
      normalized,
      showStatus ? statusMessage : null,
      normalized.strategy === "hybrid" ? "success" : "info"
    );
    maybeTriggerSuggestWidget(context);
  } catch (_error) {
    if (seq !== requestSeq) {
      return;
    }

    if (showStatus) {
      setStatus(
        `${label}: LLM 暂时不可用，已保留规则补全 / LLM unavailable, kept rule suggestions.`,
        "info"
      );
    }
  }
}

async function runRuleFirstFlow(
  context,
  { label = "Live preview", includeLlm = true, showStatus = true, retryAction = null } = {}
) {
  const seq = ++requestSeq;

  try {
    const rulePayload = await fetchSuggestions(context.sql, context.cursor, false);
    if (seq !== requestSeq) {
      return;
    }

    const normalized = normalizeSuggestionPayload(rulePayload, "rule");
    applySuggestionsForContext(
      context,
      normalized,
      showStatus
        ? `${label}: 已获得 ${normalized.items.length} 条规则建议 / ${normalized.items.length} rule suggestions`
        : null,
      "info"
    );

    if (includeLlm && context.useLLM) {
      hydrateWithLlm(context, seq, label, showStatus);
    }
  } catch (error) {
    if (seq !== requestSeq) {
      return;
    }

    clearInlinePreview();
    renderSuggestionList([]);
    activeSuggestionStrategy = "rule_only";
    updateContextLabel("通用补全 / General SQL suggestions", "Rule Only");
    if (showStatus) {
      setStatus(
        `补全请求失败：${error.message}`,
        "error",
        retryAction || (() => manualSuggest())
      );
    }
  }
}

async function manualSuggest() {
  if (getIsButtonLoading("run-complete")) {
    return;
  }

  const context = getActiveContext();
  if (!context) {
    return;
  }

  setButtonLoading("run-complete", true, "获取中 Loading...");
  setStatus("正在获取规则补全... / Fetching rule suggestions...", "info");

  try {
    await runRuleFirstFlow(context, {
      label: "Manual",
      includeLlm: true,
      showStatus: true,
      retryAction: () => manualSuggest(),
    });
  } finally {
    setButtonLoading("run-complete", false);
  }
}

function scheduleLiveRefresh(delayMs = 130) {
  if (refreshTimer) {
    window.clearTimeout(refreshTimer);
  }

  refreshTimer = window.setTimeout(() => {
    const context = getActiveContext();
    if (!context) {
      return;
    }

    runRuleFirstFlow(context, {
      label: "Live preview",
      includeLlm: true,
      showStatus: false,
    });
  }, delayMs);
}

function fillEditorWithExample(text) {
  if (!editor) {
    return;
  }

  editor.setValue(text);
  const model = editor.getModel();
  if (!model) {
    return;
  }
  const endPos = model.getPositionAt(text.length);
  editor.setPosition(endPos);
  editor.focus();
  setStatus("示例已填充，可继续编辑 / Example inserted. Continue editing.", "success");
  scheduleLiveRefresh(40);
}

function fillChatInputWithTemplate(text) {
  const input = document.getElementById("chat-input");
  if (!input) {
    return;
  }
  input.value = text;
  input.focus();
  setStatus("模板已填充，请按需修改后生成提案 / Template inserted, then generate plan.", "info");
}

function bindExampleButtons() {
  document.querySelectorAll("[data-sql-example]").forEach((button) => {
    button.addEventListener("click", () => {
      const sample = button.getAttribute("data-sql-example") || "";
      fillEditorWithExample(sample);
    });
  });

  document.querySelectorAll("[data-chat-template]").forEach((button) => {
    button.addEventListener("click", () => {
      const sample = button.getAttribute("data-chat-template") || "";
      fillChatInputWithTemplate(sample);
      switchPanel("chat");
    });
  });
}

async function submitChatPlan() {
  if (getIsButtonLoading("chat-plan")) {
    return;
  }

  const input = document.getElementById("chat-input");
  const useLlmInput = document.getElementById("use-llm");
  if (!input) {
    setStatus("未找到输入框，请刷新页面 / Chat input missing. Refresh page.", "error");
    return;
  }

  const prompt = (input.value || "").trim();
  if (!prompt) {
    setStatus(
      "请先输入改库需求 / Please enter a schema change request first.",
      "error"
    );
    return;
  }

  appendChatMessage("user", prompt);
  setButtonLoading("chat-plan", true, "生成中 Generating...");
  setStatus("正在生成 DDL 提案... / Generating DDL proposal...", "info");

  try {
    const payload = await createChatPlan(prompt, !!useLlmInput?.checked);
    renderProposal(payload.proposal, payload.summary || null);
    appendChatMessage(
      "assistant",
      payload.message || "提案已生成，请查看风险后执行 / Proposal ready. Review risk before execution."
    );
    setStatus("提案生成完成，请先阅读摘要与风险 / Proposal generated.", "success");
  } catch (error) {
    appendChatMessage("assistant", error.message);
    setStatus(`提案生成失败：${error.message}`, "error", () => submitChatPlan());
  } finally {
    setButtonLoading("chat-plan", false);
  }
}

async function refreshChatProposal() {
  if (getIsButtonLoading("chat-refresh")) {
    return;
  }

  if (!activeProposal?.proposal_id) {
    setStatus("当前没有可刷新的提案 / No proposal to refresh.", "error");
    return;
  }

  setButtonLoading("chat-refresh", true, "刷新中 Refreshing...");

  try {
    const payload = await getProposal(activeProposal.proposal_id);
    renderProposal(payload, null);
    setStatus(`提案 ${payload.proposal_id} 已刷新 / Proposal refreshed.`, "success");
  } catch (error) {
    setStatus(`刷新失败：${error.message}`, "error", () => refreshChatProposal());
  } finally {
    setButtonLoading("chat-refresh", false);
  }
}

async function approveChatProposal() {
  if (getIsButtonLoading("proposal-approve")) {
    return;
  }

  if (!activeProposal?.proposal_id) {
    setStatus("当前没有可审批提案 / No proposal available for approval.", "error");
    return;
  }

  const confirmInput = document.getElementById("approve-confirm");
  if (!confirmInput || (confirmInput.value || "").trim().toUpperCase() !== "APPROVE") {
    setStatus("请输入 APPROVE 后再执行 / Type APPROVE before execution.", "error");
    return;
  }

  setButtonLoading("proposal-approve", true, "执行中 Executing...");

  try {
    const payload = await approveProposal(activeProposal.proposal_id, activeProposal.approval_token);
    renderProposal(payload.proposal, null);

    const successCount = (payload.proposal.execution_results || []).filter(
      (item) => item.status === "success"
    ).length;
    const failedCount = (payload.proposal.execution_results || []).filter(
      (item) => item.status === "error"
    ).length;
    if (failedCount > 0) {
      appendChatMessage(
        "assistant",
        `执行完成，但有 ${failedCount} 条语句失败 / Execution completed with ${failedCount} failed statements.`
      );
      setStatus(payload.message || "执行存在失败语句 / Execution finished with errors.", "error");
    } else {
      appendChatMessage("assistant", "执行成功 / Execution succeeded.");
      setStatus(payload.message || "执行成功 / Execution succeeded.", "success");
    }

    if (successCount > 0) {
      scheduleLiveRefresh(30);
      refreshSchemaOverview(false);
    }
  } catch (error) {
    appendChatMessage("assistant", error.message);
    setStatus(`审批执行失败：${error.message}`, "error", () => approveChatProposal());
  } finally {
    setButtonLoading("proposal-approve", false);
    updateApproveAvailability();
  }
}

async function rejectChatProposal() {
  if (getIsButtonLoading("proposal-reject")) {
    return;
  }

  if (!activeProposal?.proposal_id) {
    setStatus("当前没有可拒绝提案 / No proposal available for reject action.", "error");
    return;
  }

  setButtonLoading("proposal-reject", true, "处理中 Rejecting...");

  try {
    const payload = await rejectProposal(activeProposal.proposal_id, "Rejected from web UI");
    renderProposal(payload.proposal, null);
    appendChatMessage("assistant", payload.message || "提案已拒绝 / Proposal rejected.");
    setStatus("提案已拒绝 / Proposal rejected.", "success");
  } catch (error) {
    appendChatMessage("assistant", error.message);
    setStatus(`拒绝失败：${error.message}`, "error", () => rejectChatProposal());
  } finally {
    setButtonLoading("proposal-reject", false);
    updateApproveAvailability();
  }
}

function bindClick(id, handler) {
  const element = document.getElementById(id);
  if (!element) {
    console.warn(`Missing element: #${id}`);
    return;
  }
  element.addEventListener("click", handler);
}

require.config({
  paths: {
    vs: "https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.45.0/min/vs",
  },
});

require(["vs/editor/editor.main"], () => {
  monaco.languages.register({ id: "sql" });

  editor = monaco.editor.create(document.getElementById("editor"), {
    value: ["SELECT u.", "FROM users u", "WHERE "].join("\n"),
    language: "sql",
    theme: "vs",
    fontSize: 14,
    minimap: { enabled: false },
    automaticLayout: true,
    inlineSuggest: {
      enabled: true,
      mode: "subword",
    },
  });

  monaco.languages.registerInlineCompletionsProvider("sql", {
    provideInlineCompletions(model, position) {
      const cursor = model.getOffsetAt(position);
      const context = {
        model,
        position,
        sql: model.getValue(),
        cursor,
        useLLM: getUseLlmEnabled(),
      };

      if (!hasFreshSuggestionCache(context)) {
        return { items: [] };
      }

      const preview = buildPreviewFromSuggestions(context, activeSuggestions);
      if (!preview) {
        inlinePreview = null;
        return { items: [] };
      }

      inlinePreview = preview;

      const range = getInlineRange();
      if (!range) {
        return { items: [] };
      }

      return {
        items: [
          {
            insertText: inlinePreview.text,
            range,
          },
        ],
      };
    },
    freeInlineCompletions() {},
  });

  monaco.languages.registerCompletionItemProvider("sql", {
    triggerCharacters: [".", " ", "\n"],
    provideCompletionItems: async (model, position) => {
      const context = {
        model,
        position,
        sql: model.getValue(),
        cursor: model.getOffsetAt(position),
        useLLM: getUseLlmEnabled(),
      };

      if (hasFreshSuggestionCache(context)) {
        return buildCompletionItems(model, context, activeSuggestions);
      }

      const seq = ++requestSeq;

      try {
        const rulePayload = await fetchSuggestions(context.sql, context.cursor, false);
        if (seq !== requestSeq) {
          return { suggestions: [] };
        }

        const normalized = normalizeSuggestionPayload(rulePayload, "rule");
        applySuggestionsForContext(context, normalized, null);

        if (context.useLLM) {
          hydrateWithLlm(context, seq, "Auto-complete", false);
        }

        return buildCompletionItems(model, context, normalized.items);
      } catch (error) {
        if (seq !== requestSeq) {
          return { suggestions: [] };
        }

        clearInlinePreview();
        setStatus(`自动补全失败：${error.message}`, "error");
        return { suggestions: [] };
      }
    },
  });

  bindClick("run-complete", manualSuggest);
  bindClick("tab-suggestions", () => switchPanel("suggestions"));
  bindClick("tab-chat", () => switchPanel("chat"));
  bindClick("schema-refresh", handleSchemaRefresh);
  bindClick("chat-plan", submitChatPlan);
  bindClick("chat-refresh", refreshChatProposal);
  bindClick("proposal-approve", approveChatProposal);
  bindClick("proposal-reject", rejectChatProposal);

  const retryButton = document.getElementById("status-retry");
  if (retryButton) {
    retryButton.addEventListener("click", () => {
      if (typeof lastRetryAction === "function") {
        lastRetryAction();
      }
    });
  }

  const useLlmInput = document.getElementById("use-llm");
  if (useLlmInput) {
    useLlmInput.addEventListener("change", () => {
      const modeText = useLlmInput.checked
        ? "已开启 LLM，补全将先规则后语义 / LLM enabled: rule-first then semantic."
        : "已关闭 LLM，仅使用规则补全 / LLM disabled: rule-only mode.";
      setStatus(modeText, "info");
      scheduleLiveRefresh(30);
    });
  }

  const approveInput = document.getElementById("approve-confirm");
  if (approveInput) {
    approveInput.addEventListener("input", () => {
      updateApproveAvailability();
    });
  }

  bindExampleButtons();

  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Space, () => {
    editor.trigger("keyboard", "editor.action.triggerSuggest", {});
  });

  editor.onDidChangeModelContent(() => {
    clearInlinePreview();
    scheduleLiveRefresh(130);
  });

  editor.onDidChangeCursorPosition(() => {
    clearInlinePreview();
    scheduleLiveRefresh(90);
  });

  editor.onKeyDown((event) => {
    if (event.keyCode === monaco.KeyCode.Tab && (acceptInlinePreviewWithTab() || acceptTopSuggestionWithTab())) {
      event.preventDefault();
      event.stopPropagation();
    }
  });

  window.addEventListener("keydown", (event) => {
    if (!event.altKey || !event.shiftKey || event.ctrlKey || event.metaKey) {
      return;
    }

    const match = event.code.match(/^Digit([1-9])$/);
    if (!match) {
      return;
    }

    const index = Number(match[1]) - 1;
    if (index < 0 || index >= activeSuggestions.length) {
      return;
    }

    event.preventDefault();
    insertSuggestionByIndex(index);
  });

  setStatus(
    "已就绪：Ctrl/Cmd + Space 触发补全，Tab 接受幽灵文本，Option+Shift+1..9 快速插入。",
    "info"
  );
  switchPanel("suggestions");
  renderSchemaRail();
  renderProposal(null, null);
  appendChatMessage(
    "assistant",
    "描述你想做的 Schema 变更，我会先生成可审批的 DDL 提案。"
  );
  refreshSchemaOverview(false);
  scheduleLiveRefresh(40);
});

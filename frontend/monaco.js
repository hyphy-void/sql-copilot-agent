const API_BASE = window.location.origin;
const MAX_SUGGESTIONS = 10;

let editor;
let activeSuggestions = [];
let inlinePreview = null;
let refreshTimer = null;
let requestSeq = 0;
let activeProposal = null;

function setStatus(message, isError = false) {
  const statusEl = document.getElementById("status");
  if (!statusEl) {
    return;
  }
  statusEl.textContent = message;
  statusEl.classList.toggle("error", isError);
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
    return;
  }

  suggestionPanel.classList.remove("hidden");
  chatPanel.classList.add("hidden");
  suggestionTab.classList.add("active");
  chatTab.classList.remove("active");
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
  roleEl.textContent = role === "user" ? "You" : "Agent";

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

function renderProposal(proposal) {
  activeProposal = proposal || null;

  const meta = document.getElementById("proposal-meta");
  const risk = document.getElementById("proposal-risk");
  const ops = document.getElementById("proposal-ops");
  const approveBtn = document.getElementById("proposal-approve");
  const rejectBtn = document.getElementById("proposal-reject");
  if (!meta || !risk || !ops || !approveBtn || !rejectBtn) {
    return;
  }

  if (!proposal) {
    meta.textContent = "No proposal yet.";
    risk.textContent = "SAFE";
    risk.className = "risk-badge safe";
    ops.innerHTML = "";
    approveBtn.disabled = true;
    rejectBtn.disabled = true;
    return;
  }

  const blocking = !!proposal.has_blocking_risk;
  meta.textContent = `#${proposal.proposal_id} | ${proposal.status} | ${proposal.backend.toUpperCase()} (${proposal.source})`;
  risk.textContent = blocking ? "BLOCKED" : "SAFE";
  risk.className = `risk-badge ${blocking ? "blocked" : "safe"}`;

  ops.innerHTML = "";
  (proposal.operations || []).forEach((operation) => {
    const item = document.createElement("li");
    const tag = operation.allowed ? "[SAFE]" : "[BLOCKED]";
    item.textContent = `${tag} ${operation.statement} (${operation.reason})`;
    ops.appendChild(item);
  });

  const canApprove = proposal.status === "PENDING" && !blocking;
  const canReject = proposal.status === "PENDING";
  approveBtn.disabled = !canApprove;
  rejectBtn.disabled = !canReject;
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

function normalizeSource(value) {
  return value === "llm" ? "llm" : "rule";
}

function normalizeSuggestionItems(payload, defaultSource = "rule") {
  const suggestions = (payload?.suggestions || []).slice(0, MAX_SUGGESTIONS);
  const sourceMap = payload?.debug?.suggestion_sources || {};
  const ruleSet = new Set(
    (payload?.debug?.rule_suggestions || []).map((item) => item.toLowerCase())
  );
  const llmSet = new Set(
    (payload?.debug?.llm_suggestions || []).map((item) => item.toLowerCase())
  );

  const items = suggestions.map((text) => {
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

    return { text, source };
  });

  return prioritizeLlm(items);
}

function prioritizeLlm(items) {
  if (!items || items.length === 0) {
    return [];
  }

  const llmItems = [];
  const ruleItems = [];

  items.forEach((item) => {
    if (item.source === "llm") {
      llmItems.push(item);
    } else {
      ruleItems.push(item);
    }
  });

  return [...llmItems, ...ruleItems];
}

function renderSuggestionList(items) {
  const listEl = document.getElementById("suggestions");
  listEl.innerHTML = "";

  activeSuggestions = items || [];

  if (activeSuggestions.length === 0) {
    const item = document.createElement("li");
    item.textContent = "No suggestions";
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

    const left = document.createElement("div");
    left.className = "suggestion-left";

    const text = document.createElement("span");
    text.className = "suggestion-text";
    text.textContent = suggestion.text;

    const source = document.createElement("span");
    source.className = `source-badge ${suggestion.source}`;
    source.textContent = suggestion.source.toUpperCase();

    left.appendChild(text);
    left.appendChild(source);

    const shortcut = document.createElement("span");
    shortcut.className = "shortcut-key";
    shortcut.textContent = `Alt+${index + 1}`;

    item.appendChild(left);
    item.appendChild(shortcut);

    item.addEventListener("click", () => {
      insertSuggestionByIndex(index);
    });

    listEl.appendChild(item);
  });
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
    useLLM: document.getElementById("use-llm").checked,
  };
}

function isSameContext(left, right) {
  return (
    !!left &&
    !!right &&
    left.sql === right.sql &&
    left.cursor === right.cursor
  );
}

function extractTokenPrefix(sql, cursor) {
  const prefix = sql.slice(0, cursor);
  const match = prefix.match(/([A-Za-z_][A-Za-z0-9_.]*)$/);
  return match ? match[1] : "";
}

function getReplacementRange(model, cursor, position, suggestion) {
  const sql = model.getValue();
  const tokenPrefix = extractTokenPrefix(sql, cursor);

  if (
    tokenPrefix &&
    suggestion.toLowerCase().startsWith(tokenPrefix.toLowerCase())
  ) {
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

function clearInlinePreview() {
  inlinePreview = null;
  if (editor) {
    editor.trigger("inline", "editor.action.inlineSuggest.hide", {});
  }
}

function updateInlinePreview(context, topSuggestionText) {
  if (!context || !topSuggestionText) {
    clearInlinePreview();
    return;
  }

  const range = getReplacementRange(
    context.model,
    context.cursor,
    context.position,
    topSuggestionText
  );
  const currentRangeText = context.model.getValueInRange(range);

  if (!topSuggestionText || currentRangeText === topSuggestionText) {
    clearInlinePreview();
    return;
  }

  inlinePreview = {
    text: topSuggestionText,
    cursor: context.cursor,
    modelVersion: context.model.getVersionId(),
    range: {
      startLineNumber: range.startLineNumber,
      startColumn: range.startColumn,
      endLineNumber: range.endLineNumber,
      endColumn: range.endColumn,
    },
  };

  editor.trigger("inline", "editor.action.inlineSuggest.trigger", {});
}

function applySuggestionsForContext(context, items, statusMessage = null) {
  const activeContext = getActiveContext();
  if (!isSameContext(context, activeContext)) {
    return false;
  }

  renderSuggestionList(items);
  updateInlinePreview(activeContext, items[0]?.text || "");

  if (statusMessage) {
    setStatus(statusMessage, false);
  }

  return true;
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

  if (
    context.cursor !== inlinePreview.cursor ||
    context.model.getVersionId() !== inlinePreview.modelVersion
  ) {
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

function insertSuggestionByIndex(index) {
  if (!editor || index < 0 || index >= activeSuggestions.length) {
    return;
  }

  const context = getActiveContext();
  if (!context) {
    return;
  }

  const suggestion = activeSuggestions[index];
  const range = getReplacementRange(
    context.model,
    context.cursor,
    context.position,
    suggestion.text
  );

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
    `Inserted #${index + 1} (${suggestion.source.toUpperCase()})`,
    false
  );
  scheduleLiveRefresh(70);
}

async function hydrateWithLlm(context, seq, label = "Live preview", showStatus = true) {
  try {
    const payload = await fetchSuggestions(context.sql, context.cursor, true);
    if (seq !== requestSeq) {
      return;
    }

    const items = normalizeSuggestionItems(payload, "rule");
    const llmCount = items.filter((item) => item.source === "llm").length;
    const statusMessage =
      payload.mode === "hybrid"
        ? `${label}: Rule first, then +${llmCount} LLM suggestions`
        : `${label}: Rule suggestions ready (LLM unavailable)`;

    applySuggestionsForContext(context, items, showStatus ? statusMessage : null);
  } catch (_error) {
    if (seq !== requestSeq) {
      return;
    }

    if (showStatus) {
      setStatus(`${label}: Rule suggestions ready (LLM timeout/unavailable)`, false);
    }
  }
}

async function runRuleFirstFlow(
  context,
  { label = "Live preview", includeLlm = true, showStatus = true } = {}
) {
  const seq = ++requestSeq;

  try {
    const rulePayload = await fetchSuggestions(context.sql, context.cursor, false);
    if (seq !== requestSeq) {
      return;
    }

    const ruleItems = normalizeSuggestionItems(rulePayload, "rule");
    applySuggestionsForContext(
      context,
      ruleItems,
      showStatus ? `${label}: Rule ${ruleItems.length} suggestions` : null
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
    if (showStatus) {
      setStatus(error.message, true);
    }
  }
}

async function manualSuggest() {
  const context = getActiveContext();
  if (!context) {
    return;
  }

  setStatus("Generating rule suggestions...");
  await runRuleFirstFlow(context, {
    label: "Manual",
    includeLlm: true,
    showStatus: true,
  });
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

async function submitChatPlan() {
  const input = document.getElementById("chat-input");
  if (!input) {
    setStatus("Chat input not found. Please refresh the page.", true);
    return;
  }
  const prompt = (input.value || "").trim();
  if (!prompt) {
    setStatus("Please enter a schema request first.", true);
    return;
  }

  appendChatMessage("user", prompt);
  setStatus("Generating DDL proposal...");

  try {
    const payload = await createChatPlan(
      prompt,
      document.getElementById("use-llm").checked
    );
    renderProposal(payload.proposal);
    appendChatMessage("assistant", payload.message || "Proposal created.");
    setStatus("Proposal created. Review risk and approve if safe.");
  } catch (error) {
    appendChatMessage("assistant", error.message);
    setStatus(error.message, true);
  }
}

async function refreshChatProposal() {
  if (!activeProposal?.proposal_id) {
    setStatus("No proposal to refresh.", true);
    return;
  }

  try {
    const payload = await getProposal(activeProposal.proposal_id);
    renderProposal(payload);
    setStatus(`Proposal ${payload.proposal_id} refreshed.`);
  } catch (error) {
    setStatus(error.message, true);
  }
}

async function approveChatProposal() {
  if (!activeProposal?.proposal_id) {
    setStatus("No proposal available for approval.", true);
    return;
  }

  try {
    const payload = await approveProposal(
      activeProposal.proposal_id,
      activeProposal.approval_token
    );
    renderProposal(payload.proposal);

    const failedCount = (payload.proposal.execution_results || []).filter(
      (item) => item.status === "error"
    ).length;
    if (failedCount > 0) {
      appendChatMessage(
        "assistant",
        `Execution finished with ${failedCount} failed statement(s).`
      );
      setStatus(payload.message || "Execution finished with failures.", true);
    } else {
      appendChatMessage("assistant", "Execution succeeded.");
      setStatus(payload.message || "Execution succeeded.");
      scheduleLiveRefresh(30);
    }
  } catch (error) {
    appendChatMessage("assistant", error.message);
    setStatus(error.message, true);
  }
}

async function rejectChatProposal() {
  if (!activeProposal?.proposal_id) {
    setStatus("No proposal available for reject action.", true);
    return;
  }

  try {
    const payload = await rejectProposal(
      activeProposal.proposal_id,
      "Rejected from web UI"
    );
    renderProposal(payload.proposal);
    appendChatMessage("assistant", payload.message || "Proposal rejected.");
    setStatus("Proposal rejected.");
  } catch (error) {
    appendChatMessage("assistant", error.message);
    setStatus(error.message, true);
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
      if (!inlinePreview) {
        return { items: [] };
      }

      const cursor = model.getOffsetAt(position);
      if (
        cursor !== inlinePreview.cursor ||
        model.getVersionId() !== inlinePreview.modelVersion
      ) {
        return { items: [] };
      }

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
        useLLM: document.getElementById("use-llm").checked,
      };

      const seq = ++requestSeq;

      try {
        const rulePayload = await fetchSuggestions(context.sql, context.cursor, false);
        if (seq !== requestSeq) {
          return { suggestions: [] };
        }

        const items = normalizeSuggestionItems(rulePayload, "rule");
        applySuggestionsForContext(context, items, null);

        if (context.useLLM) {
          hydrateWithLlm(context, seq, "Auto-complete", false);
        }

        return {
          suggestions: items.map((item) => ({
            label: item.text,
            kind: monaco.languages.CompletionItemKind.Field,
            insertText: item.text,
            range: getReplacementRange(model, context.cursor, position, item.text),
            detail: item.source.toUpperCase(),
          })),
        };
      } catch (error) {
        clearInlinePreview();
        setStatus(error.message, true);
        return { suggestions: [] };
      }
    },
  });

  bindClick("run-complete", manualSuggest);
  const useLlmInput = document.getElementById("use-llm");
  if (useLlmInput) {
    useLlmInput.addEventListener("change", () => {
      scheduleLiveRefresh(30);
    });
  }
  bindClick("tab-suggestions", () => switchPanel("suggestions"));
  bindClick("tab-chat", () => switchPanel("chat"));
  bindClick("chat-plan", submitChatPlan);
  bindClick("chat-refresh", refreshChatProposal);
  bindClick("proposal-approve", approveChatProposal);
  bindClick("proposal-reject", rejectChatProposal);

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
    if (event.keyCode === monaco.KeyCode.Tab && acceptInlinePreviewWithTab()) {
      event.preventDefault();
      event.stopPropagation();
    }
  });

  window.addEventListener("keydown", (event) => {
    if (!event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) {
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
    "Ready. Rule suggestions return first; Tab accepts ghost text; Alt+1..9 inserts right-panel suggestions."
  );
  switchPanel("suggestions");
  renderProposal(null);
  appendChatMessage(
    "assistant",
    "Describe the schema change you want. I will propose DDL first, then wait for approval."
  );
  scheduleLiveRefresh(40);
});

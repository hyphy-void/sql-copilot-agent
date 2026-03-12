const API_BASE = window.location.origin;

let editor;

function setStatus(message, isError = false) {
  const statusEl = document.getElementById("status");
  statusEl.textContent = message;
  statusEl.classList.toggle("error", isError);
}

function renderSuggestionList(suggestions) {
  const listEl = document.getElementById("suggestions");
  listEl.innerHTML = "";

  if (!suggestions || suggestions.length === 0) {
    const item = document.createElement("li");
    item.textContent = "No suggestions";
    listEl.appendChild(item);
    return;
  }

  suggestions.slice(0, 10).forEach((suggestion) => {
    const item = document.createElement("li");
    item.textContent = suggestion;
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
      max_suggestions: 10,
      use_llm: useLLM,
    }),
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Request failed (${response.status}): ${text}`);
  }

  return response.json();
}

async function manualSuggest() {
  const model = editor.getModel();
  const position = editor.getPosition();
  const sql = model.getValue();
  const cursor = model.getOffsetAt(position);
  const useLLM = document.getElementById("use-llm").checked;

  try {
    setStatus("Generating suggestions...");
    const payload = await fetchSuggestions(sql, cursor, useLLM);

    renderSuggestionList(payload.suggestions || []);
    setStatus(
      `Done: ${payload.mode} mode, ${payload.suggestions.length} suggestions`
    );
  } catch (error) {
    renderSuggestionList([]);
    setStatus(error.message, true);
  }
}

require.config({
  paths: {
    vs: "https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.45.0/min/vs",
  },
});

require(["vs/editor/editor.main"], () => {
  monaco.languages.register({ id: "sql" });

  editor = monaco.editor.create(document.getElementById("editor"), {
    value: [
      "SELECT u.",
      "FROM users u",
      "WHERE ",
    ].join("\n"),
    language: "sql",
    theme: "vs",
    fontSize: 14,
    minimap: { enabled: false },
    automaticLayout: true,
  });

  monaco.languages.registerCompletionItemProvider("sql", {
    triggerCharacters: [".", " ", "\n"],
    provideCompletionItems: async (model, position) => {
      const sql = model.getValue();
      const cursor = model.getOffsetAt(position);
      const useLLM = document.getElementById("use-llm").checked;

      try {
        const payload = await fetchSuggestions(sql, cursor, useLLM);

        renderSuggestionList(payload.suggestions || []);
        setStatus(
          `Auto-complete: ${payload.mode}, ${payload.suggestions.length} items`
        );

        return {
          suggestions: (payload.suggestions || []).map((item) => ({
            label: item,
            kind: monaco.languages.CompletionItemKind.Field,
            insertText: item,
            range: undefined,
          })),
        };
      } catch (error) {
        setStatus(error.message, true);
        return { suggestions: [] };
      }
    },
  });

  document.getElementById("run-complete").addEventListener("click", manualSuggest);

  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Space, () => {
    editor.trigger("keyboard", "editor.action.triggerSuggest", {});
  });

  setStatus("Ready. Press Ctrl/Cmd + Space in editor.");
});

const statusEls = {
  service: document.getElementById("serviceState"),
  model: document.getElementById("modelState"),
  adapter: document.getElementById("adapterState"),
  device: document.getElementById("deviceState"),
};

const questionEl = document.getElementById("question");
const maxTokensEl = document.getElementById("maxTokens");
const apiKeyEl = document.getElementById("apiKey");
const presetSelectEl = document.getElementById("presetSelect");
const runBtn = document.getElementById("runBtn");
const clearBtn = document.getElementById("clearBtn");
const exportBtn = document.getElementById("exportBtn");
const authBadgeEl = document.getElementById("authBadge");
const runtimeBadgeEl = document.getElementById("runtimeBadge");
const runBadgeEl = document.getElementById("runBadge");

const answerEl = document.getElementById("answer");
const traceEl = document.getElementById("trace");
const metricsEl = document.getElementById("metrics");
const runsEl = document.getElementById("runs");
const rawEl = document.getElementById("raw");
const runsPageEl = document.getElementById("runsPageContent");
const promptLabEl = document.getElementById("promptLabContent");
const adaptersEl = document.getElementById("adaptersContent");
const datasetsEl = document.getElementById("datasetsContent");
const evalEl = document.getElementById("evalContent");
const deployEl = document.getElementById("deployContent");
const navItems = document.querySelectorAll(".nav-item[data-view]");
const workspaceViews = document.querySelectorAll(".workspace-view[data-view]");

const presetMap = {
  quick_brief: {
    question: "Summarize this issue in 5 bullet points with immediate next actions.",
    mots_max: 120,
  },
  debug_api: {
    question: "Diagnose why the API call is failing and provide a step-by-step fix plan with probable root causes.",
    mots_max: 220,
  },
  product_copy: {
    question: "Write a product announcement with headline, short pitch, and 3 key benefits.",
    mots_max: 180,
  },
  code_review: {
    question: "Review this code with findings first, ordered by severity, with concrete fixes and test gaps.",
    mots_max: 220,
  },
};

const sessionRuns = [];
let latestStatus = null;
let latestTools = null;

function setBadge(element, text, tone = "") {
  element.textContent = text;
  element.classList.remove("ok", "warn", "error");
  if (tone) {
    element.classList.add(tone);
  }
}

function syncAuthBadge() {
  const hasKey = apiKeyEl.value.trim().length > 0;
  setBadge(authBadgeEl, hasKey ? "Auth: provided" : "Auth: missing", hasKey ? "warn" : "");
}

function updateRunBadge(runId) {
  if (!runId) {
    setBadge(runBadgeEl, "Run: none", "");
    return;
  }
  setBadge(runBadgeEl, `Run: ${runId.slice(0, 8)}`, "ok");
}

function renderRuns(runs) {
  if (!runs || runs.length === 0) {
    runsEl.textContent = "No runs yet.";
    if (runsPageEl) {
      runsPageEl.textContent = "No runs yet.";
    }
    return;
  }

  const lines = runs.map((run) => {
    const status = run.status || "unknown";
    const latency = run.latency_ms ?? "-";
    const preview = run.response_preview ? ` | ${run.response_preview}` : "";
    return `${run.id || "n/a"} | ${status} | ${latency}ms${preview}`;
  });

  runsEl.textContent = lines.join("\n");
  if (runsPageEl) {
    runsPageEl.textContent = lines.join("\n");
  }
}

async function fetchRuns() {
  try {
    const res = await fetch("/api/runs?limit=20");
    const body = await res.json();
    renderRuns(body.runs || []);
  } catch {
    runsEl.textContent = "Unable to load run history.";
  }
}

async function fetchStatus() {
  try {
    const res = await fetch("/api/status");
    const data = await res.json();
    latestStatus = data;

    statusEls.service.textContent = data.health || "ok";

    // active_tier is the real field (not mode)
    const activeTier = data.model?.active_tier || "none";
    const defaultTier = data.model?.default_tier || "default";
    statusEls.model.textContent = activeTier !== "none" ? activeTier : defaultTier;

    statusEls.adapter.textContent = data.model?.adapter || "none";
    statusEls.device.textContent = data.model?.device || "cpu";

    // Runtime badge: any tier ready = ready
    const tiers = data.model?.tiers || {};
    const anyReady = Object.values(tiers).some((t) => t.mode === "ready");
    const ollama = Object.keys(tiers).length > 0; // ollama mode always has tiers populated
    const runtimeMode = anyReady ? "ready" : ollama ? "ollama" : "cold";
    const runtimeTone = runtimeMode === "ready" ? "ok" : runtimeMode === "ollama" ? "warn" : "warn";
    setBadge(runtimeBadgeEl, `Runtime: ${runtimeMode}`, runtimeTone);

    metricsEl.textContent = JSON.stringify(
      { model: data.model, limits: data.limits },
      null,
      2
    );

    if (adaptersEl) {
      adaptersEl.textContent = JSON.stringify(
        {
          active_tier: activeTier,
          default_tier: defaultTier,
          tiers: tiers,
          adapter: data.model?.adapter,
          device: data.model?.device,
          limits: data.limits,
        },
        null,
        2
      );
    }

    renderDeployPanel();
  } catch (error) {
    statusEls.service.textContent = "offline";
    setBadge(runtimeBadgeEl, "Runtime: offline", "error");
    traceEl.textContent = `Status fetch failed: ${error}`;
    if (adaptersEl) {
      adaptersEl.textContent = "Unable to load adapter runtime state.";
    }
  }
}

async function fetchTools() {
  try {
    const res = await fetch("/api/tools");
    const data = await res.json();
    latestTools = data;

    if (promptLabEl) {
      const promptLabPayload = {
        presets: Object.keys(presetMap),
        api_tools: data,
      };
      promptLabEl.textContent = JSON.stringify(promptLabPayload, null, 2);
    }

    if (datasetsEl) {
      datasetsEl.textContent = JSON.stringify(data.retrieval || {}, null, 2);
    }

    if (evalEl) {
      const evalPayload = {
        model_routing: data.model_routing,
        tool_calling: data.tool_calling,
      };
      evalEl.textContent = JSON.stringify(evalPayload, null, 2);
    }

    renderDeployPanel();
  } catch {
    if (promptLabEl) {
      promptLabEl.textContent = "Unable to load Prompt Lab capabilities.";
    }
    if (datasetsEl) {
      datasetsEl.textContent = "Unable to load dataset/retrieval capabilities.";
    }
    if (evalEl) {
      evalEl.textContent = "Unable to load eval capabilities.";
    }
  }
}

async function fetchUsage() {
  if (!deployEl) {
    return;
  }

  const key = apiKeyEl.value.trim();
  if (!key) {
    renderDeployPanel("Provide X-API-Key to load usage summary.");
    return;
  }

  try {
    const res = await fetch("/api/usage?days=30", {
      headers: {
        "X-API-Key": key,
      },
    });

    if (!res.ok) {
      const body = await res.json();
      renderDeployPanel(`Usage unavailable: ${body.detail || `HTTP ${res.status}`}`);
      return;
    }

    const usage = await res.json();
    renderDeployPanel("", usage);
  } catch {
    renderDeployPanel("Usage unavailable: network error.");
  }
}

function renderDeployPanel(message = "", usage = null) {
  if (!deployEl) {
    return;
  }

  const payload = {
    service: latestStatus?.service || "unknown",
    health: latestStatus?.health || "unknown",
    limits: latestStatus?.limits || {},
    routing: latestTools?.model_routing || {},
    usage_30d: usage || message || "Provide X-API-Key to load usage summary.",
  };

  deployEl.textContent = JSON.stringify(payload, null, 2);
}

// ── Ollama Setup ──────────────────────────────────────────────────────────────
async function fetchOllamaStatus() {
  const connPill = document.getElementById("ollamaConnPill");
  const urlLabel = document.getElementById("ollamaUrlLabel");
  const modelsList = document.getElementById("ollamaModelsList");
  if (!connPill || !modelsList) return;

  connPill.textContent = "Checking…";
  connPill.className = "signal-pill";

  try {
    const res = await fetch("/api/ollama/status");
    const data = await res.json();

    if (!data.connected) {
      connPill.textContent = "Offline";
      connPill.className = "signal-pill error";
      urlLabel.textContent = data.url || "not configured";
      modelsList.innerHTML = `<div class="ollama-model-row error-row">
        <span class="model-error">${data.error || "Cannot reach Ollama"}</span>
        <span class="model-hint">Start Ollama: <code>ollama serve</code></span>
      </div>`;
      return;
    }

    connPill.textContent = data.all_ready ? "Ready" : "Partial";
    connPill.className = `signal-pill ${data.all_ready ? "ok" : "warn"}`;
    urlLabel.textContent = data.url;

    const rows = Object.entries(data.required_models).map(([model, status]) => {
      const isReady = status === "ready";
      const tierEntry = Object.entries(data.tier_map || {}).find(([, m]) => m === model);
      const tierLabel = tierEntry ? `<span class="tier-tag">${tierEntry[0]}</span>` : "";
      return `<div class="ollama-model-row ${isReady ? "ready" : "missing"}">
        <span class="model-status-dot ${isReady ? "dot-ok" : "dot-missing"}"></span>
        <span class="model-name">${model}</span>
        ${tierLabel}
        <span class="model-status-text">${isReady ? "installed" : "missing"}</span>
        ${!isReady ? `<button class="secondary small pull-btn" data-model="${model}">Pull</button>` : ""}
      </div>`;
    }).join("");

    modelsList.innerHTML = rows;

    // Wire pull buttons
    modelsList.querySelectorAll(".pull-btn").forEach((btn) => {
      btn.addEventListener("click", () => pullOllamaModel(btn.dataset.model));
    });
  } catch (e) {
    connPill.textContent = "Error";
    connPill.className = "signal-pill error";
    modelsList.innerHTML = `<div class="ollama-model-row error-row"><span class="model-error">${e}</span></div>`;
  }
}

async function pullOllamaModel(model) {
  const logEl = document.getElementById("ollamaPullLog");
  if (!logEl) return;
  logEl.style.display = "block";
  logEl.textContent = `Pulling ${model}…\n`;

  try {
    const res = await fetch("/api/ollama/pull", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model }),
    });

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let lastStatus = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const text = decoder.decode(value, { stream: true });
      for (const line of text.split("\n")) {
        if (!line.trim()) continue;
        try {
          const chunk = JSON.parse(line);
          if (chunk.error) {
            logEl.textContent += `Error: ${chunk.error}\n`;
            break;
          }
          const status = chunk.status || "";
          // Deduplicate spammy "downloading" lines but show progress
          if (status !== lastStatus || chunk.completed) {
            const pct = chunk.total > 0 ? ` (${Math.round((chunk.completed / chunk.total) * 100)}%)` : "";
            logEl.textContent += `${status}${pct}\n`;
            logEl.scrollTop = logEl.scrollHeight;
            lastStatus = status;
          }
        } catch {}
      }
    }
    logEl.textContent += `Done.\n`;
  } catch (e) {
    if (logEl) logEl.textContent += `Error: ${e}\n`;
  }

  fetchOllamaStatus();
}
// ─────────────────────────────────────────────────────────────────────────────


function switchTab(target) {
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === target);
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === target);
  });
}

function switchView(target) {
  navItems.forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.view === target);
  });
  workspaceViews.forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.view === target);
  });
}

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});

navItems.forEach((btn) => {
  btn.addEventListener("click", () => {
    switchView(btn.dataset.view);
  });
});

// ── Action button handlers ────────────────────────────────────────────────────
function getKey() {
  return apiKeyEl.value.trim();
}

function flashBtn(btn, msg, tone = "") {
  const orig = btn.textContent;
  btn.textContent = msg;
  btn.disabled = true;
  setTimeout(() => {
    btn.textContent = orig;
    btn.disabled = false;
  }, 1800);
}

document.getElementById("promptLabRefreshBtn")?.addEventListener("click", () => {
  fetchTools();
});

document.getElementById("runsRefreshBtn")?.addEventListener("click", () => {
  fetchRuns();
});

document.getElementById("adapterDefaultBtn")?.addEventListener("click", async () => {
  const btn = document.getElementById("adapterDefaultBtn");
  const tier = document.getElementById("adapterTierSelect")?.value || "fast";
  flashBtn(btn, "Saving…");
  try {
    const res = await fetch("/api/adapter/set-default-tier", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-API-Key": getKey() },
      body: JSON.stringify({ tier }),
    });
    const d = await res.json();
    flashBtn(btn, res.ok ? `✓ Set to ${tier}` : `Error: ${d.detail || res.status}`);
  } catch (e) {
    flashBtn(btn, "Network error");
  }
  fetchStatus();
});

document.getElementById("adapterReloadBtn")?.addEventListener("click", async () => {
  const btn = document.getElementById("adapterReloadBtn");
  const tier = document.getElementById("adapterTierSelect")?.value || "fast";
  flashBtn(btn, "Reloading…");
  try {
    const res = await fetch("/api/adapter/reload", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-API-Key": getKey() },
      body: JSON.stringify({ tier }),
    });
    const d = await res.json();
    flashBtn(btn, res.ok ? `✓ Reloaded` : `Error: ${d.detail || res.status}`);
  } catch (e) {
    flashBtn(btn, "Network error");
  }
  fetchStatus();
});

document.getElementById("datasetValidateBtn")?.addEventListener("click", async () => {
  const btn = document.getElementById("datasetValidateBtn");
  const file = document.getElementById("datasetFileInput")?.value || "dataset_expert.json";
  const minCount = parseInt(document.getElementById("datasetMinCountInput")?.value || "100", 10);
  flashBtn(btn, "Validating…");
  try {
    const res = await fetch("/api/dataset/validate", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-API-Key": getKey() },
      body: JSON.stringify({ file, min_count: minCount }),
    });
    const d = await res.json();
    if (datasetsEl) {
      datasetsEl.textContent = JSON.stringify(d, null, 2);
    }
    flashBtn(btn, res.ok ? "✓ Valid" : `Error: ${d.detail || res.status}`);
  } catch (e) {
    flashBtn(btn, "Network error");
  }
});

document.getElementById("evalRunBtn")?.addEventListener("click", async () => {
  const btn = document.getElementById("evalRunBtn");
  const tier = document.getElementById("evalTierSelect")?.value || "fast";
  flashBtn(btn, "Starting…");
  try {
    const res = await fetch("/api/eval/run", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-API-Key": getKey() },
      body: JSON.stringify({ tier }),
    });
    const d = await res.json();
    if (evalEl) {
      evalEl.textContent = JSON.stringify(d, null, 2);
    }
    flashBtn(btn, res.ok ? "✓ Started" : `Error: ${d.detail || res.status}`);
  } catch (e) {
    flashBtn(btn, "Network error");
  }
});

document.getElementById("evalJobsRefreshBtn")?.addEventListener("click", async () => {
  try {
    const res = await fetch("/api/eval/jobs");
    const d = await res.json();
    if (evalEl) {
      evalEl.textContent = JSON.stringify(d, null, 2);
    }
  } catch {}
});

document.getElementById("deployReloadBtn")?.addEventListener("click", async () => {
  const btn = document.getElementById("deployReloadBtn");
  const tier = document.getElementById("deployTierSelect")?.value || "fast";
  flashBtn(btn, "Reloading…");
  try {
    const res = await fetch("/api/adapter/reload", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-API-Key": getKey() },
      body: JSON.stringify({ tier }),
    });
    flashBtn(btn, res.ok ? "✓ Reloaded" : `HTTP ${res.status}`);
  } catch (e) {
    flashBtn(btn, "Network error");
  }
  fetchStatus();
});

document.getElementById("deployUsageBtn")?.addEventListener("click", () => {
  fetchUsage();
});

document.getElementById("ollamaRefreshBtn")?.addEventListener("click", () => {
  fetchOllamaStatus();
});
// ─────────────────────────────────────────────────────────────────────────────


apiKeyEl.addEventListener("input", syncAuthBadge);
apiKeyEl.addEventListener("change", () => {
  fetchUsage();
});
presetSelectEl.addEventListener("change", () => {
  const preset = presetMap[presetSelectEl.value];
  if (!preset) {
    return;
  }
  questionEl.value = preset.question;
  maxTokensEl.value = String(preset.mots_max);
});

runBtn.addEventListener("click", async () => {
  const payload = {
    question: questionEl.value.trim(),
    mots_max: Number(maxTokensEl.value || 120),
  };

  if (!payload.question) {
    answerEl.textContent = "Please provide a query.";
    return;
  }

  const t0 = performance.now();
  traceEl.textContent = "Dispatching streaming request...";
  rawEl.textContent = "";
  answerEl.textContent = "";
  answerEl.classList.add("streaming");
  switchTab("answer");
  setBadge(runtimeBadgeEl, "Runtime: generating", "warn");

  try {
    const res = await fetch("/chat/stream", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-API-Key": apiKeyEl.value.trim(),
      },
      body: JSON.stringify(payload),
    });

    if (!res.ok || !res.body) {
      const body = await res.json();
      rawEl.textContent = JSON.stringify(body, null, 2);
      traceEl.textContent = `HTTP ${res.status}`;
      answerEl.textContent = body.detail || "Request failed";
      answerEl.classList.remove("streaming");
      setBadge(authBadgeEl, res.status === 403 ? "Auth: rejected" : "Auth: provided", res.status === 403 ? "error" : "warn");
      setBadge(runtimeBadgeEl, "Runtime: error", "error");
      return;
    }

    setBadge(authBadgeEl, "Auth: accepted", "ok");

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    const rawEvents = [];

    while (true) {
      const { value, done } = await reader.read();
      if (done) {
        break;
      }

      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split("\n\n");
      buffer = chunks.pop() || "";

      for (const chunk of chunks) {
        const line = chunk
          .split("\n")
          .find((entry) => entry.startsWith("data: "));

        if (!line) {
          continue;
        }

        const event = JSON.parse(line.slice(6));
        rawEvents.push(event);

        if (event.type === "status") {
          traceEl.textContent = `Streaming: ${event.value}`;
          setBadge(runtimeBadgeEl, `Runtime: ${event.value}`, "warn");
          updateRunBadge(event.run_id || null);
        }

        if (event.type === "delta") {
          answerEl.textContent += event.value;
        }

        if (event.type === "error") {
          answerEl.textContent = event.value || "Streaming failed";
          traceEl.textContent = `HTTP ${event.status || 500}`;
          answerEl.classList.remove("streaming");
          setBadge(runtimeBadgeEl, "Runtime: error", "error");
        }

        if (event.type === "done") {
          const elapsed = Math.round(performance.now() - t0);
          traceEl.textContent = `HTTP 200 stream complete in ${elapsed} ms`;
          answerEl.classList.remove("streaming");
          setBadge(runtimeBadgeEl, "Runtime: ready", "ok");
          sessionRuns.unshift({
            run_id: event.run_id || null,
            question: payload.question,
            mots_max: payload.mots_max,
            answer: answerEl.textContent,
            elapsed_ms: elapsed,
            timestamp: new Date().toISOString(),
          });
          await fetchRuns();
        }
      }

      rawEl.textContent = JSON.stringify(rawEvents, null, 2);
    }
  } catch (error) {
    answerEl.textContent = "Network error during request.";
    traceEl.textContent = String(error);
    answerEl.classList.remove("streaming");
    setBadge(runtimeBadgeEl, "Runtime: error", "error");
  }
});

clearBtn.addEventListener("click", () => {
  questionEl.value = "";
  answerEl.textContent = "";
  traceEl.textContent = "";
  rawEl.textContent = "";
  answerEl.classList.remove("streaming");
  updateRunBadge(null);
});

exportBtn.addEventListener("click", () => {
  const payload = {
    exported_at: new Date().toISOString(),
    session_runs: sessionRuns,
  };

  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `apex-session-${Date.now()}.json`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
});

syncAuthBadge();
updateRunBadge(null);
switchView("chat-ops");
fetchStatus();
fetchRuns();
fetchTools();
renderDeployPanel();
fetchUsage();
fetchOllamaStatus();
setInterval(fetchStatus, 12000);
setInterval(fetchRuns, 15000);
setInterval(fetchTools, 30000);

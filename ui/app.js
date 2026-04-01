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
    return;
  }

  const lines = runs.map((run) => {
    const status = run.status || "unknown";
    const latency = run.latency_ms ?? "-";
    const preview = run.response_preview ? ` | ${run.response_preview}` : "";
    return `${run.id || "n/a"} | ${status} | ${latency}ms${preview}`;
  });

  runsEl.textContent = lines.join("\n");
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

    statusEls.service.textContent = data.health;
    statusEls.model.textContent = data.model.mode;
    statusEls.adapter.textContent = data.model.adapter;
    statusEls.device.textContent = data.model.device;
    const runtimeMode = data.model.mode || "unknown";
    const runtimeTone = runtimeMode === "ready" ? "ok" : runtimeMode === "error" ? "error" : "warn";
    setBadge(runtimeBadgeEl, `Runtime: ${runtimeMode}`, runtimeTone);

    metricsEl.textContent = JSON.stringify(data.limits, null, 2);
  } catch (error) {
    statusEls.service.textContent = "offline";
    setBadge(runtimeBadgeEl, "Runtime: offline", "error");
    traceEl.textContent = `Status fetch failed: ${error}`;
  }
}

function switchTab(target) {
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === target);
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === target);
  });
}

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});

apiKeyEl.addEventListener("input", syncAuthBadge);
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
fetchStatus();
fetchRuns();
setInterval(fetchStatus, 12000);
setInterval(fetchRuns, 15000);

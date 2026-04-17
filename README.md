# Apex LLM

Apex LLM is a local-first FastAPI gateway for chat inference, model-tier routing, usage metering, and LoRA experimentation.

![Apex Hero](assets/apex-hero.svg)

[![CI](https://github.com/tovrr/Apex_LLM/actions/workflows/ci.yml/badge.svg)](https://github.com/tovrr/Apex_LLM/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.12-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-API-0ea5e9)

## What You Get

- FastAPI chat API with API-key auth and quotas.
- Request-id structured logging and usage ledger.
- Model tiers: fast, default, reasoning.
- v2 chat contract with context chunks and tools metadata.
- LoRA training pipeline with Colab-ready notebook.
- Quill proxy templates for server-side key injection.

## Architecture

![Apex Architecture](assets/architecture.svg)

## Live Demo Snapshot

![Apex Demo](assets/apex-demo.svg)

## Current Model Routing

- fast: qwen3:1.7b
- default: qwen3.5:9b
- reasoning: qwen3.5:27b

Routing is configured via environment variables in [.env.example](.env.example).

## Quick Start

### 1) Install dependencies

```powershell
python -m venv venv
venv\Scripts\python.exe -m pip install --upgrade pip
venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 2) Configure environment

Create [.env](.env) from [.env.example](.env.example) and set at least:

```dotenv
APEX_API_KEY=your_key_here
APEX_MODEL_FAST_NAME=microsoft/Phi-4-mini-instruct
APEX_MODEL_FAST_LORA_DIR=
```

For local low-RAM development, you can bypass heavy model loading:

```dotenv
APEX_SKIP_MODEL_LOAD=1
```

### 3) Run API

```powershell
venv\Scripts\python.exe -m uvicorn serveur_api:app --reload
```

Health check:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -Method GET
```

### RunPod one-command bootstrap

For a single-pod setup (Apex + Ollama + 3 model tiers), use:

```bash
export APEX_API_KEY="replace-me"
./scripts/runpod_bootstrap_apex.sh
```

### RunPod recommended setup (supervised)

For production-like stability on a single pod (auto-restart for Ollama + Apex + watchdog), use:

```bash
cd /workspace/Apex_LLM
export APEX_API_KEY="replace-me"
./scripts/runpod_supervisor_setup.sh
supervisord -c ./scripts/supervisord.apex.conf
```

This setup provides:

- Process supervision for `ollama serve` and `uvicorn`.
- Automatic restart when a process exits.
- A local watchdog that checks `/health` and forces restart if Apex is stuck.
- A persistent runtime env file at `.env.runpod`.

Quick verification from inside the pod:

```bash
./venv/bin/python - <<'PY'
import httpx

health = httpx.get("http://127.0.0.1:8000/health", timeout=10)
print("health", health.status_code, health.text)

resp = httpx.post(
  "http://127.0.0.1:8000/v1/chat/completions",
  headers={"Authorization": "Bearer replace-me", "Content-Type": "application/json"},
  json={
    "model": "default",
    "messages": [{"role": "user", "content": "Reply exactly OK"}],
    "max_tokens": 32,
    "temperature": 0,
  },
  timeout=120,
)
print("chat", resp.status_code, resp.text)
PY
```

RunPod HTTP service check (outside the pod):

- `GET /health` must return `{"status":"ok"}`.
- `POST /v1/chat/completions` must be called with JSON body and Authorization header.
- Opening `/v1/chat/completions` directly in a browser issues `GET` and returns `{"detail":"Method Not Allowed"}` by design.

Common troubleshooting:

- `python: command not found`: run `./scripts/runpod_supervisor_setup.sh` (installs Python 3 + venv + pip).
- `Exit 127` on uvicorn start: usually missing interpreter path; use supervisor setup and start scripts above.
- 502 from proxy with 200 on `/health` previously: Apex process likely crashed/restarted; check `/workspace/apex-supervisor.err.log` and `/workspace/apex.log`.

### Tier smoke test

After Apex is running, validate all tiers with:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\smoke_test_tiers.ps1 -BaseUrl http://127.0.0.1:8000 -ApiKey <your_apex_key>
```

## API Surface

- POST /chat
- POST /chat/stream
- POST /chat/v2
- GET /api/tools
- GET /api/usage
- GET /api/status
- GET /api/runs
- GET /developer
- GET /pricing

## Distillation Workflow

### Local validation

Validate dataset file format and minimum size:

```powershell
venv\Scripts\python.exe evals/validate_dataset_expert.py --file dataset_expert_v4.json --min-count 200
```

Generate a 100-example template dataset (project helper):

```powershell
venv\Scripts\python.exe evals/generate_dataset_expert_100.py
```

### GPU training (Colab)

Use [colab_finetune.ipynb](colab_finetune.ipynb), then export `apex_lora_final.zip` and update [apex_lora_sauvegarde](apex_lora_sauvegarde).

Current defaults in [apex_lora.py](apex_lora.py):

- Base model: `unsloth/phi-4-unsloth-bnb-4bit`
- Dataset: `dataset_expert_v4.json`
- LoRA smoke profile: `max_steps=10`

Recommended Colab sequence:

1. Restart runtime before retrying after any training crash.
2. Re-run cells 1 -> 2 -> 3 -> 4 in order.
3. Only run export/download after training finishes without traceback.

Note: the notebook prints `=== FINE-TUNING COMPLETE ===` after the shell command returns; always verify the logs above for traceback-free completion.

## Testing

```powershell
venv\Scripts\python.exe -m pytest -q
```

CI runs on push and pull request through [ci.yml](.github/workflows/ci.yml).

## Repository Map

- [serveur_api.py](serveur_api.py): FastAPI server and model routing.
- [key_store.py](key_store.py): API keys, quotas, usage events.
- [apex_lora.py](apex_lora.py): LoRA training entrypoint.
- [dataset_expert_v4.json](dataset_expert_v4.json): Current distillation dataset.
- [evals](evals): Dataset validation and eval helpers.
- [quill-proxy](quill-proxy): Next.js proxy templates.

## Web & Mobile Integration

### Server-side proxy (recommended)

Never expose `APEX_API_KEY` in browser or mobile clients. Route all requests through a server-side proxy.

A Next.js proxy template is provided in [quill-proxy](quill-proxy). It injects the key server-side and forwards to `/chat` or `/chat/stream`.

Minimal fetch from the browser:

```js
const res = await fetch('/api/apex/chat', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ question: 'Hello', mots_max: 200, model_tier: 'fast' }),
});
const { reponse_apex } = await res.json();
```

### Streaming (SSE)

Use `/chat/stream` for token-by-token streaming. The endpoint emits `data: <token>\n\n` chunks.

```js
const res = await fetch('/api/apex/stream', { method: 'POST', ... });
const reader = res.body.getReader();
const decoder = new TextDecoder();
while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  const chunk = decoder.decode(value);
  // parse 'data: ...' lines
}
```

### OpenAI-compatible endpoint

For clients that support the OpenAI SDK, use:

```http
POST /v1/chat/completions
```

with `model: "apex:fast"` (or `apex:default`, `apex:reasoning`). Tool-call detection is supported via `tools` and `tool_choice` fields.

### Ollama local fallback

Set `APEX_OLLAMA_URL=http://127.0.0.1:11434` to delegate inference to a local Ollama instance. The LoRA adapter is **not** applied in Ollama mode — it is only used when loading the HuggingFace model directly (requires GPU).

## Notes

- Keep secrets out of git. Use [.env](.env) locally.
- LoRA artifacts and backups should stay local unless intentionally published.
- Large model files (`*.safetensors`, `*.pt`, `*.bin`) are tracked via Git LFS.

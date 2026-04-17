# RunPod Supervised Setup

This runbook configures a single RunPod pod with supervised processes:

- `ollama serve`
- Apex API (`uvicorn serveur_api:app`)
- local watchdog for `/health`

## 1. Pod Requirements

- Exposed HTTP ports: `11434,8000`
- Exposed TCP ports: `22`
- Volume mount path: `/workspace`
- Repo checked out at `/workspace/Apex_LLM`

## 2. One-Time Setup

```bash
cd /workspace/Apex_LLM
export APEX_API_KEY="replace-me"
./scripts/runpod_supervisor_setup.sh
```

This installs Python/runtime deps, prepares `venv`, installs `requirements.txt`, and writes `.env.runpod`.

## 3. Start Supervised Stack

```bash
cd /workspace/Apex_LLM
supervisord -c ./scripts/supervisord.apex.conf
```

## 4. Verify

```bash
cd /workspace/Apex_LLM
./venv/bin/python - <<'PY'
import httpx

h = httpx.get("http://127.0.0.1:8000/health", timeout=10)
print("health", h.status_code, h.text)

r = httpx.post(
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
print("chat", r.status_code, r.text)
PY
```

## 5. External Check

- `GET https://<pod>-8000.proxy.runpod.net/health`
- `POST https://<pod>-8000.proxy.runpod.net/v1/chat/completions`

Note: Opening `/v1/chat/completions` in a browser sends `GET` and returns `Method Not Allowed` by design.

## 6. Useful Logs

- `/workspace/supervisord.log`
- `/workspace/ollama-supervisor.log`
- `/workspace/apex-supervisor.log`
- `/workspace/apex-watchdog.log`

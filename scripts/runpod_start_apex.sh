#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ -f "$ROOT_DIR/.env.runpod" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env.runpod"
  set +a
fi

: "${APEX_API_KEY:?APEX_API_KEY is required}"

PORT="${PORT:-8000}"
APEX_OLLAMA_URL="${APEX_OLLAMA_URL:-http://127.0.0.1:11434}"
APEX_OLLAMA_MODEL_FAST="${APEX_OLLAMA_MODEL_FAST:-qwen3:1.7b}"
APEX_OLLAMA_MODEL_DEFAULT="${APEX_OLLAMA_MODEL_DEFAULT:-qwen3.5:9b}"
APEX_OLLAMA_MODEL_REASONING="${APEX_OLLAMA_MODEL_REASONING:-qwen3.5:27b}"

VENV_PY="$ROOT_DIR/venv/bin/python"
if [ ! -x "$VENV_PY" ]; then
  echo "[ERROR] Missing Python in $VENV_PY. Run scripts/runpod_supervisor_setup.sh first."
  exit 1
fi

if [ "${APEX_PULL_MODELS_ON_START:-1}" = "1" ]; then
  if command -v ollama >/dev/null 2>&1; then
    echo "[INFO] Pulling Ollama models (idempotent)..."
    ollama pull "$APEX_OLLAMA_MODEL_FAST" || true
    ollama pull "$APEX_OLLAMA_MODEL_DEFAULT" || true
    ollama pull "$APEX_OLLAMA_MODEL_REASONING" || true
  else
    echo "[WARN] ollama command not found; skipping model pull."
  fi
fi

echo "[INFO] Starting Apex API on 0.0.0.0:${PORT}"
exec "$VENV_PY" -m uvicorn serveur_api:app --host 0.0.0.0 --port "$PORT"

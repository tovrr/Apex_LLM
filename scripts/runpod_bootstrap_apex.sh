#!/usr/bin/env bash
set -euo pipefail

# RunPod bootstrap for Apex + Ollama on a single GPU pod.
# Usage:
#   export APEX_API_KEY="replace-me"
#   ./scripts/runpod_bootstrap_apex.sh

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

: "${APEX_API_KEY:?APEX_API_KEY is required}"

PORT="${PORT:-8000}"
APEX_OLLAMA_URL="${APEX_OLLAMA_URL:-http://127.0.0.1:11434}"
APEX_OLLAMA_MODEL_FAST="${APEX_OLLAMA_MODEL_FAST:-qwen3:1.7b}"
APEX_OLLAMA_MODEL_DEFAULT="${APEX_OLLAMA_MODEL_DEFAULT:-qwen3.5:9b}"
APEX_OLLAMA_MODEL_REASONING="${APEX_OLLAMA_MODEL_REASONING:-qwen3.5:27b}"
APEX_OLLAMA_NUM_CTX="${APEX_OLLAMA_NUM_CTX:-4096}"
APEX_MODEL_DEFAULT_TIER="${APEX_MODEL_DEFAULT_TIER:-default}"

if ! command -v ollama >/dev/null 2>&1; then
  echo "[ERROR] ollama is not installed in this container."
  exit 1
fi

PYTHON_BIN=""
if [ -x "$ROOT_DIR/venv/bin/python" ]; then
  PYTHON_BIN="$ROOT_DIR/venv/bin/python"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python)"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
else
  echo "[ERROR] python/python3 is not installed in this container."
  exit 1
fi

if ! pgrep -f "ollama serve" >/dev/null 2>&1; then
  echo "[INFO] Starting Ollama service..."
  nohup ollama serve >/tmp/ollama.log 2>&1 &
  sleep 3
fi

echo "[INFO] Pulling required models..."
ollama pull "$APEX_OLLAMA_MODEL_FAST"
ollama pull "$APEX_OLLAMA_MODEL_DEFAULT"
ollama pull "$APEX_OLLAMA_MODEL_REASONING"

echo "[INFO] Exporting Apex runtime environment..."
export APEX_API_KEY
export APEX_OLLAMA_URL
export APEX_OLLAMA_MODEL_FAST
export APEX_OLLAMA_MODEL_DEFAULT
export APEX_OLLAMA_MODEL_REASONING
export APEX_OLLAMA_NUM_CTX
export APEX_MODEL_DEFAULT_TIER

echo "[INFO] Starting Apex API on 0.0.0.0:${PORT} using $PYTHON_BIN"
exec "$PYTHON_BIN" -m uvicorn serveur_api:app --host 0.0.0.0 --port "$PORT"

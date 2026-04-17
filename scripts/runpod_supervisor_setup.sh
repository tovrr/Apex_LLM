#!/usr/bin/env bash
set -euo pipefail

# Setup supervised Apex + Ollama runtime for RunPod containers.
# Usage:
#   export APEX_API_KEY="replace-me"
#   ./scripts/runpod_supervisor_setup.sh

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
APEX_PULL_MODELS_ON_START="${APEX_PULL_MODELS_ON_START:-1}"

install_deps() {
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y python3 python3-venv python3-pip python-is-python3 curl ca-certificates supervisor
    return
  fi

  if command -v apk >/dev/null 2>&1; then
    apk add --no-cache python3 py3-pip py3-virtualenv curl ca-certificates supervisor
    return
  fi

  echo "[ERROR] Unsupported package manager. Install Python 3, pip, venv, curl, and supervisord manually."
  exit 1
}

if ! command -v ollama >/dev/null 2>&1; then
  echo "[ERROR] ollama is not installed in this container image."
  exit 1
fi

echo "[INFO] Installing runtime dependencies..."
install_deps

echo "[INFO] Creating/updating venv at $ROOT_DIR/venv"
python3 -m venv "$ROOT_DIR/venv"
"$ROOT_DIR/venv/bin/python" -m pip install --upgrade pip setuptools wheel
"$ROOT_DIR/venv/bin/pip" install -r "$ROOT_DIR/requirements.txt"

cat > "$ROOT_DIR/.env.runpod" <<EOF
APEX_API_KEY=${APEX_API_KEY}
PORT=${PORT}
APEX_OLLAMA_URL=${APEX_OLLAMA_URL}
APEX_OLLAMA_MODEL_FAST=${APEX_OLLAMA_MODEL_FAST}
APEX_OLLAMA_MODEL_DEFAULT=${APEX_OLLAMA_MODEL_DEFAULT}
APEX_OLLAMA_MODEL_REASONING=${APEX_OLLAMA_MODEL_REASONING}
APEX_OLLAMA_NUM_CTX=${APEX_OLLAMA_NUM_CTX}
APEX_MODEL_DEFAULT_TIER=${APEX_MODEL_DEFAULT_TIER}
APEX_PULL_MODELS_ON_START=${APEX_PULL_MODELS_ON_START}
EOF

chmod +x "$ROOT_DIR/scripts/runpod_start_apex.sh"
chmod +x "$ROOT_DIR/scripts/runpod_watchdog.sh"

echo "[INFO] Setup complete."
echo "[INFO] To run under supervision:"
echo "       supervisord -c $ROOT_DIR/scripts/supervisord.apex.conf"
echo "[INFO] To verify after startup:"
echo "       $ROOT_DIR/venv/bin/python -c \"import httpx; print(httpx.get('http://127.0.0.1:8000/health', timeout=5).text)\""

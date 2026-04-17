#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PY="$ROOT_DIR/venv/bin/python"
INTERVAL="${APEX_WATCHDOG_INTERVAL_SECONDS:-30}"
MAX_FAILS="${APEX_WATCHDOG_MAX_FAILS:-3}"
FAILS=0

if [ ! -x "$VENV_PY" ]; then
  echo "[WATCHDOG] Missing Python interpreter at $VENV_PY"
  exit 1
fi

while true; do
  if "$VENV_PY" - <<'PY'
import sys
import httpx

try:
    r = httpx.get("http://127.0.0.1:8000/health", timeout=5.0)
    if r.status_code == 200:
        sys.exit(0)
except Exception:
    pass
sys.exit(1)
PY
  then
    FAILS=0
  else
    FAILS=$((FAILS + 1))
    echo "[WATCHDOG] Health check failed ($FAILS/$MAX_FAILS)"
  fi

  if [ "$FAILS" -ge "$MAX_FAILS" ]; then
    echo "[WATCHDOG] Restarting Apex process"
    pkill -f "uvicorn serveur_api:app" || true
    FAILS=0
  fi

  sleep "$INTERVAL"
done

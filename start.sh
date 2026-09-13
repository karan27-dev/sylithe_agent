#!/usr/bin/env bash
# Sovereign Workbench - one command. No internet required.
set -euo pipefail
cd "$(dirname "$0")/backend"

VENV="${VENV:-../.venv}"
PY="$VENV/bin/python"
[ -x "$PY" ] || { echo "No venv at $VENV - see README (Install)."; exit 1; }

if ! curl -s --max-time 2 http://localhost:11434/api/version >/dev/null; then
  echo "Ollama is not running - starting it..."
  (ollama serve >/dev/null 2>&1 &)
  for _ in $(seq 1 30); do
    curl -s --max-time 1 http://localhost:11434/api/version >/dev/null && break
    sleep 1
  done
fi

echo "Checking models..."
"$PY" -c "
from core.llm import Client
h = Client().health()
print(f\"  profile={h['profile']}  engine={'up' if h['up'] else 'DOWN'}\")
if h.get('missing'): raise SystemExit(f\"  MISSING: {h['missing']}\")
"

echo "Refreshing index..."
"$PY" -m ingest.pipeline build 2>/dev/null | grep -E "^  |files ->" || true

exec "$PY" -m api.app

#!/usr/bin/env bash
# Sovereign Workbench - one command. No internet required.
set -euo pipefail
cd "$(dirname "$0")/backend"

# Secrets come from .env, which is gitignored. Nothing reads a key from
# models.yaml on purpose: that file is committed, and a key in git is a key
# that has leaked.
#
# Parsed, never sourced. `. .env` runs the file as a shell script, so a stray
# token becomes a command - a real .env here had two keys on one line and bash
# tried to EXECUTE the second one, which both killed startup and would run
# whatever a pasted line happened to say. Only NAME=value lines are taken;
# anything else is reported and skipped.
if [ -f ../.env ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      ''|'#'*) continue ;;
    esac
    name=${line%%=*}
    value=${line#*=}
    case "$line" in
      *=*) ;;
      *) echo "  .env: ignoring line without '=' : ${line:0:12}..." ; continue ;;
    esac
    case "$name" in
      *[!A-Za-z0-9_]*|'') echo "  .env: ignoring bad name '${name:0:24}'" ; continue ;;
    esac
    # Trim what a paste leaves behind. A real .env here read
    # "TIER_L_API_KEY= sk-..." - one leading space, which travels all the way
    # into the Authorization header and fails auth with a message that blames
    # the key. Windows line endings do the same thing invisibly.
    value=${value%$'\r'}
    value="${value#"${value%%[![:space:]]*}"}"     # strip leading whitespace
    value="${value%"${value##*[![:space:]]}"}"     # strip trailing whitespace
    case "$value" in
      *[[:space:]]*) echo "  .env: $name value has whitespace INSIDE it -"\
                          " two keys on one line?" ;;
    esac
    export "$name=$value"
  done < ../.env
fi

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

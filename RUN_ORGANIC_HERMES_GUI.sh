#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${ROOT}/ORGANIC_ENV.sh"
PYTHON_EXE="${ROOT}/.venv/bin/python"

if [[ ! -x "${PYTHON_EXE}" || ! -x "${ROOT}/organic_runtime/.venv/bin/python" ]]; then
  echo "[Organic AI] Dependencies are not ready. Run ./SETUP_ORGANIC_HERMES.sh"
  exit 1
fi

"${PYTHON_EXE}" "${ROOT}/scripts/configure_organic_hermes.py" >/dev/null
export ORGANIC_HERMES_PLUGIN_ENABLED="1"
"${PYTHON_EXE}" "${ROOT}/scripts/ensure_organic_runtime.py"

if ! command -v ollama >/dev/null 2>&1; then
  echo "[Organic AI] Warning: Ollama is not installed or not on PATH."
else
  if ! ollama list 2>/dev/null | grep -qi 'qwen3:0.6b'; then
    echo "[Organic AI] Warning: qwen3:0.6b is missing. Run: ollama pull qwen3:0.6b"
  fi
  if ! ollama list 2>/dev/null | grep -Fqi "${ORGANIC_HERMES_CHAT_MODEL}"; then
    echo "[Organic AI] Warning: ${ORGANIC_HERMES_CHAT_MODEL} is missing. Run: ollama pull ${ORGANIC_HERMES_CHAT_MODEL}"
  fi
fi

echo "[Organic AI] Starting Hermes dashboard at http://127.0.0.1:9119/organic"
exec "${PYTHON_EXE}" -m hermes_cli.main dashboard --host 127.0.0.1 --port 9119 "$@"

#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${ROOT}/ORGANIC_ENV.sh"
PYTHON_BIN="${PYTHON:-python3}"

echo "[Organic AI] Setting up Hermes and Organic runtime on Linux."

if [[ ! -x "${ROOT}/.venv/bin/python" ]]; then
  "${PYTHON_BIN}" -m venv "${ROOT}/.venv"
fi
"${ROOT}/.venv/bin/python" -m pip install --upgrade pip
"${ROOT}/.venv/bin/python" -m pip install -e "${ROOT}"
"${ROOT}/.venv/bin/python" -m pip install mcp==1.28.1

if command -v uv >/dev/null 2>&1; then
  (cd "${ROOT}/organic_runtime" && uv sync --extra dev --extra thought)
else
  if [[ ! -x "${ROOT}/organic_runtime/.venv/bin/python" ]]; then
    "${PYTHON_BIN}" -m venv "${ROOT}/organic_runtime/.venv"
  fi
  "${ROOT}/organic_runtime/.venv/bin/python" -m pip install --upgrade pip
  "${ROOT}/organic_runtime/.venv/bin/python" -m pip install -e "${ROOT}/organic_runtime[dev,thought]"
fi

"${ROOT}/.venv/bin/python" "${ROOT}/scripts/configure_organic_hermes.py"
echo "[Organic AI] Setup complete. Run ./RUN_ORGANIC_HERMES_GUI.sh"

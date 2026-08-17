#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${ROOT}/ORGANIC_ENV.sh"

if [[ ! -x "${ROOT}/.venv/bin/python" ]]; then
  echo "[Organic AI] Dependencies are not ready. Run ./SETUP_ORGANIC_HERMES.sh"
  exit 1
fi
"${ROOT}/.venv/bin/python" "${ROOT}/scripts/ensure_organic_runtime.py"
echo "[Organic AI] Standalone runtime GUI: http://127.0.0.1:8788"
if command -v xdg-open >/dev/null 2>&1; then
  xdg-open http://127.0.0.1:8788 >/dev/null 2>&1 || true
fi

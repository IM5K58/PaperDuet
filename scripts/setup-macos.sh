#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "$(uname -s)" != Darwin ]]; then echo 'Run this script on macOS.' >&2; exit 1; fi
python_bin="${PYTHON:-python3.12}"
if [[ ! -x .venv/bin/python ]]; then "$python_bin" -m venv .venv; fi
.venv/bin/python -m pip install -r backend/requirements-lock.txt
npm ci
npm run build:sidecar
echo 'Setup complete. Run npm run dev.'

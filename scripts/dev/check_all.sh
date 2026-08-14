#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
REPO_DIR=$(realpath "$SCRIPT_DIR/../..")
PROJECT_PYTHON="$REPO_DIR/.venv-ruff/bin/python"

if [ -x "$PROJECT_PYTHON" ]; then
	PYTHON=("$PROJECT_PYTHON")
else
	PYTHON=(python3)
fi

cd "$REPO_DIR"

echo "[1/8] Repository confidentiality guard"
"${PYTHON[@]}" scripts/dev/check_repository_confidentiality.py

echo "[2/8] DBus isolation guard"
"${PYTHON[@]}" scripts/dev/check_dbus_isolation.py

echo "[3/8] Architecture contracts"
"${PYTHON[@]}" scripts/dev/check_architecture_contracts.py

echo "[4/8] Syntax check"
"${PYTHON[@]}" -m compileall -q venus_evcharger scripts
"${PYTHON[@]}" -m py_compile \
	venus_evcharger_service.py \
	venus_evcharger_dbus_adapter.py \
	venus_evchargerctl.py

echo "[5/8] Rust observer contracts"
bash "$SCRIPT_DIR/run_rust_observer_checks.sh"

echo "[6/8] Lint"
bash "$SCRIPT_DIR/run_lint.sh"

echo "[7/8] Unit tests"
"${PYTHON[@]}" -m unittest discover -s tests -p 'test_*.py'

echo "[8/8] Type check"
bash "$SCRIPT_DIR/run_typecheck.sh"

echo "All checks passed."

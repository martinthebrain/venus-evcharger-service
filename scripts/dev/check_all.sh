#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
REPO_DIR=$(realpath "$SCRIPT_DIR/../..")
PROJECT_PYTHON="$REPO_DIR/.venv-ruff/bin/python"
RUN_PYTHON_TESTS=true

usage() {
	echo "Usage: $0 [--skip-python-tests]" >&2
	exit 2
}

if [ "$#" -gt 0 ]; then
	[ "$1" = "--skip-python-tests" ] || usage
	RUN_PYTHON_TESTS=false
	shift
fi
[ "$#" -eq 0 ] || usage

if [ -x "$PROJECT_PYTHON" ]; then
	PYTHON=("$PROJECT_PYTHON")
else
	PYTHON=(python3)
fi

cd "$REPO_DIR"

echo "[1/10] Repository confidentiality guard"
"${PYTHON[@]}" scripts/dev/check_repository_confidentiality.py

echo "[2/10] DBus isolation guard"
"${PYTHON[@]}" scripts/dev/check_dbus_isolation.py

echo "[3/10] Architecture contracts"
"${PYTHON[@]}" scripts/dev/check_architecture_contracts.py

echo "[4/10] Syntax check"
"${PYTHON[@]}" -m compileall -q venus_evcharger scripts
"${PYTHON[@]}" -m py_compile \
	venus_evcharger_service.py \
	venus_evcharger_dbus_adapter.py \
	venus_evchargerctl.py

echo "[5/10] Rust observer contracts"
bash "$SCRIPT_DIR/run_rust_observer_checks.sh"

echo "[6/10] Rust auto-input-helper contracts"
bash "$SCRIPT_DIR/run_rust_auto_input_checks.sh"

echo "[7/10] Rust DBus adapter contracts"
bash "$SCRIPT_DIR/run_rust_dbus_adapter_checks.sh"

echo "[8/10] Lint"
bash "$SCRIPT_DIR/run_lint.sh"

echo "[9/10] Unit tests"
if [ "$RUN_PYTHON_TESTS" = true ]; then
	"${PYTHON[@]}" -m unittest discover -s tests -p 'test_*.py'
else
	echo "Deferred to the instrumented coverage gate"
fi

echo "[10/10] Type check"
bash "$SCRIPT_DIR/run_typecheck.sh"

echo "All checks passed."

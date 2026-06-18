#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
REPO_DIR=$(realpath "$SCRIPT_DIR/../..")
DEFAULT_VENV_MYPY="$HOME/.venvs/mypy/bin/mypy"
MYPY_MODULE_CHECK='import importlib.util, sys; sys.exit(0 if importlib.util.find_spec("mypy") else 1)'

if command -v mypy >/dev/null 2>&1; then
	MYPY_CMD=("$(command -v mypy)")
elif [[ -x "${MYPY_BIN:-}" ]]; then
	MYPY_CMD=("${MYPY_BIN}")
elif [[ -x "$DEFAULT_VENV_MYPY" ]]; then
	MYPY_CMD=("$DEFAULT_VENV_MYPY")
elif python3 -c "$MYPY_MODULE_CHECK" >/dev/null 2>&1; then
	# Some CI environments expose mypy only as a Python module and not as a
	# standalone executable on PATH.
	MYPY_CMD=(python3 -m mypy)
else
	echo "mypy is not installed or not on PATH."
	echo "Install it in a venv, for example:"
	echo "  python3 -m venv ~/.venvs/mypy"
	echo "  ~/.venvs/mypy/bin/pip install mypy"
	exit 127
fi

cd "$REPO_DIR"
"${MYPY_CMD[@]}" --config-file "$REPO_DIR/mypy.ini"
"${MYPY_CMD[@]}" --config-file "$REPO_DIR/mypy_strict.ini"

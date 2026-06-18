#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
REPO_DIR=$(realpath "$SCRIPT_DIR/../..")
DEFAULT_VENV_MYPY="$HOME/.venvs/mypy/bin/mypy"
DEFAULT_VENV_PYRIGHT="$HOME/.venvs/pyright/bin/pyright"
PROJECT_VENV_PYRIGHT="$REPO_DIR/.venv-ruff/bin/pyright"
MYPY_MODULE_CHECK='import importlib.util, sys; sys.exit(0 if importlib.util.find_spec("mypy") else 1)'
PYRIGHT_MODULE_CHECK='import importlib.util, sys; sys.exit(0 if importlib.util.find_spec("pyright") else 1)'

if command -v mypy >/dev/null 2>&1; then
	MYPY_CMD=("$(command -v mypy)")
elif [[ -x "${MYPY_BIN:-}" ]]; then
	MYPY_CMD=("${MYPY_BIN}")
elif [[ -x "$DEFAULT_VENV_MYPY" ]]; then
	MYPY_CMD=("$DEFAULT_VENV_MYPY")
elif python3 -c "$MYPY_MODULE_CHECK" >/dev/null 2>&1; then
	MYPY_CMD=(python3 -m mypy)
else
	echo "mypy is not installed or not on PATH."
	echo "Install it in a venv, for example:"
	echo "  python3 -m venv ~/.venvs/mypy"
	echo "  ~/.venvs/mypy/bin/pip install mypy"
	exit 127
fi

if command -v pyright >/dev/null 2>&1 && "$(command -v pyright)" --version >/dev/null 2>&1; then
	PYRIGHT_CMD=("$(command -v pyright)")
elif [[ -x "${PYRIGHT_BIN:-}" ]]; then
	PYRIGHT_CMD=("${PYRIGHT_BIN}")
elif [[ -x "$PROJECT_VENV_PYRIGHT" ]] && "$PROJECT_VENV_PYRIGHT" --version >/dev/null 2>&1; then
	PYRIGHT_CMD=("$PROJECT_VENV_PYRIGHT")
elif [[ -x "$DEFAULT_VENV_PYRIGHT" ]] && "$DEFAULT_VENV_PYRIGHT" --version >/dev/null 2>&1; then
	PYRIGHT_CMD=("$DEFAULT_VENV_PYRIGHT")
elif python3 -c "$PYRIGHT_MODULE_CHECK" >/dev/null 2>&1; then
	PYRIGHT_CMD=(python3 -m pyright)
else
	echo "pyright is not installed or not on PATH."
	echo "Install it in a venv, for example:"
	echo "  python3 -m venv ~/.venvs/pyright"
	echo "  ~/.venvs/pyright/bin/pip install pyright"
	exit 127
fi

cd "$REPO_DIR"
"${MYPY_CMD[@]}" --config-file "$REPO_DIR/mypy_strict.ini"
"${PYRIGHT_CMD[@]}" --project "$REPO_DIR/pyrightconfig.json"

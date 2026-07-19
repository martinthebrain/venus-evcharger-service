#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
REPO_DIR=$(realpath "$SCRIPT_DIR/../..")
PROJECT_VENV_PYTHON="$REPO_DIR/.venv-ruff/bin/python"
COVERAGE_MODULE_CHECK='import importlib.util, sys; sys.exit(0 if importlib.util.find_spec("coverage") else 1)'

if [ -x "$PROJECT_VENV_PYTHON" ] && "$PROJECT_VENV_PYTHON" -c "$COVERAGE_MODULE_CHECK"; then
	COVERAGE=("$PROJECT_VENV_PYTHON" -m coverage)
elif python3 -c "$COVERAGE_MODULE_CHECK"; then
	COVERAGE=(python3 -m coverage)
else
	echo "coverage is required. Install the pinned dev tools from requirements-dev.lock." >&2
	exit 1
fi

cd "$REPO_DIR"
"${COVERAGE[@]}" erase
"${COVERAGE[@]}" run --rcfile="$REPO_DIR/.coveragerc" -m unittest discover -s tests -p 'test_*.py' -b

echo "[coverage] Unit profile"
"${COVERAGE[@]}" report --rcfile="$REPO_DIR/.coveragerc.unit"

echo "[coverage] Platform-contract profile"
"${COVERAGE[@]}" report --rcfile="$REPO_DIR/.coveragerc.platform"

"${COVERAGE[@]}" xml --rcfile="$REPO_DIR/.coveragerc"

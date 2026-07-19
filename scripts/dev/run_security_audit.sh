#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
REPO_DIR=$(realpath "$SCRIPT_DIR/../..")

cd "$REPO_DIR"

if [ -x "$REPO_DIR/.venv-ruff/bin/python" ] && "$REPO_DIR/.venv-ruff/bin/python" -m bandit --version >/dev/null 2>&1; then
	BANDIT=("$REPO_DIR/.venv-ruff/bin/python" -m bandit)
elif python3 -m bandit --version >/dev/null 2>&1; then
	BANDIT=(python3 -m bandit)
else
	echo "bandit is required for the security audit. Install it with: .venv-ruff/bin/python -m pip install bandit" >&2
	exit 1
fi

"${BANDIT[@]}" \
	-q \
	-r \
	--severity-level high \
	venus_evcharger \
	venus_evcharger_service.py \
	venus_evcharger_observer.py \
	venus_evcharger_dbus_adapter.py \
	venus_evchargerctl.py \
	scripts/dev

"$SCRIPT_DIR/run_dependency_audit.sh"

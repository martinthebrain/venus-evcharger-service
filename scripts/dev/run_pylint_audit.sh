#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
REPO_DIR=$(realpath "$SCRIPT_DIR/../..")

cd "$REPO_DIR"

if [ -x "$REPO_DIR/.venv-ruff/bin/python" ] && "$REPO_DIR/.venv-ruff/bin/python" -m pylint --version >/dev/null 2>&1; then
	PYLINT=("$REPO_DIR/.venv-ruff/bin/python" -m pylint)
elif python3 -m pylint --version >/dev/null 2>&1; then
	PYLINT=(python3 -m pylint)
else
	echo "pylint is required for the optional audit. Install it with: .venv-ruff/bin/python -m pip install pylint" >&2
	exit 1
fi

"${PYLINT[@]}" \
	--persistent=n \
	--reports=n \
	--score=n \
	--disable=all \
	--enable=undefined-variable,used-before-assignment,unreachable,redefined-builtin,broad-exception-raised \
	venus_evcharger \
	scripts \
	venus_evcharger_service.py \
	venus_evcharger_observer.py \
	venus_evcharger_dbus_adapter.py \
	venus_evchargerctl.py

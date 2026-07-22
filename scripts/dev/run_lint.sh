#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
REPO_DIR=$(realpath "$SCRIPT_DIR/../..")
PROJECT_PYTHON="$REPO_DIR/.venv-ruff/bin/python"

cd "$REPO_DIR"

if [ -x "$PROJECT_PYTHON" ]; then
	"$PROJECT_PYTHON" scripts/dev/check_test_facades.py
else
	python3 scripts/dev/check_test_facades.py
fi

if [ -x "$REPO_DIR/.venv-ruff/bin/python" ] && "$REPO_DIR/.venv-ruff/bin/python" -m ruff --version >/dev/null 2>&1; then
	RUFF=("$REPO_DIR/.venv-ruff/bin/python" -m ruff)
elif python3 -m ruff --version >/dev/null 2>&1; then
	RUFF=(python3 -m ruff)
else
	echo "ruff is required for linting. Install it with: python3 -m venv .venv-ruff && .venv-ruff/bin/python -m pip install ruff" >&2
	exit 1
fi

"${RUFF[@]}" check .
"${RUFF[@]}" check \
	--select ARG,B,C4,DTZ,E9,F,I,PIE,PLC,PLE,PLR0913,PLR2004,PLW,PERF,RET,RUF,SIM,UP \
	--ignore B007,B904,RUF100 \
	scripts/dev/architecture_command_mailbox_contracts.py \
	scripts/dev/check_architecture_contracts.py \
	scripts/dev/check_dbus_isolation.py \
	scripts/dev/check_python_syntax_venus.py \
	scripts/dev/dbus_gateway_chaos.py \
	scripts/dev/pi_gateway_release_gate.py \
	scripts/dev/pi_gateway_release_gate_assertions.py \
	scripts/dev/pi_gateway_release_gate_common.py \
	scripts/dev/pi_gateway_release_gate_health.py \
	scripts/dev/pi_gateway_release_gate_remote.py \
	scripts/dev/pi_gateway_release_gate_shelly.py \
	scripts/dev/pi_gateway_release_gate_support.py \
	scripts/dev/pi_safety_invariants_gate.py \
	scripts/dev/run_mutation_audit.py \
	venus_evcharger_dbus_adapter.py \
	venus_evcharger/dbus_adapter \
	venus_evcharger/dbus_gateway.py \
	venus_evcharger/dbus_gateway_cache.py \
	venus_evcharger/dbus_gateway_client.py \
	venus_evcharger/dbus_gateway_commands.py \
	venus_evcharger/dbus_gateway_core.py \
	venus_evcharger/dbus_gateway_latency.py \
	venus_evcharger/dbus_gateway_policy.py

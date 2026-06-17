#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
REPO_DIR=$(realpath "$SCRIPT_DIR/../..")

cd "$REPO_DIR"

if python3 -m ruff --version >/dev/null 2>&1; then
    RUFF=(python3 -m ruff)
elif [ -x "$REPO_DIR/.venv-ruff/bin/python" ]; then
    RUFF=("$REPO_DIR/.venv-ruff/bin/python" -m ruff)
else
    echo "ruff is required for linting. Install it with: python3 -m venv .venv-ruff && .venv-ruff/bin/python -m pip install ruff" >&2
    exit 1
fi

"${RUFF[@]}" check .
"${RUFF[@]}" check \
    --select ARG,B,C4,DTZ,E9,F,I,PIE,PLC,PLE,PLR0913,PLR2004,PLW,PERF,RET,RUF,SIM,UP \
    --ignore B007,B904,RUF100 \
    scripts/dev/check_dbus_isolation.py \
    scripts/dev/dbus_gateway_chaos.py \
    scripts/dev/pi_gateway_release_gate.py \
    scripts/dev/pi_gateway_release_gate_assertions.py \
    scripts/dev/pi_gateway_release_gate_common.py \
    scripts/dev/pi_gateway_release_gate_health.py \
    scripts/dev/pi_gateway_release_gate_remote.py \
    scripts/dev/pi_gateway_release_gate_shelly.py \
    scripts/dev/pi_gateway_release_gate_support.py \
    venus_evcharger_dbus_adapter.py \
    venus_evcharger/dbus_adapter_components.py \
    venus_evcharger/dbus_adapter_components_rate.py \
    venus_evcharger/dbus_adapter_components_resource.py \
    venus_evcharger/dbus_adapter_components_scheduler.py \
    venus_evcharger/dbus_adapter_process.py \
    venus_evcharger/dbus_adapter_process_health.py \
    venus_evcharger/dbus_adapter_process_introspection.py \
    venus_evcharger/dbus_adapter_process_introspection_snapshot.py \
    venus_evcharger/dbus_adapter_process_io.py \
    venus_evcharger/dbus_adapter_process_loop.py \
    venus_evcharger/dbus_adapter_process_protocols.py \
    venus_evcharger/dbus_adapter_process_runtime.py \
    venus_evcharger/dbus_adapter_read.py \
    venus_evcharger/dbus_adapter_write.py \
    venus_evcharger/dbus_adapter_write_core.py \
    venus_evcharger/dbus_adapter_write_health.py \
    venus_evcharger/dbus_adapter_write_publish.py \
    venus_evcharger/dbus_adapter_write_support.py \
    venus_evcharger/dbus_gateway.py \
    venus_evcharger/dbus_gateway_cache.py \
    venus_evcharger/dbus_gateway_client.py \
    venus_evcharger/dbus_gateway_commands.py \
    venus_evcharger/dbus_gateway_core.py \
    venus_evcharger/dbus_gateway_latency.py \
    venus_evcharger/dbus_gateway_policy.py

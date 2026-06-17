#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
REPO_DIR=$(realpath "$SCRIPT_DIR/../..")

cd "$REPO_DIR"

if python3 -m pylint --version >/dev/null 2>&1; then
    PYLINT=(python3 -m pylint)
elif [ -x "$REPO_DIR/.venv-ruff/bin/python" ] && "$REPO_DIR/.venv-ruff/bin/python" -m pylint --version >/dev/null 2>&1; then
    PYLINT=("$REPO_DIR/.venv-ruff/bin/python" -m pylint)
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

#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
REPO_DIR=$(realpath "$SCRIPT_DIR/../..")

cd "$REPO_DIR"

echo "[1/6] DBus isolation guard"
python3 scripts/dev/check_dbus_isolation.py

echo "[2/6] Architecture contracts"
python3 scripts/dev/check_architecture_contracts.py

echo "[3/6] Syntax check"
python3 -m py_compile \
	venus_evcharger_service.py \
	venus_evcharger_observer.py \
	venus_evcharger_dbus_adapter.py \
	venus_evcharger/dbus_adapter_process.py \
	venus_evcharger/dbus_adapter_process_health.py \
	venus_evcharger/dbus_adapter_process_introspection.py \
	venus_evcharger/dbus_adapter_process_introspection_snapshot.py \
	venus_evcharger/dbus_adapter_process_io.py \
	venus_evcharger/dbus_adapter_process_loop.py \
	venus_evcharger/dbus_adapter_process_protocols.py \
	venus_evcharger/dbus_adapter_process_runtime.py \
	venus_evcharger/dbus_adapter_components.py \
	venus_evcharger/dbus_adapter_components_rate.py \
	venus_evcharger/dbus_adapter_components_resource.py \
	venus_evcharger/dbus_adapter_components_scheduler.py \
	venus_evcharger/dbus_adapter_read.py \
	venus_evcharger/dbus_adapter_write.py \
	venus_evcharger/dbus_adapter_write_core.py \
	venus_evcharger/dbus_adapter_write_health.py \
	venus_evcharger/dbus_adapter_write_publish.py \
	venus_evcharger/dbus_adapter_write_support.py \
	venus_evcharger/bootstrap/controller.py \
	venus_evcharger/dbus_gateway.py \
	venus_evcharger/dbus_gateway_cache.py \
	venus_evcharger/dbus_gateway_client.py \
	venus_evcharger/dbus_gateway_commands.py \
	venus_evcharger/dbus_gateway_core.py \
	venus_evcharger/dbus_gateway_latency.py \
	venus_evcharger/dbus_gateway_policy.py \
	scripts/dev/pi_gateway_release_gate.py \
	scripts/dev/pi_gateway_release_gate_assertions.py \
	scripts/dev/pi_gateway_release_gate_common.py \
	scripts/dev/pi_gateway_release_gate_health.py \
	scripts/dev/pi_gateway_release_gate_remote.py \
	scripts/dev/pi_gateway_release_gate_shelly.py \
	scripts/dev/pi_gateway_release_gate_support.py \
	venus_evcharger/core/common.py \
	venus_evcharger/ports/__init__.py \
	venus_evcharger/controllers/auto.py \
	venus_evcharger/auto/workflow.py \
	venus_evcharger/inputs/dbus.py \
	venus_evcharger/inputs/helper/config_runtime.py \
	venus_evcharger/inputs/helper/sources_dbus.py \
	venus_evcharger/inputs/helper/sources_dbus_common.py \
	venus_evcharger/inputs/helper/sources_dbus_gateway.py \
	venus_evcharger/inputs/helper/sources_dbus_primary.py \
	venus_evcharger/inputs/helper/sources_dbus_resolve.py \
	venus_evcharger/inputs/helper/sources_dbus_snapshot.py \
	venus_evcharger/runtime/support.py \
	venus_evcharger/runtime/async_mainloop.py \
	venus_evcharger/runtime/async_mainloop_control.py \
	venus_evcharger/runtime/async_mainloop_executor.py \
	venus_evcharger/runtime/async_mainloop_publish.py \
	venus_evcharger/runtime/async_mainloop_state.py \
	venus_evcharger/runtime/async_mainloop_types.py \
	venus_evcharger/runtime/async_mainloop_watchdog.py \
	venus_evcharger/controllers/write.py \
	venus_evcharger/update/controller.py

echo "[4/6] Lint"
bash "$SCRIPT_DIR/run_lint.sh"

echo "[5/6] Unit tests"
python3 -m unittest discover -s tests -p 'test_*.py'

echo "[6/6] Type check"
bash "$SCRIPT_DIR/run_typecheck.sh"

echo "All checks passed."

#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
REPO_DIR=$(realpath "$SCRIPT_DIR/../..")

cd "$REPO_DIR"

echo "[1/4] DBus isolation guard"
python3 scripts/dev/check_dbus_isolation.py

echo "[2/4] Syntax check"
python3 -m py_compile \
    venus_evcharger_service.py \
    venus_evcharger_observer.py \
    venus_evcharger_dbus_adapter.py \
    venus_evcharger/dbus_adapter_components.py \
    venus_evcharger/dbus_adapter_read.py \
    venus_evcharger/dbus_adapter_write.py \
    venus_evcharger/bootstrap/controller.py \
    venus_evcharger_dbus_introspection_worker.py \
    venus_evcharger/dbus_gateway.py \
    venus_evcharger/core/common.py \
    venus_evcharger/ports/__init__.py \
    venus_evcharger/controllers/auto.py \
    venus_evcharger/auto/workflow.py \
    venus_evcharger/inputs/dbus.py \
    venus_evcharger/runtime/support.py \
    venus_evcharger/controllers/write.py \
    venus_evcharger/update/controller.py

echo "[3/4] Unit tests"
python3 -m unittest discover -s tests -p 'test_*.py'

echo "[4/4] Type check"
bash "$SCRIPT_DIR/run_typecheck.sh"

echo "All checks passed."

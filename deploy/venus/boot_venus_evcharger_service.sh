#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later

# Lightweight boot helper for Venus OS.
#
# This script is meant to be called from /data/rc.local on every boot. It does
# not start the Python service directly. Instead, it makes sure the runit
# service symlink exists and optionally launches the one-shot helper that
# disables a conflicting generic Shelly integration.

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
REPO_DIR=$(realpath "$SCRIPT_DIR/../..")
SERVICE_NAME="dbus-venus-evcharger"
DBUS_ADAPTER_SERVICE_NAME="dbus-venus-evcharger-dbus-adapter"
OBSERVER_SERVICE_NAME="dbus-venus-evcharger-observer"
SERVICE_DIR="$SCRIPT_DIR/service_venus_evcharger"
DBUS_ADAPTER_SERVICE_DIR="$SCRIPT_DIR/service_venus_evcharger_dbus_adapter"
OBSERVER_SERVICE_DIR="$SCRIPT_DIR/service_venus_evcharger_observer"
CONFIG_PATH="$SCRIPT_DIR/config.venus_evcharger.ini"
GENERIC_SHELLY_HELPER="$REPO_DIR/venus_evcharger/ops/disable_generic_shelly_once.py"

# Ensure the runit service directory structure exists even on fresh copies.
mkdir -p "$SERVICE_DIR/log" "$DBUS_ADAPTER_SERVICE_DIR/log"

# Keep the run scripts executable in case the deployment medium lost mode bits.
if [ -f "$SERVICE_DIR/run" ]; then
    chmod 755 "$SERVICE_DIR/run"
fi

if [ -f "$SERVICE_DIR/log/run" ]; then
    chmod 755 "$SERVICE_DIR/log/run"
fi

if [ -f "$OBSERVER_SERVICE_DIR/run" ]; then
    chmod 755 "$OBSERVER_SERVICE_DIR/run"
fi

if [ -f "$DBUS_ADAPTER_SERVICE_DIR/run" ]; then
    chmod 755 "$DBUS_ADAPTER_SERVICE_DIR/run"
fi

if [ -f "$DBUS_ADAPTER_SERVICE_DIR/log/run" ]; then
    chmod 755 "$DBUS_ADAPTER_SERVICE_DIR/log/run"
fi

cleanup_duplicate_supervisors() {
    service_name="$1"
    pids=$(ps w 2>/dev/null | awk -v name="$service_name" '$5 == "supervise" && $6 == name {print $1}')
    keep_pid=""
    count=0
    for pid in $pids; do
        keep_pid="$pid"
        count=$((count + 1))
    done
    if [ "$count" -le 1 ]; then
        return
    fi
    for pid in $pids; do
        if [ "$pid" != "$keep_pid" ]; then
            kill "$pid" >/dev/null 2>&1 || true
        fi
    done
}

# Register the wallbox service with runit. Existing symlink targets are updated
# in place, which makes this safe to call repeatedly at boot.
ln -sfn "$SERVICE_DIR" "/service/$SERVICE_NAME"
ln -sfn "$DBUS_ADAPTER_SERVICE_DIR" "/service/$DBUS_ADAPTER_SERVICE_NAME"
ln -sfn "$OBSERVER_SERVICE_DIR" "/service/$OBSERVER_SERVICE_NAME"
cleanup_duplicate_supervisors "$SERVICE_NAME"
cleanup_duplicate_supervisors "$DBUS_ADAPTER_SERVICE_NAME"
cleanup_duplicate_supervisors "$OBSERVER_SERVICE_NAME"

# Optionally kick off the generic Shelly disable helper in the background. This
# avoids two Venus services trying to own the same physical Shelly device.
if [ -f "$GENERIC_SHELLY_HELPER" ] && [ -f "$CONFIG_PATH" ]; then
    "$GENERIC_SHELLY_HELPER" "$CONFIG_PATH" >/dev/null 2>&1 &
fi

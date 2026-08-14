#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later

# Installer for the Venus EV charger service on Venus OS / Cerbo GX.
#
# The installer does four main things:
# 1. validate that the wallbox config exists
# 2. restore executable bits on runtime entrypoints
# 3. register the runit service symlink under /service
# 4. make sure rc.local calls the lightweight boot helper on every reboot
#
# It is safe to run this script repeatedly after updates.

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
REPO_DIR=$(realpath "$SCRIPT_DIR/../..")
SERVICE_NAME="dbus-venus-evcharger"
DBUS_ADAPTER_SERVICE_NAME="dbus-venus-evcharger-dbus-adapter"
OBSERVER_SERVICE_NAME="dbus-venus-evcharger-observer"
SERVICE_DIR="$SCRIPT_DIR/service_venus_evcharger"
DBUS_ADAPTER_SERVICE_DIR="$SCRIPT_DIR/service_venus_evcharger_dbus_adapter"
OBSERVER_SERVICE_DIR="$SCRIPT_DIR/service_venus_evcharger_observer"
SERVICE_LIFECYCLE="$SCRIPT_DIR/service_lifecycle.sh"
SERVICE_ROOT="${VENUS_EVCHARGER_SERVICE_ROOT:-/service}"
PROC_ROOT="${VENUS_EVCHARGER_PROC_ROOT:-/proc}"
CONFIG_PATH="$SCRIPT_DIR/config.venus_evcharger.ini"
GENERIC_SHELLY_HELPER="$REPO_DIR/venus_evcharger_generic_shelly_configuration.py"
BOOT_HELPER="$SCRIPT_DIR/boot_venus_evcharger_service.sh"
MAIN_ENTRYPOINT="$REPO_DIR/venus_evcharger_service.py"
DBUS_ADAPTER_ENTRYPOINT="$REPO_DIR/venus_evcharger_dbus_adapter.py"
OBSERVER_ENTRYPOINT="$REPO_DIR/deploy/venus/bin/venus-evcharger-forensic-observer"
AUTO_INPUT_HELPER="$REPO_DIR/deploy/venus/bin/venus-evcharger-auto-input-helper"
CONFIGURE_HELPER="$SCRIPT_DIR/configure_venus_evcharger_service.sh"
RESTART_HELPER="$SCRIPT_DIR/restart_venus_evcharger_service.sh"
RESET_CONFIG_HELPER="$SCRIPT_DIR/reset_venus_evcharger_config.sh"
UNINSTALL_HELPER="$SCRIPT_DIR/uninstall_venus_evcharger_service.sh"
CONTROL_API_CLI_HELPER="$SCRIPT_DIR/venus_evchargerctl.sh"
GX_SMOKE_HELPER="$SCRIPT_DIR/gx_api_smoke_test_skeleton.sh"
SOAK_HELPER="$REPO_DIR/scripts/ops/cerbo_soak_check.sh"
RC_LOCAL_FILE="${VENUS_EVCHARGER_RC_LOCAL_FILE:-/data/rc.local}"

mkdir -p "$SERVICE_DIR/log" "$DBUS_ADAPTER_SERVICE_DIR/log" "$OBSERVER_SERVICE_DIR/log"

# The service is intentionally config-driven. Fail early if the config file is
# missing so the user notices a broken deployment immediately.
if [ ! -f "$CONFIG_PATH" ]; then
	echo "config.venus_evcharger.ini file not found."
	exit 1
fi

if [ ! -f "$OBSERVER_ENTRYPOINT" ]; then
	echo "Rust forensic observer binary not found: $OBSERVER_ENTRYPOINT" >&2
	exit 1
fi

if [ ! -f "$AUTO_INPUT_HELPER" ]; then
	echo "Rust auto input helper binary not found: $AUTO_INPUT_HELPER" >&2
	exit 1
fi

# Restore execute bits for all directly launched entrypoints.
chmod a+x "$MAIN_ENTRYPOINT"
chmod 755 "$MAIN_ENTRYPOINT"

if [ -f "$DBUS_ADAPTER_ENTRYPOINT" ]; then
	chmod a+x "$DBUS_ADAPTER_ENTRYPOINT"
	chmod 755 "$DBUS_ADAPTER_ENTRYPOINT"
fi

if [ -f "$GENERIC_SHELLY_HELPER" ]; then
	chmod a+x "$GENERIC_SHELLY_HELPER"
	chmod 755 "$GENERIC_SHELLY_HELPER"
fi

chmod a+x "$AUTO_INPUT_HELPER"
chmod 755 "$AUTO_INPUT_HELPER"

chmod a+x "$OBSERVER_ENTRYPOINT"
chmod 755 "$OBSERVER_ENTRYPOINT"

# The checked-in binary targets Venus OS ARMv7 and cannot run in the x86
# installer harness. On actual GX architectures, validate it before touching
# the active runit services.
case "$(uname -m)" in
arm* | aarch64)
	if ! "$OBSERVER_ENTRYPOINT" --validate-config "$CONFIG_PATH"; then
		echo "Rust forensic observer rejected the active configuration." >&2
		exit 1
	fi
	if ! "$AUTO_INPUT_HELPER" --validate-launch "$CONFIG_PATH"; then
		echo "Rust auto input helper rejected the active launch configuration." >&2
		exit 1
	fi
	;;
esac

if [ -f "$CONFIGURE_HELPER" ]; then
	chmod a+x "$CONFIGURE_HELPER"
	chmod 755 "$CONFIGURE_HELPER"
fi

if [ -f "$RESTART_HELPER" ]; then
	chmod a+x "$RESTART_HELPER"
	chmod 744 "$RESTART_HELPER"
fi

if [ -f "$RESET_CONFIG_HELPER" ]; then
	chmod a+x "$RESET_CONFIG_HELPER"
	chmod 744 "$RESET_CONFIG_HELPER"
fi

if [ -f "$BOOT_HELPER" ]; then
	chmod a+x "$BOOT_HELPER"
	chmod 755 "$BOOT_HELPER"
fi

if [ -f "$UNINSTALL_HELPER" ]; then
	chmod a+x "$UNINSTALL_HELPER"
	chmod 744 "$UNINSTALL_HELPER"
fi

if [ -f "$CONTROL_API_CLI_HELPER" ]; then
	chmod a+x "$CONTROL_API_CLI_HELPER"
	chmod 755 "$CONTROL_API_CLI_HELPER"
fi

if [ -f "$GX_SMOKE_HELPER" ]; then
	chmod a+x "$GX_SMOKE_HELPER"
	chmod 755 "$GX_SMOKE_HELPER"
fi

if [ -f "$SOAK_HELPER" ]; then
	chmod a+x "$SOAK_HELPER"
	chmod 744 "$SOAK_HELPER"
fi

chmod a+x "$SERVICE_DIR/run"
chmod 755 "$SERVICE_DIR/run"
chmod a+x "$SERVICE_DIR/log/run"
chmod 755 "$SERVICE_DIR/log/run"
chmod a+x "$DBUS_ADAPTER_SERVICE_DIR/run"
chmod 755 "$DBUS_ADAPTER_SERVICE_DIR/run"
chmod a+x "$DBUS_ADAPTER_SERVICE_DIR/log/run"
chmod 755 "$DBUS_ADAPTER_SERVICE_DIR/log/run"
chmod a+x "$OBSERVER_SERVICE_DIR/run"
chmod 755 "$OBSERVER_SERVICE_DIR/run"
chmod a+x "$OBSERVER_SERVICE_DIR/log/run"
chmod 755 "$OBSERVER_SERVICE_DIR/log/run"

# shellcheck source=deploy/venus/service_lifecycle.sh
. "$SERVICE_LIFECYCLE"

# Venus classic uses svscan/supervise and may retain replaced service-directory
# inodes. Reconcile only this project's process tree before starting one fresh
# instance of each service.
if ! venus_reconcile_services; then
	echo "Failed to start Venus EV charger services under runit." >&2
	exit 1
fi

remove_rc_local_line() {
	line="$1"
	if [ ! -f "$RC_LOCAL_FILE" ]; then
		return
	fi
	tmp_file=$(mktemp)
	grep -vxF "$line" "$RC_LOCAL_FILE" >"$tmp_file" || true
	cat "$tmp_file" >"$RC_LOCAL_FILE"
	rm -f "$tmp_file"
}

# Create rc.local if the target system does not yet have one.
if [ ! -f "$RC_LOCAL_FILE" ]; then
	touch "$RC_LOCAL_FILE"
	chmod 755 "$RC_LOCAL_FILE"
	echo "#!/bin/bash" >>"$RC_LOCAL_FILE"
	echo >>"$RC_LOCAL_FILE"
fi

remove_rc_local_line "$SCRIPT_DIR/install_venus_evcharger_service.sh"
remove_rc_local_line "$REPO_DIR/install_venus_evcharger_service.sh"
remove_rc_local_line "$SCRIPT_DIR/install.sh"
remove_rc_local_line "$REPO_DIR/install.sh"

# Keep rc.local lean: call the dedicated boot helper, not the full installer.
STARTUP="$BOOT_HELPER"
grep -qxF "$STARTUP" "$RC_LOCAL_FILE" || echo "$STARTUP" >>"$RC_LOCAL_FILE"

# Remove an obsolete direct generic helper line if one exists. The boot helper
# handles that logic now.
GENERIC_SHELLY_HELPER_CMD="$GENERIC_SHELLY_HELPER $CONFIG_PATH >/dev/null 2>&1 &"
LEGACY_GENERIC_SHELLY_HELPER_CMD="$GENERIC_SHELLY_HELPER $REPO_DIR/config.venus_evcharger.ini >/dev/null 2>&1 &"
remove_rc_local_line "$GENERIC_SHELLY_HELPER_CMD"
remove_rc_local_line "$LEGACY_GENERIC_SHELLY_HELPER_CMD"

#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later

# Reset the deployed Venus wallbox configuration back to the shipped,
# unconfigured default without trying to guess whether an existing host/IP is
# intentional. This is an explicit user action for "start over from scratch".

set -eu

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
CONFIG_PATH="$SCRIPT_DIR/config.venus_evcharger.ini"
DEFAULT_CONFIG_PATH="$SCRIPT_DIR/config.venus_evcharger.default.ini"
SERVICE_PATH="/service/dbus-venus-evcharger"
BACKUP_PATH=""

usage() {
	status="${1:-1}"
	echo "Usage: $0" >&2
	exit "$status"
}

log() {
	printf '%s\n' "[reset-config] $*"
}

runtime_paths_from_config() {
	config_path="$1"
	[ -f "$config_path" ] || return 0
	python3 - "$config_path" <<'PY'
import configparser
import sys

parser = configparser.ConfigParser()
parser.optionxform = str
try:
    with open(sys.argv[1], "r", encoding="utf-8") as handle:
        parser.read_file(handle)
except (OSError, configparser.Error):
    raise SystemExit(0)

for key in ("RuntimeStatePath", "RuntimeOverridesPath"):
    value = parser.defaults().get(key, "").strip()
    if value:
        print(value)
PY
}

remove_runtime_state_files() {
	for path in "$@"; do
		[ -n "$path" ] || continue
		rm -f "$path"
	done
}

remove_generated_device_files() {
	rm -f "$SCRIPT_DIR"/wizard-*.ini
	rm -f "$CONFIG_PATH".wizard-result.json
	rm -f "$CONFIG_PATH".wizard-audit.jsonl
	rm -f "$CONFIG_PATH".wizard-topology.txt
	rm -f "$CONFIG_PATH".wizard-inventory.ini
}

restart_service_if_registered() {
	if command -v svc >/dev/null 2>&1 && [ -L "$SERVICE_PATH" ]; then
		svc -t "$SERVICE_PATH" >/dev/null 2>&1 || true
		log "Requested service restart for $SERVICE_PATH"
		return 0
	fi
	log "Service restart skipped (svc missing or service not registered)"
}

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
	usage 0
fi

if [ "${1:-}" != "" ]; then
	usage
fi

if [ ! -f "$DEFAULT_CONFIG_PATH" ]; then
	echo "Default reset template not found: $DEFAULT_CONFIG_PATH" >&2
	exit 1
fi

old_runtime_paths=$(runtime_paths_from_config "$CONFIG_PATH" || true)
new_runtime_paths=$(runtime_paths_from_config "$DEFAULT_CONFIG_PATH" || true)

if [ -f "$CONFIG_PATH" ]; then
	timestamp=$(date +%Y%m%d-%H%M%S)
	BACKUP_PATH="${CONFIG_PATH}.reset-backup-${timestamp}"
	cp "$CONFIG_PATH" "$BACKUP_PATH"
	log "Backed up current config to $BACKUP_PATH"
fi

cp "$DEFAULT_CONFIG_PATH" "$CONFIG_PATH"
log "Restored factory-default unconfigured config"

remove_generated_device_files
log "Removed wizard-generated device files and summaries"

all_runtime_paths=$(printf '%s\n%s\n' "$old_runtime_paths" "$new_runtime_paths" | awk 'NF && !seen[$0]++')
if [ -n "$all_runtime_paths" ]; then
	# shellcheck disable=SC2086
	remove_runtime_state_files $all_runtime_paths
	log "Cleared runtime state and override files"
fi

restart_service_if_registered

if [ -n "$BACKUP_PATH" ]; then
	log "Reset complete; previous config saved at $BACKUP_PATH"
else
	log "Reset complete; no previous config existed"
fi

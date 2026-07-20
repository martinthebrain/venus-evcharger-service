#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -u

SERVICE_PATH="${SERVICE_PATH:-/service/dbus-venus-evcharger}"
DBUS_NAME="${DBUS_NAME:-com.victronenergy.evcharger.http_60}"
GATEWAY_CACHE_PATH="${GATEWAY_CACHE_PATH:-/run/venus-evcharger/dbus-cache.json}"
AUTO_REASON_LOG="${AUTO_REASON_LOG:-/var/volatile/log/dbus-venus-evcharger/auto-reasons.log}"
AUTO_SNAPSHOT_PATH="${AUTO_SNAPSHOT_PATH:-/run/dbus-venus-evcharger-auto-60.json}"
TAIL_LINES="${TAIL_LINES:-40}"

section() {
	printf '\n== %s ==\n' "$1"
}

run_cmd() {
	label="$1"
	shift
	printf '\n$ %s\n' "$label"
	if ! command -v "$1" >/dev/null 2>&1; then
		printf 'Command not available: %s\n' "$1"
		return 0
	fi
	"$@"
	status=$?
	if [ $status -ne 0 ]; then
		printf '[exit %s]\n' "$status"
	fi
	return 0
}

run_shell() {
	label="$1"
	cmd="$2"
	printf '\n$ %s\n' "$label"
	sh -c "$cmd"
	status=$?
	if [ $status -ne 0 ]; then
		printf '[exit %s]\n' "$status"
	fi
	return 0
}

print_gateway_values() {
	python3 - "$GATEWAY_CACHE_PATH" "$DBUS_NAME" <<'PY'
import json
import sys

cache_path, service = sys.argv[1:3]
with open(cache_path, encoding="utf-8") as handle:
    snapshot = json.load(handle)
values = snapshot.get("values", {})
for path in ("/ProductName", "/Connected", "/Status", "/Mode", "/DeviceInstance", "/Ac/Power"):
    entry = values.get(f"path:{service}{path}")
    if not isinstance(entry, dict):
        print(f"{path}: <missing>")
        continue
    print(f"{path}: {entry.get('value')!r} status={entry.get('status')} age_s={entry.get('age_s')}")
PY
}

section "Time"
run_cmd "date" date
run_cmd "uptime" uptime

section "Service"
run_cmd "svstat $SERVICE_PATH" svstat "$SERVICE_PATH"
run_cmd "svstat $SERVICE_PATH/log" svstat "$SERVICE_PATH/log"
run_cmd "ls -l $SERVICE_PATH" ls -l "$SERVICE_PATH"

section "Processes"
run_shell "ps | grep -E 'venus_evcharger|venus-evcharger|dbus-venus-evcharger' | grep -v grep" \
	"ps | grep -E 'venus_evcharger|venus-evcharger|dbus-venus-evcharger' | grep -v grep"

section "Gateway Cache"
if [ -f "$GATEWAY_CACHE_PATH" ]; then
	run_cmd "read EVCS values from $GATEWAY_CACHE_PATH" print_gateway_values
else
	printf 'Gateway cache missing: %s\n' "$GATEWAY_CACHE_PATH"
fi

section "Snapshot"
if [ -f "$AUTO_SNAPSHOT_PATH" ]; then
	run_cmd "ls -l $AUTO_SNAPSHOT_PATH" ls -l "$AUTO_SNAPSHOT_PATH"
	run_cmd "cat $AUTO_SNAPSHOT_PATH" cat "$AUTO_SNAPSHOT_PATH"
else
	printf 'Snapshot file missing: %s\n' "$AUTO_SNAPSHOT_PATH"
fi

section "Auto Audit"
if [ -f "$AUTO_REASON_LOG" ]; then
	run_cmd "tail -n $TAIL_LINES $AUTO_REASON_LOG" tail -n "$TAIL_LINES" "$AUTO_REASON_LOG"
else
	printf 'Auto audit log missing: %s\n' "$AUTO_REASON_LOG"
fi

section "Hints"
printf '%s\n' 'Healthy signs:'
printf '%s\n' '- service stays up with stable pid'
printf '%s\n' '- one main process plus one helper process'
printf '%s\n' '- gateway cache values are fresh and plausible'
printf '%s\n' '- auto audit log shows plausible reasons instead of restart noise'

#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later

venus_service_path() {
	printf '%s/%s\n' "$SERVICE_ROOT" "$1"
}

venus_service_registered() {
	service_path=$(venus_service_path "$1")
	{ [ -e "$service_path" ] || [ -L "$service_path" ]; } && command -v svc >/dev/null 2>&1
}

venus_service_down() {
	venus_service_registered "$1" || return 0
	svc -d "$(venus_service_path "$1")" >/dev/null 2>&1 || true
}

venus_service_up() {
	venus_service_registered "$1" || return 0
	svc -u "$(venus_service_path "$1")" >/dev/null 2>&1 || true
}

venus_stop_and_deregister_services() {
	venus_service_down "$OBSERVER_SERVICE_NAME"
	venus_service_down "$SERVICE_NAME"
	venus_service_down "$DBUS_ADAPTER_SERVICE_NAME"
	rm -f \
		"$(venus_service_path "$OBSERVER_SERVICE_NAME")" \
		"$(venus_service_path "$SERVICE_NAME")" \
		"$(venus_service_path "$DBUS_ADAPTER_SERVICE_NAME")"
	sleep "${VENUS_EVCHARGER_SERVICE_SETTLE_SECONDS:-1}"
}

venus_deleted_service_pids() {
	for process_dir in "$PROC_ROOT"/[0-9]*; do
		[ -d "$process_dir" ] || continue
		process_cwd=$(readlink "$process_dir/cwd" 2>/dev/null || true)
		case "$process_cwd" in
		"$SCRIPT_DIR"/service_venus_evcharger*" (deleted)")
			basename "$process_dir"
			;;
		esac
	done
}

venus_cleanup_deleted_service_processes() {
	stale_pids=$(venus_deleted_service_pids)
	[ -n "$stale_pids" ] || return 0
	for pid in $stale_pids; do
		kill "$pid" >/dev/null 2>&1 || true
	done
	sleep "${VENUS_EVCHARGER_SERVICE_SETTLE_SECONDS:-1}"
	for pid in $(venus_deleted_service_pids); do
		kill -KILL "$pid" >/dev/null 2>&1 || true
	done
}

venus_register_service_links() {
	mkdir -p "$SERVICE_ROOT"
	ln -sfn "$SERVICE_DIR" "$(venus_service_path "$SERVICE_NAME")"
	ln -sfn "$DBUS_ADAPTER_SERVICE_DIR" "$(venus_service_path "$DBUS_ADAPTER_SERVICE_NAME")"
	ln -sfn "$OBSERVER_SERVICE_DIR" "$(venus_service_path "$OBSERVER_SERVICE_NAME")"
}

venus_start_services() {
	venus_service_up "$DBUS_ADAPTER_SERVICE_NAME"
	sleep "${VENUS_EVCHARGER_ADAPTER_START_SECONDS:-1}"
	venus_service_up "$SERVICE_NAME"
	venus_service_up "$OBSERVER_SERVICE_NAME"
}

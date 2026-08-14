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
	service_path=$(venus_service_path "$1")
	venus_wait_for_supervisor "$1" || return 1
	remaining_seconds="${VENUS_EVCHARGER_SUPERVISOR_WAIT_SECONDS:-10}"
	while ! svc -u "$service_path" >/dev/null 2>&1; do
		[ "$remaining_seconds" -gt 0 ] || return 1
		sleep 1
		remaining_seconds=$((remaining_seconds - 1))
	done
}

venus_supervisor_ready() {
	service_path=$(venus_service_path "$1")
	[ -p "$service_path/supervise/ok" ] || return 1
	command -v svstat >/dev/null 2>&1 || return 1
	svstat "$service_path" >/dev/null 2>&1
}

venus_wait_for_supervisor() {
	remaining_seconds="${VENUS_EVCHARGER_SUPERVISOR_WAIT_SECONDS:-10}"
	while ! venus_supervisor_ready "$1"; do
		[ "$remaining_seconds" -gt 0 ] || return 1
		sleep 1
		remaining_seconds=$((remaining_seconds - 1))
	done
}

venus_stop_services() {
	venus_service_down "$OBSERVER_SERVICE_NAME"
	venus_service_down "$SERVICE_NAME"
	venus_service_down "$DBUS_ADAPTER_SERVICE_NAME"
}

venus_service_tree_pids() {
	for process_dir in "$PROC_ROOT"/[0-9]*; do
		[ -d "$process_dir" ] || continue
		process_cwd=$(readlink "$process_dir/cwd" 2>/dev/null || true)
		case "$process_cwd" in
		"$SCRIPT_DIR"/service_venus_evcharger*)
			basename "$process_dir"
			;;
		esac
	done
}

venus_managed_service_pids() {
	[ -n "${REPO_DIR:-}" ] || return 0
	for process_dir in "$PROC_ROOT"/[0-9]*; do
		[ -d "$process_dir" ] || continue
		[ -r "$process_dir/cmdline" ] || continue
		command_line=$(tr '\000' ' ' <"$process_dir/cmdline" 2>/dev/null || true)
		for entrypoint in \
			"$REPO_DIR/venus_evcharger_service.py" \
			"$REPO_DIR/venus_evcharger_dbus_adapter.py"; do
			case "$command_line" in
			"python3 $entrypoint "* | "/usr/bin/python3 $entrypoint "*)
				basename "$process_dir"
				break
				;;
			esac
		done
		# Reconcile a legacy helper left running by a pre-Rust installation.
		case "$command_line" in
		"python3 $REPO_DIR/venus_evcharger_auto_input_helper.py "* | \
			"/usr/bin/python3 $REPO_DIR/venus_evcharger_auto_input_helper.py "*)
			basename "$process_dir"
			;;
		esac
		case "$command_line" in
		"$REPO_DIR/deploy/venus/bin/venus-evcharger-forensic-observer "* | \
			"$REPO_DIR/deploy/venus/bin/venus-evcharger-auto-input-helper "* | \
			"python3 $REPO_DIR/venus_evcharger_observer.py "* | \
			"/usr/bin/python3 $REPO_DIR/venus_evcharger_observer.py "*)
			basename "$process_dir"
			;;
		esac
	done
}

venus_stale_service_pids() {
	{
		venus_service_tree_pids
		venus_managed_service_pids
	} | sort -u
}

venus_cleanup_stale_service_processes() {
	stale_pids=$(venus_stale_service_pids)
	[ -n "$stale_pids" ] || return 0
	for pid in $stale_pids; do
		kill "$pid" >/dev/null 2>&1 || true
	done
	sleep "${VENUS_EVCHARGER_SERVICE_SETTLE_SECONDS:-2}"
	for pid in $stale_pids; do
		kill -KILL "$pid" >/dev/null 2>&1 || true
	done
	sleep "${VENUS_EVCHARGER_SERVICE_RESPAWN_SECONDS:-1}"
}

venus_register_service_links() {
	mkdir -p "$SERVICE_ROOT"
	ln -sfn "$SERVICE_DIR" "$(venus_service_path "$SERVICE_NAME")"
	ln -sfn "$DBUS_ADAPTER_SERVICE_DIR" "$(venus_service_path "$DBUS_ADAPTER_SERVICE_NAME")"
	ln -sfn "$OBSERVER_SERVICE_DIR" "$(venus_service_path "$OBSERVER_SERVICE_NAME")"
}

venus_reconcile_services() {
	venus_register_service_links
	venus_stop_services
	venus_cleanup_stale_service_processes
	venus_start_services
}

venus_start_services() {
	venus_service_up "$DBUS_ADAPTER_SERVICE_NAME" || return 1
	sleep "${VENUS_EVCHARGER_ADAPTER_START_SECONDS:-1}"
	venus_service_up "$SERVICE_NAME" || return 1
	venus_service_up "$OBSERVER_SERVICE_NAME" || return 1
}

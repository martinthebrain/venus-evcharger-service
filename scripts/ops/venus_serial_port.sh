#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
STOP_CMD="${VENUS_SERIAL_STOP_COMMAND:-/opt/victronenergy/serial-starter/stop-tty.sh}"
START_CMD="${VENUS_SERIAL_START_COMMAND:-/opt/victronenergy/serial-starter/start-tty.sh}"

usage() {
	cat <<'EOF'
Usage:
  scripts/ops/venus_serial_port.sh take <tty-device>
  scripts/ops/venus_serial_port.sh release <tty-device>
  scripts/ops/venus_serial_port.sh probe-charger <tty-device> <charger-config>

Environment overrides:
  VENUS_SERIAL_STOP_COMMAND
  VENUS_SERIAL_START_COMMAND
EOF
}

require_arg() {
	local value="${1:-}"
	local label="${2:-argument}"
	if [[ -z "${value}" ]]; then
		echo "Missing ${label}" >&2
		usage >&2
		exit 2
	fi
}

run_stop() {
	local tty_device="$1"
	"${STOP_CMD}" "${tty_device}"
}

run_start() {
	local tty_device="$1"
	"${START_CMD}" "${tty_device}"
}

action="${1:-}"
tty_device="${2:-}"

case "${action}" in
take)
	require_arg "${tty_device}" "tty-device"
	run_stop "${tty_device}"
	;;
release)
	require_arg "${tty_device}" "tty-device"
	run_start "${tty_device}"
	;;
probe-charger)
	config_path="${3:-}"
	require_arg "${tty_device}" "tty-device"
	require_arg "${config_path}" "charger-config"
	trap 'run_start "${tty_device}"' EXIT
	run_stop "${tty_device}"
	cd "${REPO_ROOT}"
	python3 -m venus_evcharger.backend.probe read-charger "${config_path}"
	;;
*)
	usage >&2
	exit 2
	;;
esac

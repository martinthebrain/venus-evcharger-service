#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later

# Optional Venus/GX API smoke-test skeleton.
#
# This helper is intentionally not part of the normal PC-side test suite and
# not part of installation. It exists as a prepared manual checklist for the
# day when a real Venus OS / GX target is available again.
#
# What it is:
# - a small manual runbook encoded as a shell helper
# - a future-friendly place to grow target-system checks
# - a read-first smoke path that can optionally try one safe write
#
# What it is not:
# - a substitute for the hermetic repo tests
# - a guarantee that every GX setup behaves identically
# - an always-on post-install action

set -u

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" >/dev/null 2>&1 && pwd)
WRAPPER="$SCRIPT_DIR/venus_evchargerctl.sh"
SERVICE_DIR="/service/dbus-venus-evcharger"
BASE_URL="${CONTROL_API_BASE_URL:-http://127.0.0.1:8765}"
UNIX_SOCKET="${CONTROL_API_UNIX_SOCKET:-}"
READ_TOKEN="${CONTROL_API_READ_TOKEN:-}"
CONTROL_TOKEN="${CONTROL_API_CONTROL_TOKEN:-}"
TRY_WRITE="${TRY_WRITE:-0}"
EVENT_KIND="${EVENT_KIND:-command}"

log() {
	printf '%s\n' "$*"
}

run_step() {
	step_name="$1"
	shift
	log
	log "[gx-smoke] $step_name"
	"$@"
}

wrapper_cmd() {
	if [ -n "$UNIX_SOCKET" ]; then
		"$WRAPPER" --unix-socket "$UNIX_SOCKET" "$@"
		return
	fi
	"$WRAPPER" "$@"
}

skip_step() {
	log "[gx-smoke] SKIP: $*"
}

log "Optional GX smoke-test skeleton"
log "This helper is for manual target validation when real Venus/GX hardware is available."
log "It is intentionally conservative and does not run automatically."

if [ ! -x "$WRAPPER" ]; then
	log "[gx-smoke] ERROR: expected CLI wrapper not found or not executable: $WRAPPER"
	exit 1
fi

if command -v svstat >/dev/null 2>&1; then
	run_step "runit service status" svstat "$SERVICE_DIR"
else
	skip_step "svstat not available on this host"
fi

run_step "unauthenticated health" wrapper_cmd health

if [ -n "$READ_TOKEN" ]; then
	run_step "authenticated capabilities" wrapper_cmd --token "$READ_TOKEN" capabilities
	run_step "authenticated state summary" wrapper_cmd --token "$READ_TOKEN" state summary
	run_step "authenticated state topology" wrapper_cmd --token "$READ_TOKEN" state topology
	run_step "authenticated state health" wrapper_cmd --token "$READ_TOKEN" state health
	run_step "single event batch" wrapper_cmd --token "$READ_TOKEN" events --kind "$EVENT_KIND" --once
else
	skip_step "set CONTROL_API_READ_TOKEN to exercise authenticated reads"
fi

if [ "$TRY_WRITE" = "1" ]; then
	if [ -z "$READ_TOKEN" ] || [ -z "$CONTROL_TOKEN" ]; then
		skip_step "set both CONTROL_API_READ_TOKEN and CONTROL_API_CONTROL_TOKEN before TRY_WRITE=1"
	else
		log
		log "[gx-smoke] preparing optimistic-concurrency write"
		STATE_HEALTH_JSON=$(wrapper_cmd --token "$READ_TOKEN" state health) || exit 1
		STATE_TOKEN=$(printf '%s\n' "$STATE_HEALTH_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("state", {}).get("state_token", ""))')
		if [ -z "$STATE_TOKEN" ]; then
			log "[gx-smoke] ERROR: no state token available for If-Match write"
			exit 1
		fi
		run_step \
			"safe write example (set-mode 1 with If-Match)" \
			wrapper_cmd --token "$CONTROL_TOKEN" command set-mode 1 --if-match "$STATE_TOKEN"
	fi
else
	skip_step "set TRY_WRITE=1 to attempt one safe write against the live target"
fi

log
log "[gx-smoke] finished"
log "[gx-smoke] BASE_URL=$BASE_URL"
if [ -n "$UNIX_SOCKET" ]; then
	log "[gx-smoke] unix socket: $UNIX_SOCKET"
else
	log "[gx-smoke] tcp mode via default local wrapper settings"
fi

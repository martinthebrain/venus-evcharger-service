#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
REPO_DIR=$(realpath "$SCRIPT_DIR/../..")
PI_TARGET="root@192.168.142.129"
SSH_CONFIG="/dev/null"
RECEIPT_PATH="/tmp/venus-evcharger-release-candidate.json"
TESTBED_MARKER="/data/venus-evcharger-testbed"

usage() {
	echo "Usage: $0 [--pi user@host] [--ssh-config path] [--receipt path] [--testbed-marker path]" >&2
	exit 2
}

while [ "$#" -gt 0 ]; do
	case "$1" in
	--pi)
		[ "$#" -ge 2 ] || usage
		PI_TARGET="$2"
		shift 2
		;;
	--ssh-config)
		[ "$#" -ge 2 ] || usage
		SSH_CONFIG="$2"
		shift 2
		;;
	--receipt)
		[ "$#" -ge 2 ] || usage
		RECEIPT_PATH="$2"
		shift 2
		;;
	--testbed-marker)
		[ "$#" -ge 2 ] || usage
		TESTBED_MARKER="$2"
		shift 2
		;;
	*)
		usage
		;;
	esac
done

cd "$REPO_DIR"
if [ -n "$(git status --porcelain --untracked-files=normal)" ]; then
	echo "Release-candidate gate requires a clean Git worktree" >&2
	exit 1
fi

SOURCE_COMMIT=$(git rev-parse HEAD)
STARTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)

if ! ssh -F "$SSH_CONFIG" -o BatchMode=yes "$PI_TARGET" test -f "$TESTBED_MARKER"; then
	echo "Refusing release deployment: $PI_TARGET does not expose testbed marker $TESTBED_MARKER" >&2
	exit 1
fi

bash scripts/dev/check_all.sh
make audit
bash scripts/dev/run_coverage.sh
python3 scripts/dev/pi_safety_invariants_gate.py \
	--pi "$PI_TARGET" \
	--ssh-config "$SSH_CONFIG"
python3 scripts/dev/pi_gateway_release_gate.py \
	--pi "$PI_TARGET" \
	--ssh-config "$SSH_CONFIG" \
	--deploy \
	--configure-host \
	--start-host-shelly \
	--restart

mkdir -p "$(dirname -- "$RECEIPT_PATH")"
python3 - "$RECEIPT_PATH" "$SOURCE_COMMIT" "$PI_TARGET" "$STARTED_AT" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

path, commit, target, started_at = sys.argv[1:]
payload = {
    "schema_version": 1,
    "result": "passed",
    "source_commit": commit,
    "hardware_target": target,
    "started_at": started_at,
    "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "checks": [
        "host-check-all",
        "host-audit",
        "host-coverage",
        "pi-safety-invariants",
        "pi-gateway-chaos",
        "pi-live-shelly-network",
        "pi-gui-read-write",
    ],
}
Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(payload, indent=2, sort_keys=True))
PY

#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
REPO_DIR=$(realpath "$SCRIPT_DIR/../..")
MAX_LOC=${QUALITY_MAX_LOC:-500}

cd "$REPO_DIR"

if python3 -m radon --version >/dev/null 2>&1; then
	RADON=(python3 -m radon)
elif [ -x "$REPO_DIR/.venv-ruff/bin/python" ]; then
	RADON=("$REPO_DIR/.venv-ruff/bin/python" -m radon)
else
	echo "radon is required for quality audits. Install it in .venv-ruff with: .venv-ruff/bin/python -m pip install radon" >&2
	exit 1
fi

QUALITY_PATHS=(
	venus_evcharger_service.py
	venus_evcharger_observer.py
	venus_evcharger_dbus_adapter.py
	venus_evchargerctl.py
	venus_evcharger
	scripts
)

cc_offenders=$("${RADON[@]}" cc -s -n B "${QUALITY_PATHS[@]}")
if [ -n "$cc_offenders" ]; then
	echo "Radon CC found B-or-worse blocks in runtime/script paths:" >&2
	printf '%s\n' "$cc_offenders" >&2
	exit 1
fi

mi_offenders=$("${RADON[@]}" mi -s -n B "${QUALITY_PATHS[@]}")
if [ -n "$mi_offenders" ]; then
	echo "Radon MI found B-or-worse files in runtime/script paths:" >&2
	printf '%s\n' "$mi_offenders" >&2
	exit 1
fi

loc_offenders=()
while IFS= read -r -d '' path; do
	lines=$(wc -l <"$path")
	if [ "$lines" -gt "$MAX_LOC" ]; then
		loc_offenders+=("$lines $path")
	fi
done < <(
	{
		printf '%s\0' venus_evcharger_service.py venus_evcharger_observer.py venus_evcharger_dbus_adapter.py venus_evchargerctl.py
		find venus_evcharger scripts -name '*.py' -print0
	}
)

if [ "${#loc_offenders[@]}" -gt 0 ]; then
	echo "Runtime/script files over ${MAX_LOC} LOC:" >&2
	printf '%s\n' "${loc_offenders[@]}" >&2
	exit 1
fi

echo "Quality audit passed: radon CC A, radon MI A, runtime/script files <= ${MAX_LOC} LOC."

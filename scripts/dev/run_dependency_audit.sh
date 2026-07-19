#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
REPO_DIR=$(realpath "$SCRIPT_DIR/../..")
PROJECT_AUDIT="$REPO_DIR/.venv-ruff/bin/pip-audit"
AUDIT_CACHE_DIR=${PIP_AUDIT_CACHE_DIR:-"${TMPDIR:-/tmp}/venus-evcharger-pip-audit"}

if [ -x "$PROJECT_AUDIT" ]; then
	PIP_AUDIT=("$PROJECT_AUDIT")
elif command -v pip-audit >/dev/null 2>&1; then
	PIP_AUDIT=("$(command -v pip-audit)")
else
	echo "pip-audit is required. Install the pinned dev tools from requirements-dev.lock." >&2
	exit 1
fi

AUDIT_ARGS=(
	--requirement "$REPO_DIR/requirements-dev.lock"
	--disable-pip
	--require-hashes
	--strict
	--progress-spinner off
	--cache-dir "$AUDIT_CACHE_DIR"
)
OFFLINE_AUDIT=0

if [ "${DEPENDENCY_AUDIT_OFFLINE:-0}" = "1" ]; then
	echo "[dependency-audit] Offline dry-run: validating the locked dependency set without an advisory lookup."
	AUDIT_ARGS+=(--dry-run)
	OFFLINE_AUDIT=1
fi

"${PIP_AUDIT[@]}" "${AUDIT_ARGS[@]}"

if [ "$OFFLINE_AUDIT" -eq 1 ]; then
	echo "[dependency-audit] Lock validation passed; vulnerability status was not evaluated offline."
fi

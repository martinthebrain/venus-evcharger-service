#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
REPO_DIR=$(realpath "$SCRIPT_DIR/../..")
PROJECT_VENV_BIN="$REPO_DIR/.venv-ruff/bin"

cd "$REPO_DIR"

if [ -x "$PROJECT_VENV_BIN/shellcheck" ]; then
	SHELLCHECK_CMD="$PROJECT_VENV_BIN/shellcheck"
elif command -v shellcheck >/dev/null 2>&1; then
	SHELLCHECK_CMD="$(command -v shellcheck)"
else
	echo "shellcheck is required for shell audit." >&2
	exit 1
fi

if [ -x "$PROJECT_VENV_BIN/shfmt" ]; then
	SHFMT_CMD="$PROJECT_VENV_BIN/shfmt"
elif command -v shfmt >/dev/null 2>&1; then
	SHFMT_CMD="$(command -v shfmt)"
else
	echo "shfmt is required for shell audit." >&2
	exit 1
fi

mapfile -t SHELL_FILES < <(find . \
	-path "./.git" -prune -o \
	-path "./.venv-ruff" -prune -o \
	-path "./rust/forensic-observer/target" -prune -o \
	-type f \( -name "*.sh" -o -name "install.sh" \) \
	-print | sort)

if [ "${#SHELL_FILES[@]}" -eq 0 ]; then
	echo "No shell files found."
	exit 0
fi

"$SHELLCHECK_CMD" -s bash -e SC2034 "${SHELL_FILES[@]}"
"$SHFMT_CMD" -d "${SHELL_FILES[@]}"

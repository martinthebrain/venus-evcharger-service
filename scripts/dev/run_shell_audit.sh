#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
REPO_DIR=$(realpath "$SCRIPT_DIR/../..")

cd "$REPO_DIR"

if ! command -v shellcheck >/dev/null 2>&1; then
    echo "shellcheck is required for shell audit." >&2
    exit 1
fi

if ! command -v shfmt >/dev/null 2>&1; then
    echo "shfmt is required for shell audit." >&2
    exit 1
fi

mapfile -t SHELL_FILES < <(find . \
    -path "./.git" -prune -o \
    -path "./.venv-ruff" -prune -o \
    -type f \( -name "*.sh" -o -name "install.sh" \) \
    -print | sort)

if [ "${#SHELL_FILES[@]}" -eq 0 ]; then
    echo "No shell files found."
    exit 0
fi

shellcheck "${SHELL_FILES[@]}"
shfmt -d "${SHELL_FILES[@]}"

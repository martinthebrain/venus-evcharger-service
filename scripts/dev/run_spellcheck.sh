#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
REPO_DIR=$(realpath "$SCRIPT_DIR/../..")

cd "$REPO_DIR"

if [ -x "$REPO_DIR/.venv-ruff/bin/codespell" ]; then
	CODESPELL=("$REPO_DIR/.venv-ruff/bin/codespell")
elif command -v codespell >/dev/null 2>&1; then
	CODESPELL=(codespell)
else
	echo "codespell is required for spellcheck. Install it with: .venv-ruff/bin/python -m pip install codespell" >&2
	exit 1
fi

"${CODESPELL[@]}" \
	--ignore-words-list "astroid,eto,thur" \
	--skip "./.git,./.venv-ruff,./__pycache__,./.mypy_cache,./build,./rust/forensic-observer/target,./rust/auto-input-helper/target,./rust/dbus-adapter/target,./.coverage,./coverage.xml"

#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
REPO_DIR=$(realpath "$SCRIPT_DIR/../..")
PIP_COMPILE="$REPO_DIR/.venv-ruff/bin/pip-compile"
PIP_TOOLS_CACHE_DIR=${PIP_TOOLS_CACHE_DIR:-"${TMPDIR:-/tmp}/venus-evcharger-pip-tools"}

if [ ! -x "$PIP_COMPILE" ]; then
	echo "pip-compile is required. Install the current lock with scripts/dev/install_dev_tools.sh first." >&2
	exit 1
fi

cd "$REPO_DIR"
mkdir -p "$PIP_TOOLS_CACHE_DIR"
"$PIP_COMPILE" \
	--allow-unsafe \
	--cache-dir "$PIP_TOOLS_CACHE_DIR" \
	--generate-hashes \
	--output-file=requirements-dev.lock \
	--strip-extras \
	requirements-dev.in

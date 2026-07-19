#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
REPO_DIR=$(realpath "$SCRIPT_DIR/../..")
VENV_DIR=${DEV_VENV_DIR:-"$REPO_DIR/.venv-ruff"}
PIP_CACHE_DIR=${PIP_CACHE_DIR:-"${TMPDIR:-/tmp}/venus-evcharger-pip"}

# Re-running venv is intentional: it repairs older project environments that
# were created without access to the system-provided dbus/gi modules.
python3 -m venv --system-site-packages "$VENV_DIR"
mkdir -p "$PIP_CACHE_DIR"
export PIP_CACHE_DIR
"$VENV_DIR/bin/python" -m pip install --require-hashes --requirement "$REPO_DIR/requirements-dev.lock"

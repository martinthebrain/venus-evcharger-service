#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later

# Lightweight target-system wrapper for the local Control and State API CLI.
#
# This keeps the operator-facing entrypoint in the deploy tree, while the
# actual CLI implementation stays in the repository root.

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" >/dev/null 2>&1 && pwd)
REPO_DIR=$(realpath "$SCRIPT_DIR/../..")

exec python3 "$(realpath "$REPO_DIR/venus_evchargerctl.py")" "$@"

#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
REPO_DIR=$(CDPATH='' cd -- "$SCRIPT_DIR/../.." && pwd)

exec "$REPO_DIR/rust/auto-input-helper/scripts/check.sh"

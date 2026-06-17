#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

"$SCRIPT_DIR/run_pylint_audit.sh"
"$SCRIPT_DIR/run_security_audit.sh"
"$SCRIPT_DIR/run_spellcheck.sh"
"$SCRIPT_DIR/run_quality_audit.sh"

if command -v shellcheck >/dev/null 2>&1 && command -v shfmt >/dev/null 2>&1; then
    "$SCRIPT_DIR/run_shell_audit.sh"
else
    echo "Skipping shell audit; install shellcheck and shfmt to enable it."
fi

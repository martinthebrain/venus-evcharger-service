#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)

exec python3 -m venus_evcharger.bootstrap.wizard --config-path "$SCRIPT_DIR/config.venus_evcharger.ini" "$@"

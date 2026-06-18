#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -eu

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
REPO_DIR=$(realpath "$SCRIPT_DIR/../..")

: "${SHELLY_STRESS_ITERS:=2000}"
: "${SHELLY_STRESS_THREADS:=6}"

export SHELLY_STRESS_ITERS
export SHELLY_STRESS_THREADS

cd "$REPO_DIR"

echo "Running Venus EV charger stress tests with SHELLY_STRESS_ITERS=${SHELLY_STRESS_ITERS} SHELLY_STRESS_THREADS=${SHELLY_STRESS_THREADS}"
python3 -m unittest tests.test_venus_evcharger_stress

#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
REPO_DIR=$(realpath "$SCRIPT_DIR/../..")
OUTPUT_DIR="$REPO_DIR/build/doxygen"
GENERATED_DIR="$OUTPUT_DIR/generated"
MANIFEST="$GENERATED_DIR/function_inventory.json"

if ! command -v doxygen >/dev/null 2>&1; then
	echo "doxygen is required; install the doxygen package" >&2
	exit 2
fi

cd "$REPO_DIR"
mkdir -p "$GENERATED_DIR"
rm -rf "$OUTPUT_DIR/html" "$OUTPUT_DIR/xml"
python3 "$SCRIPT_DIR/generate_doxygen_inventory.py" \
	--repository "$REPO_DIR" \
	--output "$GENERATED_DIR/function_inventory.md" \
	--manifest "$MANIFEST"
doxygen "$REPO_DIR/Doxyfile"
python3 "$SCRIPT_DIR/check_doxygen_output.py" \
	--manifest "$MANIFEST" \
	--xml-directory "$OUTPUT_DIR/xml" \
	--html-index "$OUTPUT_DIR/html/index.html"

echo "Doxygen documentation: $OUTPUT_DIR/html/index.html"

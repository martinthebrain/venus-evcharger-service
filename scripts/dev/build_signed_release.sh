#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
REPO_DIR=$(realpath "$SCRIPT_DIR/../..")

if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
	echo "Usage: $0 <output-dir> <release-base-url> [source-dir]" >&2
	exit 1
fi

OUTPUT_DIR="$1"
RELEASE_BASE_URL="${2%/}"
SOURCE_DIR="${3:-$REPO_DIR}"
SIGNING_KEY="${VENUS_EVCHARGER_BOOTSTRAP_SIGNING_KEY:-}"
PUBKEY="${VENUS_EVCHARGER_BOOTSTRAP_PUBKEY:-${SOURCE_DIR}/deploy/venus/bootstrap_manifest.pub}"

if [ -z "$SIGNING_KEY" ] || [ ! -r "$SIGNING_KEY" ]; then
	echo "VENUS_EVCHARGER_BOOTSTRAP_SIGNING_KEY must name a readable private key" >&2
	exit 1
fi
if [ ! -r "$PUBKEY" ]; then
	echo "Bootstrap public key is not readable: $PUBKEY" >&2
	exit 1
fi
if ! command -v openssl >/dev/null 2>&1; then
	echo "openssl is required to build a signed release" >&2
	exit 1
fi

SOURCE_COMMIT="${VENUS_EVCHARGER_SOURCE_COMMIT:-$(git -C "$SOURCE_DIR" rev-parse HEAD)}"
if ! printf '%s\n' "$SOURCE_COMMIT" | grep -Eq '^[0-9a-f]{40}$'; then
	echo "Release source commit must be exactly 40 lowercase hexadecimal characters" >&2
	exit 1
fi

VENUS_EVCHARGER_SOURCE_COMMIT="$SOURCE_COMMIT" \
	VENUS_EVCHARGER_REQUIRE_SIGNED_RELEASE=1 \
	VENUS_EVCHARGER_BOOTSTRAP_SIGNING_KEY="$SIGNING_KEY" \
	bash "$SOURCE_DIR/deploy/venus/build_bootstrap_bundle.sh" \
	"$OUTPUT_DIR" \
	"$SOURCE_DIR" \
	"$RELEASE_BASE_URL/wallbox-bundle.tar.gz" \
	"$RELEASE_BASE_URL/bootstrap_updater.sh"

openssl dgst -sha256 \
	-verify "$PUBKEY" \
	-signature "$OUTPUT_DIR/bootstrap_manifest.json.sig" \
	"$OUTPUT_DIR/bootstrap_manifest.json" >/dev/null
openssl dgst -sha256 \
	-verify "$PUBKEY" \
	-signature "$OUTPUT_DIR/install.sh.sig" \
	"$OUTPUT_DIR/install.sh" >/dev/null

printf '%s\n' "Verified signed release for commit $SOURCE_COMMIT"

#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
set -eu

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
REPO_ROOT=$(CDPATH='' cd -- "$ROOT/../.." && pwd)
CARGO_CACHE=${CARGO_CACHE:-/tmp/venus-evcharger-observer-cargo}
TARGET_CACHE=${TARGET_CACHE:-/tmp/venus-evcharger-observer-arm-target}
TARGET=armv7-unknown-linux-gnueabihf
RUST_IMAGE=${RUST_IMAGE:-rust:1.88-bookworm@sha256:af306cfa71d987911a781c37b59d7d67d934f49684058f96cf72079c3626bfe0}
OUTPUT_DIR="$REPO_ROOT/deploy/venus/bin"

mkdir -p "$CARGO_CACHE" "$TARGET_CACHE" "$OUTPUT_DIR"

if command -v cargo >/dev/null 2>&1 && command -v arm-linux-gnueabihf-gcc >/dev/null 2>&1; then
	rustup target add "$TARGET" >/dev/null
	CARGO_HOME="$CARGO_CACHE" \
		CARGO_TARGET_DIR="$TARGET_CACHE" \
		CARGO_TARGET_ARMV7_UNKNOWN_LINUX_GNUEABIHF_LINKER=arm-linux-gnueabihf-gcc \
		cargo build --manifest-path "$ROOT/Cargo.toml" --release --locked --target "$TARGET"
else
	docker run --rm \
		-e CARGO_HOME=/tmp/cargo \
		-e CARGO_TARGET_DIR=/tmp/target \
		-v "$CARGO_CACHE:/tmp/cargo" \
		-v "$TARGET_CACHE:/tmp/target" \
		-v "$ROOT:/work:ro" \
		-w /work \
		"$RUST_IMAGE" \
		sh -c 'apt-get update -qq &&
			apt-get install -y -qq gcc-arm-linux-gnueabihf >/dev/null &&
			rustup target add armv7-unknown-linux-gnueabihf >/dev/null &&
			CARGO_TARGET_ARMV7_UNKNOWN_LINUX_GNUEABIHF_LINKER=arm-linux-gnueabihf-gcc \
			cargo build --release --locked --target armv7-unknown-linux-gnueabihf'
fi

install -m 0755 \
	"$TARGET_CACHE/$TARGET/release/venus-evcharger-forensic-observer" \
	"$OUTPUT_DIR/venus-evcharger-forensic-observer"

echo "ARMv7 binary: $OUTPUT_DIR/venus-evcharger-forensic-observer"

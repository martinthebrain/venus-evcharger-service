#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
set -eu

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
REPO_ROOT=$(CDPATH='' cd -- "$ROOT/../.." && pwd)
CARGO_CACHE=${CARGO_CACHE:-/tmp/venus-evcharger-auto-input-cargo}
TARGET_CACHE=${TARGET_CACHE:-/tmp/venus-evcharger-auto-input-arm-target}
TARGET=armv7-unknown-linux-gnueabihf
RUST_IMAGE=${RUST_IMAGE:-rust:1.88-bookworm@sha256:af306cfa71d987911a781c37b59d7d67d934f49684058f96cf72079c3626bfe0}
USE_HOST_TOOLCHAIN=${VENUS_EVCHARGER_AUTO_INPUT_USE_HOST_TOOLCHAIN:-0}
ARM_GCC_META_VERSION=4:12.2.0-3
ARM_GCC_VERSION=12.2.0-14cross1
ARM_BINUTILS_VERSION=2.40-2
ARM_LIBC_DEV_VERSION=2.36-8cross1
OUTPUT_DIR="$REPO_ROOT/deploy/venus/bin"
OUTPUT_NAME=venus-evcharger-auto-input-helper

if [ ! -f "$ROOT/Cargo.lock" ]; then
	echo "rust/auto-input-helper/Cargo.lock is required for reproducible builds" >&2
	exit 1
fi

mkdir -p "$CARGO_CACHE" "$TARGET_CACHE" "$OUTPUT_DIR"

case "$USE_HOST_TOOLCHAIN" in
0 | 1) ;;
*)
	echo "VENUS_EVCHARGER_AUTO_INPUT_USE_HOST_TOOLCHAIN must be 0 or 1" >&2
	exit 2
	;;
esac

if [ "$USE_HOST_TOOLCHAIN" = "1" ]; then
	if ! command -v cargo >/dev/null 2>&1 || ! command -v arm-linux-gnueabihf-gcc >/dev/null 2>&1; then
		echo "Host ARM build requires cargo and arm-linux-gnueabihf-gcc" >&2
		exit 1
	fi
	rustup target add "$TARGET" >/dev/null
	CARGO_HOME="$CARGO_CACHE" \
		CARGO_TARGET_DIR="$TARGET_CACHE" \
		CARGO_TARGET_ARMV7_UNKNOWN_LINUX_GNUEABIHF_LINKER=arm-linux-gnueabihf-gcc \
		cargo build --manifest-path "$ROOT/Cargo.toml" --release --locked --target "$TARGET"
else
	if ! command -v docker >/dev/null 2>&1; then
		echo "Reproducible ARM build requires Docker; set VENUS_EVCHARGER_AUTO_INPUT_USE_HOST_TOOLCHAIN=1 for an explicit host build" >&2
		exit 1
	fi
	docker run --rm \
		-e CARGO_HOME=/tmp/cargo \
		-e CARGO_TARGET_DIR=/tmp/target \
		-e ARM_GCC_META_VERSION="$ARM_GCC_META_VERSION" \
		-e ARM_GCC_VERSION="$ARM_GCC_VERSION" \
		-e ARM_BINUTILS_VERSION="$ARM_BINUTILS_VERSION" \
		-e ARM_LIBC_DEV_VERSION="$ARM_LIBC_DEV_VERSION" \
		-v "$CARGO_CACHE:/tmp/cargo" \
		-v "$TARGET_CACHE:/tmp/target" \
		-v "$ROOT:/work:ro" \
		-w /work \
		"$RUST_IMAGE" \
		sh -c 'apt-get update -qq &&
			apt-get install -y -qq --no-install-recommends \
				"gcc-arm-linux-gnueabihf=$ARM_GCC_META_VERSION" \
				"gcc-12-arm-linux-gnueabihf=$ARM_GCC_VERSION" \
				"binutils-arm-linux-gnueabihf=$ARM_BINUTILS_VERSION" \
				"libc6-dev-armhf-cross=$ARM_LIBC_DEV_VERSION" >/dev/null &&
			rustup target add armv7-unknown-linux-gnueabihf >/dev/null &&
			CARGO_TARGET_ARMV7_UNKNOWN_LINUX_GNUEABIHF_LINKER=arm-linux-gnueabihf-gcc \
			cargo build --release --locked --target armv7-unknown-linux-gnueabihf'
fi

install -m 0755 \
	"$TARGET_CACHE/$TARGET/release/$OUTPUT_NAME" \
	"$OUTPUT_DIR/$OUTPUT_NAME"

echo "ARMv7 binary: $OUTPUT_DIR/$OUTPUT_NAME"

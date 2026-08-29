#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
set -eu

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
REPO_ROOT=$(CDPATH='' cd -- "$ROOT/../.." && pwd)
cd "$ROOT"

if [ ! -f Cargo.lock ]; then
	echo "rust/auto-input-helper/Cargo.lock is required for reproducible checks" >&2
	exit 1
fi

run_differential() {
	RUST_BINARY=$1
	PYTHON=${PYTHON:-python3}
	"$PYTHON" "$REPO_ROOT/scripts/dev/run_auto_input_helper_differential.py" \
		--rust-binary "$RUST_BINARY"
}

run_checks() {
	cargo fmt --all -- --check
	cargo test --all-targets --locked
	run_differential "${CARGO_TARGET_DIR:-$ROOT/target}/debug/venus-evcharger-auto-input-helper"
	cargo clippy --all-targets --locked -- -D warnings
	RUSTDOCFLAGS='-D warnings' cargo doc --no-deps --locked
}

if command -v cargo >/dev/null 2>&1; then
	run_checks
	exit 0
fi

CHECK_IMAGE=${RUST_IMAGE:-rust:1.88-bookworm@sha256:af306cfa71d987911a781c37b59d7d67d934f49684058f96cf72079c3626bfe0}
CARGO_CACHE=${CARGO_CACHE:-/tmp/venus-evcharger-auto-input-cargo}
TARGET_CACHE=${TARGET_CACHE:-/tmp/venus-evcharger-auto-input-check-target}
mkdir -p "$CARGO_CACHE" "$TARGET_CACHE"

docker run --rm \
	-e CARGO_HOME=/tmp/cargo \
	-e CARGO_TARGET_DIR=/tmp/target \
	-e RUSTDOCFLAGS=-Dwarnings \
	-v "$CARGO_CACHE:/tmp/cargo" \
	-v "$TARGET_CACHE:/tmp/target" \
	-v "$ROOT:/work" \
	-w /work \
	"$CHECK_IMAGE" \
	sh -c 'rustup component add rustfmt clippy >/dev/null &&
		cargo fmt --all -- --check &&
		cargo test --all-targets --locked &&
		cargo clippy --all-targets --locked -- -D warnings &&
		cargo doc --no-deps --locked'

run_differential "$TARGET_CACHE/debug/venus-evcharger-auto-input-helper"

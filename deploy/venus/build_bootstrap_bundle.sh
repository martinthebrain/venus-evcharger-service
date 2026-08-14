#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later

set -eu

if [ "${1:-}" = "" ]; then
	echo "Usage: $0 <output-dir> [source-dir] [bundle-url] [updater-url]" >&2
	exit 1
fi

OUTPUT_DIR="$1"
SOURCE_DIR="${2:-$(cd -- "$(dirname -- "$0")/../.." >/dev/null 2>&1 && pwd)}"
BUNDLE_URL="${3:-wallbox-bundle.tar.gz}"
UPDATER_URL="${4:-bootstrap_updater.sh}"
MANIFEST_PATH="${OUTPUT_DIR}/bootstrap_manifest.json"
BUNDLE_PATH="${OUTPUT_DIR}/wallbox-bundle.tar.gz"
MANIFEST_SIG_PATH="${OUTPUT_DIR}/bootstrap_manifest.json.sig"
INSTALL_SIG_PATH="${OUTPUT_DIR}/install.sh.sig"
SIGNING_KEY="${VENUS_EVCHARGER_BOOTSTRAP_SIGNING_KEY:-}"
REQUIRE_SIGNATURE="${VENUS_EVCHARGER_REQUIRE_SIGNED_RELEASE:-0}"

if [ "$REQUIRE_SIGNATURE" = "1" ] && [ -z "$SIGNING_KEY" ]; then
	echo "VENUS_EVCHARGER_BOOTSTRAP_SIGNING_KEY is required for a signed release" >&2
	exit 1
fi

copy_item() {
	src_root="$1"
	dst_root="$2"
	rel_path="$3"
	src_path="${src_root}/${rel_path}"
	dst_path="${dst_root}/${rel_path}"

	[ -e "$src_path" ] || return 0
	mkdir -p "$(dirname "$dst_path")"
	if [ -d "$src_path" ]; then
		cp -R "$src_path" "$dst_path"
	else
		cp "$src_path" "$dst_path"
	fi
}

mkdir -p "$OUTPUT_DIR"
mkdir -p "${OUTPUT_DIR}/bootstrap_updater.d"
cp "${SOURCE_DIR}/deploy/venus/bootstrap_updater.sh" "${OUTPUT_DIR}/bootstrap_updater.sh"
cp "${SOURCE_DIR}/install.sh" "${OUTPUT_DIR}/install.sh"
cp "${SOURCE_DIR}/deploy/venus/bootstrap_manifest.pub" "${OUTPUT_DIR}/bootstrap_manifest.pub"
cp "${SOURCE_DIR}/deploy/venus/bootstrap_updater.d/"*.sh "${OUTPUT_DIR}/bootstrap_updater.d/"
stage_dir=$(mktemp -d)
cleanup_stage() {
	rm -rf "$stage_dir"
}
trap cleanup_stage EXIT

for rel_path in \
	install.sh \
	LICENSE \
	README.md \
	SHELLY_PROFILES.md \
	version.txt \
	venus_evcharger_service.py \
	venus_evcharger_dbus_adapter.py \
	venus_evcharger_auto_input_helper.py \
	venus_evchargerctl.py \
	deploy/venus \
	venus_evcharger \
	scripts/ops; do
	copy_item "$SOURCE_DIR" "$stage_dir" "$rel_path"
done

rm -rf "${stage_dir}/tests" "${stage_dir}/docs" "${stage_dir}/.github" "${stage_dir}/scripts/dev"
rm -f "${stage_dir}/Makefile" "${stage_dir}/mypy.ini" "${stage_dir}/mypy_strict.ini" "${stage_dir}/pyrightconfig.json"

tar -czf "$BUNDLE_PATH" -C "$stage_dir" .
bundle_sha=$(sha256sum "$BUNDLE_PATH" | awk '{print $1}')
updater_sha=$(sha256sum "${SOURCE_DIR}/deploy/venus/bootstrap_updater.sh" | awk '{print $1}')
updater_lib_core_sha=$(sha256sum "${SOURCE_DIR}/deploy/venus/bootstrap_updater.d/00_core.sh" | awk '{print $1}')
updater_lib_config_sha=$(sha256sum "${SOURCE_DIR}/deploy/venus/bootstrap_updater.d/10_config_merge.sh" | awk '{print $1}')
updater_lib_layout_sha=$(sha256sum "${SOURCE_DIR}/deploy/venus/bootstrap_updater.d/20_layout.sh" | awk '{print $1}')
updater_lib_status_sha=$(sha256sum "${SOURCE_DIR}/deploy/venus/bootstrap_updater.d/30_status_main.sh" | awk '{print $1}')
version="dev"
if [ -f "${SOURCE_DIR}/version.txt" ]; then
	version=$(head -n 1 "${SOURCE_DIR}/version.txt" | tr -d '\r' | sed 's/^[Vv]ersion:[[:space:]]*//')
fi
source_commit="${VENUS_EVCHARGER_SOURCE_COMMIT:-}"
source_repo="${VENUS_EVCHARGER_REPO_SLUG:-martinthebrain/venus-evcharger-service}"
if [ -z "$source_commit" ] && command -v git >/dev/null 2>&1; then
	source_commit=$(git -C "$SOURCE_DIR" rev-parse HEAD 2>/dev/null || true)
fi

if [ "$REQUIRE_SIGNATURE" = "1" ]; then
	if ! printf '%s\n' "$source_commit" | grep -Eq '^[0-9a-f]{40}$'; then
		echo "Signed release source commit must be exactly 40 lowercase hexadecimal characters" >&2
		exit 1
	fi
	if [ -z "$version" ] || [ "$version" = "dev" ]; then
		echo "Signed release must have an explicit version" >&2
		exit 1
	fi
fi

python3 - "$MANIFEST_PATH" "$version" "$source_repo" "$source_commit" \
	"$BUNDLE_URL" "$bundle_sha" "$UPDATER_URL" "$updater_sha" \
	"$updater_lib_core_sha" "$updater_lib_config_sha" "$updater_lib_layout_sha" "$updater_lib_status_sha" <<'PY'
import json
import sys

(
    manifest_path,
    version,
    source_repo,
    source_commit,
    bundle_url,
    bundle_sha256,
    updater_url,
    updater_sha256,
    updater_lib_core_sha256,
    updater_lib_config_sha256,
    updater_lib_layout_sha256,
    updater_lib_status_sha256,
) = sys.argv[1:]
payload = {
    "format": 1,
    "channel": "release",
    "version": version,
    "source_repo": source_repo,
    "source_commit": source_commit,
    "bundle_url": bundle_url,
    "bundle_sha256": bundle_sha256,
    "updater_url": updater_url,
    "updater_sha256": updater_sha256,
    "updater_lib_sha256": {
        "00_core.sh": updater_lib_core_sha256,
        "10_config_merge.sh": updater_lib_config_sha256,
        "20_layout.sh": updater_lib_layout_sha256,
        "30_status_main.sh": updater_lib_status_sha256,
    },
}
with open(manifest_path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
PY

printf '%s  %s\n' "$bundle_sha" "$(basename "$BUNDLE_PATH")" >"${BUNDLE_PATH}.sha256"
printf '%s  bootstrap_manifest.json\n' "$(sha256sum "$MANIFEST_PATH" | awk '{print $1}')" >"${MANIFEST_PATH}.sha256"
printf '%s  bootstrap_updater.sh\n' "$updater_sha" >"${OUTPUT_DIR}/bootstrap_updater.sh.sha256"
printf '%s  install.sh\n' "$(sha256sum "${OUTPUT_DIR}/install.sh" | awk '{print $1}')" >"${OUTPUT_DIR}/install.sh.sha256"

rm -f "$MANIFEST_SIG_PATH" "$INSTALL_SIG_PATH"
if [ -n "$SIGNING_KEY" ]; then
	openssl dgst -sha256 -sign "$SIGNING_KEY" -out "$MANIFEST_SIG_PATH" "$MANIFEST_PATH"
	openssl dgst -sha256 -sign "$SIGNING_KEY" -out "$INSTALL_SIG_PATH" "${OUTPUT_DIR}/install.sh"
	printf '%s\n' "Wrote ${MANIFEST_SIG_PATH}"
	printf '%s\n' "Wrote ${INSTALL_SIG_PATH}"
fi

if [ "$REQUIRE_SIGNATURE" = "1" ] && { [ ! -s "$MANIFEST_SIG_PATH" ] || [ ! -s "$INSTALL_SIG_PATH" ]; }; then
	echo "Signed release bootstrap artifacts were not produced" >&2
	exit 1
fi

printf '%s\n' "Wrote ${BUNDLE_PATH}"
printf '%s\n' "Wrote ${MANIFEST_PATH}"

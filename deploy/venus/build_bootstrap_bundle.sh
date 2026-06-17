#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later

set -eu

if [ "${1:-}" = "" ]; then
    echo "Usage: $0 <output-dir> [source-dir] [bundle-url]" >&2
    exit 1
fi

OUTPUT_DIR="$1"
SOURCE_DIR="${2:-$(cd -- "$(dirname -- "$0")/../.." >/dev/null 2>&1 && pwd)}"
BUNDLE_URL="${3:-wallbox-bundle.tar.gz}"
MANIFEST_PATH="${OUTPUT_DIR}/bootstrap_manifest.json"
BUNDLE_PATH="${OUTPUT_DIR}/wallbox-bundle.tar.gz"
MANIFEST_SIG_PATH="${OUTPUT_DIR}/bootstrap_manifest.json.sig"
SIGNING_KEY="${VENUS_EVCHARGER_BOOTSTRAP_SIGNING_KEY:-}"

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
    venus_evcharger_observer.py \
    venus_evcharger_auto_input_helper.py \
    deploy/venus \
    venus_evcharger \
    scripts/ops
do
    copy_item "$SOURCE_DIR" "$stage_dir" "$rel_path"
done

rm -rf "${stage_dir}/tests" "${stage_dir}/docs" "${stage_dir}/.github" "${stage_dir}/scripts/dev"
rm -f "${stage_dir}/Makefile" "${stage_dir}/mypy.ini" "${stage_dir}/mypy_strict.ini" "${stage_dir}/pyrightconfig.json"

tar -czf "$BUNDLE_PATH" -C "$stage_dir" .
bundle_sha=$(sha256sum "$BUNDLE_PATH" | awk '{print $1}')
updater_sha=$(sha256sum "${SOURCE_DIR}/deploy/venus/bootstrap_updater.sh" | awk '{print $1}')
version="dev"
if [ -f "${SOURCE_DIR}/version.txt" ]; then
    version=$(head -n 1 "${SOURCE_DIR}/version.txt" | tr -d '\r')
fi

cat > "$MANIFEST_PATH" <<EOF
{
  "format": 1,
  "channel": "release",
  "version": "${version}",
  "bundle_url": "${BUNDLE_URL}",
  "bundle_sha256": "${bundle_sha}",
  "updater_url": "bootstrap_updater.sh",
  "updater_sha256": "${updater_sha}"
}
EOF

printf '%s  %s\n' "$bundle_sha" "$(basename "$BUNDLE_PATH")" > "${BUNDLE_PATH}.sha256"
printf '%s  bootstrap_manifest.json\n' "$(sha256sum "$MANIFEST_PATH" | awk '{print $1}')" > "${MANIFEST_PATH}.sha256"

if [ -n "$SIGNING_KEY" ]; then
    openssl dgst -sha256 -sign "$SIGNING_KEY" -out "$MANIFEST_SIG_PATH" "$MANIFEST_PATH"
    printf '%s\n' "Wrote ${MANIFEST_SIG_PATH}"
fi

printf '%s\n' "Wrote ${BUNDLE_PATH}"
printf '%s\n' "Wrote ${MANIFEST_PATH}"

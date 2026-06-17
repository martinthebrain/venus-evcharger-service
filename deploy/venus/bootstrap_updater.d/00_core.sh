# SPDX-License-Identifier: GPL-3.0-or-later

log() {
    printf '%s\n' "[updater] $*" >&2
}

set_failure_reason_once() {
    if [ -z "$RUN_FAILURE_REASON" ]; then
        RUN_FAILURE_REASON="$1"
    fi
}

require_command() {
    command_name="$1"
    if ! command -v "$command_name" >/dev/null 2>&1; then
        log "Required command is missing: $command_name"
        set_failure_reason_once "missing-command:${command_name}"
        exit 1
    fi
}

ensure_download_tool() {
    if command -v wget >/dev/null 2>&1 || command -v curl >/dev/null 2>&1; then
        return 0
    fi
    log "Neither wget nor curl is available"
    set_failure_reason_once "missing-download-tool"
    exit 1
}

ensure_updater_prereqs() {
    require_command cp
    require_command rm
    require_command mv
    require_command ln
    require_command mkdir
    require_command chmod
    require_command awk
    require_command mktemp
    require_command tar
    require_command sha256sum
    require_command python3
    if [ -z "$SOURCE_DIR_OVERRIDE" ]; then
        ensure_download_tool
    fi
    if [ "$REQUIRE_SIGNED_MANIFEST" = "1" ]; then
        require_command openssl
    fi
}

download_to() {
    src="$1"
    dst="$2"
    if [ -f "$src" ]; then
        cp "$src" "$dst"
        return 0
    fi
    if command -v wget >/dev/null 2>&1; then
        wget -q -O "$dst" "$src"
        return 0
    fi
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL -o "$dst" "$src"
        return 0
    fi
    log "Neither wget nor curl is available to fetch $src"
    set_failure_reason_once "download-unavailable"
    return 1
}

require_source_layout() {
    src_dir="$1"
    [ -f "${src_dir}/venus_evcharger_service.py" ] || return 1
    [ -f "${src_dir}/venus_evcharger_auto_input_helper.py" ] || return 1
    [ -f "${src_dir}/deploy/venus/install_venus_evcharger_service.sh" ] || return 1
    [ -d "${src_dir}/venus_evcharger" ] || return 1
    return 0
}

write_builtin_pubkey() {
    destination="$1"
    cat > "$destination" <<'EOF'
-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAsWdKDZgN3QdCJ9VXsbk6
xEwJ/8l92kxAyNgLWJ6QwgvusA8mTKEpYfYLoKszqVCiO8nH4O8/MYrOAXqpwfa9
er2lBaIiUhvbuzUKKlfz5iq7hJ7/G2jWvTizUpY1NtwT0LY2hm9xELfbzintKK9r
Gpd1QLxbJ2b7X4K1l+I/3DsoH59dbLUGP4yQgGH0x0vO3tgULKu/oVKp2bEjae9i
ukU9eZio9Yry5YsFwSnuqfiLO5frFXt8Jeikf24vQTGz5bjG1kjQTYDGVO/4WLPj
graKJ4MBJXTsEs4Gy7kcSRDMfc4CvziUx9he8FI34j/qT3eQ9A1Fi9Sfti3dCZB7
FwIDAQAB
-----END PUBLIC KEY-----
EOF
}

resolve_pubkey_path() {
    if [ -n "$BOOTSTRAP_PUBKEY_OVERRIDE" ] && [ -f "$BOOTSTRAP_PUBKEY_OVERRIDE" ]; then
        printf '%s\n' "$BOOTSTRAP_PUBKEY_OVERRIDE"
        return 0
    fi
    sibling_pubkey="$(cd -- "$(dirname -- "$0")" >/dev/null 2>&1 && pwd)/bootstrap_manifest.pub"
    if [ -f "$sibling_pubkey" ]; then
        printf '%s\n' "$sibling_pubkey"
        return 0
    fi
    if [ -n "${TMP_DIR:-}" ]; then
        pubkey_path="${TMP_DIR}/bootstrap_manifest.pub"
        mkdir -p "$TMP_DIR"
    else
        pubkey_path="${STATE_DIR}/bootstrap_manifest.pub"
        mkdir -p "$STATE_DIR"
    fi
    write_builtin_pubkey "$pubkey_path"
    printf '%s\n' "$pubkey_path"
}

verify_signature() {
    manifest_path="$1"
    signature_path="$2"
    pubkey_path="$3"
    command -v openssl >/dev/null 2>&1 || return 1
    openssl dgst -sha256 -verify "$pubkey_path" -signature "$signature_path" "$manifest_path" >/dev/null 2>&1
}

json_field() {
    json_path="$1"
    field_name="$2"
    python3 - "$json_path" "$field_name" <<'PY'
import json
import sys

path, field = sys.argv[1], sys.argv[2]
try:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
except Exception:
    sys.exit(1)
value = data.get(field, "")
if value is None:
    sys.exit(0)
if isinstance(value, bool):
    print("1" if value else "0")
elif isinstance(value, (str, int, float)):
    print(value)
PY
}

json_lines_field() {
    json_path="$1"
    field_name="$2"
    python3 - "$json_path" "$field_name" <<'PY'
import json
import sys

path, field = sys.argv[1], sys.argv[2]
try:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
except Exception:
    sys.exit(1)
value = data.get(field, [])
if isinstance(value, list):
    for item in value:
        if item is None:
            continue
        print(str(item))
PY
}

manifest_field() {
    json_field "$1" "$2"
}

read_text_file() {
    file_path="$1"
    if [ -f "$file_path" ]; then
        awk 'NF { print; exit }' "$file_path"
    fi
}

normalize_version_value() {
    raw_value="$1"
    printf '%s\n' "$raw_value" | awk '
        NF {
            line=$0
            sub(/^[Vv]ersion:[[:space:]]*/, "", line)
            print line
            exit
        }
    '
}

read_tree_version() {
    root_dir="$1"
    raw_version=$(read_text_file "${root_dir}/version.txt" || true)
    normalize_version_value "$raw_version"
}

detect_current_version() {
    current_version=$(read_text_file "$INSTALLED_VERSION_FILE" || true)
    if [ -n "$current_version" ]; then
        normalize_version_value "$current_version"
        return 0
    fi
    active_dir=$(current_codebase_dir)
    read_tree_version "$active_dir" || true
}

detect_current_bundle_hash() {
    read_text_file "$INSTALLED_BUNDLE_HASH_FILE" || true
}

normalize_multiline_var() {
    input_value="$1"
    printf '%s' "$input_value" | awk 'NF {print}'
}

load_manifest() {
    manifest_path="$1"
    signature_path="${manifest_path}.sig"
    [ -n "$MANIFEST_SOURCE" ] || return 1
    download_to "$MANIFEST_SOURCE" "$manifest_path" || return 1
    pubkey_path=$(resolve_pubkey_path)
    if download_to "$MANIFEST_SIG_SOURCE" "$signature_path"; then
        if ! verify_signature "$manifest_path" "$signature_path" "$pubkey_path"; then
            log "Manifest signature verification failed"
            set_failure_reason_once "manifest-signature-verification-failed"
            return 1
        fi
    elif [ "$REQUIRE_SIGNED_MANIFEST" = "1" ]; then
        log "Signed manifest required but signature could not be fetched"
        set_failure_reason_once "manifest-signature-missing"
        return 1
    else
        return 1
    fi

    MANIFEST_BUNDLE_URL=$(manifest_field "$manifest_path" "bundle_url" || true)
    MANIFEST_BUNDLE_SHA256=$(manifest_field "$manifest_path" "bundle_sha256" || true)
    MANIFEST_VERSION=$(manifest_field "$manifest_path" "version" || true)
    MANIFEST_CHANNEL=$(manifest_field "$manifest_path" "channel" || true)

    [ -n "$MANIFEST_BUNDLE_URL" ] || return 1
    [ -n "$MANIFEST_BUNDLE_SHA256" ] || return 1
    return 0
}

current_codebase_dir() {
    if [ -L "$CURRENT_LINK" ] || [ -d "$CURRENT_LINK" ]; then
        printf '%s\n' "$CURRENT_LINK"
        return 0
    fi
    printf '%s\n' "$TARGET_DIR"
}

target_is_current_for_manifest() {
    [ -f "$INSTALLED_BUNDLE_HASH_FILE" ] || return 1
    [ -n "${MANIFEST_BUNDLE_SHA256:-}" ] || return 1
    current_hash=$(awk 'NF {print $1; exit}' "$INSTALLED_BUNDLE_HASH_FILE")
    [ "$current_hash" = "$MANIFEST_BUNDLE_SHA256" ] || return 1
    active_dir=$(current_codebase_dir)
    require_source_layout "$active_dir"
}

copy_item() {
    src_root="$1"
    dst_root="$2"
    rel_path="$3"
    src_path="${src_root}/${rel_path}"
    dst_path="${dst_root}/${rel_path}"

    if [ ! -e "$src_path" ]; then
        return 0
    fi

    rm -rf "$dst_path"
    mkdir -p "$(dirname "$dst_path")"
    if [ -d "$src_path" ]; then
        cp -R "$src_path" "$dst_path"
    else
        cp "$src_path" "$dst_path"
    fi
}

managed_layout_paths() {
    printf '%s\n' \
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
}

copy_managed_layout_items() {
    src_dir="$1"
    dst_dir="$2"
    while IFS= read -r rel_path; do
        copy_item "$src_dir" "$dst_dir" "$rel_path"
    done <<EOF
$(managed_layout_paths)
EOF
}

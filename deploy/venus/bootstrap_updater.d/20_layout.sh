# SPDX-License-Identifier: GPL-3.0-or-later

cleanup_unwanted_paths() {
    cleanup_root="$1"
    rm -rf "${cleanup_root}/tests"
    rm -rf "${cleanup_root}/docs"
    rm -rf "${cleanup_root}/.github"
    rm -rf "${cleanup_root}/scripts/dev"
    rm -rf "${cleanup_root}/__pycache__"
    rm -f "${cleanup_root}/Makefile"
    rm -f "${cleanup_root}/mypy.ini"
    rm -f "${cleanup_root}/mypy_strict.ini"
    rm -f "${cleanup_root}/pyrightconfig.json"
}

write_managed_layout() {
    src_dir="$1"
    destination_root="${2:-$TARGET_DIR}"
    mkdir -p "$destination_root"

    preserve_dir=$(mktemp -d)
    preserve_local_config "$preserve_dir"

    copy_managed_layout_items "$src_dir" "$destination_root"

    restore_local_config "$preserve_dir" "$destination_root"
    if ! merge_local_config_template "$src_dir" "$destination_root"; then
        rm -rf "$preserve_dir"
        return 1
    fi
    rm -rf "$preserve_dir"
    cleanup_unwanted_paths "$destination_root"
    if ! validate_wallbox_config "$destination_root"; then
        return 1
    fi

    chmod 755 "${destination_root}/install.sh" 2>/dev/null || true
    chmod 755 "${destination_root}/venus_evcharger_service.py" 2>/dev/null || true
    chmod 755 "${destination_root}/venus_evcharger_observer.py" 2>/dev/null || true
    chmod 755 "${destination_root}/venus_evcharger_auto_input_helper.py" 2>/dev/null || true
    chmod 755 "${destination_root}/deploy/venus/install_venus_evcharger_service.sh" 2>/dev/null || true
    chmod 755 "${destination_root}/deploy/venus/boot_venus_evcharger_service.sh" 2>/dev/null || true
    chmod 744 "${destination_root}/deploy/venus/restart_venus_evcharger_service.sh" 2>/dev/null || true
    chmod 744 "${destination_root}/deploy/venus/uninstall_venus_evcharger_service.sh" 2>/dev/null || true
    chmod 755 "${destination_root}/deploy/venus/service_venus_evcharger/run" 2>/dev/null || true
    chmod 755 "${destination_root}/deploy/venus/service_venus_evcharger/log/run" 2>/dev/null || true
    chmod 755 "${destination_root}/deploy/venus/service_venus_evcharger_observer/run" 2>/dev/null || true
}

promote_target_layout() {
    staged_root="$1"
    mkdir -p "$TARGET_DIR"
    copy_managed_layout_items "$staged_root" "$TARGET_DIR"
    cleanup_unwanted_paths "$TARGET_DIR"
}

promote_release_layout() {
    src_dir="$1"
    release_version="${MANIFEST_VERSION:-bundle}"
    mkdir -p "$RELEASES_DIR"
    final_release_dir="${RELEASES_DIR}/${release_version}"
    staging_release_dir="${RELEASES_DIR}/.${release_version}.staging.$$"
    rm -rf "$staging_release_dir"
    if ! write_managed_layout "$src_dir" "$staging_release_dir"; then
        CURRENT_PRESERVED=1
        rm -rf "$staging_release_dir"
        return 1
    fi
    rm -rf "$final_release_dir"
    mv "$staging_release_dir" "$final_release_dir"
    if [ -n "$CONFIG_MERGE_BACKUP_PATH" ]; then
        CONFIG_MERGE_BACKUP_PATH="${CONFIG_MERGE_BACKUP_PATH/$staging_release_dir/$final_release_dir}"
    fi
    if [ -L "$CURRENT_LINK" ] && command -v readlink >/dev/null 2>&1; then
        current_target=$(readlink "$CURRENT_LINK" 2>/dev/null || true)
        if [ -n "$current_target" ]; then
            ln -sfn "$current_target" "$PREVIOUS_LINK"
        fi
    fi
    ln -sfn "$final_release_dir" "$CURRENT_LINK"
    PROMOTED_RELEASE="$final_release_dir"
}

record_install_state() {
    mkdir -p "$STATE_DIR"
    if [ -n "${MANIFEST_BUNDLE_SHA256:-}" ]; then
        printf '%s\n' "$MANIFEST_BUNDLE_SHA256" > "$INSTALLED_BUNDLE_HASH_FILE"
    fi
    if [ -n "${MANIFEST_VERSION:-}" ]; then
        printf '%s\n' "$MANIFEST_VERSION" > "$INSTALLED_VERSION_FILE"
    elif [ -n "$RUN_NEW_VERSION" ]; then
        printf '%s\n' "$(normalize_version_value "$RUN_NEW_VERSION")" > "$INSTALLED_VERSION_FILE"
    fi
}

materialize_source_from_bundle() {
    archive_path="$1"
    extract_dir="$2"
    log "Downloading code bundle${MANIFEST_VERSION:+ version $MANIFEST_VERSION}"
    download_to "$MANIFEST_BUNDLE_URL" "$archive_path"
    archive_hash=$(sha256sum "$archive_path" | awk '{print $1}')
    if [ "$archive_hash" != "$MANIFEST_BUNDLE_SHA256" ]; then
        log "Bundle hash mismatch for $MANIFEST_BUNDLE_URL"
        set_failure_reason_once "bundle-hash-mismatch"
        exit 1
    fi
    mkdir -p "$extract_dir"
    tar -xzf "$archive_path" -C "$extract_dir"
    SOURCE_DIR="$extract_dir"
    if ! require_source_layout "$SOURCE_DIR"; then
        SOURCE_DIR=$(find "$extract_dir" -mindepth 1 -maxdepth 1 -type d | head -n 1)
        if [ -z "$SOURCE_DIR" ] || ! require_source_layout "$SOURCE_DIR"; then
            log "Downloaded code bundle is incomplete"
            set_failure_reason_once "incomplete-downloaded-bundle"
            exit 1
        fi
    fi
}

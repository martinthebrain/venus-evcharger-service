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
	rm -f "${cleanup_root}/DBUS_INTROSPECTION_WORKER.md"
	rm -f "${cleanup_root}/dbus_adapter_write.py"
	rm -f "${cleanup_root}"/venus_evcharger/dbus_adapter_*.py
	rm -f "${cleanup_root}/venus_evcharger_dbus_introspection_worker.py"
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
	chmod 755 "${destination_root}/venus_evcharger_dbus_adapter.py" 2>/dev/null || true
	chmod 755 "${destination_root}/venus_evcharger_observer.py" 2>/dev/null || true
	chmod 755 "${destination_root}/venus_evcharger_auto_input_helper.py" 2>/dev/null || true
	chmod 755 "${destination_root}/venus_evchargerctl.py" 2>/dev/null || true
	chmod 755 "${destination_root}/deploy/venus/install_venus_evcharger_service.sh" 2>/dev/null || true
	chmod 755 "${destination_root}/deploy/venus/boot_venus_evcharger_service.sh" 2>/dev/null || true
	chmod 755 "${destination_root}/deploy/venus/service_lifecycle.sh" 2>/dev/null || true
	chmod 744 "${destination_root}/deploy/venus/restart_venus_evcharger_service.sh" 2>/dev/null || true
	chmod 744 "${destination_root}/deploy/venus/uninstall_venus_evcharger_service.sh" 2>/dev/null || true
	chmod 755 "${destination_root}/deploy/venus/service_venus_evcharger/run" 2>/dev/null || true
	chmod 755 "${destination_root}/deploy/venus/service_venus_evcharger/log/run" 2>/dev/null || true
	chmod 755 "${destination_root}/deploy/venus/service_venus_evcharger_dbus_adapter/run" 2>/dev/null || true
	chmod 755 "${destination_root}/deploy/venus/service_venus_evcharger_dbus_adapter/log/run" 2>/dev/null || true
	chmod 755 "${destination_root}/deploy/venus/service_venus_evcharger_observer/run" 2>/dev/null || true
	chmod 755 "${destination_root}/deploy/venus/service_venus_evcharger_observer/log/run" 2>/dev/null || true
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
	if [ -e "$final_release_dir" ] || [ -L "$final_release_dir" ]; then
		release_suffix=$(printf '%s' "${RUN_NEW_BUNDLE_SHA256:-$$}" | awk '{print substr($0, 1, 12)}')
		final_release_dir="${RELEASES_DIR}/${release_version}-${release_suffix}"
		if [ -e "$final_release_dir" ] || [ -L "$final_release_dir" ]; then
			final_release_dir="${final_release_dir}-$$"
		fi
	fi
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

resolve_bootstrap_entrypoint() {
	if [ -n "$BOOTSTRAP_ENTRYPOINT" ]; then
		printf '%s\n' "$BOOTSTRAP_ENTRYPOINT"
		return 0
	fi
	inferred_path="$(dirname "$TARGET_DIR")/install.sh"
	if [ -f "$inferred_path" ] && grep -q "Minimal GX bootstrap installer" "$inferred_path"; then
		printf '%s\n' "$inferred_path"
		return 0
	fi
	return 1
}

refresh_bootstrap_entrypoint() {
	active_root=$(current_codebase_dir)
	source_path="${active_root}/install.sh"
	destination_path=$(resolve_bootstrap_entrypoint || true)
	[ -n "$destination_path" ] || return 0
	[ -f "$source_path" ] || return 0
	BOOTSTRAP_ENTRYPOINT="$destination_path"
	export BOOTSTRAP_ENTRYPOINT
	if [ "$(cd -- "$(dirname "$source_path")" && pwd)/$(basename "$source_path")" = \
		"$(cd -- "$(dirname "$destination_path")" && pwd)/$(basename "$destination_path")" ]; then
		BOOTSTRAP_REFRESHED=1
		export BOOTSTRAP_REFRESHED
		return 0
	fi
	temporary_path="${destination_path}.tmp.$$"
	if ! cp "$source_path" "$temporary_path" || ! chmod 755 "$temporary_path" || ! mv "$temporary_path" "$destination_path"; then
		rm -f "$temporary_path"
		log "Could not refresh outer bootstrap at $destination_path"
		return 0
	fi
	BOOTSTRAP_REFRESHED=1
	export BOOTSTRAP_REFRESHED
}

record_install_state() {
	mkdir -p "$STATE_DIR"
	if [ -n "$RUN_NEW_BUNDLE_SHA256" ]; then
		atomic_write_line "$RUN_NEW_BUNDLE_SHA256" "$INSTALLED_BUNDLE_HASH_FILE"
	fi
	if [ -n "${MANIFEST_VERSION:-}" ]; then
		atomic_write_line "$MANIFEST_VERSION" "$INSTALLED_VERSION_FILE"
	elif [ -n "$RUN_NEW_VERSION" ]; then
		atomic_write_line "$(normalize_version_value "$RUN_NEW_VERSION")" "$INSTALLED_VERSION_FILE"
	fi
	if [ -n "$RUN_NEW_SOURCE_COMMIT" ]; then
		atomic_write_line "$RUN_NEW_SOURCE_COMMIT" "$INSTALLED_SOURCE_COMMIT_FILE"
	fi
}

atomic_write_line() {
	line_value="$1"
	destination="$2"
	temporary_path="${destination}.tmp.$$"
	printf '%s\n' "$line_value" >"$temporary_path"
	mv "$temporary_path" "$destination"
}

deployment_sentinel_paths() {
	printf '%s\n' \
		install.sh \
		venus_evcharger_service.py \
		venus_evcharger_dbus_adapter.py \
		venus_evcharger_auto_input_helper.py \
		venus_evcharger/dbus_adapter/process/adapter.py \
		venus_evcharger/core/contracts_bootstrap.py \
		deploy/venus/bootstrap_updater.sh \
		deploy/venus/service_lifecycle.sh \
		deploy/venus/install_venus_evcharger_service.sh
}

write_deployment_receipt() {
	active_root=$(current_codebase_dir)
	sentinel_list="${TMP_DIR}/deployment_sentinels.txt"
	deployment_sentinel_paths >"$sentinel_list"
	python3 - "$DEPLOYMENT_RECEIPT_FILE" "$active_root" "$sentinel_list" <<'PY'
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


receipt_path = Path(sys.argv[1])
active_root = Path(sys.argv[2]).resolve()
sentinel_paths = Path(sys.argv[3]).read_text(encoding="utf-8").splitlines()
critical_files: dict[str, str] = {}
missing_files: list[str] = []


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


for relative_path in sentinel_paths:
    candidate = active_root / relative_path
    if not candidate.is_file():
        missing_files.append(relative_path)
        continue
    critical_files[relative_path] = file_sha256(candidate)

payload = {
    "schema_version": 1,
    "installed_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    "target_dir": os.environ.get("TARGET_DIR", ""),
    "active_root": str(active_root),
    "source_repo": os.environ.get("REPO_SLUG", ""),
    "source_channel": os.environ.get("CHANNEL", ""),
    "source_commit": os.environ.get("RUN_NEW_SOURCE_COMMIT", ""),
    "bundle_sha256": os.environ.get("RUN_NEW_BUNDLE_SHA256", ""),
    "version": os.environ.get("RUN_NEW_VERSION", ""),
    "critical_files": critical_files,
    "missing_critical_files": missing_files,
}
receipt_path.parent.mkdir(parents=True, exist_ok=True)
temporary_path = receipt_path.with_name(f"{receipt_path.name}.tmp.{os.getpid()}")
with temporary_path.open("w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
os.replace(temporary_path, receipt_path)
try:
    directory_fd = os.open(receipt_path.parent, os.O_RDONLY | os.O_DIRECTORY)
except (AttributeError, OSError):
    pass
else:
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
PY
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

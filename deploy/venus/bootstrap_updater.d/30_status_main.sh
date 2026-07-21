# SPDX-License-Identifier: GPL-3.0-or-later

write_update_status() {
	mkdir -p "$STATE_DIR"
	CONFIG_MERGE_ADDED_KEYS="$CONFIG_MERGE_ADDED_KEYS" \
		CONFIG_MERGE_ADDED_SECTIONS="$CONFIG_MERGE_ADDED_SECTIONS" \
		CONFIG_MIGRATIONS_APPLIED="$CONFIG_MIGRATIONS_APPLIED" \
		python3 - "$STATUS_FILE" "$AUDIT_LOG_FILE" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone


def split_lines(name: str) -> list[str]:
    value = os.environ.get(name, "")
    return [item for item in value.splitlines() if item]


status_path, audit_path = sys.argv[1:3]
payload = {
    "timestamp_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    "mode": os.environ.get("RUN_MODE", ""),
    "result": os.environ.get("RUN_RESULT", ""),
    "failure_reason": os.environ.get("RUN_FAILURE_REASON", ""),
    "target_dir": os.environ.get("TARGET_DIR", ""),
    "old_version": os.environ.get("RUN_OLD_VERSION", ""),
    "new_version": os.environ.get("RUN_NEW_VERSION", ""),
    "old_bundle_sha256": os.environ.get("RUN_OLD_BUNDLE_SHA256", ""),
    "new_bundle_sha256": os.environ.get("RUN_NEW_BUNDLE_SHA256", ""),
    "old_source_commit": os.environ.get("RUN_OLD_SOURCE_COMMIT", ""),
    "new_source_commit": os.environ.get("RUN_NEW_SOURCE_COMMIT", ""),
    "source_repo": os.environ.get("REPO_SLUG", ""),
    "source_channel": os.environ.get("CHANNEL", ""),
    "deployment_receipt_path": os.environ.get("DEPLOYMENT_RECEIPT_FILE", ""),
    "work_storage": os.environ.get("WORK_STORAGE", ""),
    "work_root": os.environ.get("WORK_ROOT", ""),
    "bootstrap_entrypoint_path": os.environ.get("BOOTSTRAP_ENTRYPOINT", ""),
    "bootstrap_refreshed": os.environ.get("BOOTSTRAP_REFRESHED", "0") == "1",
    "current_preserved": os.environ.get("CURRENT_PRESERVED", "0") == "1",
    "already_current": os.environ.get("CURRENT_ALREADY_MATCHED", "0") == "1",
    "promoted_release": os.environ.get("PROMOTED_RELEASE", ""),
    "promotion_aborted_reason": os.environ.get("PROMOTION_ABORTED_REASON", ""),
    "rollback_reason": os.environ.get("ROLLBACK_REASON", ""),
    "config_merge_changed": os.environ.get("CONFIG_MERGE_CHANGED", "0") == "1",
    "config_merge_comment_preserved": os.environ.get("CONFIG_MERGE_COMMENT_PRESERVED", "1") == "1",
    "config_merge_skipped_reason": os.environ.get("CONFIG_MERGE_SKIPPED_REASON", ""),
    "config_merge_backup_path": os.environ.get("CONFIG_MERGE_BACKUP_PATH", ""),
    "config_merge_backup_required": os.environ.get("CONFIG_MERGE_BACKUP_REQUIRED", "0") == "1",
    "config_merge_added_keys": split_lines("CONFIG_MERGE_ADDED_KEYS"),
    "config_merge_added_sections": split_lines("CONFIG_MERGE_ADDED_SECTIONS"),
    "config_schema_before": os.environ.get("CONFIG_SCHEMA_BEFORE", ""),
    "config_schema_target": os.environ.get("CONFIG_SCHEMA_TARGET", ""),
    "config_migrations_applied": split_lines("CONFIG_MIGRATIONS_APPLIED"),
    "config_validation_passed": os.environ.get("VALIDATION_PASSED", "0") == "1",
    "config_validation_mode": os.environ.get("CONFIG_VALIDATION_MODE", ""),
}

temporary_status_path = f"{status_path}.tmp.{os.getpid()}"
with open(temporary_status_path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
os.replace(temporary_status_path, status_path)

with open(audit_path, "a", encoding="utf-8") as handle:
    handle.write(json.dumps(payload, sort_keys=True))
    handle.write("\n")
PY
}

print_preview_summary() {
	CONFIG_MERGE_ADDED_KEYS="$CONFIG_MERGE_ADDED_KEYS" \
		CONFIG_MERGE_ADDED_SECTIONS="$CONFIG_MERGE_ADDED_SECTIONS" \
		CONFIG_MIGRATIONS_APPLIED="$CONFIG_MIGRATIONS_APPLIED" \
		python3 - <<'PY'
import json
import os
from datetime import datetime, timezone


def split_lines(name: str) -> list[str]:
    value = os.environ.get(name, "")
    return [item for item in value.splitlines() if item]


payload = {
    "timestamp_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    "mode": os.environ.get("RUN_MODE", ""),
    "result": os.environ.get("RUN_RESULT", ""),
    "failure_reason": os.environ.get("RUN_FAILURE_REASON", ""),
    "target_dir": os.environ.get("TARGET_DIR", ""),
    "old_version": os.environ.get("RUN_OLD_VERSION", ""),
    "new_version": os.environ.get("RUN_NEW_VERSION", ""),
    "old_bundle_sha256": os.environ.get("RUN_OLD_BUNDLE_SHA256", ""),
    "new_bundle_sha256": os.environ.get("RUN_NEW_BUNDLE_SHA256", ""),
    "old_source_commit": os.environ.get("RUN_OLD_SOURCE_COMMIT", ""),
    "new_source_commit": os.environ.get("RUN_NEW_SOURCE_COMMIT", ""),
    "source_repo": os.environ.get("REPO_SLUG", ""),
    "source_channel": os.environ.get("CHANNEL", ""),
    "deployment_receipt_path": os.environ.get("DEPLOYMENT_RECEIPT_FILE", ""),
    "work_storage": os.environ.get("WORK_STORAGE", ""),
    "work_root": os.environ.get("WORK_ROOT", ""),
    "bootstrap_entrypoint_path": os.environ.get("BOOTSTRAP_ENTRYPOINT", ""),
    "bootstrap_refreshed": os.environ.get("BOOTSTRAP_REFRESHED", "0") == "1",
    "already_current": os.environ.get("CURRENT_ALREADY_MATCHED", "0") == "1",
    "config_merge_changed": os.environ.get("CONFIG_MERGE_CHANGED", "0") == "1",
    "config_merge_comment_preserved": os.environ.get("CONFIG_MERGE_COMMENT_PRESERVED", "1") == "1",
    "config_merge_skipped_reason": os.environ.get("CONFIG_MERGE_SKIPPED_REASON", ""),
    "config_merge_backup_required": os.environ.get("CONFIG_MERGE_BACKUP_REQUIRED", "0") == "1",
    "config_merge_added_keys": split_lines("CONFIG_MERGE_ADDED_KEYS"),
    "config_merge_added_sections": split_lines("CONFIG_MERGE_ADDED_SECTIONS"),
    "config_schema_before": os.environ.get("CONFIG_SCHEMA_BEFORE", ""),
    "config_schema_target": os.environ.get("CONFIG_SCHEMA_TARGET", ""),
    "config_migrations_applied": split_lines("CONFIG_MIGRATIONS_APPLIED"),
    "config_validation_passed": os.environ.get("VALIDATION_PASSED", "0") == "1",
    "config_validation_mode": os.environ.get("CONFIG_VALIDATION_MODE", ""),
}

print(json.dumps(payload, sort_keys=True))
PY
}

filesystem_available_kb() {
	df -Pk "$1" 2>/dev/null | awk 'NR == 2 { print $4 }'
}

filesystem_mountpoint() {
	df -Pk "$1" 2>/dev/null | awk 'NR == 2 { print $6 }'
}

ram_work_area_available() {
	[ -d "$RAM_WORK_BASE" ] && [ -w "$RAM_WORK_BASE" ] || return 1
	ram_mountpoint=$(filesystem_mountpoint "$RAM_WORK_BASE")
	[ -n "$ram_mountpoint" ] || return 1
	awk -v mountpoint="$ram_mountpoint" '$2 == mountpoint && $3 == "tmpfs" { found=1 } END { exit found ? 0 : 1 }' \
		"$MOUNTS_PATH" || return 1
	ram_mem_available=$(read_mem_available_kb)
	ram_fs_available=$(filesystem_available_kb "$RAM_WORK_BASE")
	[ -n "$ram_mem_available" ] && [ "$ram_mem_available" -ge "$RAM_MIN_MEM_AVAILABLE_KB" ] || return 1
	[ -n "$ram_fs_available" ] && [ "$ram_fs_available" -ge "$RAM_MIN_FILESYSTEM_AVAILABLE_KB" ]
}

detected_sd_mountpoint() {
	[ -r "$MOUNTS_PATH" ] || return 1
	awk '
		$1 ~ /^\/dev\/mmcblk[0-9]+p?[0-9]*$/ &&
		($2 ~ /^\/media\// || $2 ~ /^\/run\/media\//) &&
		("," $4 ",") ~ /,rw,/ { print $2; exit }
	' "$MOUNTS_PATH"
}

select_sd_work_root() {
	if [ -n "$SD_WORK_ROOT_OVERRIDE" ]; then
		sd_work_root="$SD_WORK_ROOT_OVERRIDE"
		sd_parent=$(dirname "$sd_work_root")
	else
		sd_mountpoint=$(detected_sd_mountpoint)
		[ -n "$sd_mountpoint" ] || return 1
		sd_parent="$sd_mountpoint"
		sd_work_root="${sd_mountpoint}/.venus-evcharger-updater-work"
	fi
	[ -d "$sd_parent" ] && [ -w "$sd_parent" ] || return 1
	sd_available=$(filesystem_available_kb "$sd_parent")
	[ -n "$sd_available" ] && [ "$sd_available" -ge "$RESOURCE_MIN_DISK_AVAILABLE_KB" ] || return 1
	printf '%s\n' "$sd_work_root"
}

select_update_work_root() {
	if [ -n "$WORK_ROOT_OVERRIDE" ]; then
		WORK_ROOT="$WORK_ROOT_OVERRIDE"
		WORK_STORAGE="override"
	elif ram_work_area_available; then
		WORK_ROOT="${RAM_WORK_BASE}/venus-evcharger-updater-work"
		WORK_STORAGE="ram"
	elif selected_sd_root=$(select_sd_work_root); then
		WORK_ROOT="$selected_sd_root"
		WORK_STORAGE="sd"
	else
		WORK_ROOT="$FALLBACK_WORK_ROOT"
		WORK_STORAGE="data"
	fi
	export WORK_ROOT WORK_STORAGE
	log "Using ${WORK_STORAGE} updater workspace: ${WORK_ROOT}"
}

finalize_run() {
	status=$?
	set +e
	if [ -n "${TMP_DIR:-}" ] && [ -d "$TMP_DIR" ]; then
		rm -rf "$TMP_DIR"
	fi
	if [ "${DRY_RUN:-0}" != "1" ] && [ -n "${TARGET_DIR:-}" ]; then
		write_update_status
	fi
	release_update_lock
	return "$status"
}

trap finalize_run EXIT

main() {
	ensure_updater_prereqs
	select_update_work_root
	mkdir -p "$WORK_ROOT"
	acquire_update_lock || exit $?
	TMP_DIR=$(mktemp -d "${WORK_ROOT}/update.XXXXXX")
	wait_for_update_resources "update-start" || exit $?

	RUN_OLD_VERSION=$(detect_current_version || true)
	RUN_OLD_BUNDLE_SHA256=$(detect_current_bundle_hash || true)
	RUN_OLD_SOURCE_COMMIT=$(detect_current_source_commit || true)

	if [ -n "$SOURCE_DIR_OVERRIDE" ]; then
		SOURCE_DIR="$SOURCE_DIR_OVERRIDE"
		RUN_NEW_SOURCE_COMMIT="$SOURCE_COMMIT_OVERRIDE"
		if [ -n "$RUN_NEW_SOURCE_COMMIT" ] && ! valid_source_commit "$RUN_NEW_SOURCE_COMMIT"; then
			set_failure_reason_once "invalid-source-commit"
			exit 1
		fi
		if ! require_source_layout "$SOURCE_DIR"; then
			log "Local source directory is incomplete: $SOURCE_DIR"
			set_failure_reason_once "incomplete-local-source"
			exit 1
		fi
	elif [ -n "$MANIFEST_SOURCE" ]; then
		if ! load_manifest "${TMP_DIR}/bootstrap_manifest.json"; then
			log "Configured update manifest could not be authenticated; refusing fallback update"
			set_failure_reason_once "manifest-authentication-failed"
			exit 1
		fi
		CHANNEL="${MANIFEST_CHANNEL:-$CHANNEL}"
		REPO_SLUG="${MANIFEST_SOURCE_REPO:-$REPO_SLUG}"
		RUN_NEW_BUNDLE_SHA256="${MANIFEST_BUNDLE_SHA256:-}"
		RUN_NEW_SOURCE_COMMIT="${MANIFEST_SOURCE_COMMIT:-}"
		if target_is_current_for_manifest; then
			CURRENT_ALREADY_MATCHED=1
			RUN_NEW_VERSION="${MANIFEST_VERSION:-$RUN_OLD_VERSION}"
			VALIDATION_PASSED=1
			CONFIG_VALIDATION_MODE="current-state"
			RUN_RESULT="success"
			if [ "$DRY_RUN" != "1" ]; then
				record_install_state
				write_deployment_receipt
				refresh_bootstrap_entrypoint
			fi
			log "Target already matches manifest${MANIFEST_VERSION:+ version $MANIFEST_VERSION}; skipping refresh"
			if [ "$DRY_RUN" = "1" ]; then
				RUN_RESULT="preview"
				print_preview_summary
			fi
			exit 0
		fi
		archive_path="${TMP_DIR}/bundle.tar.gz"
		extract_dir="${TMP_DIR}/extract"
		materialize_source_from_bundle "$archive_path" "$extract_dir"
	else
		archive_path="${TMP_DIR}/bundle.tar.gz"
		extract_dir="${TMP_DIR}/extract"
		mkdir -p "$extract_dir"
		if [ -z "$ARCHIVE_URL_OVERRIDE" ]; then
			resolve_github_source_commit "${TMP_DIR}/source_commit.json"
		elif [ -n "$SOURCE_COMMIT_OVERRIDE" ]; then
			RUN_NEW_SOURCE_COMMIT="$SOURCE_COMMIT_OVERRIDE"
			if ! valid_source_commit "$RUN_NEW_SOURCE_COMMIT"; then
				set_failure_reason_once "invalid-source-commit"
				exit 1
			fi
		fi
		log "Downloading code bundle for ${REPO_SLUG}:${CHANNEL}"
		if ! download_to "$ARCHIVE_URL" "$archive_path"; then
			set_failure_reason_once "bundle-download-failed"
			exit 1
		fi
		guarded_sha256_file "$archive_path" "${TMP_DIR}/bundle.sha256" || exit $?
		RUN_NEW_BUNDLE_SHA256="$GUARDED_SHA256"
		run_resource_guarded_command "bundle-extraction" tar -xzf "$archive_path" -C "$extract_dir" || exit $?
		SOURCE_DIR=$(find "$extract_dir" -mindepth 1 -maxdepth 1 -type d | head -n 1)
		if [ -z "$SOURCE_DIR" ] || ! require_source_layout "$SOURCE_DIR"; then
			log "Downloaded code bundle is incomplete"
			set_failure_reason_once "incomplete-downloaded-bundle"
			exit 1
		fi
	fi

	RUN_NEW_VERSION="${MANIFEST_VERSION:-$(read_tree_version "$SOURCE_DIR" || true)}"

	if [ "$DRY_RUN" = "1" ]; then
		preview_root="${TMP_DIR}/preview"
		if ! write_managed_layout "$SOURCE_DIR" "$preview_root"; then
			RUN_RESULT="failed"
			print_preview_summary
			exit 1
		fi
		RUN_RESULT="preview"
		print_preview_summary
		exit 0
	fi
	wait_for_update_resources "layout-staging" || exit $?

	if [ -n "${MANIFEST_VERSION:-}" ]; then
		if ! promote_release_layout "$SOURCE_DIR"; then
			exit 1
		fi
	else
		direct_staging_root="${TMP_DIR}/target-layout"
		if ! write_managed_layout "$SOURCE_DIR" "$direct_staging_root"; then
			exit 1
		fi
		promote_target_layout "$direct_staging_root"
		if [ -n "$CONFIG_MERGE_BACKUP_PATH" ]; then
			CONFIG_MERGE_BACKUP_PATH="${CONFIG_MERGE_BACKUP_PATH/$direct_staging_root/$TARGET_DIR}"
		fi
	fi

	record_install_state
	wait_for_update_resources "deployment-receipt" || exit $?
	write_deployment_receipt
	refresh_bootstrap_entrypoint
	RUN_RESULT="success"
	log "Codebase refreshed in $TARGET_DIR"
}

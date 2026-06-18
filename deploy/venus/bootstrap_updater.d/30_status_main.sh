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

with open(status_path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")

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

finalize_run() {
	status=$?
	set +e
	if [ -n "${TMP_DIR:-}" ] && [ -d "$TMP_DIR" ]; then
		rm -rf "$TMP_DIR"
	fi
	if [ "${DRY_RUN:-0}" != "1" ] && [ -n "${TARGET_DIR:-}" ]; then
		write_update_status
	fi
	return "$status"
}

trap finalize_run EXIT

main() {
	TMP_DIR=$(mktemp -d)
	ensure_updater_prereqs

	RUN_OLD_VERSION=$(detect_current_version || true)
	RUN_OLD_BUNDLE_SHA256=$(detect_current_bundle_hash || true)

	if [ -n "$SOURCE_DIR_OVERRIDE" ]; then
		SOURCE_DIR="$SOURCE_DIR_OVERRIDE"
		if ! require_source_layout "$SOURCE_DIR"; then
			log "Local source directory is incomplete: $SOURCE_DIR"
			set_failure_reason_once "incomplete-local-source"
			exit 1
		fi
	elif load_manifest "${TMP_DIR}/bootstrap_manifest.json"; then
		RUN_NEW_BUNDLE_SHA256="${MANIFEST_BUNDLE_SHA256:-}"
		if target_is_current_for_manifest; then
			CURRENT_ALREADY_MATCHED=1
			RUN_NEW_VERSION="${MANIFEST_VERSION:-$RUN_OLD_VERSION}"
			VALIDATION_PASSED=1
			CONFIG_VALIDATION_MODE="current-state"
			RUN_RESULT="success"
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
		log "Downloading code bundle for ${REPO_SLUG}:${CHANNEL}"
		download_to "$ARCHIVE_URL" "$archive_path"
		tar -xzf "$archive_path" -C "$extract_dir"
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
	RUN_RESULT="success"
	log "Codebase refreshed in $TARGET_DIR"
}

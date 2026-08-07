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
	require_command df
	if [ -z "$SOURCE_DIR_OVERRIDE" ]; then
		ensure_download_tool
	fi
	if [ "$REQUIRE_SIGNED_MANIFEST" = "1" ]; then
		require_command openssl
	fi
}

numeric_greater_than() {
	awk -v value="$1" -v limit="$2" 'BEGIN { exit !(value > limit) }'
}

resource_snapshot() {
	RESOURCE_LOAD1=""
	RESOURCE_LOAD5=""
	RESOURCE_LOAD15=""
	RESOURCE_MEM_AVAILABLE_KB=""
	RESOURCE_DISK_AVAILABLE_KB=""

	if [ -r "$RESOURCE_LOADAVG_PATH" ]; then
		read -r RESOURCE_LOAD1 RESOURCE_LOAD5 RESOURCE_LOAD15 _ <"$RESOURCE_LOADAVG_PATH" || true
	fi
	RESOURCE_MEM_AVAILABLE_KB=$(read_mem_available_kb)
	RESOURCE_DISK_AVAILABLE_KB=$(df -Pk "$WORK_ROOT" 2>/dev/null | awk 'NR == 2 { print $4 }')
}

read_mem_available_kb() {
	if [ -r "$RESOURCE_MEMINFO_PATH" ]; then
		awk '
			$1 == "MemAvailable:" { print $2; found=1; exit }
			$1 == "MemFree:" { free=$2 }
			$1 == "Buffers:" { buffers=$2 }
			$1 == "Cached:" { cached=$2 }
			END { if (!found && free != "") print free + buffers + cached }
		' "$RESOURCE_MEMINFO_PATH"
	fi
}

resource_min_mem_available_kb() {
	case "$WORK_STORAGE" in
	sd | data)
		printf '%s\n' "$RESOURCE_MIN_MEM_AVAILABLE_PERSISTENT_KB"
		;;
	*)
		printf '%s\n' "$RESOURCE_MIN_MEM_AVAILABLE_KB"
		;;
	esac
}

resource_pressure_reason() {
	[ "$RESOURCE_GUARD_ENABLED" = "1" ] || return 1
	resource_snapshot
	resource_min_mem=$(resource_min_mem_available_kb)
	if [ -n "$RESOURCE_LOAD1" ] && numeric_greater_than "$RESOURCE_LOAD1" "$RESOURCE_MAX_LOAD1"; then
		printf 'load1=%s>%s' "$RESOURCE_LOAD1" "$RESOURCE_MAX_LOAD1"
		return 0
	fi
	if [ -n "$RESOURCE_LOAD5" ] && numeric_greater_than "$RESOURCE_LOAD5" "$RESOURCE_MAX_LOAD5"; then
		printf 'load5=%s>%s' "$RESOURCE_LOAD5" "$RESOURCE_MAX_LOAD5"
		return 0
	fi
	if [ -n "$RESOURCE_LOAD15" ] && numeric_greater_than "$RESOURCE_LOAD15" "$RESOURCE_MAX_LOAD15"; then
		printf 'load15=%s>%s' "$RESOURCE_LOAD15" "$RESOURCE_MAX_LOAD15"
		return 0
	fi
	if [ -n "$RESOURCE_MEM_AVAILABLE_KB" ] && [ "$RESOURCE_MEM_AVAILABLE_KB" -lt "$resource_min_mem" ]; then
		printf 'mem_available_kb=%s<%s' "$RESOURCE_MEM_AVAILABLE_KB" "$resource_min_mem"
		return 0
	fi
	if [ -n "$RESOURCE_DISK_AVAILABLE_KB" ] && [ "$RESOURCE_DISK_AVAILABLE_KB" -lt "$RESOURCE_MIN_DISK_AVAILABLE_KB" ]; then
		printf 'disk_available_kb=%s<%s' "$RESOURCE_DISK_AVAILABLE_KB" "$RESOURCE_MIN_DISK_AVAILABLE_KB"
		return 0
	fi
	return 1
}

wait_for_update_resources() {
	phase="$1"
	waited=0
	while pressure_reason=$(resource_pressure_reason); do
		if [ "$waited" -eq 0 ]; then
			log "Resource pressure before ${phase}; waiting (${pressure_reason})"
		fi
		if [ "$waited" -ge "$RESOURCE_WAIT_SECONDS" ]; then
			log "Resource pressure persisted during ${phase}; update aborted (${pressure_reason})"
			set_failure_reason_once "resource-pressure:${phase}:${pressure_reason}"
			return 75
		fi
		sleep "$RESOURCE_POLL_SECONDS"
		waited=$((waited + RESOURCE_POLL_SECONDS))
	done
	if [ "$waited" -gt 0 ]; then
		log "Resources recovered after ${waited}s; continuing ${phase}"
	fi
	return 0
}

run_resource_guarded_command() {
	phase="$1"
	shift
	wait_for_update_resources "$phase" || return $?
	"$@" &
	guarded_pid=$!
	while kill -0 "$guarded_pid" 2>/dev/null; do
		sleep "$RESOURCE_POLL_SECONDS"
		if pressure_reason=$(resource_pressure_reason); then
			log "Resource pressure during ${phase}; stopping operation (${pressure_reason})"
			kill "$guarded_pid" 2>/dev/null || true
			wait "$guarded_pid" 2>/dev/null || true
			set_failure_reason_once "resource-pressure:${phase}:${pressure_reason}"
			return 75
		fi
	done
	guarded_status=0
	wait "$guarded_pid" || guarded_status=$?
	return "$guarded_status"
}

guarded_sha256_file() {
	input_path="$1"
	output_path="$2"
	run_resource_guarded_command "bundle-hash" sha256sum "$input_path" >"$output_path" || return $?
	GUARDED_SHA256=$(awk '{print $1; exit}' "$output_path")
	[ -n "$GUARDED_SHA256" ]
}

acquire_update_lock() {
	mkdir -p "$STATE_DIR"
	if mkdir "$UPDATE_LOCK_DIR" 2>/dev/null; then
		printf '%s\n' "$$" >"${UPDATE_LOCK_DIR}/pid"
		UPDATE_LOCK_HELD=1
		return 0
	fi
	lock_pid=$(awk 'NF { print $1; exit }' "${UPDATE_LOCK_DIR}/pid" 2>/dev/null || true)
	if [ -n "$lock_pid" ] && kill -0 "$lock_pid" 2>/dev/null; then
		log "Another updater is already running with pid ${lock_pid}"
		set_failure_reason_once "update-already-running"
		return 73
	fi
	log "Removing stale updater lock"
	rm -rf "$UPDATE_LOCK_DIR"
	if ! mkdir "$UPDATE_LOCK_DIR"; then
		set_failure_reason_once "update-lock-unavailable"
		return 73
	fi
	printf '%s\n' "$$" >"${UPDATE_LOCK_DIR}/pid"
	UPDATE_LOCK_HELD=1
}

release_update_lock() {
	if [ "${UPDATE_LOCK_HELD:-0}" = "1" ]; then
		rm -rf "$UPDATE_LOCK_DIR"
		UPDATE_LOCK_HELD=0
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
		if wget -q -T "$DOWNLOAD_TIMEOUT_SECONDS" -t "$DOWNLOAD_ATTEMPTS" -O "$dst" "$src"; then
			return 0
		fi
		log "Download failed or timed out: $src"
		return 1
	fi
	if command -v curl >/dev/null 2>&1; then
		if curl -fsSL --connect-timeout "$DOWNLOAD_TIMEOUT_SECONDS" --max-time "$DOWNLOAD_TIMEOUT_SECONDS" \
			--retry "$DOWNLOAD_ATTEMPTS" -o "$dst" "$src"; then
			return 0
		fi
		log "Download failed or timed out: $src"
		return 1
	fi
	log "Neither wget nor curl is available to fetch $src"
	set_failure_reason_once "download-unavailable"
	return 1
}

require_source_layout() {
	src_dir="$1"
	[ -f "${src_dir}/install.sh" ] || return 1
	[ -f "${src_dir}/venus_evcharger_service.py" ] || return 1
	[ -f "${src_dir}/venus_evcharger_auto_input_helper.py" ] || return 1
	[ -f "${src_dir}/deploy/venus/install_venus_evcharger_service.sh" ] || return 1
	[ -f "${src_dir}/deploy/venus/service_lifecycle.sh" ] || return 1
	[ -f "${src_dir}/deploy/venus/service_venus_evcharger/run" ] || return 1
	[ -f "${src_dir}/deploy/venus/service_venus_evcharger_dbus_adapter/run" ] || return 1
	[ -f "${src_dir}/deploy/venus/service_venus_evcharger_observer/run" ] || return 1
	[ -f "${src_dir}/deploy/venus/service_venus_evcharger_observer/log/run" ] || return 1
	[ -d "${src_dir}/venus_evcharger" ] || return 1
	return 0
}

write_builtin_pubkey() {
	destination="$1"
	cat >"$destination" <<'EOF'
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

detect_current_source_commit() {
	read_text_file "$INSTALLED_SOURCE_COMMIT_FILE" || true
}

valid_source_commit() {
	printf '%s\n' "$1" | awk 'length($0) == 40 && $0 ~ /^[0-9a-f]+$/ { valid=1 } END { exit valid ? 0 : 1 }'
}

resolve_github_source_commit() {
	metadata_path="$1"
	if [ -n "$SOURCE_COMMIT_OVERRIDE" ]; then
		RUN_NEW_SOURCE_COMMIT="$SOURCE_COMMIT_OVERRIDE"
	elif download_to "$SOURCE_REF_URL" "$metadata_path"; then
		RUN_NEW_SOURCE_COMMIT=$(json_field "$metadata_path" "sha" || true)
	else
		set_failure_reason_once "source-commit-resolution-failed"
		return 1
	fi
	if ! valid_source_commit "$RUN_NEW_SOURCE_COMMIT"; then
		log "Resolved source commit is invalid"
		set_failure_reason_once "invalid-source-commit"
		return 1
	fi
	ARCHIVE_URL="https://codeload.github.com/${REPO_SLUG}/tar.gz/${RUN_NEW_SOURCE_COMMIT}"
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
	MANIFEST_SOURCE_COMMIT=$(manifest_field "$manifest_path" "source_commit" || true)
	MANIFEST_SOURCE_REPO=$(manifest_field "$manifest_path" "source_repo" || true)

	[ -n "$MANIFEST_BUNDLE_URL" ] || return 1
	[ -n "$MANIFEST_BUNDLE_SHA256" ] || return 1
	if [ -n "$MANIFEST_SOURCE_COMMIT" ] && ! valid_source_commit "$MANIFEST_SOURCE_COMMIT"; then
		set_failure_reason_once "invalid-manifest-source-commit"
		return 1
	fi
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

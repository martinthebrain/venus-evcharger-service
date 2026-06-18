#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later

# Minimal GX bootstrap installer.
#
# Users can copy this one file anywhere under /data and execute it. The script
# optionally refreshes a local updater, lets that updater materialize or refresh
# the real codebase, and finally calls the existing Venus installer from the
# synchronized repository tree.

set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" >/dev/null 2>&1 && pwd)
BOOTSTRAP_STATE_DIR="${SCRIPT_DIR}/.venus-evcharger-bootstrap"
NO_UPDATE_FILE="${SCRIPT_DIR}/noUpdate"
DEFAULT_TARGET_DIR="${SCRIPT_DIR}/dbus-venus-evcharger"
DEFAULT_REPO_SLUG="martinthebrain/venus-evcharger-service"
DEFAULT_CHANNEL="main"

TARGET_DIR="${VENUS_EVCHARGER_TARGET_DIR:-}"
if [ -z "$TARGET_DIR" ]; then
	if [ -f "${SCRIPT_DIR}/deploy/venus/install_venus_evcharger_service.sh" ]; then
		TARGET_DIR="$SCRIPT_DIR"
	else
		TARGET_DIR="$DEFAULT_TARGET_DIR"
	fi
fi

REPO_SLUG="${VENUS_EVCHARGER_REPO_SLUG:-$DEFAULT_REPO_SLUG}"
CHANNEL="${VENUS_EVCHARGER_CHANNEL:-$DEFAULT_CHANNEL}"
UPDATER_PATH="${BOOTSTRAP_STATE_DIR}/bootstrap_updater.sh"
MANIFEST_SOURCE="${VENUS_EVCHARGER_MANIFEST_SOURCE:-}"
if [ -n "${VENUS_EVCHARGER_MANIFEST_SIG_SOURCE:-}" ]; then
	MANIFEST_SIG_SOURCE="$VENUS_EVCHARGER_MANIFEST_SIG_SOURCE"
elif [ -n "$MANIFEST_SOURCE" ]; then
	MANIFEST_SIG_SOURCE="${MANIFEST_SOURCE}.sig"
else
	MANIFEST_SIG_SOURCE=""
fi
UPDATER_SOURCE="${VENUS_EVCHARGER_UPDATER_SOURCE:-https://raw.githubusercontent.com/${REPO_SLUG}/${CHANNEL}/deploy/venus/bootstrap_updater.sh}"
UPDATER_HASH_SOURCE="${VENUS_EVCHARGER_UPDATER_HASH_SOURCE:-${UPDATER_SOURCE}.sha256}"
UPDATER_LIB_DIR="${BOOTSTRAP_STATE_DIR}/bootstrap_updater.d"
BOOTSTRAP_PUBKEY_OVERRIDE="${VENUS_EVCHARGER_BOOTSTRAP_PUBKEY:-}"
REQUIRE_SIGNED_MANIFEST="${VENUS_EVCHARGER_REQUIRE_SIGNED_MANIFEST:-0}"
INSTALLER_OVERRIDE="${VENUS_EVCHARGER_INSTALLER_PATH:-}"
MANIFEST_UPDATER_URL=""
MANIFEST_UPDATER_SHA256=""
MANIFEST_VERSION=""

log() {
	printf '%s\n' "[bootstrap] $*"
}

require_command() {
	command_name="$1"
	if ! command -v "$command_name" >/dev/null 2>&1; then
		log "Required command is missing: $command_name"
		exit 1
	fi
}

ensure_download_tool() {
	if command -v wget >/dev/null 2>&1 || command -v curl >/dev/null 2>&1; then
		return 0
	fi
	log "Neither wget nor curl is available"
	exit 1
}

ensure_bootstrap_prereqs() {
	require_command cp
	require_command rm
	require_command chmod
	require_command ln
	require_command mkdir
	require_command awk
	require_command mktemp
	require_command sha256sum
	require_command python3
	ensure_download_tool
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
	return 1
}

hash_file() {
	sha256sum "$1" | awk '{print $1}'
}

expected_hash_from_file() {
	awk 'NF {print $1; exit}' "$1"
}

updater_lib_names() {
	printf '%s\n' 00_core.sh 10_config_merge.sh 20_layout.sh 30_status_main.sh
}

updater_source_base() {
	source_path="$1"
	case "$source_path" in
	*/bootstrap_updater.sh)
		printf '%s\n' "${source_path%/bootstrap_updater.sh}"
		;;
	*)
		printf '%s\n' "$(dirname "$source_path")"
		;;
	esac
}

install_updater_libs() {
	selected_source="$1"
	tmp_dir="$2"
	source_base=$(updater_source_base "$selected_source")
	tmp_lib_dir="${tmp_dir}/bootstrap_updater.d"
	rm -rf "$tmp_lib_dir"
	mkdir -p "$tmp_lib_dir"

	while IFS= read -r lib_name; do
		download_to "${source_base}/bootstrap_updater.d/${lib_name}" "${tmp_lib_dir}/${lib_name}" || return 1
	done <<EOF
$(updater_lib_names)
EOF

	rm -rf "$UPDATER_LIB_DIR"
	mkdir -p "$UPDATER_LIB_DIR"
	cp "${tmp_lib_dir}/"* "$UPDATER_LIB_DIR/"
	chmod 755 "$UPDATER_LIB_DIR"/*.sh
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
	pubkey_path="${BOOTSTRAP_STATE_DIR}/bootstrap_manifest.pub"
	mkdir -p "$BOOTSTRAP_STATE_DIR"
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

manifest_field() {
	manifest_path="$1"
	field_name="$2"
	python3 - "$manifest_path" "$field_name" <<'PY'
import json
import sys

path, field = sys.argv[1], sys.argv[2]
try:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
except Exception:
    sys.exit(1)
value = data.get(field, "")
if isinstance(value, (str, int, float)):
    print(value)
PY
}

load_manifest() {
	manifest_path="$1"
	[ -n "$MANIFEST_SOURCE" ] || return 1
	signature_path="${manifest_path}.sig"
	pubkey_path=$(resolve_pubkey_path)
	if ! download_to "$MANIFEST_SOURCE" "$manifest_path"; then
		return 1
	fi
	if download_to "$MANIFEST_SIG_SOURCE" "$signature_path"; then
		if ! verify_signature "$manifest_path" "$signature_path" "$pubkey_path"; then
			log "Manifest signature verification failed"
			return 1
		fi
	elif [ "$REQUIRE_SIGNED_MANIFEST" = "1" ]; then
		log "Signed manifest required but signature could not be fetched"
		return 1
	else
		return 1
	fi

	MANIFEST_UPDATER_URL=$(manifest_field "$manifest_path" "updater_url" || true)
	MANIFEST_UPDATER_SHA256=$(manifest_field "$manifest_path" "updater_sha256" || true)
	MANIFEST_VERSION=$(manifest_field "$manifest_path" "version" || true)

	if [ -z "$MANIFEST_UPDATER_URL" ] || [ -z "$MANIFEST_UPDATER_SHA256" ]; then
		return 1
	fi
	return 0
}

ensure_updater() {
	mkdir -p "$BOOTSTRAP_STATE_DIR"

	tmp_dir=$(mktemp -d)
	cleanup_tmp() {
		rm -rf "$tmp_dir"
	}
	trap cleanup_tmp RETURN

	candidate_path="${tmp_dir}/bootstrap_updater.sh"
	candidate_hash_path="${tmp_dir}/bootstrap_updater.sh.sha256"
	manifest_path="${tmp_dir}/bootstrap_manifest.json"
	selected_source="$UPDATER_SOURCE"
	expected_hash=""

	if load_manifest "$manifest_path"; then
		selected_source="$MANIFEST_UPDATER_URL"
		expected_hash="$MANIFEST_UPDATER_SHA256"
		log "Using updater from manifest${MANIFEST_VERSION:+ (version $MANIFEST_VERSION)}"
		if ! download_to "$selected_source" "$candidate_path"; then
			log "Manifest updater download failed; falling back to hash-source flow if available"
			expected_hash=""
		fi
	fi

	if [ -n "$expected_hash" ]; then
		candidate_hash=$(hash_file "$candidate_path")
		if [ -z "$expected_hash" ] || [ "$candidate_hash" != "$expected_hash" ]; then
			log "Manifest updater hash validation failed; keeping local updater if available"
		else
			local_hash=""
			if [ -f "$UPDATER_PATH" ]; then
				local_hash=$(hash_file "$UPDATER_PATH")
			fi
			if [ ! -f "$UPDATER_PATH" ] || [ "$local_hash" != "$expected_hash" ]; then
				cp "$candidate_path" "$UPDATER_PATH"
				chmod 755 "$UPDATER_PATH"
				log "Updated local bootstrap updater"
			fi
			install_updater_libs "$selected_source" "$tmp_dir" || log "Could not refresh updater helper files"
		fi
	elif download_to "$UPDATER_SOURCE" "$candidate_path" && download_to "$UPDATER_HASH_SOURCE" "$candidate_hash_path"; then
		expected_hash=$(expected_hash_from_file "$candidate_hash_path")
		candidate_hash=$(hash_file "$candidate_path")
		if [ -z "$expected_hash" ] || [ "$candidate_hash" != "$expected_hash" ]; then
			log "Remote updater hash validation failed; keeping local updater if available"
		else
			local_hash=""
			if [ -f "$UPDATER_PATH" ]; then
				local_hash=$(hash_file "$UPDATER_PATH")
			fi
			if [ ! -f "$UPDATER_PATH" ] || [ "$local_hash" != "$expected_hash" ]; then
				cp "$candidate_path" "$UPDATER_PATH"
				chmod 755 "$UPDATER_PATH"
				log "Updated local bootstrap updater"
			fi
			install_updater_libs "$UPDATER_SOURCE" "$tmp_dir" || log "Could not refresh updater helper files"
		fi
	else
		log "Could not refresh updater from source; falling back to local updater if present"
	fi

	if [ ! -x "$UPDATER_PATH" ]; then
		log "No valid local updater available at $UPDATER_PATH"
		exit 1
	fi
}

resolve_installer_path() {
	if [ -n "$INSTALLER_OVERRIDE" ]; then
		printf '%s\n' "$INSTALLER_OVERRIDE"
		return 0
	fi
	if [ -x "${TARGET_DIR}/current/deploy/venus/install_venus_evcharger_service.sh" ]; then
		printf '%s\n' "${TARGET_DIR}/current/deploy/venus/install_venus_evcharger_service.sh"
		return 0
	fi
	printf '%s\n' "${TARGET_DIR}/deploy/venus/install_venus_evcharger_service.sh"
}

resolve_previous_installer_path() {
	previous_link="${TARGET_DIR}/previous"
	if [ -L "$previous_link" ] && command -v readlink >/dev/null 2>&1; then
		previous_target=$(readlink "$previous_link" 2>/dev/null || true)
		if [ -n "$previous_target" ] && [ -x "${previous_target}/deploy/venus/install_venus_evcharger_service.sh" ]; then
			printf '%s\n' "${previous_target}/deploy/venus/install_venus_evcharger_service.sh"
			return 0
		fi
	fi
	return 1
}

activate_previous_release() {
	previous_link="${TARGET_DIR}/previous"
	current_link="${TARGET_DIR}/current"
	if [ ! -L "$previous_link" ] || ! command -v readlink >/dev/null 2>&1; then
		return 1
	fi
	previous_target=$(readlink "$previous_link" 2>/dev/null || true)
	if [ -z "$previous_target" ]; then
		return 1
	fi
	ln -sfn "$previous_target" "$current_link"
	return 0
}

run_installer_path() {
	installer_path="$1"
	if [ ! -x "$installer_path" ]; then
		log "Installer not found or not executable: $installer_path"
		exit 1
	fi
	log "Starting installer: $installer_path"
	"$installer_path"
}

run_existing_installer() {
	installer_path=$(resolve_installer_path)
	if run_installer_path "$installer_path"; then
		return 0
	fi

	previous_installer_path=$(resolve_previous_installer_path || true)
	if [ -n "$previous_installer_path" ] && activate_previous_release; then
		log "Installer failed; rolling back to previous release"
		run_installer_path "$previous_installer_path"
		return 0
	fi
	return 1
}

ensure_bootstrap_prereqs

if [ -f "$NO_UPDATE_FILE" ]; then
	log "noUpdate marker found; skipping updater phase"
	run_existing_installer
	exit $?
fi

ensure_updater
log "Running updater for target: $TARGET_DIR"
export VENUS_EVCHARGER_MANIFEST_SOURCE="$MANIFEST_SOURCE"
export VENUS_EVCHARGER_MANIFEST_SIG_SOURCE="$MANIFEST_SIG_SOURCE"
VENUS_EVCHARGER_BOOTSTRAP_PUBKEY="$(resolve_pubkey_path)"
export VENUS_EVCHARGER_BOOTSTRAP_PUBKEY
export VENUS_EVCHARGER_REQUIRE_SIGNED_MANIFEST="$REQUIRE_SIGNED_MANIFEST"
"$UPDATER_PATH" "$TARGET_DIR"
run_existing_installer

# SPDX-License-Identifier: GPL-3.0-or-later

preserve_local_config() {
	preserve_dir="$1"
	active_dir=$(current_codebase_dir)
	local_deploy_dir=""
	if [ -d "${active_dir}/deploy/venus" ]; then
		local_deploy_dir="${active_dir}/deploy/venus"
	elif [ -d "${TARGET_DIR}/deploy/venus" ]; then
		local_deploy_dir="${TARGET_DIR}/deploy/venus"
	fi

	if [ -f "${active_dir}/deploy/venus/config.venus_evcharger.ini" ]; then
		mkdir -p "${preserve_dir}/deploy/venus"
		cp "${active_dir}/deploy/venus/config.venus_evcharger.ini" "${preserve_dir}/deploy/venus/config.venus_evcharger.ini"
	elif [ -f "${TARGET_DIR}/deploy/venus/config.venus_evcharger.ini" ]; then
		mkdir -p "${preserve_dir}/deploy/venus"
		cp "${TARGET_DIR}/deploy/venus/config.venus_evcharger.ini" "${preserve_dir}/deploy/venus/config.venus_evcharger.ini"
	fi

	if [ -n "$local_deploy_dir" ]; then
		mkdir -p "${preserve_dir}/deploy/venus"
		find "$local_deploy_dir" -maxdepth 1 -type f \
			\( -name 'wizard-*.ini' -o -name 'config.venus_evcharger.ini.wizard-*' \) \
			-exec cp {} "${preserve_dir}/deploy/venus/" \;
	fi

	if [ -f "${TARGET_DIR}/noUpdate" ]; then
		cp "${TARGET_DIR}/noUpdate" "${preserve_dir}/noUpdate"
	fi
	if [ -f "${TARGET_DIR}/update-channel" ]; then
		cp "${TARGET_DIR}/update-channel" "${preserve_dir}/update-channel"
	fi
}

restore_local_config() {
	preserve_dir="$1"
	destination_root="$2"
	if [ -f "${preserve_dir}/deploy/venus/config.venus_evcharger.ini" ]; then
		mkdir -p "${destination_root}/deploy/venus"
		cp "${preserve_dir}/deploy/venus/config.venus_evcharger.ini" "${destination_root}/deploy/venus/config.venus_evcharger.ini"
	fi
	if [ -d "${preserve_dir}/deploy/venus" ]; then
		mkdir -p "${destination_root}/deploy/venus"
		find "${preserve_dir}/deploy/venus" -maxdepth 1 -type f \
			\( -name 'wizard-*.ini' -o -name 'config.venus_evcharger.ini.wizard-*' \) \
			-exec cp {} "${destination_root}/deploy/venus/" \;
	fi
	if [ "$DRY_RUN" != "1" ]; then
		if [ -f "${preserve_dir}/noUpdate" ]; then
			cp "${preserve_dir}/noUpdate" "${TARGET_DIR}/noUpdate"
		fi
		if [ -f "${preserve_dir}/update-channel" ]; then
			cp "${preserve_dir}/update-channel" "${TARGET_DIR}/update-channel"
		fi
	fi
}

apply_merge_result() {
	result_path="$1"
	CONFIG_MERGE_CHANGED=$(json_field "$result_path" "changed" || printf '0')
	CONFIG_MERGE_COMMENT_PRESERVED=$(json_field "$result_path" "comment_preserved" || printf '1')
	CONFIG_MERGE_SKIPPED_REASON=$(json_field "$result_path" "skipped_reason" || true)
	CONFIG_MERGE_BACKUP_PATH=$(json_field "$result_path" "backup_path" || true)
	CONFIG_MERGE_BACKUP_REQUIRED=$(json_field "$result_path" "backup_required" || printf '0')
	CONFIG_SCHEMA_BEFORE=$(json_field "$result_path" "schema_before" || true)
	CONFIG_SCHEMA_TARGET=$(json_field "$result_path" "schema_target" || true)
	CONFIG_MERGE_ADDED_KEYS=$(normalize_multiline_var "$(json_lines_field "$result_path" "added_keys" || true)")
	CONFIG_MERGE_ADDED_SECTIONS=$(normalize_multiline_var "$(json_lines_field "$result_path" "added_sections" || true)")
	CONFIG_MIGRATIONS_APPLIED=$(normalize_multiline_var "$(json_lines_field "$result_path" "migrations_applied" || true)")
}

merge_local_config_template() {
	source_root="$1"
	destination_root="$2"
	template_path="${source_root}/deploy/venus/config.venus_evcharger.ini"
	local_path="${destination_root}/deploy/venus/config.venus_evcharger.ini"
	[ -f "$template_path" ] || return 0
	[ -f "$local_path" ] || return 0

	merge_result_path="${TMP_DIR}/config-merge-result.json"
	if python3 - "$local_path" "$template_path" "$merge_result_path" "$RUN_MODE" <<'PY'; then
import configparser
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime


SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*(?:[#;].*)?$")


class CaseConfigParser(configparser.ConfigParser):
    optionxform = str


def read_config(path: str) -> CaseConfigParser | None:
    parser = CaseConfigParser(interpolation=None)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            parser.read_file(handle)
    except (OSError, configparser.Error):
        return None
    return parser


def schema_version(config: CaseConfigParser) -> int:
    raw_value = config.defaults().get("ConfigSchemaVersion", "").strip()
    if not raw_value:
        return 0
    try:
        return int(raw_value)
    except ValueError:
        return 0


def apply_schema_migrations(config: CaseConfigParser, source_version: int, target_version: int) -> list[str]:
    migrations: list[str] = []
    current_version = source_version
    while current_version < target_version:
        next_version = current_version + 1
        # Explicit migration hook for future renamed or semantic config changes.
        current_version = next_version
    return migrations


def section_ranges(lines: list[str]) -> tuple[int | None, dict[str, tuple[int, int]]]:
    section_positions: list[tuple[str, int]] = []
    first_named_section: int | None = None
    for index, line in enumerate(lines):
        match = SECTION_RE.match(line)
        if not match:
            continue
        section_name = match.group(1).strip()
        if section_name == "DEFAULT":
            continue
        if first_named_section is None:
            first_named_section = index
        section_positions.append((section_name, index))

    ranges: dict[str, tuple[int, int]] = {}
    for index, (section_name, start) in enumerate(section_positions):
        if index + 1 < len(section_positions):
            end = section_positions[index + 1][1]
        else:
            end = len(lines)
        ranges[section_name] = (start, end)
    return first_named_section, ranges


def write_atomically(path: str, content: str) -> None:
    directory = os.path.dirname(path) or "."
    fd, temp_path = tempfile.mkstemp(prefix=".config-merge-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def build_backup_path(path: str) -> str:
    directory = os.path.dirname(path) or "."
    base_name = os.path.basename(path)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = os.path.join(directory, f"{base_name}.bak-{timestamp}")
    suffix = 1
    while os.path.exists(candidate):
        candidate = os.path.join(directory, f"{base_name}.bak-{timestamp}-{suffix}")
        suffix += 1
    return candidate


def merge_with_layout(
    local_path: str,
    template_path: str,
    mode: str,
) -> dict[str, object]:
    result: dict[str, object] = {
        "changed": False,
        "comment_preserved": True,
        "skipped_reason": "",
        "backup_path": "",
        "backup_required": False,
        "added_keys": [],
        "added_sections": [],
        "migrations_applied": [],
        "schema_before": "",
        "schema_target": "",
    }

    local_config = read_config(local_path)
    template_config = read_config(template_path)
    if template_config is None:
        result["skipped_reason"] = "malformed-template-config"
        raise SystemExit(json.dumps(result))
    if local_config is None:
        result["skipped_reason"] = "malformed-local-config"
        return result

    local_schema = schema_version(local_config)
    template_schema = schema_version(template_config)
    migrations = apply_schema_migrations(local_config, local_schema, template_schema)
    result["migrations_applied"] = migrations
    result["schema_before"] = str(local_schema)
    result["schema_target"] = str(template_schema)

    missing_defaults: list[tuple[str, str]] = []
    for key, value in template_config.defaults().items():
        if key not in local_config.defaults():
            missing_defaults.append((key, value))
            result["added_keys"].append(f"DEFAULT.{key}")

    missing_section_keys: dict[str, list[tuple[str, str]]] = {}
    for section_name, section_values in template_config._sections.items():
        if not local_config.has_section(section_name):
            result["added_sections"].append(section_name)
            result["added_keys"].extend(f"{section_name}.{key}" for key in section_values.keys())
            continue
        section_missing: list[tuple[str, str]] = []
        local_section = local_config[section_name]
        for key, value in section_values.items():
            if key not in local_section:
                section_missing.append((key, value))
                result["added_keys"].append(f"{section_name}.{key}")
        if section_missing:
            missing_section_keys[section_name] = section_missing

    if not result["added_keys"] and not result["added_sections"] and not migrations:
        return result

    with open(local_path, "r", encoding="utf-8") as handle:
        local_text = handle.read()
    with open(template_path, "r", encoding="utf-8") as handle:
        template_text = handle.read()

    local_lines = local_text.splitlines()
    template_lines = template_text.splitlines()
    has_trailing_newline = local_text.endswith("\n")
    first_named_section, local_ranges = section_ranges(local_lines)
    _, template_ranges = section_ranges(template_lines)

    insertions: list[tuple[int, list[str]]] = []

    if missing_defaults:
        insert_at = first_named_section if first_named_section is not None else len(local_lines)
        snippet: list[str] = []
        if insert_at > 0 and local_lines[insert_at - 1].strip():
            snippet.append("")
        snippet.append("# Added from refreshed template")
        for key, value in missing_defaults:
            snippet.append(f"{key}={value}")
        if insert_at < len(local_lines) and local_lines[insert_at].strip():
            snippet.append("")
        insertions.append((insert_at, snippet))

    for section_name, missing_pairs in missing_section_keys.items():
        _, end = local_ranges[section_name]
        snippet = []
        if end > 0 and local_lines[end - 1].strip():
            snippet.append("")
        snippet.append("# Added from refreshed template")
        for key, value in missing_pairs:
            snippet.append(f"{key}={value}")
        if end < len(local_lines) and local_lines[end].strip():
            snippet.append("")
        insertions.append((end, snippet))

    if result["added_sections"]:
        appended_sections: list[str] = []
        if local_lines and local_lines[-1].strip():
            appended_sections.append("")
        for section_name in result["added_sections"]:
            start, end = template_ranges[section_name]
            section_block = template_lines[start:end]
            while section_block and not section_block[-1].strip():
                section_block = section_block[:-1]
            appended_sections.extend(section_block)
            appended_sections.append("")
        while appended_sections and not appended_sections[-1].strip():
            appended_sections.pop()
        insertions.append((len(local_lines), appended_sections))

    merged_lines = list(local_lines)
    for insert_at, snippet in sorted(insertions, key=lambda item: item[0], reverse=True):
        if snippet:
            merged_lines[insert_at:insert_at] = snippet

    merged_text = "\n".join(merged_lines)
    if has_trailing_newline or merged_lines:
        merged_text += "\n"

    if merged_text == local_text:
        return result

    result["changed"] = True
    result["backup_required"] = True
    if mode != "dry-run":
        backup_path = build_backup_path(local_path)
        shutil.copy2(local_path, backup_path)
        write_atomically(local_path, merged_text)
        result["backup_path"] = backup_path
    return result


local_path, template_path, result_path, mode = sys.argv[1:5]
try:
    result = merge_with_layout(local_path, template_path, mode)
except SystemExit as exc:
    payload = exc.code
    if isinstance(payload, str) and payload.startswith("{"):
        with open(result_path, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.write("\n")
        sys.exit(2)
    raise

with open(result_path, "w", encoding="utf-8") as handle:
    json.dump(result, handle, indent=2, sort_keys=True)
    handle.write("\n")
PY
		merge_status=0
	else
		merge_status=$?
	fi
	apply_merge_result "$merge_result_path"

	if [ "$merge_status" -eq 2 ] && [ "$CONFIG_MERGE_SKIPPED_REASON" = "malformed-template-config" ]; then
		log "Refreshed template config is malformed"
		set_failure_reason_once "malformed-template-config"
		return 1
	fi

	if [ "$CONFIG_MERGE_CHANGED" = "1" ]; then
		if [ "$DRY_RUN" = "1" ]; then
			log "Previewed additive config merge with preserved comments/layout"
		else
			log "Merged missing config keys from the refreshed template"
		fi
	elif [ -n "$CONFIG_MERGE_SKIPPED_REASON" ]; then
		log "Skipping additive config merge; reason: $CONFIG_MERGE_SKIPPED_REASON"
	else
		log "Preserved local config already contains the refreshed template keys"
	fi
}

validate_wallbox_config() {
	destination_root="$1"
	config_path="${destination_root}/deploy/venus/config.venus_evcharger.ini"
	[ -f "$config_path" ] || return 0
	if [ -f "${destination_root}/venus_evcharger/backend/probe.py" ]; then
		if (
			cd "$destination_root" &&
				python3 -m venus_evcharger.backend.probe validate-wallbox "$config_path" >/dev/null
		); then
			VALIDATION_PASSED=1
			CONFIG_VALIDATION_MODE="probe"
			log "Validated merged wallbox config"
			return 0
		fi
		VALIDATION_PASSED=0
		CONFIG_VALIDATION_MODE="probe"
		PROMOTION_ABORTED_REASON="${PROMOTION_ABORTED_REASON:-config-validation-failed}"
		set_failure_reason_once "config-validation-failed"
		log "Wallbox config validation failed for $config_path"
		return 1
	fi
	if python3 - "$config_path" <<'PY'; then
import configparser
import sys

config_path = sys.argv[1]
parser = configparser.ConfigParser()
try:
    with open(config_path, "r", encoding="utf-8") as handle:
        parser.read_file(handle)
except (OSError, configparser.Error):
    raise SystemExit(1)
if "DEFAULT" not in parser or not parser["DEFAULT"].get("Host", "").strip():
    raise SystemExit(1)
PY
		VALIDATION_PASSED=1
		CONFIG_VALIDATION_MODE="fallback"
		log "Validated merged wallbox config with the bootstrap fallback validator"
		return 0
	fi
	VALIDATION_PASSED=0
	CONFIG_VALIDATION_MODE="fallback"
	PROMOTION_ABORTED_REASON="${PROMOTION_ABORTED_REASON:-config-validation-failed}"
	set_failure_reason_once "config-validation-failed"
	log "Wallbox config validation failed for $config_path"
	return 1
}

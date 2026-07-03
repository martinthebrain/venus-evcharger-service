# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import configparser
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

from venus_evcharger.backend.factory import build_service_backends
from venus_evcharger.backend.probe import (
    probe_meter_backend,
    probe_switch_backend,
    read_charger_backend,
    validate_wallbox_config,
)
from venus_evcharger.bootstrap.errors import WIZARD_ROLE_PROBE_ERRORS
from venus_evcharger.bootstrap.wizard_layouts import resolve_role_hosts
from venus_evcharger.bootstrap.wizard_models import WizardAnswers
from venus_evcharger.bootstrap.wizard_support import host_from_input
from venus_evcharger.bootstrap.wizard_topology import build_wizard_topology_config
from venus_evcharger.bootstrap.wizard_topology_render import render_adapter_files_from_topology
from venus_evcharger.core.common_values import normalize_phase
from venus_evcharger.topology import EvChargerTopologyConfig, MeasurementConfig


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


class CasePreservingConfigParser(configparser.ConfigParser):
    """Config parser that keeps option names exactly as written."""

    def optionxform(self, optionstr: str) -> str:
        return optionstr


def default_template_path() -> Path:
    return repo_root() / "deploy" / "venus" / "config.venus_evcharger.ini"


def default_config_path() -> Path:
    return default_template_path()


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def mode_value(policy_mode: str) -> str:
    return {"manual": "0", "auto": "1", "scheduled": "2"}[policy_mode]


def replace_assignment(text: str, key: str, value: str) -> str:
    replaced = False
    lines: list[str] = []
    for line in text.splitlines():
        if line.startswith(f"{key}="):
            lines.append(f"{key}={value}")
            replaced = True
            continue
        lines.append(line)
    if not replaced:
        raise ValueError(f"Config template is missing required key '{key}'")
    return "\n".join(lines) + "\n"


def replace_optional_assignment(text: str, key: str, value: object) -> str:
    if value is None:
        return text
    if isinstance(value, float):
        rendered = f"{value:g}"
    else:
        rendered = str(value)
    return replace_assignment(text, key, rendered)


def upsert_default_assignments(text: str, assignments: dict[str, str]) -> str:
    if not assignments:
        return text
    lines = text.splitlines()
    remaining = dict(assignments)
    rendered: list[str] = []
    inserted = False
    in_default_section = False

    for line in lines:
        if line == "[DEFAULT]":
            in_default_section = True
            rendered.append(line)
            continue
        rendered, inserted = _maybe_insert_default_assignments(
            line,
            rendered,
            remaining,
            inserted=inserted,
            in_default_section=in_default_section,
        )
        matched_key = _matching_default_assignment_key(line, remaining)
        if matched_key is None:
            rendered.append(line)
            continue
        rendered.append(_render_default_assignment_line(matched_key, remaining))
    rendered.extend(_remaining_default_assignment_lines(rendered, remaining))
    return "\n".join(rendered) + "\n"


def remove_section(text: str, section_name: str) -> str:
    result: list[str] = []
    in_section = False
    for line in text.splitlines():
        if _is_section_header(line):
            in_section = line == f"[{section_name}]"
            if in_section:
                continue
        if in_section:
            continue
        result.append(line)
    return "\n".join(result).rstrip() + "\n"


def _maybe_insert_default_assignments(
    line: str,
    rendered: list[str],
    remaining: dict[str, str],
    *,
    inserted: bool,
    in_default_section: bool,
) -> tuple[list[str], bool]:
    if inserted or not in_default_section or not _is_section_header(line):
        return rendered, inserted
    rendered.extend(_render_remaining_default_assignments(remaining))
    remaining.clear()
    return rendered, True


def _matching_default_assignment_key(line: str, remaining: dict[str, str]) -> str | None:
    return next((key for key in remaining if line.startswith(f"{key}=")), None)


def _render_default_assignment_line(key: str, remaining: dict[str, str]) -> str:
    return f"{key}={remaining.pop(key)}"


def _remaining_default_assignment_lines(rendered: list[str], remaining: dict[str, str]) -> list[str]:
    if not remaining:
        return []
    lines: list[str] = []
    if rendered and rendered[-1].strip():
        lines.append("")
    lines.extend(_render_remaining_default_assignments(remaining))
    return lines


def _render_remaining_default_assignments(remaining: dict[str, str]) -> list[str]:
    return [f"{key}={value}" for key, value in remaining.items()]


def _is_section_header(line: str) -> bool:
    return line.startswith("[") and line.endswith("]")


def append_backends(text: str, lines: list[str]) -> str:
    if not lines:
        return remove_section(text, "Backends")
    result = remove_section(text, "Backends").rstrip()
    return f"{result}\n\n[Backends]\n" + "\n".join(lines) + "\n"


def render_wizard_config(template_text: str, answers: WizardAnswers) -> tuple[str, dict[str, str], dict[str, str]]:
    config_text = replace_assignment(template_text, "Host", host_from_input(answers.host_input))
    config_text = replace_assignment(config_text, "DeviceInstance", str(int(answers.device_instance)))
    config_text = replace_assignment(config_text, "DigestAuth", "1" if answers.digest_auth else "0")
    config_text = replace_assignment(config_text, "Username", answers.username.strip())
    config_text = replace_assignment(config_text, "Password", answers.password.strip())
    config_text = replace_assignment(config_text, "Phase", normalize_phase(answers.phase))
    config_text = replace_assignment(config_text, "Mode", mode_value(answers.policy_mode))
    config_text = replace_optional_assignment(config_text, "AutoStartSurplusWatts", answers.auto_start_surplus_watts)
    config_text = replace_optional_assignment(config_text, "AutoStopSurplusWatts", answers.auto_stop_surplus_watts)
    config_text = replace_optional_assignment(config_text, "AutoMinSoc", answers.auto_min_soc)
    config_text = replace_optional_assignment(config_text, "AutoResumeSoc", answers.auto_resume_soc)
    config_text = replace_optional_assignment(config_text, "AutoScheduledEnabledDays", answers.scheduled_enabled_days)
    config_text = replace_optional_assignment(config_text, "AutoScheduledLatestEndTime", answers.scheduled_latest_end_time)
    config_text = replace_optional_assignment(config_text, "AutoScheduledNightCurrentAmps", answers.scheduled_night_current_amps)
    role_hosts = resolve_role_hosts(
        profile=answers.profile,
        primary_host_input=answers.host_input,
        meter_host_input=answers.meter_host_input,
        switch_host_input=answers.switch_host_input,
        charger_host_input=answers.charger_host_input,
        topology_preset=answers.topology_preset,
    )
    topology_config = build_wizard_topology_config(answers)
    adapter_files = render_adapter_files_from_topology(topology_config, answers, role_hosts)
    backend_lines = render_legacy_backends_from_topology(topology_config, adapter_files)
    return append_backends(config_text, backend_lines), adapter_files, role_hosts


def render_legacy_backends_from_topology(
    topology_config: EvChargerTopologyConfig,
    adapter_files: dict[str, str],
) -> list[str]:
    if topology_config.topology.type == "simple_relay" and not adapter_files:
        return []
    lines = ["Mode=split"]
    lines.extend(_measurement_backend_lines(topology_config.measurement, adapter_files))
    lines.extend(_actuator_backend_lines(topology_config))
    lines.extend(_charger_backend_lines(topology_config))
    return lines


def _measurement_backend_lines(
    measurement: MeasurementConfig | None,
    adapter_files: dict[str, str],
) -> list[str]:
    if measurement is None or measurement.type in {"none", "charger_native", "actuator_native", "fixed_reference", "learned_reference"}:
        return ["MeterType=none"]
    if measurement.type != "external_meter" or measurement.config_path is None:
        raise ValueError(f"unsupported legacy meter mapping for measurement type '{measurement.type}'")
    return [
        f"MeterType={_adapter_type_from_file(adapter_files, measurement.config_path)}",
        f"MeterConfigPath={measurement.config_path}",
    ]


def _actuator_backend_lines(topology_config: EvChargerTopologyConfig) -> list[str]:
    actuator = topology_config.actuator
    if actuator is None:
        return ["SwitchType=none"]
    lines = [f"SwitchType={actuator.type}"]
    if actuator.config_path:
        lines.append(f"SwitchConfigPath={actuator.config_path}")
    return lines


def _charger_backend_lines(topology_config: EvChargerTopologyConfig) -> list[str]:
    charger = topology_config.charger
    if charger is None:
        return ["ChargerType="]
    lines = [f"ChargerType={charger.type}"]
    if charger.config_path:
        lines.append(f"ChargerConfigPath={charger.config_path}")
    return lines


def _adapter_type_from_file(adapter_files: dict[str, str], relative_path: str) -> str:
    content = adapter_files.get(relative_path)
    if content is None:
        raise ValueError(f"missing adapter file '{relative_path}' while rendering legacy backends")
    parser = configparser.ConfigParser()
    parser.read_string(content)
    if not parser.has_section("Adapter"):
        raise ValueError(f"adapter file '{relative_path}' is missing required [Adapter] section")
    adapter_type = str(parser["Adapter"].get("Type", "")).strip()
    if not adapter_type:
        raise ValueError(f"adapter file '{relative_path}' is missing Adapter.Type")
    return adapter_type


def materialized_config_text(config_text: str, output_dir: Path, adapter_files: dict[str, str]) -> str:
    rendered = config_text
    for relative_path in sorted(adapter_files):
        rendered = rendered.replace(f"={relative_path}\n", f"={output_dir / relative_path}\n")
    return rendered


SENSITIVE_ASSIGNMENT_KEYS = frozenset(("Password", "ControlApiAuthToken"))


def redact_sensitive_assignments(text: str) -> str:
    """Remove secret assignment values before temporary wizard materialization."""
    redacted: list[str] = []
    for line in text.splitlines():
        key, separator, _value = line.partition("=")
        if separator and key.strip() in SENSITIVE_ASSIGNMENT_KEYS:
            continue
        redacted.append(line)
    return "\n".join(redacted) + ("\n" if text.endswith("\n") else "")


def sensitive_defaults_from_config_text(config_text: str) -> dict[str, str]:
    parser = configparser.ConfigParser()
    try:
        parser.read_string(config_text)
    except configparser.MissingSectionHeaderError:
        parser.read_string("[DEFAULT]\n" + config_text)
    defaults = parser["DEFAULT"]
    return {
        "Username": defaults.get("Username", "").strip(),
        "Password": defaults.get("Password", "").strip(),
        "DigestAuth": defaults.get("DigestAuth", "0").strip(),
        "ControlApiAuthToken": defaults.get("ControlApiAuthToken", "").strip(),
    }


def redact_sensitive_rendered_setup(
    config_text: str,
    adapter_files: dict[str, str],
) -> tuple[str, dict[str, str]]:
    return (
        redact_sensitive_assignments(config_text),
        {relative_path: redact_sensitive_assignments(content) for relative_path, content in adapter_files.items()},
    )


def write_private_text(target: Path, content: str) -> None:
    """Write sensitive wizard material with owner-only permissions."""
    target.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    with os.fdopen(os.open(target, flags, 0o600), "w", encoding="utf-8") as handle:
        handle.write(content)
    target.chmod(0o600)


def materialize_rendered_setup(
    config_text: str,
    output_dir: Path,
    adapter_files: dict[str, str],
    config_name: str,
) -> Path:
    """Write one rendered setup to disk using private file permissions."""
    materialized_text = materialized_config_text(config_text, output_dir, adapter_files)
    main_path = output_dir / config_name
    write_private_text(main_path, materialized_text)
    for relative_path, content in adapter_files.items():
        write_private_text(output_dir / relative_path, content)
    return main_path


def validate_rendered_setup(config_text: str, adapter_files: dict[str, str], config_name: str) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        redacted_config_text, redacted_adapter_files = redact_sensitive_rendered_setup(config_text, adapter_files)
        main_path = materialize_rendered_setup(redacted_config_text, temp_path, redacted_adapter_files, config_name)
        return validate_wallbox_config(str(main_path))


def _secret_default(
    defaults: configparser.SectionProxy,
    secret_defaults: dict[str, str] | None,
    key: str,
    fallback: str = "",
) -> str:
    if secret_defaults is not None and secret_defaults.get(key, ""):
        return secret_defaults[key]
    return defaults.get(key, fallback).strip()


def _probe_service_from_wallbox_config(
    parser: configparser.ConfigParser,
    secret_defaults: dict[str, str] | None = None,
) -> object:
    defaults = parser["DEFAULT"]
    return SimpleNamespace(
        config=parser,
        session=None,
        host=defaults.get("Host", "").strip(),
        username=_secret_default(defaults, secret_defaults, "Username"),
        password=_secret_default(defaults, secret_defaults, "Password"),
        use_digest_auth=_secret_default(defaults, secret_defaults, "DigestAuth", "0").lower() in ("1", "true", "yes", "on"),
        shelly_request_timeout_seconds=float(defaults.get("ShellyRequestTimeoutSeconds", "2.0") or 2.0),
        pm_component=defaults.get("ShellyComponent", "Switch").strip(),
        pm_id=int(defaults.get("ShellyId", "0") or 0),
        phase=defaults.get("Phase", "L1").strip(),
        max_current=float(defaults.get("MaxCurrent", "16.0") or 16.0),
        _last_voltage=None,
        _adapter_auth_fallback_enabled=secret_defaults is not None,
    )


def live_connectivity_payload(
    main_path: Path,
    selected_roles: tuple[str, ...] | None,
    secret_defaults: dict[str, str] | None = None,
) -> dict[str, object]:
    parser = configparser.ConfigParser()
    parser.read(main_path, encoding="utf-8")
    runtime = build_service_backends(_probe_service_from_wallbox_config(parser, secret_defaults)).runtime
    role_results: dict[str, dict[str, object]] = {}
    ok = True

    def run_role(
        role: str,
        config_path: Path | None,
        probe: Callable[[str], dict[str, object]],
    ) -> None:
        nonlocal ok
        if selected_roles is not None and role not in selected_roles:
            role_results[role] = {"status": "skipped", "reason": "not requested"}
            return
        if config_path is None:
            role_results[role] = {"status": "skipped", "reason": "not configured"}
            return
        resolved_path = config_path if config_path.is_absolute() else main_path.parent / config_path
        try:
            role_results[role] = {"status": "ok", "payload": probe(str(resolved_path))}
        except WIZARD_ROLE_PROBE_ERRORS as exc:
            ok = False
            role_results[role] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

    run_role("meter", runtime.meter_config_path, probe_meter_backend)
    run_role("switch", runtime.switch_config_path, probe_switch_backend)
    run_role("charger", runtime.charger_config_path, read_charger_backend)
    checked_roles = tuple(role for role, payload in role_results.items() if payload.get("status") != "skipped")
    return {
        "ok": ok,
        "checked_roles": checked_roles,
        "roles": role_results,
    }


def live_check_rendered_setup(
    config_text: str,
    adapter_files: dict[str, str],
    config_name: str,
    selected_roles: tuple[str, ...] | None,
) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        redacted_config_text, redacted_adapter_files = redact_sensitive_rendered_setup(config_text, adapter_files)
        main_path = materialize_rendered_setup(redacted_config_text, temp_path, redacted_adapter_files, config_name)
        return live_connectivity_payload(main_path, selected_roles, sensitive_defaults_from_config_text(config_text))


def answer_defaults(answers: WizardAnswers) -> dict[str, object]:
    return {
        "profile": answers.profile,
        "host_input": answers.host_input,
        "meter_host_input": answers.meter_host_input,
        "switch_host_input": answers.switch_host_input,
        "charger_host_input": answers.charger_host_input,
        "device_instance": answers.device_instance,
        "phase": answers.phase,
        "policy_mode": answers.policy_mode,
        "digest_auth": answers.digest_auth,
        "username": answers.username,
        "password_present": bool(answers.password),
        "topology_preset": answers.topology_preset,
        "charger_backend": answers.charger_backend,
        "charger_preset": answers.charger_preset,
        "request_timeout_seconds": answers.request_timeout_seconds,
        "cerbo_relay_index": answers.cerbo_relay_index,
        "cerbo_relay_contact_mode": answers.cerbo_relay_contact_mode,
        "switch_group_supported_phase_selections": answers.switch_group_supported_phase_selections,
        "auto_start_surplus_watts": answers.auto_start_surplus_watts,
        "auto_stop_surplus_watts": answers.auto_stop_surplus_watts,
        "auto_min_soc": answers.auto_min_soc,
        "auto_resume_soc": answers.auto_resume_soc,
        "scheduled_enabled_days": answers.scheduled_enabled_days,
        "scheduled_latest_end_time": answers.scheduled_latest_end_time,
        "scheduled_night_current_amps": answers.scheduled_night_current_amps,
        "transport_kind": answers.transport_kind,
        "transport_host": answers.transport_host,
        "transport_port": answers.transport_port,
        "transport_device": answers.transport_device,
        "transport_unit_id": answers.transport_unit_id,
    }


def backup_path(target: Path) -> Path:
    return target.with_name(f"{target.name}.wizard-backup-{timestamp()}")


def write_with_backup(target: Path, content: str) -> str | None:
    backup_file: str | None = None
    if target.exists():
        destination = backup_path(target)
        shutil.copy2(target, destination)
        backup_file = str(destination)
    write_private_text(target, content)
    return backup_file


def write_generated_files(config_path: Path, materialized_text: str, adapter_files: dict[str, str]) -> list[str]:
    backup_files: list[str] = []
    backup_file = write_with_backup(config_path, materialized_text)
    if backup_file is not None:
        backup_files.append(backup_file)
    for relative_path, content in sorted(adapter_files.items()):
        backup_file = write_with_backup(config_path.parent / relative_path, content)
        if backup_file is not None:
            backup_files.append(backup_file)
    return backup_files

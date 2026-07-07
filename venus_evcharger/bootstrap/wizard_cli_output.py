# SPDX-License-Identifier: GPL-3.0-or-later
"""Text rendering helpers for wizard results."""

from __future__ import annotations

from venus_evcharger.bootstrap.wizard_models import WizardResult
from venus_evcharger.bootstrap.wizard_support import (
    policy_mode_label,
    profile_label,
    setup_responsibility_summary,
    topology_preset_label,
    topology_uses_cerbo_relay,
)
from venus_evcharger.bootstrap.wizard_cli_output_next import (
    result_next_step_lines,
    result_post_install_checklist_lines,
    result_setup_note_lines,
)


def result_text(result: WizardResult) -> str:
    lines = [
        *_result_header_lines(result),
        *_result_configuration_summary_lines(result),
        *_result_role_host_lines(result),
        *_result_generated_file_lines(result),
        *_result_artifact_lines(result),
        *_result_warning_lines(result),
        *_result_live_check_lines(result),
        *result_setup_note_lines(result),
        *result_next_step_lines(result),
        *result_post_install_checklist_lines(result),
        *_result_suggested_energy_source_lines(result),
        *_result_suggested_energy_merge_lines(result),
        *_result_suggested_block_lines(result),
        "Manual review:",
        *(f"  - {item}" for item in result.manual_review),
    ]
    return "\n".join(lines)


def _result_header_lines(result: WizardResult) -> list[str]:
    return [
        f"Config written to: {result.config_path}" if not result.dry_run else f"Config preview for: {result.config_path}",
        _optional_header_line("Imported defaults", result.imported_from, "none"),
        f"Selected setup: {profile_label(result.profile)}",
        _optional_header_line("Selected topology preset", topology_preset_label(result.topology_preset), "n/a"),
        f"Responsibilities: {setup_responsibility_summary(result.profile, result.topology_preset)}",
        f"Initial policy mode: {policy_mode_label(result.policy_mode)}",
        _optional_header_line("Selected charger backend", result.charger_backend, "none"),
        _optional_header_line("Transport", result.transport_kind, "n/a"),
        "Validation: ok",
        f"Live connectivity: {_live_connectivity_label(result)}",
    ]


def _optional_header_line(label: str, value: object, fallback: str) -> str:
    return f"{label}: {value if value is not None else fallback}"


def _live_connectivity_label(result: WizardResult) -> str:
    if result.live_check is None:
        return "not run"
    return "ok" if result.live_check.get("ok") else "check reported issues"


def _result_role_host_lines(result: WizardResult) -> list[str]:
    lines = ["Role endpoints:"]
    if result.role_hosts:
        lines.extend(f"  - {role}: {value}" for role, value in sorted(result.role_hosts.items()))
    else:
        lines.append("  - none")
    return lines


def _result_configuration_summary_lines(result: WizardResult) -> list[str]:
    return [
        "Configuration summary:",
        f"  - Target config: {result.config_path}",
        f"  - Charging policy: {policy_mode_label(result.policy_mode)}",
        f"  - Hardware flow: {_hardware_flow_summary(result)}",
    ]


def _hardware_flow_summary(result: WizardResult) -> str:
    meter = result.role_hosts.get("meter")
    switch = result.role_hosts.get("switch")
    charger = result.role_hosts.get("charger")
    if topology_uses_cerbo_relay(result.topology_preset):
        return _cerbo_hardware_flow(meter)
    if result.topology_preset is not None and "switch-group" in result.topology_preset:
        return _switch_group_hardware_flow(meter, switch, charger)
    if result.profile == "simple_relay":
        return _simple_relay_hardware_flow(meter, switch)
    return _native_or_generic_hardware_flow(result, meter, charger)


def _cerbo_hardware_flow(meter: str | None) -> str:
    meter_text = _endpoint_text(meter, "the configured meter")
    return f"{meter_text} measures energy; the local Cerbo GX relay switches the contactor."


def _switch_group_hardware_flow(meter: str | None, switch: str | None, charger: str | None) -> str:
    charger_text = _endpoint_text(charger, "the charger backend")
    switch_text = _endpoint_text(switch, "the external switch group")
    meter_prefix = f"{meter} measures energy; " if meter else ""
    return f"{meter_prefix}{charger_text} controls charging; {switch_text} switches phases/contactors."


def _simple_relay_hardware_flow(meter: str | None, switch: str | None) -> str:
    endpoint = _endpoint_text(meter or switch, "the configured meter/relay device")
    return f"{endpoint} measures energy and switches the relay/contactor."


def _native_or_generic_hardware_flow(result: WizardResult, meter: str | None, charger: str | None) -> str:
    if charger is None:
        return setup_responsibility_summary(result.profile, result.topology_preset) + "."
    if meter is None:
        return f"{charger} owns charger control and status where supported."
    return f"{meter} measures energy; {charger} controls charging."


def _endpoint_text(endpoint: str | None, fallback: str) -> str:
    return endpoint if endpoint else fallback


def _result_generated_file_lines(result: WizardResult) -> list[str]:
    lines = ["Generated files:", *(f"  - {item}" for item in result.generated_files)]
    if result.backup_files:
        lines.extend(["Backup files:", *(f"  - {item}" for item in result.backup_files)])
    return lines


def _result_artifact_lines(result: WizardResult) -> list[str]:
    lines: list[str] = []
    if result.result_path is not None:
        lines.append(f"Wizard result: {result.result_path}")
    if result.audit_path is not None:
        lines.append(f"Wizard audit: {result.audit_path}")
    if result.topology_summary_path is not None:
        lines.append(f"Topology summary: {result.topology_summary_path}")
    if result.inventory_path is not None:
        lines.append(f"Device inventory: {result.inventory_path}")
    return lines


def _result_warning_lines(result: WizardResult) -> list[str]:
    warnings = _risk_warning_lines(result)
    if not warnings:
        return []
    return ["Warnings by risk:", *(f"  - {item}" for item in warnings)]


def _risk_warning_lines(result: WizardResult) -> list[str]:
    items = [*_derived_risk_warning_items(result), *((_warning_severity(item), item) for item in result.warnings)]
    return [f"{severity}: {message}" for severity, message in sorted(items, key=_risk_sort_key)]


def _optional_warning(condition: bool, severity: str, message: str) -> tuple[tuple[str, str], ...]:
    return ((severity, message),) if condition else tuple()


def _derived_risk_warning_items(result: WizardResult) -> tuple[tuple[str, str], ...]:
    return (
        *_optional_warning(
            result.live_check is not None and not result.live_check.get("ok"),
            "High",
            "Live connectivity check reported issues; fix these before unattended charging.",
        ),
        *_optional_warning(
            bool(result.answer_defaults.get("password_present")),
            "Medium",
            "Authentication credentials are configured; keep generated config and wizard artifacts private.",
        ),
        *_optional_warning(
            topology_uses_cerbo_relay(result.topology_preset)
            and result.answer_defaults.get("cerbo_relay_contact_mode") == "NC",
            "Medium",
            "Cerbo relay uses NC wiring; verify fail-safe behavior before connecting a vehicle.",
        ),
    )


def _warning_severity(message: str) -> str:
    lowered = message.lower()
    if _contains_any(lowered, ("live connectivity", "dbus", "auth", "password")):
        return "High"
    if _contains_any(lowered, ("relay", "contactor", "phase", "switch")):
        return "Medium"
    if _contains_any(lowered, ("auto", "threshold", "surplus")):
        return "Medium"
    return "Low"


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token in text for token in tokens)


def _risk_sort_key(item: tuple[str, str]) -> tuple[int, str]:
    severity_order = {"High": 0, "Medium": 1, "Low": 2}
    return severity_order.get(item[0], 3), item[1]


def _result_live_check_lines(result: WizardResult) -> list[str]:
    if result.live_check is None:
        return []
    lines = ["Live connectivity by role:"]
    roles = result.live_check.get("roles")
    if isinstance(roles, dict) and roles:
        lines.extend(filter(None, (_result_live_check_role_line(role, payload) for role, payload in sorted(roles.items()))))
    return lines


def _result_live_check_role_line(role: str, payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    status = payload.get("status", "unknown")
    detail = payload.get("error") or payload.get("reason") or "ok"
    return f"  - {role}: {status} ({detail})"


def _result_suggested_block_lines(result: WizardResult) -> list[str]:
    if not result.suggested_blocks:
        return []
    lines = ["Suggested config blocks:"]
    for label, content in sorted(result.suggested_blocks.items()):
        lines.append(f"  - {label}:")
        lines.extend(f"    {line}" if line else "" for line in content.strip().splitlines())
    return lines


def _suggested_energy_source_summary(source: dict[str, object]) -> str:
    source_id = str(source.get("source_id", "unknown"))
    profile = str(source.get("profile", ""))
    config_path = str(source.get("configPath", source.get("config_path", "")))
    host = str(source.get("host", ""))
    port = source.get("port")
    unit_id = source.get("unitId", source.get("unit_id"))
    summary = f"  - {source_id}: profile={profile}"
    if config_path:
        summary += f", config={config_path}"
    if host:
        summary += f", host={host}"
    if port not in (None, ""):
        summary += f", port={port}"
    if unit_id not in (None, ""):
        summary += f", unit_id={unit_id}"
    return summary


def _result_suggested_energy_source_lines(result: WizardResult) -> list[str]:
    if not result.suggested_energy_sources:
        return []
    lines = ["Suggested energy sources:"]
    for source in result.suggested_energy_sources:
        lines.append(_suggested_energy_source_summary(source))
        capacity_key = str(source.get("capacityConfigKey", ""))
        if capacity_key:
            lines.append(f"    capacity follow-up: {capacity_key}=<set-me>")
    return lines


def _suggested_energy_merge_capacity_lines(merge: dict[str, object]) -> list[str]:
    capacity_follow_up = merge.get("capacity_follow_up")
    if not isinstance(capacity_follow_up, list) or not capacity_follow_up:
        return []
    lines = ["  - capacity follow-up:"]
    for item in capacity_follow_up:
        config_line = _suggested_energy_merge_capacity_line(item)
        if config_line is not None:
            lines.append(config_line)
    return lines


def _suggested_energy_merge_capacity_line(item: object) -> str | None:
    if not isinstance(item, dict):
        return None
    config_key = str(item.get("config_key", "")).strip()
    if not config_key:
        return None
    return f"    {config_key}=<set-me>"


def _merged_source_ids_line(merge: dict[str, object]) -> str | None:
    merged_source_ids = merge.get("merged_source_ids")
    if not isinstance(merged_source_ids, list) or not merged_source_ids:
        return None
    return "  - merged source ids: " + ",".join(str(item) for item in merged_source_ids)


def _helper_file_line(merge: dict[str, object]) -> str | None:
    helper_file = merge.get("helper_file")
    if isinstance(helper_file, str) and helper_file:
        return f"  - helper file: {helper_file}"
    return None


def _suggested_energy_merge_block_lines(block: object) -> list[str]:
    if not isinstance(block, str) or not block.strip():
        return []
    return ["  - merge block:", *(f"    {line}" if line else "" for line in block.strip().splitlines())]


def _suggested_energy_merge_header_lines(merge: dict[str, object]) -> list[str]:
    applied_to_config = bool(merge.get("applied_to_config"))
    lines = ["Suggested AutoEnergy merge:"]
    merged_source_ids_line = _merged_source_ids_line(merge)
    if merged_source_ids_line is not None:
        lines.append(merged_source_ids_line)
    helper_file_line = _helper_file_line(merge)
    if helper_file_line is not None:
        lines.append(helper_file_line)
    lines.append(f"  - applied to main config: {'yes' if applied_to_config else 'no'}")
    return lines


def _result_suggested_energy_merge_lines(result: WizardResult) -> list[str]:
    merge = result.suggested_energy_merge
    if not isinstance(merge, dict):
        return []
    return [
        *_suggested_energy_merge_header_lines(merge),
        *_suggested_energy_merge_capacity_lines(merge),
        *_suggested_energy_merge_block_lines(merge.get("merge_block")),
    ]

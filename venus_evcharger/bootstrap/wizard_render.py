# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from venus_evcharger.bootstrap.wizard_layouts import resolve_role_hosts
from venus_evcharger.bootstrap.wizard_models import WizardAnswers
from venus_evcharger.bootstrap.wizard_render_defaults import answer_defaults
from venus_evcharger.bootstrap.wizard_render_backends import (
    _actuator_backend_lines,
    _adapter_type_from_file,
    _charger_backend_lines,
    _measurement_backend_lines,
    render_legacy_backends_from_topology,
)
from venus_evcharger.bootstrap.wizard_render_io import (
    backup_path,
    materialize_rendered_setup,
    materialized_config_text,
    validate_rendered_setup,
    write_generated_files,
    write_private_text,
    write_with_backup,
)
from venus_evcharger.bootstrap.wizard_render_live import (
    live_check_rendered_setup,
    live_connectivity_payload,
)
from venus_evcharger.bootstrap.wizard_render_secrets import (
    SENSITIVE_ASSIGNMENT_KEYS,
    _probe_service_from_wallbox_config,
    _secret_default,
    probe_service_from_wallbox_config,
    redact_sensitive_assignments,
    redact_sensitive_rendered_setup,
    secret_default,
    sensitive_defaults_from_config_text,
)
from venus_evcharger.bootstrap.wizard_render_text import (
    CasePreservingConfigParser,
    _maybe_insert_default_assignments,
    _matching_default_assignment_key,
    _remaining_default_assignment_lines,
    _render_default_assignment_line,
    _render_remaining_default_assignments,
    append_backends,
    default_config_path,
    default_template_path,
    mode_value,
    remove_section,
    replace_assignment,
    replace_optional_assignment,
    repo_root,
    timestamp,
    upsert_default_assignments,
)
from venus_evcharger.bootstrap.wizard_support import host_from_input
from venus_evcharger.bootstrap.wizard_topology import build_wizard_topology_config
from venus_evcharger.bootstrap.wizard_topology_render import render_adapter_files_from_topology
from venus_evcharger.core.common_values import normalize_phase

__all__ = (
    "CasePreservingConfigParser",
    "SENSITIVE_ASSIGNMENT_KEYS",
    "_actuator_backend_lines",
    "_adapter_type_from_file",
    "_charger_backend_lines",
    "_maybe_insert_default_assignments",
    "_matching_default_assignment_key",
    "_measurement_backend_lines",
    "_probe_service_from_wallbox_config",
    "_remaining_default_assignment_lines",
    "_render_default_assignment_line",
    "_render_remaining_default_assignments",
    "_secret_default",
    "answer_defaults",
    "append_backends",
    "backup_path",
    "default_config_path",
    "default_template_path",
    "live_check_rendered_setup",
    "live_connectivity_payload",
    "materialize_rendered_setup",
    "materialized_config_text",
    "mode_value",
    "probe_service_from_wallbox_config",
    "redact_sensitive_assignments",
    "redact_sensitive_rendered_setup",
    "remove_section",
    "render_legacy_backends_from_topology",
    "render_wizard_config",
    "replace_assignment",
    "replace_optional_assignment",
    "repo_root",
    "secret_default",
    "sensitive_defaults_from_config_text",
    "timestamp",
    "upsert_default_assignments",
    "validate_rendered_setup",
    "write_generated_files",
    "write_private_text",
    "write_with_backup",
)


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

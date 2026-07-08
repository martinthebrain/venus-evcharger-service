# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime and rendering helpers for the setup wizard."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable

from venus_evcharger.bootstrap.wizard_energy import merged_recommendation_prefixes
from venus_evcharger.bootstrap.wizard_guidance import compatibility_warnings
from venus_evcharger.bootstrap.wizard_inventory import (
    build_wizard_inventory,
    inventory_payload,
    inventory_text,
)
from venus_evcharger.bootstrap.wizard_models import WizardAnswers, WizardResult
from venus_evcharger.bootstrap.wizard_render import (
    materialized_config_text,
    render_wizard_config,
    validate_rendered_setup,
)
from venus_evcharger.bootstrap.wizard_review import manual_review_items
from venus_evcharger.bootstrap.wizard_runtime_energy import suggested_energy_state
from venus_evcharger.bootstrap.wizard_runtime_live import json_ready_dict, live_check_rendered_setup
from venus_evcharger.bootstrap.wizard_runtime_persistence import wizard_persisted_result
from venus_evcharger.bootstrap.wizard_runtime_results import preview_result
from venus_evcharger.bootstrap.wizard_topology import build_wizard_topology_config


def configure_wallbox(
    answers: WizardAnswers,
    *,
    config_path: Path,
    template_path: Path,
    dry_run: bool = False,
    live_check: bool = False,
    selected_probe_roles: tuple[str, ...] | None = None,
    imported_from: str | None = None,
    energy_recommendation_prefix: str | tuple[str, ...] | list[str] | None = None,
    huawei_recommendation_prefix: str | tuple[str, ...] | list[str] | None = None,
    apply_suggested_energy_merge: bool = False,
    suggested_energy_capacity_wh: float | None = None,
    suggested_energy_capacity_overrides: dict[str, float] | None = None,
    live_check_runner: Callable[[str, dict[str, str], str, tuple[str, ...] | None], dict[str, object]] | None = None,
) -> WizardResult:
    template_text = template_path.read_text(encoding="utf-8")
    created_at = datetime.now().isoformat(timespec="seconds")
    config_text, adapter_files, role_hosts = render_wizard_config(template_text, answers)
    manual_review = manual_review_items(
        answers.profile,
        answers.policy_mode,
        answers.charger_backend,
        answers.transport_kind,
        answers.topology_preset,
    )
    recommendation_prefixes = merged_recommendation_prefixes(energy_recommendation_prefix, huawei_recommendation_prefix)
    (
        config_text,
        adapter_files,
        manual_review,
        suggested_blocks,
        suggested_energy_sources,
        suggested_energy_merge,
    ) = suggested_energy_state(
        config_path,
        config_text,
        adapter_files,
        manual_review,
        recommendation_prefixes,
        apply_suggested_energy_merge=apply_suggested_energy_merge,
        suggested_energy_capacity_wh=suggested_energy_capacity_wh,
        suggested_energy_capacity_overrides=suggested_energy_capacity_overrides,
    )
    validation = validate_rendered_setup(config_text, adapter_files, config_path.name)
    live_check_payload = None
    if live_check:
        if live_check_runner is None:
            live_check_runner = live_check_rendered_setup
        live_check_payload = live_check_runner(config_text, adapter_files, config_path.name, selected_probe_roles)
    topology_config = build_wizard_topology_config(answers)
    topology_config_payload = json_ready_dict(topology_config, "topology config")
    device_inventory_payload = inventory_payload(build_wizard_inventory(answers, role_hosts, topology_config))
    inventory_sidecar_text = inventory_text(answers, role_hosts, topology_config)
    materialized_text = materialized_config_text(config_text, config_path.parent, adapter_files)
    generated_files = (config_path.name,) + tuple(sorted(adapter_files))
    warnings = compatibility_warnings(
        profile=answers.profile,
        topology_preset=answers.topology_preset,
        charger_backend=answers.charger_backend,
        charger_preset=answers.charger_preset,
        primary_host_input=answers.host_input,
        role_hosts=role_hosts,
        transport_kind=answers.transport_kind,
        transport_host=answers.transport_host,
        switch_group_supported_phase_selections=answers.switch_group_supported_phase_selections,
    )
    result = preview_result(
        answers,
        config_path,
        created_at,
        validation,
        live_check_payload,
        topology_config_payload,
        device_inventory_payload,
        role_hosts,
        generated_files,
        warnings,
        imported_from,
        dry_run,
        manual_review,
        suggested_blocks,
        suggested_energy_sources,
        suggested_energy_merge,
    )
    if dry_run:
        return result
    return wizard_persisted_result(result, config_path, materialized_text, adapter_files, inventory_sidecar_text)

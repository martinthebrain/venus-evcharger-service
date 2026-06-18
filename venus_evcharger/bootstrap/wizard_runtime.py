# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime and rendering helpers for the setup wizard."""

from __future__ import annotations

import configparser
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, cast

from venus_evcharger.backend.factory import build_service_backends
from venus_evcharger.backend.probe import probe_meter_backend, probe_switch_backend, read_charger_backend
from venus_evcharger.bootstrap.wizard_cli import prompt_yes_no
from venus_evcharger.bootstrap.wizard_energy import (
    build_suggested_energy_merge,
    existing_auto_energy_assignments,
    huawei_bundle_files,
    manual_review_union,
    merged_recommendation_prefixes,
    optional_capacity_wh,
    suggested_energy_assignments,
    suggested_energy_sources_with_capacity,
    suggested_energy_sources_with_capacity_overrides,
    validate_unique_suggested_energy_sources,
)
from venus_evcharger.bootstrap.wizard_guidance import compatibility_warnings
from venus_evcharger.bootstrap.wizard_inventory import (
    build_wizard_inventory,
    inventory_payload,
    inventory_text,
)
from venus_evcharger.bootstrap.wizard_models import WizardAnswers, WizardResult
from venus_evcharger.bootstrap.wizard_persistence import persist_inventory_sidecar
from venus_evcharger.bootstrap.wizard_render import (
    materialize_rendered_setup,
    materialized_config_text,
    redact_sensitive_rendered_setup,
    render_wizard_config,
    sensitive_defaults_from_config_text,
    upsert_default_assignments,
    validate_rendered_setup,
    write_generated_files,
)
from venus_evcharger.bootstrap.wizard_review import manual_review_items
from venus_evcharger.bootstrap.wizard_runtime_results import json_ready, persisted_result, preview_result
from venus_evcharger.bootstrap.wizard_topology import build_wizard_topology_config


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


def _combined_role_payload(role: str, backend: object, main_path: Path, backend_type: object) -> dict[str, object]:
    if role == "meter":
        return {"path": str(main_path), "type": str(backend_type), "meter": json_ready(getattr(backend, "read_meter")())}
    if role == "switch":
        return {
            "path": str(main_path),
            "type": str(backend_type),
            "capabilities": json_ready(getattr(backend, "capabilities")()),
            "switch_state": json_ready(getattr(backend, "read_switch_state")()),
        }
    return {
        "path": str(main_path),
        "type": str(backend_type),
        "charger_state": json_ready(getattr(backend, "read_charger_state")()),
    }
def _live_connectivity_payload(
    main_path: Path,
    selected_roles: tuple[str, ...] | None,
    secret_defaults: dict[str, str] | None = None,
) -> dict[str, object]:
    return _live_connectivity_payload_with_hooks(
        main_path,
        selected_roles,
        secret_defaults=secret_defaults,
        build_backends_fn=build_service_backends,
        probe_meter_fn=probe_meter_backend,
        probe_switch_fn=probe_switch_backend,
        read_charger_fn=read_charger_backend,
    )


def _live_connectivity_payload_with_hooks(
    main_path: Path,
    selected_roles: tuple[str, ...] | None,
    *,
    build_backends_fn: Callable[[object], object],
    probe_meter_fn: Callable[[str], dict[str, object]],
    probe_switch_fn: Callable[[str], dict[str, object]],
    read_charger_fn: Callable[[str], dict[str, object]],
    secret_defaults: dict[str, str] | None = None,
) -> dict[str, object]:
    parser = configparser.ConfigParser()
    parser.read(main_path, encoding="utf-8")
    role_results: dict[str, dict[str, object]] = {}
    ok = True
    resolved_backends = cast(Any, build_backends_fn(_probe_service_from_wallbox_config(parser, secret_defaults)))
    runtime = resolved_backends.runtime

    def backend_for(role: str) -> object | None:
        return getattr(resolved_backends, role, None)

    def run_role(
        role: str,
        backend_type: object,
        config_path: Path | None,
        probe: Callable[[str], dict[str, object]],
    ) -> None:
        nonlocal ok
        if selected_roles is not None and role not in selected_roles:
            role_results[role] = {"status": "skipped", "reason": "not requested"}
            return
        if getattr(runtime, "backend_mode", None) == "combined" and config_path is None and role in {"meter", "switch"}:
            backend = backend_for(role)
            if backend is None:
                role_results[role] = {"status": "skipped", "reason": "not configured"}
                return
            try:
                role_results[role] = {
                    "status": "ok",
                    "payload": _combined_role_payload(role, backend, main_path, backend_type),
                }
            except Exception as exc:
                ok = False
                role_results[role] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
            return
        if config_path is None:
            role_results[role] = {"status": "skipped", "reason": "not configured"}
            return
        resolved_path = config_path if config_path.is_absolute() else main_path.parent / config_path
        try:
            if secret_defaults is not None:
                backend = backend_for(role)
                if backend is None:
                    role_results[role] = {"status": "skipped", "reason": "not configured"}
                    return
                role_results[role] = {
                    "status": "ok",
                    "payload": _combined_role_payload(role, backend, resolved_path, backend_type),
                }
                return
            role_results[role] = {"status": "ok", "payload": probe(str(resolved_path))}
        except Exception as exc:
            ok = False
            role_results[role] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

    run_role("meter", getattr(runtime, "meter_type", "shelly_meter"), runtime.meter_config_path, probe_meter_fn)
    run_role("switch", getattr(runtime, "switch_type", "shelly_contactor_switch"), runtime.switch_config_path, probe_switch_fn)
    run_role("charger", getattr(runtime, "charger_type", ""), runtime.charger_config_path, read_charger_fn)
    checked_roles = tuple(role for role, payload in role_results.items() if payload.get("status") != "skipped")
    return {"ok": ok, "checked_roles": checked_roles, "roles": role_results}


def _live_check_rendered_setup(
    config_text: str,
    adapter_files: dict[str, str],
    config_name: str,
    selected_roles: tuple[str, ...] | None,
) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        redacted_config_text, redacted_adapter_files = redact_sensitive_rendered_setup(config_text, adapter_files)
        main_path = materialize_rendered_setup(redacted_config_text, temp_path, redacted_adapter_files, config_name)
        return _live_connectivity_payload(main_path, selected_roles, sensitive_defaults_from_config_text(config_text))


def _suggested_energy_state(
    config_path: Path,
    config_text: str,
    adapter_files: dict[str, str],
    manual_review: tuple[str, ...],
    recommendation_prefixes: tuple[str, ...],
    *,
    apply_suggested_energy_merge: bool,
    suggested_energy_capacity_wh: float | None,
    suggested_energy_capacity_overrides: dict[str, float] | None,
) -> tuple[str, dict[str, str], tuple[str, ...], dict[str, str], tuple[dict[str, object], ...], dict[str, object] | None]:
    suggested_blocks: dict[str, str] = {}
    suggested_energy_sources: tuple[dict[str, object], ...] = tuple()
    suggested_energy_merge: dict[str, object] | None = None
    if not recommendation_prefixes:
        return config_text, adapter_files, manual_review, suggested_blocks, suggested_energy_sources, suggested_energy_merge
    bundle_files, manual_review, suggested_blocks, suggested_energy_sources = _merged_recommendation_bundle_state(
        recommendation_prefixes,
        manual_review,
    )
    adapter_files = {**adapter_files, **bundle_files}
    suggested_energy_sources = _suggested_energy_sources_with_requested_capacity(
        suggested_energy_sources,
        suggested_energy_capacity_wh,
        suggested_energy_capacity_overrides,
    )
    suggested_energy_merge, merge_files = build_suggested_energy_merge(config_path, suggested_energy_sources)
    adapter_files = {**adapter_files, **merge_files}
    if apply_suggested_energy_merge and suggested_energy_merge is not None:
        config_text, suggested_energy_merge = _config_text_with_suggested_energy_merge(
            config_path,
            config_text,
            suggested_energy_sources,
            suggested_energy_merge,
        )
    return config_text, adapter_files, manual_review, suggested_blocks, suggested_energy_sources, suggested_energy_merge


def _merged_recommendation_bundle_state(
    recommendation_prefixes: tuple[str, ...],
    manual_review: tuple[str, ...],
) -> tuple[dict[str, str], tuple[str, ...], dict[str, str], tuple[dict[str, object], ...]]:
    bundle_files: dict[str, str] = {}
    bundle_blocks: dict[str, str] = {}
    bundle_sources: list[dict[str, object]] = []
    for recommendation_prefix in recommendation_prefixes:
        huawei_files, huawei_review_items, huawei_blocks, huawei_sources = huawei_bundle_files(recommendation_prefix)
        bundle_files.update(huawei_files)
        bundle_blocks.update(huawei_blocks)
        bundle_sources.extend(dict(source) for source in huawei_sources)
        manual_review = manual_review_union(manual_review, huawei_review_items)
    suggested_energy_sources = validate_unique_suggested_energy_sources(tuple(bundle_sources))
    return bundle_files, manual_review, dict(bundle_blocks), suggested_energy_sources


def _suggested_energy_sources_with_requested_capacity(
    suggested_energy_sources: tuple[dict[str, object], ...],
    suggested_energy_capacity_wh: float | None,
    suggested_energy_capacity_overrides: dict[str, float] | None,
) -> tuple[dict[str, object], ...]:
    if suggested_energy_capacity_wh is not None and len(suggested_energy_sources) == 1:
        suggested_energy_sources = suggested_energy_sources_with_capacity(suggested_energy_sources, suggested_energy_capacity_wh)
    return suggested_energy_sources_with_capacity_overrides(
        suggested_energy_sources,
        suggested_energy_capacity_overrides or {},
    )


def _config_text_with_suggested_energy_merge(
    config_path: Path,
    config_text: str,
    suggested_energy_sources: tuple[dict[str, object], ...],
    suggested_energy_merge: dict[str, object],
) -> tuple[str, dict[str, object]]:
    config_text = upsert_default_assignments(
        config_text,
        suggested_energy_assignments(existing_auto_energy_assignments(config_path), suggested_energy_sources),
    )
    updated_merge = dict(suggested_energy_merge)
    updated_merge["applied_to_config"] = True
    return config_text, updated_merge


def _wizard_persisted_result(
    result: WizardResult,
    config_path: Path,
    materialized_text: str,
    adapter_files: dict[str, str],
    inventory_sidecar_text: str,
) -> WizardResult:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    backup_files = tuple(write_generated_files(config_path, materialized_text, adapter_files))
    inventory_sidecar_path = persist_inventory_sidecar(config_path, inventory_sidecar_text)
    result = WizardResult(
        created_at=result.created_at,
        config_path=result.config_path,
        imported_from=result.imported_from,
        profile=result.profile,
        policy_mode=result.policy_mode,
        topology_preset=result.topology_preset,
        charger_backend=result.charger_backend,
        charger_preset=result.charger_preset,
        transport_kind=result.transport_kind,
        role_hosts=result.role_hosts,
        validation=result.validation,
        live_check=result.live_check,
        warnings=result.warnings,
        answer_defaults=result.answer_defaults,
        generated_files=result.generated_files,
        backup_files=backup_files,
        result_path=None,
        audit_path=None,
        topology_summary_path=None,
        inventory_path=inventory_sidecar_path,
        manual_review=result.manual_review,
        dry_run=result.dry_run,
        topology_config=result.topology_config,
        device_inventory=result.device_inventory,
        suggested_blocks=result.suggested_blocks,
        suggested_energy_sources=result.suggested_energy_sources,
        suggested_energy_merge=result.suggested_energy_merge,
    )
    return persisted_result(result)


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
    live_check_runner: Callable[[str, dict[str, str], str, tuple[str, ...] | None], dict[str, object]] = _live_check_rendered_setup,
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
    ) = _suggested_energy_state(
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
    live_check_payload = live_check_runner(config_text, adapter_files, config_path.name, selected_probe_roles) if live_check else None
    topology_config = build_wizard_topology_config(answers)
    topology_config_payload = cast(dict[str, object], json_ready(topology_config))
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
    return _wizard_persisted_result(result, config_path, materialized_text, adapter_files, inventory_sidecar_text)


def _existing_output_paths(config_path: Path, generated_files: tuple[str, ...]) -> tuple[str, ...]:
    existing = []
    for relative_path in generated_files:
        candidate = config_path.parent / relative_path
        if candidate.exists():
            existing.append(str(candidate))
    return tuple(existing)


def _interactive_write_confirmed(preview: WizardResult, existing_files: tuple[str, ...]) -> bool:
    from venus_evcharger.bootstrap.wizard_cli_output import result_text

    print(result_text(preview))
    prompt = "Write config now and create backups of existing files?" if existing_files else "Write config now?"
    return prompt_yes_no(prompt, not bool(existing_files))


def _non_interactive_write_allowed(namespace: object, existing_files: tuple[str, ...]) -> bool:
    if not getattr(namespace, "non_interactive"):
        return False
    if existing_files and not getattr(namespace, "force"):
        raise ValueError(
            "Refusing to overwrite existing files in --non-interactive mode without --force: "
            + ", ".join(existing_files)
        )
    return True


def _skip_write_confirmation(namespace: object, existing_files: tuple[str, ...]) -> bool:
    if getattr(namespace, "dry_run"):
        return True
    if _non_interactive_write_allowed(namespace, existing_files):
        return True
    return bool(getattr(namespace, "yes"))


def _confirm_write(namespace: object, preview: WizardResult, existing_files: tuple[str, ...]) -> None:
    if _skip_write_confirmation(namespace, existing_files):
        return
    if not _interactive_write_confirmed(preview, existing_files):
        raise ValueError("Wizard write cancelled by user")

# SPDX-License-Identifier: GPL-3.0-or-later
"""Result and JSON helpers for the setup wizard runtime."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, cast

from venus_evcharger.bootstrap.wizard_models import WizardAnswers, WizardResult, WizardTransportKind
from venus_evcharger.bootstrap.wizard_persistence import persist_wizard_state
from venus_evcharger.bootstrap.wizard_render import answer_defaults
from venus_evcharger.bootstrap.wizard_support import transport_summary


def json_ready(value: object) -> object:
    """Convert wizard result fragments into JSON-serializable values."""
    if is_dataclass(value):
        return json_ready(asdict(cast(Any, value)))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return _json_ready_mapping(value)
    if isinstance(value, (list, tuple)):
        return _json_ready_sequence(value)
    return value


def _json_ready_mapping(value: dict[object, object]) -> dict[str, object]:
    """Return one JSON-ready mapping with stringified keys."""
    return {str(key): json_ready(item) for key, item in value.items()}


def _json_ready_sequence(value: list[object] | tuple[object, ...]) -> list[object]:
    """Return one JSON-ready list converted from a sequence."""
    return [json_ready(item) for item in value]


def preview_result(
    answers: WizardAnswers,
    config_path: Path,
    created_at: str,
    validation: dict[str, object],
    live_check_payload: dict[str, object] | None,
    topology_config_payload: dict[str, object],
    device_inventory_payload: dict[str, object],
    role_hosts: dict[str, str],
    generated_files: tuple[str, ...],
    warnings: tuple[str, ...],
    imported_from: str | None,
    dry_run: bool,
    manual_review: tuple[str, ...],
    suggested_blocks: dict[str, str],
    suggested_energy_sources: tuple[dict[str, object], ...],
    suggested_energy_merge: dict[str, object] | None,
) -> WizardResult:
    """Build one preview result before files are written."""
    return WizardResult(
        created_at=created_at,
        config_path=str(config_path),
        imported_from=imported_from,
        profile=answers.profile,
        policy_mode=answers.policy_mode,
        topology_preset=answers.topology_preset,
        charger_backend=answers.charger_backend,
        charger_preset=answers.charger_preset,
        transport_kind=cast(WizardTransportKind | None, transport_summary(answers.charger_backend, answers.transport_kind)),
        role_hosts=role_hosts,
        validation=validation,
        live_check=live_check_payload,
        warnings=warnings,
        answer_defaults=answer_defaults(answers),
        generated_files=generated_files,
        backup_files=tuple(),
        result_path=None,
        audit_path=None,
        topology_summary_path=None,
        inventory_path=None,
        manual_review=manual_review,
        dry_run=dry_run,
        topology_config=topology_config_payload,
        device_inventory=device_inventory_payload,
        suggested_blocks=suggested_blocks,
        suggested_energy_sources=suggested_energy_sources,
        suggested_energy_merge=suggested_energy_merge,
    )


def persisted_result(result: WizardResult) -> WizardResult:
    """Attach persisted wizard sidecar paths to one result."""
    result_path, audit_path, topology_summary_path = persist_wizard_state(Path(result.config_path), result.as_dict())
    return WizardResult(
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
        backup_files=result.backup_files,
        result_path=result_path,
        audit_path=audit_path,
        topology_summary_path=topology_summary_path,
        inventory_path=result.inventory_path,
        manual_review=result.manual_review,
        dry_run=result.dry_run,
        topology_config=result.topology_config,
        device_inventory=result.device_inventory,
        suggested_blocks=result.suggested_blocks,
        suggested_energy_sources=result.suggested_energy_sources,
        suggested_energy_merge=result.suggested_energy_merge,
    )

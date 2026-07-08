# SPDX-License-Identifier: GPL-3.0-or-later
"""Persistence step for completed wizard runtime previews."""

from __future__ import annotations

from pathlib import Path

from venus_evcharger.bootstrap.wizard_models import WizardResult
from venus_evcharger.bootstrap.wizard_persistence import persist_inventory_sidecar
from venus_evcharger.bootstrap.wizard_render import write_generated_files
from venus_evcharger.bootstrap.wizard_runtime_results import persisted_result


def wizard_persisted_result(
    result: WizardResult,
    config_path: Path,
    materialized_text: str,
    adapter_files: dict[str, str],
    inventory_sidecar_text: str,
) -> WizardResult:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    backup_files = tuple(write_generated_files(config_path, materialized_text, adapter_files))
    inventory_sidecar_path = persist_inventory_sidecar(config_path, inventory_sidecar_text)
    return persisted_result(
        WizardResult(
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
    )

# SPDX-License-Identifier: GPL-3.0-or-later
"""Import/default resolution helpers for the setup wizard CLI."""

from __future__ import annotations

import argparse
from pathlib import Path

from venus_evcharger.bootstrap.wizard_import import ImportedWizardDefaults, load_imported_defaults


def empty_imported_defaults() -> ImportedWizardDefaults:
    return ImportedWizardDefaults(
        imported_from="",
        profile=None,
        host_input=None,
        meter_host_input=None,
        switch_host_input=None,
        charger_host_input=None,
        device_instance=None,
        phase=None,
        policy_mode=None,
        digest_auth=None,
        username=None,
        password=None,
        topology_preset=None,
        charger_backend=None,
        charger_preset=None,
        request_timeout_seconds=None,
        switch_group_phase_layout=None,
        auto_start_surplus_watts=None,
        auto_stop_surplus_watts=None,
        auto_min_soc=None,
        auto_resume_soc=None,
        scheduled_enabled_days=None,
        scheduled_latest_end_time=None,
        scheduled_night_current_amps=None,
        transport_kind=None,
        transport_host=None,
        transport_port=None,
        transport_device=None,
        transport_unit_id=None,
    )


def resume_import_path(namespace: argparse.Namespace) -> Path | None:
    if not namespace.resume_last:
        return None
    candidate = Path(f"{namespace.config_path}.wizard-result.json")
    if not candidate.exists():
        raise ValueError(f"--resume-last requested but no prior wizard result exists: {candidate}")
    return candidate


def clone_import_path(namespace: argparse.Namespace) -> Path | None:
    if not namespace.clone_current:
        return None
    candidate = Path(namespace.config_path)
    if not candidate.exists():
        raise ValueError(f"--clone-current requested but config does not exist: {candidate}")
    return candidate


def resolve_import_path(namespace: argparse.Namespace) -> Path | None:
    if namespace.import_config:
        return Path(namespace.import_config)
    return resume_import_path(namespace) or clone_import_path(namespace)


def resolve_imported_defaults(namespace: argparse.Namespace) -> ImportedWizardDefaults | None:
    import_path = resolve_import_path(namespace)
    if import_path is None:
        return None
    return load_imported_defaults(import_path)

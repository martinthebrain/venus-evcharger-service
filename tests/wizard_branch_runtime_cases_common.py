# SPDX-License-Identifier: GPL-3.0-or-later
import argparse
import io
import json
import runpy
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from venus_evcharger.bootstrap import (
    wizard,
    wizard_cli,
    wizard_cli_imports,
    wizard_cli_interactive,
    wizard_cli_non_interactive,
    wizard_cli_prompts,
    wizard_main,
    wizard_render,
    wizard_render_secrets,
    wizard_runtime,
    wizard_runtime_live,
    wizard_runtime_write,
)
from venus_evcharger.bootstrap.wizard_guidance import _topology_choice_labels, prompt_topology_preset, resolved_primary_host
from venus_evcharger.bootstrap.wizard_import import (
    ImportedWizardDefaults,
    _adapter_path,
    _as_bool,
    _as_float,
    _as_int,
    _load_from_result_json,
    _policy_mode,
    _profile_defaults_from_types,
    _switch_group_member_host,
    load_imported_defaults,
)
from venus_evcharger.bootstrap.wizard_models import WizardResult
from venus_evcharger.bootstrap.wizard_persistence import _topology_summary_text
from venus_evcharger.bootstrap.wizard_policy_guidance import prompt_policy_defaults
from venus_evcharger.bootstrap.wizard_transport_guidance import preset_specific_defaults
from venus_evcharger.core.common_schedule import scheduled_mode_snapshot


def _imported_defaults(**overrides: object) -> ImportedWizardDefaults:
    values = {
        "imported_from": "",
        "profile": None,
        "host_input": None,
        "meter_host_input": None,
        "switch_host_input": None,
        "charger_host_input": None,
        "device_instance": None,
        "phase": None,
        "policy_mode": None,
        "digest_auth": None,
        "username": None,
        "password": None,
        "topology_preset": None,
        "charger_backend": None,
        "charger_preset": None,
        "request_timeout_seconds": None,
        "switch_group_phase_layout": None,
        "auto_start_surplus_watts": None,
        "auto_stop_surplus_watts": None,
        "auto_min_soc": None,
        "auto_resume_soc": None,
        "scheduled_enabled_days": None,
        "scheduled_latest_end_time": None,
        "scheduled_night_current_amps": None,
        "transport_kind": None,
        "transport_host": None,
        "transport_port": None,
        "transport_device": None,
        "transport_unit_id": None,
        "inventory_path": None,
    }
    values.update(overrides)
    return ImportedWizardDefaults(**values)


def _namespace(**overrides: object) -> argparse.Namespace:
    values = {
        "profile": None,
        "topology_preset": None,
        "charger_backend": None,
        "charger_preset": None,
        "host": None,
        "meter_host": None,
        "switch_host": None,
        "charger_host": None,
        "device_instance": None,
        "phase": None,
        "policy_mode": None,
        "transport": None,
        "transport_host": None,
        "transport_port": None,
        "transport_device": None,
        "transport_unit_id": None,
        "digest_auth": False,
        "username": None,
        "password": None,
        "import_config": None,
        "resume_last": False,
        "clone_current": False,
        "yes": False,
        "force": False,
        "dry_run": False,
        "json": False,
        "live_check": False,
        "probe_roles": None,
        "request_timeout_seconds": None,
        "switch_group_phase_layout": None,
        "auto_start_surplus_watts": None,
        "auto_stop_surplus_watts": None,
        "auto_min_soc": None,
        "auto_resume_soc": None,
        "scheduled_enabled_days": None,
        "scheduled_latest_end_time": None,
        "scheduled_night_current_amps": None,
        "non_interactive": False,
        "config_path": "/tmp/config.ini",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _result() -> WizardResult:
    return WizardResult(
        created_at="2026-04-20T02:53:57",
        config_path="/tmp/config.ini",
        imported_from=None,
        profile="simple_relay",
        policy_mode="manual",
        topology_preset=None,
        charger_backend=None,
        charger_preset=None,
        transport_kind=None,
        role_hosts={},
        validation={"resolved_roles": {"meter": False}},
        live_check=None,
        generated_files=("config.ini",),
        backup_files=tuple(),
        result_path=None,
        audit_path=None,
        topology_summary_path=None,
        manual_review=("Auth",),
        dry_run=False,
        warnings=tuple(),
        answer_defaults={},
    )


__all__ = [name for name in globals() if not name.startswith("__")]

# SPDX-License-Identifier: GPL-3.0-or-later
import argparse
import configparser
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import cast
from unittest.mock import patch

from venus_evcharger.bootstrap.wizard_inventory_cli import run_inventory_editor
from venus_evcharger.bootstrap.wizard_inventory_editor import (
    _binding_capability_kind_for_role,
    add_inventory_capability,
    add_inventory_device,
    add_inventory_profile,
    inventory_role_capability_choices,
    remove_inventory_binding,
    remove_inventory_binding_member,
    remove_inventory_device,
    set_inventory_device_endpoint,
    set_inventory_binding_member,
)
from venus_evcharger.bootstrap.wizard_inventory import build_wizard_inventory, inventory_payload, inventory_text
from venus_evcharger.bootstrap.wizard_inventory import (
    _endpoint,
    _measurement_profile_label,
    _phase_scope,
    _switch_group_phase_scope,
)
from venus_evcharger.bootstrap.wizard_inventory_prompts import (
    inventory_bool_field,
    inventory_choice_field,
    inventory_field,
    inventory_field_with_default,
    inventory_optional_field,
)
from venus_evcharger.bootstrap.wizard_inventory_support import (
    inventory_action_path,
    load_inventory,
    inventory_summary_text,
    parse_inventory_binding_role,
    parse_inventory_kind,
    parse_inventory_phases,
    parse_inventory_switching_mode,
)
from venus_evcharger.bootstrap import wizard
from venus_evcharger.bootstrap.wizard_inventory_cli_actions import run_simple_inventory_action
from venus_evcharger.bootstrap.wizard_inventory_cli_guided_binding import (
    maybe_replace_binding as _maybe_replace_binding,
    prompt_binding_choice as _prompt_binding_choice,
    validated_guided_binding as _validated_guided_binding,
    guided_inventory_edit_binding,
)
from venus_evcharger.bootstrap.wizard_inventory_cli_guided_common import (
    binding_label_default as _binding_label_default,
    binding_scope_default as _binding_scope_default,
)
from venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile import (
    guided_capability_flags as _guided_capability_flags,
    guided_inventory_add_profile,
    maybe_add_guided_device_and_binding as _maybe_add_guided_device_and_binding,
)
from venus_evcharger.bootstrap.wizard_topology import build_wizard_topology_config
from venus_evcharger.bootstrap.wizard_topology_render import render_adapter_files_from_topology
from venus_evcharger.bootstrap.wizard_runtime_results import json_ready
from venus_evcharger.bootstrap.wizard_models import WizardAnswers
from venus_evcharger.inventory import (
    DeviceCapability,
    DeviceInstance,
    DeviceInventory,
    DeviceProfile,
    RoleBinding,
    RoleBindingMember,
)
from venus_evcharger.topology.schema import (
    ActuatorConfig,
    ChargerConfig,
    EvChargerTopologyConfig,
    MeasurementConfig,
    PolicyConfig,
    TopologyConfig,
)


def _namespace(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "config_path": "/tmp/config.ini",
        "inventory_action": "show",
        "inventory_path": None,
        "inventory_kind": None,
        "inventory_profile_id": None,
        "inventory_label": None,
        "inventory_capability_id": None,
        "inventory_adapter_type": None,
        "inventory_supported_phases": None,
        "inventory_channel": None,
        "inventory_binding_role": None,
        "inventory_binding_id": None,
        "inventory_binding_label": None,
        "inventory_binding_phase_scope": None,
        "inventory_device_id": None,
        "inventory_member_phases": None,
        "inventory_endpoint": None,
        "inventory_switching_mode": None,
        "inventory_measures_power": False,
        "inventory_measures_energy": False,
        "inventory_supports_feedback": False,
        "inventory_supports_phase_selection": False,
        "inventory_vendor": None,
        "inventory_model": None,
        "inventory_description": None,
        "non_interactive": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _inventory_with_binding() -> DeviceInventory:
    profile = DeviceProfile(
        id="meter_profile",
        label="Meter profile",
        capabilities=(
            DeviceCapability(
                id="meter",
                kind="meter",
                adapter_type="template_meter",
                supported_phases=("L1", "L2", "L3"),
                measures_power=True,
                measures_energy=True,
            ),
        ),
    )
    return DeviceInventory(
        profiles=(profile,),
        devices=(
            DeviceInstance(id="meter_l1", profile_id="meter_profile", label="Meter L1"),
            DeviceInstance(id="meter_l2", profile_id="meter_profile", label="Meter L2"),
        ),
        bindings=(
            RoleBinding(
                id="measurement",
                role="measurement",
                label="Measurement",
                phase_scope=("L1", "L2"),
                members=(
                    RoleBindingMember(device_id="meter_l1", capability_id="meter", phases=("L1",)),
                    RoleBindingMember(device_id="meter_l2", capability_id="meter", phases=("L2",)),
                ),
            ),
        ),
    )


def parser_to_text(parser: configparser.ConfigParser) -> str:
    buffer = io.StringIO()
    parser.write(buffer)
    return buffer.getvalue()


if __name__ == "__main__":
    unittest.main()

__all__ = [name for name in globals() if not name.startswith("__")]

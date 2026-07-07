# SPDX-License-Identifier: GPL-3.0-or-later
"""Mutmut configuration helpers for the optional mutation audit."""

from __future__ import annotations

import contextlib
import re
from collections.abc import Iterator
from pathlib import Path

if __package__:
    from .mutation_audit_targets import DEFAULT_TEST_SELECTION
else:
    from mutation_audit_targets import DEFAULT_TEST_SELECTION

_FOCUSED_TEST_SELECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("venus_evcharger/app/", ("tests/test_app_bootstrap_support.py",)),
    ("scripts/dev/", ("tests/test_mutation_audit_script.py",)),
    ("venus_evcharger/topology/", ("tests/test_topology_config.py",)),
    (
        "venus_evcharger/backend/template_meter.py",
        (
            "tests/test_venus_evcharger_backend_template_meter.py",
            "tests/test_venus_evcharger_backend_template_support.py",
        ),
    ),
    (
        "venus_evcharger/backend/template_switch.py",
        (
            "tests/test_venus_evcharger_backend_template_switch.py",
            "tests/test_venus_evcharger_backend_template_support.py",
            "tests/test_venus_evcharger_backend_tuya.py",
        ),
    ),
    (
        "venus_evcharger/backend/template_charger.py",
        (
            "tests/test_venus_evcharger_backend_template_charger.py",
            "tests/test_venus_evcharger_backend_template_support.py",
        ),
    ),
    (
        "venus_evcharger/backend/template_support.py",
        (
            "tests/test_venus_evcharger_backend_template_support.py",
            "tests/test_venus_evcharger_backend_template_meter.py",
            "tests/test_venus_evcharger_backend_template_switch.py",
            "tests/test_venus_evcharger_backend_template_charger.py",
        ),
    ),
    (
        "venus_evcharger/backend/shelly_support.py",
        (
            "tests/test_venus_evcharger_backend_shelly_support.py",
            "tests/test_venus_evcharger_backend_shelly_meter.py",
            "tests/test_venus_evcharger_backend_switch.py",
        ),
    ),
    (
        "venus_evcharger/backend/shelly_io_runtime.py",
        (
            "tests/test_venus_evcharger_shelly_io_controller.py",
            "tests/test_venus_evcharger_backend_shelly_support.py",
            "tests/test_venus_evcharger_backend_shelly_meter.py",
        ),
    ),
    (
        "venus_evcharger/backend/shelly_meter.py",
        (
            "tests/test_venus_evcharger_backend_shelly_meter.py",
            "tests/test_venus_evcharger_backend_shelly_support.py",
        ),
    ),
    (
        "venus_evcharger/backend/shelly_switch.py",
        (
            "tests/test_venus_evcharger_backend_switch.py",
            "tests/test_venus_evcharger_backend_shelly_support.py",
        ),
    ),
    ("venus_evcharger/backend/base.py", ("tests/test_backend_base_contracts.py",)),
    (
        "venus_evcharger/backend/cerbo_gx_relay_switch.py",
        ("tests/test_venus_evcharger_backend_cerbo_gx_relay_switch.py",),
    ),
    (
        "venus_evcharger/backend/goe_charger.py",
        (
            "tests/test_venus_evcharger_backend_goe_charger.py",
            "tests/venus_evcharger_backend_factory_charger_cases.py",
            "tests/venus_evcharger_backend_probe_command_cases.py",
        ),
    ),
    (
        "venus_evcharger/backend/modbus_charger.py",
        (
            "tests/test_venus_evcharger_backend_modbus_charger.py",
            "tests/test_venus_evcharger_backend_modbus_profiles.py",
            "tests/venus_evcharger_backend_factory_charger_cases.py",
        ),
    ),
    (
        "venus_evcharger/backend/modbus_client.py",
        ("tests/test_venus_evcharger_backend_modbus_client.py",),
    ),
    (
        "venus_evcharger/backend/modbus_profile_models.py",
        (
            "tests/test_venus_evcharger_backend_modbus_profiles.py",
            "tests/test_venus_evcharger_backend_modbus_charger.py",
        ),
    ),
    (
        "venus_evcharger/backend/modbus_profiles.py",
        (
            "tests/test_venus_evcharger_backend_modbus_profiles.py",
            "tests/test_venus_evcharger_backend_modbus_charger.py",
            "tests/venus_evcharger_backend_factory_charger_cases.py",
        ),
    ),
    (
        "venus_evcharger/backend/modbus_transport.py",
        ("tests/test_venus_evcharger_backend_modbus_transport.py",),
    ),
    (
        "venus_evcharger/backend/modbus_transport_config.py",
        ("tests/test_venus_evcharger_backend_modbus_transport.py",),
    ),
    (
        "venus_evcharger/backend/models.py",
        ("tests/test_venus_evcharger_backend_models.py",),
    ),
    (
        "venus_evcharger/backend/native_modbus_backend.py",
        ("tests/test_native_modbus_backend_contracts.py",),
    ),
    (
        "venus_evcharger/backend/shelly_contactor_switch.py",
        ("tests/test_shelly_contactor_switch_contracts.py",),
    ),
    (
        "venus_evcharger/backend/shelly_io.py",
        ("tests/test_venus_evcharger_shelly_io_controller.py",),
    ),
    (
        "venus_evcharger/backend/shelly_io_capabilities.py",
        ("tests/test_venus_evcharger_shelly_io_controller.py",),
    ),
    (
        "venus_evcharger/backend/shelly_io_requests.py",
        ("tests/test_venus_evcharger_shelly_io_controller.py",),
    ),
    (
        "venus_evcharger/backend/shelly_io_runtime_cache.py",
        ("tests/test_venus_evcharger_shelly_io_controller.py",),
    ),
    (
        "venus_evcharger/backend/shelly_io_split.py",
        ("tests/test_venus_evcharger_shelly_io_controller.py",),
    ),
    (
        "venus_evcharger/backend/shelly_io_types.py",
        ("tests/test_venus_evcharger_backend_shelly_support.py",),
    ),
    (
        "venus_evcharger/backend/shelly_io_worker.py",
        (
            "tests/test_venus_evcharger_shelly_io_controller.py",
            "tests/test_venus_evcharger_backend_shelly_support.py",
        ),
    ),
    (
        "venus_evcharger/backend/shelly_io_worker_lifecycle.py",
        ("tests/test_venus_evcharger_shelly_io_controller.py",),
    ),
    (
        "venus_evcharger/backend/shelly_io_worker_transport.py",
        ("tests/test_venus_evcharger_shelly_io_controller.py",),
    ),
    (
        "venus_evcharger/backend/shelly_profiles.py",
        ("tests/test_venus_evcharger_backend_shelly_support.py",),
    ),
    (
        "venus_evcharger/backend/shelly_support_phase.py",
        ("tests/test_venus_evcharger_backend_shelly_support.py",),
    ),
    (
        "venus_evcharger/backend/simpleevse_charger.py",
        ("tests/test_venus_evcharger_backend_simpleevse_charger.py",),
    ),
    (
        "venus_evcharger/backend/smartevse_charger.py",
        ("tests/test_venus_evcharger_backend_smartevse_charger.py",),
    ),
    (
        "venus_evcharger/backend/switch_group.py",
        ("tests/test_venus_evcharger_backend_switch.py",),
    ),
    (
        "venus_evcharger/backend/tasmota_meter.py",
        ("tests/test_venus_evcharger_backend_tasmota.py",),
    ),
    (
        "venus_evcharger/backend/tasmota_switch.py",
        ("tests/test_venus_evcharger_backend_tasmota.py",),
    ),
    (
        "venus_evcharger/backend/template_charger_contract.py",
        ("tests/test_venus_evcharger_backend_template_charger.py",),
    ),
    (
        "venus_evcharger/backend/template_meter_contract.py",
        ("tests/test_venus_evcharger_backend_template_meter.py",),
    ),
    (
        "venus_evcharger/backend/template_support_contract.py",
        ("tests/test_venus_evcharger_backend_template_support.py",),
    ),
    (
        "venus_evcharger/backend/template_switch_contract.py",
        ("tests/test_venus_evcharger_backend_template_switch.py",),
    ),
    (
        "venus_evcharger/backend/tuya_meter.py",
        ("tests/test_venus_evcharger_backend_tuya.py",),
    ),
    (
        "venus_evcharger/backend/tuya_switch.py",
        ("tests/test_venus_evcharger_backend_tuya.py",),
    ),
    (
        "venus_evcharger/bootstrap/config_auto.py",
        ("tests/test_bootstrap_config_auto_contracts.py",),
    ),
    (
        "venus_evcharger/bootstrap/config_auto_daytime.py",
        ("tests/test_bootstrap_config_auto_contracts.py",),
    ),
    (
        "venus_evcharger/bootstrap/config_auto_helper.py",
        ("tests/test_bootstrap_config_auto_contracts.py",),
    ),
    (
        "venus_evcharger/bootstrap/config_auto_helper_gateway.py",
        ("tests/test_bootstrap_config_auto_contracts.py",),
    ),
    (
        "venus_evcharger/bootstrap/config_auto_helper_polling.py",
        ("tests/test_bootstrap_config_auto_contracts.py",),
    ),
    (
        "venus_evcharger/bootstrap/config_auto_helper_resilience.py",
        ("tests/test_bootstrap_config_auto_contracts.py",),
    ),
    (
        "venus_evcharger/bootstrap/config_auto_sources.py",
        ("tests/test_bootstrap_config_auto_contracts.py",),
    ),
    (
        "venus_evcharger/bootstrap/config_auto_sources_battery.py",
        ("tests/test_bootstrap_config_auto_contracts.py",),
    ),
    (
        "venus_evcharger/bootstrap/config_auto_sources_energy.py",
        ("tests/test_bootstrap_config_auto_contracts.py",),
    ),
    (
        "venus_evcharger/bootstrap/config_auto_sources_grid.py",
        ("tests/test_bootstrap_config_auto_contracts.py",),
    ),
    (
        "venus_evcharger/bootstrap/config_auto_sources_pv.py",
        ("tests/test_bootstrap_config_auto_contracts.py",),
    ),
    (
        "venus_evcharger/bootstrap/config_auto_timing.py",
        ("tests/test_bootstrap_config_auto_contracts.py",),
    ),
    (
        "venus_evcharger/bootstrap/config_auto_timing_audit.py",
        ("tests/test_bootstrap_config_auto_contracts.py",),
    ),
    (
        "venus_evcharger/bootstrap/config_auto_timing_balance.py",
        ("tests/test_bootstrap_config_auto_contracts.py",),
    ),
    (
        "venus_evcharger/bootstrap/config_auto_timing_core.py",
        ("tests/test_bootstrap_config_auto_contracts.py",),
    ),
    (
        "venus_evcharger/bootstrap/config_auto_timing_victron_bias.py",
        ("tests/test_bootstrap_config_auto_contracts.py",),
    ),
    (
        "venus_evcharger/bootstrap/config_auto_timing_victron_bias_apply.py",
        ("tests/test_bootstrap_config_auto_contracts.py",),
    ),
    (
        "venus_evcharger/bootstrap/config_auto_timing_victron_bias_base.py",
        ("tests/test_bootstrap_config_auto_contracts.py",),
    ),
    (
        "venus_evcharger/bootstrap/config_auto_timing_victron_bias_pid.py",
        ("tests/test_bootstrap_config_auto_contracts.py",),
    ),
    (
        "venus_evcharger/bootstrap/config_auto_timing_victron_bias_safety.py",
        ("tests/test_bootstrap_config_auto_contracts.py",),
    ),
    (
        "venus_evcharger/bootstrap/config_backend.py",
        ("tests/test_venus_evcharger_bootstrap_controller.py",),
    ),
    (
        "venus_evcharger/bootstrap/config_shared.py",
        ("tests/venus_evcharger_bootstrap_controller_config_cases.py",),
    ),
    (
        "venus_evcharger/bootstrap/controller.py",
        ("tests/test_venus_evcharger_bootstrap_controller.py",),
    ),
    (
        "venus_evcharger/bootstrap/paths.py",
        ("tests/venus_evcharger_bootstrap_controller_path_cases.py",),
    ),
    (
        "venus_evcharger/bootstrap/runtime.py",
        ("tests/venus_evcharger_bootstrap_controller_runtime_cases.py",),
    ),
    (
        "venus_evcharger/bootstrap/runtime_controllers.py",
        ("tests/test_bootstrap_runtime_controllers.py",),
    ),
    (
        "venus_evcharger/bootstrap/runtime_loops.py",
        ("tests/test_bootstrap_runtime_loops.py",),
    ),
    (
        "venus_evcharger/bootstrap/runtime_metadata.py",
        ("tests/test_bootstrap_runtime_metadata.py",),
    ),
    (
        "venus_evcharger/bootstrap/runtime_virtual_state.py",
        ("tests/test_bootstrap_runtime_virtual_state.py",),
    ),
    (
        "venus_evcharger/bootstrap/wizard.py",
        (
            "tests/test_venus_evcharger_setup_wizard.py",
            "tests/test_venus_evcharger_setup_wizard_extensions.py",
            "tests/test_venus_evcharger_setup_wizard_branch_runtime.py",
            "tests/test_bootstrap_wizard_main.py",
        ),
    ),
    (
        "venus_evcharger/bootstrap/wizard_main.py",
        (
            "tests/test_venus_evcharger_setup_wizard.py",
            "tests/test_venus_evcharger_setup_wizard_extensions.py",
            "tests/test_venus_evcharger_setup_wizard_branch_runtime.py",
            "tests/test_branch_coverage_next_cluster_six.py",
            "tests/test_bootstrap_wizard_main.py",
        ),
    ),
    (
        "venus_evcharger/bootstrap/wizard_runtime.py",
        (
            "tests/test_venus_evcharger_setup_wizard.py",
            "tests/test_venus_evcharger_setup_wizard_extensions.py",
            "tests/test_venus_evcharger_setup_wizard_branch_runtime.py",
        ),
    ),
    (
        "venus_evcharger/bootstrap/wizard_adapters.py",
        (
            "tests/test_venus_evcharger_setup_wizard.py",
            "tests/test_venus_evcharger_setup_wizard_extensions.py",
            "tests/test_venus_evcharger_setup_wizard_branch_coverage.py",
            "tests/test_bootstrap_wizard_adapters_contracts.py",
        ),
    ),
    (
        "venus_evcharger/bootstrap/wizard_capacity.py",
        (
            "tests/test_venus_evcharger_setup_wizard_branch_runtime.py",
            "tests/test_branch_coverage_next_cluster_six.py",
            "tests/test_bootstrap_wizard_capacity.py",
        ),
    ),
    (
        "venus_evcharger/bootstrap/wizard_charger_presets.py",
        (
            "tests/test_venus_evcharger_setup_wizard_branch_coverage.py",
            "tests/test_bootstrap_wizard_charger_presets.py",
        ),
    ),
    (
        "venus_evcharger/bootstrap/wizard_choices.py",
        ("tests/test_bootstrap_wizard_choices.py",),
    ),
    (
        "venus_evcharger/bootstrap/wizard_cli.py",
        (
            "tests/test_venus_evcharger_setup_wizard.py",
            "tests/test_venus_evcharger_setup_wizard_extensions.py",
            "tests/test_venus_evcharger_setup_wizard_branch_runtime.py",
            "tests/test_branch_coverage_next_cluster_seven.py",
            "tests/test_bootstrap_wizard_cli_facade.py",
        ),
    ),
    (
        "venus_evcharger/bootstrap/wizard_cli_prompts.py",
        ("tests/test_bootstrap_wizard_cli_prompts.py",),
    ),
    (
        "venus_evcharger/bootstrap/wizard_cli_interactive.py",
        (
            "tests/test_venus_evcharger_setup_wizard.py",
            "tests/test_venus_evcharger_setup_wizard_extensions.py",
            "tests/test_venus_evcharger_setup_wizard_branch_runtime.py",
            "tests/test_bootstrap_wizard_cli_interactive_contracts.py",
        ),
    ),
    (
        "venus_evcharger/bootstrap/wizard_cli_imports.py",
        ("tests/test_bootstrap_wizard_cli_imports.py",),
    ),
    (
        "venus_evcharger/bootstrap/wizard_cli_non_interactive.py",
        (
            "tests/test_venus_evcharger_setup_wizard.py",
            "tests/test_venus_evcharger_setup_wizard_extensions.py",
            "tests/test_venus_evcharger_setup_wizard_branch_runtime.py",
            "tests/test_branch_coverage_next_cluster_seven.py",
            "tests/test_bootstrap_wizard_cli_non_interactive_contracts.py",
        ),
    ),
    (
        "venus_evcharger/bootstrap/wizard_cli_output.py",
        (
            "tests/test_venus_evcharger_setup_wizard_branch_coverage.py",
            "tests/test_branch_coverage_next_cluster.py",
            "tests/test_bootstrap_wizard_cli_output_contracts.py",
        ),
    ),
    (
        "venus_evcharger/bootstrap/wizard_cli_output_next.py",
        (
            "tests/test_venus_evcharger_setup_wizard_branch_coverage.py",
            "tests/test_bootstrap_wizard_cli_output_next_contracts.py",
        ),
    ),
    (
        "venus_evcharger/bootstrap/wizard_cli_parser.py",
        ("tests/test_bootstrap_wizard_cli_parser.py",),
    ),
    (
        "venus_evcharger/bootstrap/wizard_energy.py",
        (
            "tests/test_branch_coverage_hotspots.py",
            "tests/test_venus_evcharger_setup_wizard.py",
            "tests/test_bootstrap_wizard_energy_contracts.py",
        ),
    ),
    (
        "venus_evcharger/bootstrap/wizard_guidance.py",
        (
            "tests/test_venus_evcharger_setup_wizard_branch_coverage.py",
            "tests/test_venus_evcharger_setup_wizard_branch_runtime.py",
            "tests/test_bootstrap_wizard_guidance_contracts.py",
        ),
    ),
    (
        "venus_evcharger/bootstrap/wizard_import.py",
        ("tests/test_bootstrap_wizard_import_contracts.py",),
    ),
    (
        "venus_evcharger/bootstrap/wizard_inventory.py",
        ("tests/test_bootstrap_wizard_inventory_contracts.py",),
    ),
    (
        "venus_evcharger/bootstrap/wizard_inventory_cli.py",
        ("tests/test_bootstrap_wizard_inventory_cli_contracts.py",),
    ),
    (
        "venus_evcharger/bootstrap/wizard_inventory_cli_actions.py",
        ("tests/test_bootstrap_wizard_inventory_cli_actions_contracts.py",),
    ),
    (
        "venus_evcharger/bootstrap/wizard_inventory_cli_payload.py",
        ("tests/test_bootstrap_wizard_inventory_cli_payload_contracts.py",),
    ),
    (
        "venus_evcharger/bootstrap/wizard_inventory_cli_guided_common.py",
        ("tests/test_bootstrap_wizard_inventory_cli_guided_common_contracts.py",),
    ),
    (
        "venus_evcharger/bootstrap/wizard_inventory_cli_guided_profile_specs.py",
        ("tests/test_bootstrap_wizard_inventory_cli_guided_profile_contracts.py",),
    ),
    (
        "venus_evcharger/bootstrap/wizard_inventory_cli_guided_profile.py",
        ("tests/test_bootstrap_wizard_inventory_cli_guided_profile_contracts.py",),
    ),
    (
        "venus_evcharger/bootstrap/wizard_inventory_cli_guided_binding.py",
        ("tests/test_wizard_inventory_helpers.py",),
    ),
    (
        "venus_evcharger/bootstrap/path_defaults.py",
        ("tests/test_bootstrap_path_defaults.py",),
    ),
    (
        "venus_evcharger/bootstrap/path_groups.py",
        ("tests/test_bootstrap_path_groups.py",),
    ),
    (
        "venus_evcharger/auto/",
        (
            "tests/test_auto_battery_balance_contracts.py",
            "tests/test_auto_logic_decisions_preaverage.py",
            "tests/test_auto_logic_gates_metrics.py",
            "tests/test_auto_logic_types.py",
            "tests/test_auto_policy_builders.py",
            "tests/test_auto_tracking.py",
            "tests/test_venus_evcharger_auto_policy.py",
            "tests/test_venus_evcharger_auto_controller.py",
            "tests/venus_evcharger_auto_controller_cases_primary.py",
            "tests/venus_evcharger_auto_controller_cases_recovery.py",
        ),
    ),
    (
        "venus_evcharger/inventory/",
        (
            "tests/test_device_inventory_config.py",
            "tests/test_wizard_inventory_helpers.py",
        ),
    ),
    (
        "venus_evcharger/backend/",
        (
            "tests/test_backend_config_file.py",
            "tests/test_venus_evcharger_backend_factory.py",
            "tests/test_venus_evcharger_backend_probe.py",
            "tests/test_backend_factory_probe_contracts.py",
            "tests/test_topology_config.py",
        ),
    ),
    ("venus_evcharger/bootstrap/", ("tests/test_venus_evcharger_bootstrap_controller.py",)),
)


@contextlib.contextmanager
def mutmut_config_for_target(repo: Path, target_path: str) -> Iterator[None]:
    pyproject = repo / "pyproject.toml"
    original = pyproject.read_text(encoding="utf-8")
    pyproject.write_text(pyproject_with_mutmut_config(original, target_path), encoding="utf-8")
    try:
        yield
    finally:
        pyproject.write_text(original, encoding="utf-8")


def pyproject_with_mutmut_config(original: str, target_path: str) -> str:
    return f"{strip_tool_mutmut_section(original)}\n{mutmut_config_toml(target_path)}"


def strip_tool_mutmut_section(content: str) -> str:
    return re.sub(r"(?ms)^\[tool\.mutmut\]\n.*?(?=^\[|\Z)", "", content).strip() + "\n"


def mutmut_config_toml(target_path: str) -> str:
    test_selection = test_selection_for_target(target_path)
    lines = [
        "[tool.mutmut]",
        f'source_paths = ["{source_path_for_target(target_path)}"]',
        f'only_mutate = ["{target_path}"]',
        "also_copy = [",
        '    "venus_evcharger_auto_input_helper.py",',
        '    "venus_evcharger_dbus_adapter.py",',
        '    "venus_evcharger_service.py",',
        '    "CONTROL_API.md",',
        '    "deploy/venus",',
        "]",
        'pytest_add_cli_args = ["-k", "not socket"]',
        "pytest_add_cli_args_test_selection = [",
    ]
    lines.extend(f'    "{path}",' for path in test_selection)
    return "\n".join([*lines, "]", ""])


def source_path_for_target(target_path: str) -> str:
    if target_path.startswith("scripts/dev/"):
        return "scripts/dev"
    top_level, _separator, _remainder = target_path.partition("/")
    return top_level


def test_selection_for_target(target_path: str) -> tuple[str, ...]:
    for prefix, selection in _FOCUSED_TEST_SELECTIONS:
        if target_path.startswith(prefix):
            return selection
    return DEFAULT_TEST_SELECTION

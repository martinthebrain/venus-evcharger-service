# SPDX-License-Identifier: GPL-3.0-or-later
"""Focused mutmut test selections for energy and input-pipeline mutation selections."""

from __future__ import annotations


FOCUSED_TEST_SELECTIONS_ENERGY_INPUTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("venus_evcharger/energy/config.py", ("tests/test_energy_config_contracts.py",)),
    ("venus_evcharger/energy/aggregate.py", ("tests/test_venus_evcharger_energy_aggregate.py",)),
    ("venus_evcharger/energy/bounded_subprocess.py", ("tests/test_external_energy_io_boundaries.py",)),
    ("venus_evcharger/energy/connectors.py", ("tests/test_energy_connectors_facade_contracts.py",)),
    ("venus_evcharger/energy/connectors_command.py", ("tests/test_energy_connectors_command_contracts.py",)),
    ("venus_evcharger/energy/connectors_common.py", ("tests/test_energy_connectors_common_contracts.py",)),
    ("venus_evcharger/energy/connectors_modbus.py", ("tests/test_energy_connectors_modbus_contracts.py",)),
    ("venus_evcharger/energy/connectors_opendtu.py", ("tests/test_energy_connectors_opendtu_contracts.py",)),
    ("venus_evcharger/energy/connectors_opendtu_payload.py", ("tests/test_energy_connectors_opendtu_contracts.py",)),
    ("venus_evcharger/energy/connectors_template.py", ("tests/test_energy_connectors_template_contracts.py",)),
    ("venus_evcharger/energy/http_session.py", ("tests/test_external_energy_io_boundaries.py",)),
    ("venus_evcharger/energy/learning.py", ("tests/test_venus_evcharger_energy_aggregate.py",)),
    ("venus_evcharger/energy/learning_coercion.py", ("tests/test_energy_learning_coercion_contracts.py",)),
    (
        "venus_evcharger/energy/models.py",
        (
            "tests/test_venus_evcharger_energy_aggregate.py",
            "tests/test_energy_learning_coercion_contracts.py",
        ),
    ),
    ("venus_evcharger/energy/physical_sources.py", ("tests/test_energy_physical_source_contracts.py",)),
    (
        "venus_evcharger/energy/probe.py",
        (
            "tests/test_energy_probe_facade_contracts.py",
            "tests/test_venus_evcharger_energy_probe.py",
        ),
    ),
    ("venus_evcharger/energy/probe_cli.py", ("tests/test_energy_probe_cli_contracts.py",)),
    ("venus_evcharger/energy/probe_core.py", ("tests/test_energy_probe_core_contracts.py",)),
    ("venus_evcharger/energy/probe_huawei.py", ("tests/test_energy_probe_huawei_contracts.py",)),
    ("venus_evcharger/energy/profiles.py", ("tests/test_energy_profiles_contracts.py",)),
    (
        "venus_evcharger/energy/read_steps.py",
        (
            "tests/test_energy_connectors_facade_contracts.py",
            "tests/test_auto_input_external_scheduler_contracts.py",
        ),
    ),
    ("venus_evcharger/energy/recommendation_schema.py", ("tests/test_energy_recommendation_schema_contracts.py",)),
    ("venus_evcharger/inputs/helper/capacity_persistence.py", ("tests/test_auto_input_capacity_persistence.py",)),
    (
        "venus_evcharger/inputs/helper/config_runtime.py",
        ("tests/test_auto_input_helper_config_contracts.py", "tests/test_venus_evcharger_auto_input_helper.py"),
    ),
    (
        "venus_evcharger/inputs/helper/external_pv_projection.py",
        ("tests/test_auto_input_external_sources_contracts.py",),
    ),
    (
        "venus_evcharger/inputs/helper/external_soc.py",
        (
            "tests/test_auto_input_external_sources_contracts.py",
            "tests/test_auto_input_external_energy_scenario.py",
        ),
    ),
    ("venus_evcharger/inputs/helper/glib_runtime.py", ("tests/test_auto_input_helper_glib_runtime_contracts.py",)),
    (
        "venus_evcharger/inputs/helper/liveness.py",
        (
            "tests/test_auto_input_helper_snapshot_liveness_contracts.py",
            "tests/test_venus_evcharger_auto_input_helper.py",
        ),
    ),
    ("venus_evcharger/inputs/helper/snapshot.py", ("tests/test_auto_input_helper_snapshot_liveness_contracts.py",)),
    (
        "venus_evcharger/inputs/helper/snapshot_builder.py",
        ("tests/test_auto_input_helper_snapshot_liveness_contracts.py",),
    ),
    (
        "venus_evcharger/inputs/helper/snapshot_defaults.py",
        ("tests/test_auto_input_helper_snapshot_liveness_contracts.py",),
    ),
    (
        "venus_evcharger/inputs/helper/sources.py",
        (
            "tests/test_auto_input_helper_sources_contracts.py",
            "tests/test_auto_input_helper_snapshot_liveness_contracts.py",
        ),
    ),
    (
        "venus_evcharger/inputs/helper/energy_gateway.py",
        ("tests/test_auto_input_helper_gateway_contracts.py", "tests/test_auto_input_helper_refresh_contracts.py"),
    ),
    ("venus_evcharger/inputs/supervisor.py", ("tests/test_venus_evcharger_auto_input_supervisor.py",)),
    (
        "venus_evcharger/inputs/supervisor_process.py",
        (
            "tests/test_auto_input_supervisor_process_contracts.py",
            "tests/test_venus_evcharger_auto_input_supervisor.py",
        ),
    ),
    (
        "venus_evcharger/inputs/supervisor_snapshot_runtime.py",
        (
            "tests/test_auto_input_supervisor_snapshot_runtime_contracts.py",
            "tests/test_venus_evcharger_auto_input_supervisor.py",
        ),
    ),
    (
        "venus_evcharger/inputs/supervisor_snapshot_validation.py",
        ("tests/test_auto_input_supervisor_snapshot_validation_contracts.py",),
    ),
    (
        "venus_evcharger/inputs/supervisor_snapshot_values.py",
        ("tests/test_auto_input_supervisor_snapshot_validation_contracts.py",),
    ),
)

# SPDX-License-Identifier: GPL-3.0-or-later
"""Focused mutmut test selections for application, controller, and core-domain mutation selections."""

from __future__ import annotations


FOCUSED_TEST_SELECTIONS_APPLICATION: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("venus_evcharger/app/", ("tests/test_app_bootstrap_support.py",)),
    ("scripts/dev/", ("tests/test_mutation_audit_script.py",)),
    ("venus_evcharger_auto_input_helper.py", ("tests/test_venus_evcharger_auto_input_helper.py",)),
    (
        "venus_evcharger/energy/grid_fusion",
        ("tests/test_grid_measurement_fusion.py", "tests/test_grid_fusion_timestamp_contracts.py"),
    ),
    (
        "venus_evcharger/energy/timestamped_measurement.py",
        ("tests/test_timestamped_measurement_contracts.py",),
    ),
    (
        "venus_evcharger/inputs/helper/grid_fusion_snapshot.py",
        ("tests/test_grid_measurement_fusion.py", "tests/test_grid_fusion_timestamp_contracts.py"),
    ),
    ("venus_evcharger/backend/modbus_transport_serial.py", ("tests/test_venus_evcharger_backend_modbus_transport.py",)),
    ("venus_evcharger/backend/shelly_io_worker_status.py", ("tests/test_venus_evcharger_backend_shelly_support.py",)),
    ("venus_evcharger/bootstrap/wizard_energy_bundle.py", ("tests/test_bootstrap_wizard_energy_contracts.py",)),
    ("venus_evcharger/control/http_api.py", ("tests/test_control_http_lifecycle_contracts.py",)),
    ("venus_evcharger/control/http_api_events.py", ("tests/test_control_http_events_contracts.py",)),
    ("venus_evcharger/control/service.py", ("tests/test_control_service_contracts.py",)),
    ("venus_evcharger/controllers/auto.py", ("tests/test_auto_controller_facade_contracts.py",)),
    ("venus_evcharger/controllers/state.py", ("tests/test_state_controller_config_contracts.py",)),
    ("venus_evcharger/controllers/state_config.py", ("tests/test_state_controller_config_contracts.py",)),
    ("venus_evcharger/controllers/state_json.py", ("tests/test_state_json_contracts.py",)),
    ("venus_evcharger/controllers/state_persistence.py", ("tests/test_state_persistence_contracts.py",)),
    (
        "venus_evcharger/controllers/state_restore.py",
        ("tests/test_venus_evcharger_state_controller.py", "tests/test_state_restore_contracts.py"),
    ),
    (
        "venus_evcharger/controllers/state_restore_victron_ess.py",
        ("tests/test_state_restore_victron_ess_contracts.py",),
    ),
    ("venus_evcharger/controllers/state_runtime_normalize.py", ("tests/test_state_runtime_normalize_contracts.py",)),
    ("venus_evcharger/controllers/state_runtime_overrides.py", ("tests/test_state_runtime_overrides_contracts.py",)),
    (
        "venus_evcharger/controllers/state_runtime_snapshot.py",
        (
            "tests/test_state_runtime_snapshot_contracts.py",
            "tests/test_state_runtime_snapshot_defaults_contracts.py",
            "tests/venus_evcharger_state_controller_cases_primary.py",
            "tests/venus_evcharger_state_controller_cases_quaternary.py",
        ),
    ),
    (
        "venus_evcharger/controllers/state_runtime_snapshot_victron.py",
        (
            "tests/test_state_runtime_snapshot_contracts.py",
            "tests/test_state_runtime_snapshot_defaults_contracts.py",
            "tests/test_branch_coverage_next_cluster_two.py",
        ),
    ),
    ("venus_evcharger/controllers/state_specs.py", ("tests/test_state_runtime_overrides_contracts.py",)),
    (
        "venus_evcharger/controllers/state_summary.py",
        (
            "tests/test_state_summary_contracts.py",
            "tests/test_state_summary_edge_contracts.py",
            "tests/venus_evcharger_state_controller_cases_primary.py",
            "tests/venus_evcharger_state_controller_cases_quaternary.py",
        ),
    ),
    (
        "venus_evcharger/controllers/state_validation.py",
        (
            "tests/test_state_validation_contracts.py",
            "tests/test_state_validation_logging_contracts.py",
            "tests/venus_evcharger_state_controller_cases_tertiary.py",
        ),
    ),
    (
        "venus_evcharger/controllers/write.py",
        (
            "tests/test_write_controller_contracts.py",
            "tests/test_write_controller_handler_contracts.py",
            "tests/test_venus_evcharger_write_controller.py",
        ),
    ),
    (
        "venus_evcharger/controllers/write_snapshot.py",
        (
            "tests/test_write_snapshot_contracts.py",
            "tests/test_venus_evcharger_write_controller.py",
            "tests/test_remaining_coverage_helpers.py",
        ),
    ),
    (
        "venus_evcharger/controllers/write_support.py",
        (
            "tests/test_write_support_contracts.py",
            "tests/test_write_controller_handler_contracts.py",
            "tests/test_venus_evcharger_write_controller.py",
        ),
    ),
    ("venus_evcharger/auto/logic_samples.py", ("tests/test_auto_backend_bootstrap_edge_contracts.py",)),
    (
        "venus_evcharger/core/common.py",
        ("tests/test_core_common_boundary_contracts.py", "tests/test_venus_evcharger_common.py"),
    ),
    (
        "venus_evcharger/core/common_auto.py",
        ("tests/test_core_common_auto_contracts.py", "tests/test_venus_evcharger_common.py"),
    ),
    (
        "venus_evcharger/core/common_schedule.py",
        ("tests/test_core_common_schedule_contracts.py", "tests/test_venus_evcharger_common.py"),
    ),
    (
        "venus_evcharger/core/common_types.py",
        ("tests/test_core_common_types_contracts.py", "tests/test_venus_evcharger_common.py"),
    ),
    (
        "venus_evcharger/core/common_values.py",
        ("tests/test_core_common_values_contracts.py", "tests/test_venus_evcharger_common.py"),
    ),
    (
        "venus_evcharger/core/contracts_basic.py",
        ("tests/test_core_contracts_basic_contracts.py", "tests/test_venus_evcharger_common.py"),
    ),
    (
        "venus_evcharger/core/contracts_bootstrap.py",
        ("tests/test_core_contracts_bootstrap_contracts.py", "tests/test_bootstrap_contracts.py"),
    ),
    (
        "venus_evcharger/core/contracts_control.py",
        (
            "tests/test_core_contracts_control_command_contracts.py",
            "tests/test_core_contracts_control_api_contracts.py",
            "tests/test_venus_evcharger_control_contracts.py",
        ),
    ),
    (
        "venus_evcharger/core/contracts_outward.py",
        ("tests/test_core_contracts_outward_contracts.py", "tests/test_venus_evcharger_common.py"),
    ),
    (
        "venus_evcharger/core/contracts_snapshot.py",
        ("tests/test_core_contracts_snapshot_contracts.py", "tests/test_venus_evcharger_common.py"),
    ),
    (
        "venus_evcharger/core/contracts_state_endpoints.py",
        ("tests/test_core_contracts_state_endpoints_contracts.py", "tests/test_venus_evcharger_state_contracts.py"),
    ),
    ("venus_evcharger/core/contracts_state_operational.py", ("tests/test_venus_evcharger_state_contracts.py",)),
    (
        "venus_evcharger/core/contracts_state_shared.py",
        (
            "tests/test_core_contracts_state_shared_contracts.py",
            "tests/test_core_contracts_state_endpoints_contracts.py",
            "tests/test_venus_evcharger_state_contracts.py",
        ),
    ),
    ("venus_evcharger/core/return_contracts.py", ("tests/test_core_return_contracts.py",)),
    ("venus_evcharger/core/shared.py", ("tests/test_core_shared_contracts.py", "tests/test_venus_evcharger_shared.py")),
)

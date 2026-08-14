# SPDX-License-Identifier: GPL-3.0-or-later
"""Focused mutmut test selections for runtime, operations, ports, and service mutation selections."""

from __future__ import annotations


FOCUSED_TEST_SELECTIONS_RUNTIME_SERVICES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "venus_evcharger/inventory/config.py",
        ("tests/test_device_inventory_config.py", "tests/test_inventory_config_contracts.py"),
    ),
    ("venus_evcharger/inventory/render.py", ("tests/test_device_inventory_config.py",)),
    ("venus_evcharger/inventory/schema.py", ("tests/test_device_inventory_config.py",)),
    ("venus_evcharger/ops/disable_generic_shelly_once.py", ("tests/test_disable_generic_shelly_once_contracts.py",)),
    ("venus_evcharger/ports/auto.py", ("tests/test_venus_evcharger_ports.py", "tests/test_ports_auto_contracts.py")),
    ("venus_evcharger/ports/gateway_diagnostics.py", ("tests/test_gateway_diagnostics_contracts.py",)),
    ("venus_evcharger/ports/gateway_diagnostic_values.py", ("tests/test_gateway_diagnostics_contracts.py",)),
    ("venus_evcharger/ports/gateway_diagnostics_validation.py", ("tests/test_gateway_diagnostics_contracts.py",)),
    ("venus_evcharger/ports/gateway_operations.py", ("tests/test_gateway_semantic_operations.py",)),
    ("venus_evcharger/ports/gateway_pressure.py", ("tests/test_gateway_pressure_contracts.py",)),
    ("venus_evcharger/ports/gateway_publication.py", ("tests/test_gateway_publication_contracts.py",)),
    (
        "venus_evcharger/ports/write.py",
        (
            "tests/test_ports_write_contracts.py",
            "tests/test_venus_evcharger_ports.py",
            "tests/test_boundary_port_edge_contracts.py",
        ),
    ),
    (
        "venus_evcharger/ports/write_runtime.py",
        ("tests/test_venus_evcharger_ports.py", "tests/test_boundary_port_edge_contracts.py"),
    ),
    (
        "venus_evcharger/publish/dbus_diagnostics_sources.py",
        ("tests/test_venus_evcharger_publisher_controller.py", "tests/test_boundary_port_edge_contracts.py"),
    ),
    ("venus_evcharger/publish/dbus_config.py", ("tests/test_venus_evcharger_publisher_controller.py",)),
    (
        "venus_evcharger/runtime/async_mainloop_executor.py",
        ("tests/test_venus_evcharger_runtime_support_controller.py",),
    ),
    ("venus_evcharger/runtime/async_control_types.py", ("tests/test_runtime_async_control_types.py",)),
    (
        "venus_evcharger/runtime/async_mainloop_control.py",
        (
            "tests/test_runtime_async_mainloop_control_contracts.py",
            "tests/test_venus_evcharger_runtime_support_controller.py",
        ),
    ),
    (
        "venus_evcharger/runtime/async_mainloop_state.py",
        (
            "tests/test_runtime_async_mainloop_state_contracts.py",
            "tests/test_venus_evcharger_runtime_support_controller.py",
        ),
    ),
    (
        "venus_evcharger/runtime/async_mainloop_watchdog.py",
        ("tests/test_runtime_async_mainloop_watchdog_contracts.py",),
    ),
    (
        "venus_evcharger/runtime/audit.py",
        ("tests/test_runtime_audit_contracts.py", "tests/test_venus_evcharger_runtime_support_controller.py"),
    ),
    ("venus_evcharger/runtime/audit_fields.py", ("tests/test_runtime_audit_fields_contracts.py",)),
    ("venus_evcharger/runtime/health.py", ("tests/test_runtime_health_contracts.py",)),
    (
        "venus_evcharger/runtime/output_path.py",
        (
            "tests/test_runtime_output_path_contracts.py",
            "tests/test_dbus_adapter_jsonl_contracts.py",
            "tests/test_dbus_adapter_process_config_contracts.py",
        ),
    ),
    ("venus_evcharger/runtime/setup.py", ("tests/test_runtime_setup_contracts.py",)),
    ("venus_evcharger/runtime/setup_support.py", ("tests/test_runtime_setup_support_contracts.py",)),
    ("venus_evcharger/runtime/software_update_setup.py", ("tests/test_runtime_software_update_setup.py",)),
    ("venus_evcharger/runtime/support.py", ("tests/test_runtime_support_contracts.py",)),
    (
        "venus_evcharger/service/control_state_core.py",
        (
            "tests/test_control_service_contracts.py",
            "tests/test_venus_evcharger_control_api.py",
            "tests/test_service_control_state_contracts.py",
        ),
    ),
    (
        "venus_evcharger/service/control_runtime.py",
        ("tests/test_service_control_runtime_contracts.py", "tests/test_service_control_state_contracts.py"),
    ),
    ("venus_evcharger/service/control.py", ("tests/test_service_control_state_contracts.py",)),
    ("venus_evcharger/service/control_state_config.py", ("tests/test_service_control_state_contracts.py",)),
    ("venus_evcharger/service/control_state_meta.py", ("tests/test_service_control_state_contracts.py",)),
    ("venus_evcharger/service/control_state_operational.py", ("tests/test_service_operational_state_contracts.py",)),
    (
        "venus_evcharger/service/control_state_operational_support.py",
        ("tests/test_service_operational_state_contracts.py",),
    ),
    ("venus_evcharger/service/control_state_victron.py", ("tests/test_service_operational_state_contracts.py",)),
    (
        "venus_evcharger/service/composition_guards.py",
        ("tests/test_service_composition_contracts.py", "tests/test_boundary_port_edge_contracts.py"),
    ),
)

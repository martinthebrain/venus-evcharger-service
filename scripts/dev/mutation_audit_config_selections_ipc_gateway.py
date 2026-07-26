# SPDX-License-Identifier: GPL-3.0-or-later
"""Focused mutmut test selections for IPC and DBus gateway mutation selections."""

from __future__ import annotations


FOCUSED_TEST_SELECTIONS_IPC_GATEWAY: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "venus_evcharger/ipc/command_mailbox.py",
        (
            "tests/test_ipc_command_mailbox_contracts.py",
            "tests/test_publication_mailbox_contracts.py",
            "tests/test_fast_publication_ipc.py",
        ),
    ),
    ("venus_evcharger/ipc/core_commands.py", ("tests/test_ipc_command_mailbox_contracts.py",)),
    ("venus_evcharger/ipc/energy_binary.py", ("tests/test_energy_binary_ipc_contracts.py",)),
    ("venus_evcharger/ipc/energy_refresh.py", ("tests/test_energy_ipc_contracts.py",)),
    ("venus_evcharger/ipc/energy_snapshots.py", ("tests/test_energy_ipc_contracts.py",)),
    ("venus_evcharger/ipc/energy_types.py", ("tests/test_energy_ipc_contracts.py",)),
    ("venus_evcharger/ipc/energy_validation.py", ("tests/test_energy_ipc_contracts.py",)),
    ("venus_evcharger/ipc/energy_values.py", ("tests/test_energy_ipc_contracts.py",)),
    ("venus_evcharger/ipc/gateway_diagnostics.py", ("tests/test_gateway_diagnostics_contracts.py",)),
    ("venus_evcharger/ipc/gateway_path_config.py", ("tests/test_gateway_path_config_contracts.py",)),
    ("venus_evcharger/ipc/gateway_operations.py", ("tests/test_gateway_semantic_operations.py",)),
    (
        "venus_evcharger/ipc/gateway_pressure.py",
        (
            "tests/test_gateway_pressure_contracts.py",
            "tests/test_gateway_pressure_mutation_contracts.py",
        ),
    ),
    ("venus_evcharger/ipc/gateway_publication.py", ("tests/test_gateway_publication_contracts.py",)),
    (
        "venus_evcharger/dbus_gateway_commands.py",
        (
            "tests/test_dbus_gateway_primitives.py",
            "tests/test_publication_mailbox_contracts.py",
            "tests/test_fast_publication_scheduler_contracts.py",
            "tests/test_ipc_publication_edges.py",
        ),
    ),
    ("venus_evcharger/dbus_adapter/rate.py", ("tests/test_dbus_gateway_adapter_scheduler.py",)),
    ("venus_evcharger/dbus_adapter/resources.py", ("tests/test_dbus_adapter_resource_contracts.py",)),
    (
        "venus_evcharger/dbus_gateway_cache.py",
        (
            "tests/test_dbus_gateway_primitives.py",
            "tests/test_dbus_adapter_freshness_contracts.py",
            "tests/test_energy_binary_ipc_contracts.py",
        ),
    ),
    (
        "venus_evcharger/dbus_gateway_client.py",
        (
            "tests/test_dbus_gateway_primitives.py",
            "tests/test_energy_binary_ipc_contracts.py",
            "tests/test_gateway_boundary_edge_contracts.py",
            "tests/test_gateway_semantic_operations.py",
            "tests/test_disable_generic_shelly_once_contracts.py",
        ),
    ),
    (
        "venus_evcharger/dbus_gateway_core.py",
        (
            "tests/test_dbus_gateway_primitives.py",
            "tests/test_energy_binary_ipc_contracts.py",
            "tests/test_gateway_boundary_edge_contracts.py",
        ),
    ),
    (
        "venus_evcharger/dbus_adapter/read/pv.py",
        ("tests/test_dbus_adapter_read_pv_contracts.py", "tests/test_dbus_adapter_discovery_mutation_contracts.py"),
    ),
    ("venus_evcharger/dbus_adapter/refresh_state.py", ("tests/test_dbus_adapter_refresh_state_contracts.py",)),
    ("venus_evcharger/dbus_adapter/read/executor.py", ("tests/test_dbus_gateway_adapter_scheduler.py",)),
    ("venus_evcharger/dbus_adapter/scheduling.py", ("tests/test_dbus_adapter_scheduler_contracts.py",)),
    ("venus_evcharger/dbus_adapter/health/backpressure.py", ("tests/test_dbus_adapter_backpressure_contracts.py",)),
    ("venus_evcharger/dbus_adapter/health/freshness.py", ("tests/test_dbus_adapter_freshness_contracts.py",)),
    ("venus_evcharger/dbus_adapter/health/history.py", ("tests/test_dbus_adapter_health_history_contracts.py",)),
    ("venus_evcharger/dbus_adapter/health/queue.py", ("tests/test_dbus_adapter_health_queue_contracts.py",)),
    ("venus_evcharger/dbus_adapter/health/slo.py", ("tests/test_dbus_adapter_health_slo_contracts.py",)),
    ("venus_evcharger/dbus_adapter/jsonl.py", ("tests/test_dbus_adapter_jsonl_contracts.py",)),
    ("venus_evcharger/dbus_adapter/process/adapter.py", ("tests/test_dbus_adapter_process_contracts.py",)),
    ("venus_evcharger/dbus_adapter/process/config.py", ("tests/test_dbus_adapter_process_config_contracts.py",)),
    (
        "venus_evcharger/dbus_adapter/process/introspection.py",
        (
            "tests/test_energy_ipc_contracts.py",
            "tests/test_gateway_energy_snapshot_contracts.py",
            "tests/test_dbus_gateway_adapter_scheduler.py::DbusGatewayAdapterSchedulerTests::test_background_introspection_uses_discovery_targets_and_opaque_topology",
            "tests/test_dbus_gateway_adapter_scheduler.py::DbusGatewayAdapterSchedulerTests::test_gateway_non_write_introspection_command_contracts",
            "tests/test_dbus_gateway_adapter_scheduler.py::DbusGatewayAdapterSchedulerTests::test_non_write_introspection_timed_logging_main_and_json_ready",
        ),
    ),
    ("venus_evcharger/dbus_adapter/process/loop.py", ("tests/test_dbus_gateway_adapter_scheduler.py",)),
    (
        "venus_evcharger/dbus_adapter/process/introspection_snapshot.py",
        ("tests/test_dbus_adapter_introspection_snapshot_contracts.py",),
    ),
    (
        "venus_evcharger/dbus_adapter/process/io.py",
        (
            "tests/test_dbus_gateway_adapter_scheduler.py::DbusGatewayAdapterSchedulerTests::test_poll_and_discovery_contracts",
            "tests/test_dbus_gateway_adapter_scheduler.py::DbusGatewayAdapterSchedulerTests::test_poll_and_discovery_edges",
            "tests/test_dbus_gateway_adapter_scheduler.py::DbusGatewayAdapterSchedulerTests::test_cache_publish_interval_contracts",
            "tests/test_dbus_gateway_adapter_scheduler.py::DbusGatewayAdapterSchedulerTests::test_signal_handlers_andlist_services_edges",
            "tests/test_dbus_gateway_adapter_scheduler.py::DbusGatewayAdapterSchedulerTests::test_timed_operation_contracts_record_latency_and_errors",
        ),
    ),
    (
        "venus_evcharger/dbus_adapter/process/runtime.py",
        (
            "tests/test_dbus_gateway_adapter_scheduler.py::DbusGatewayAdapterSchedulerTests::test_signal_handlers_andlist_services_edges",
        ),
    ),
    ("venus_evcharger/dbus_adapter/process/socket.py", ("tests/test_dbus_adapter_process_ipc_contracts.py",)),
    ("venus_evcharger/dbus_adapter/read/aggregate.py", ("tests/test_dbus_adapter_read_aggregate_contracts.py",)),
    (
        "venus_evcharger/dbus_adapter/read/discovery.py",
        (
            "tests/test_gateway_energy_snapshot_contracts.py",
            "tests/test_dbus_adapter_discovery_mutation_contracts.py",
        ),
    ),
    ("venus_evcharger/dbus_adapter/read/keys.py", ("tests/test_gateway_energy_snapshot_contracts.py",)),
    ("venus_evcharger/dbus_adapter/read/semantic.py", ("tests/test_gateway_energy_snapshot_contracts.py",)),
    ("venus_evcharger/dbus_adapter/read/targets.py", ("tests/test_dbus_adapter_read_targets_contracts.py",)),
    (
        "venus_evcharger/dbus_adapter/read/spec.py",
        (
            "tests/test_dbus_gateway_adapter_scheduler.py::DbusGatewayAdapterSchedulerTests::test_read_spec_from_mapping_validates_known_fields",
        ),
    ),
    ("venus_evcharger/dbus_adapter/write/scheduler.py", ("tests/test_dbus_gateway_adapter_scheduler.py",)),
    ("venus_evcharger/dbus_adapter/write/health.py", ("tests/test_dbus_gateway_adapter_scheduler.py",)),
    (
        "venus_evcharger/dbus_adapter/write/publish.py",
        (
            "tests/test_dbus_gateway_adapter_scheduler.py",
            "tests/test_dbus_adapter_write_publish_mutation_contracts.py",
        ),
    ),
    (
        "venus_evcharger/dbus_adapter/write/semantic.py",
        ("tests/test_dbus_adapter_write_semantic_mutation_contracts.py",),
    ),
    ("venus_evcharger/dbus_adapter/write/core.py", ("tests/test_dbus_gateway_adapter_scheduler.py",)),
    ("venus_evcharger/dbus_adapter/write/support.py", ("tests/test_dbus_gateway_adapter_scheduler.py",)),
    ("venus_evcharger/dbus_gateway_surface.py", ("tests/test_dbus_gateway_primitives.py",)),
    ("venus_evcharger/publish/gateway_diagnostics.py", ("tests/test_publish_gateway_diagnostics_contracts.py",)),
)

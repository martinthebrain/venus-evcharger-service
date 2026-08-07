# SPDX-License-Identifier: GPL-3.0-or-later
"""Focused mutmut selections for the gateway runtime and its semantic boundaries."""

from __future__ import annotations

_ARCHITECTURE_TESTS = ("tests/test_architecture_contracts_script.py",)
_CACHE_SCRIPT_TESTS = ("tests/test_gateway_cache_read_script.py",)
_CACHE_TESTS = (
    "tests/test_dbus_gateway_primitives.py",
    "tests/test_dbus_adapter_freshness_contracts.py",
    "tests/test_energy_binary_ipc_contracts.py",
)
_GATEWAY_SCHEDULER_TESTS = ("tests/test_dbus_gateway_adapter_scheduler.py",)
_PROCESS_CONTRACT_TESTS = (
    "tests/test_dbus_adapter_process_contracts.py",
    "tests/test_dbus_adapter_process_ipc_contracts.py",
)
_PROCESS_IPC_TESTS = (
    "tests/test_dbus_adapter_process_ipc_contracts.py",
    "tests/test_dbus_adapter_socket_lifecycle_mutation_contracts.py",
    "tests/test_dbus_adapter_socket_protocol_mutation_contracts.py",
    "tests/test_fast_publication_ipc.py",
)
_DIAGNOSTICS_TESTS = (
    "tests/test_gateway_diagnostics_contracts.py",
    "tests/test_gateway_diagnostics_adapter_contracts.py",
)
_PUBLICATION_REGISTRY_TESTS = (
    "tests/test_dbus_adapter_publication_boundary_contracts.py",
    "tests/test_gateway_diagnostics_adapter_contracts.py",
    "tests/test_dbus_adapter_process_contracts.py",
)
_WRITE_PUBLISH_TESTS = (
    "tests/test_dbus_adapter_write_publish_mutation_contracts.py",
    "tests/test_dbus_gateway_adapter_scheduler.py",
    "tests/test_fast_publication_ipc.py",
)
_WRITE_SEMANTIC_TESTS = (
    "tests/test_dbus_adapter_write_semantic_mutation_contracts.py",
    "tests/test_gateway_semantic_operations.py",
)
_EXTERNAL_ENERGY_TESTS = (
    "tests/test_auto_input_external_sources_contracts.py",
    "tests/test_auto_input_external_energy_scenario.py",
)
_FORENSIC_OBSERVER_TESTS = (
    "tests/test_forensic_observer_contracts.py",
    "tests/test_venus_evcharger_forensic_observer.py",
)
_GATEWAY_DIAGNOSTIC_PORT_TESTS = (
    "tests/test_gateway_diagnostics_contracts.py",
    "tests/test_gateway_diagnostics_boundary_contracts.py",
    "tests/test_gateway_diagnostic_value_contracts.py",
    "tests/test_gateway_diagnostics_snapshot_contracts.py",
)
_RESOURCE_TESTS = (
    "tests/test_dbus_adapter_resource_contracts.py",
    "tests/test_dbus_adapter_resource_metrics_contracts.py",
    "tests/test_dbus_adapter_resource_pressure_contracts.py",
    "tests/test_dbus_adapter_resource_procfs_contracts.py",
    "tests/test_dbus_adapter_tick_health_contracts.py",
)
_PUBLICATION_ORDER_TESTS = (
    "tests/test_publication_order_contracts.py",
    "tests/test_fast_publication_queue_contracts.py",
    "tests/test_ipc_publication_edges.py",
    "tests/test_fast_publication_ipc.py",
)
_PUBLICATION_QUEUE_TESTS = (
    "tests/test_fast_publication_queue_contracts.py",
    "tests/test_ipc_publication_edges.py",
    "tests/test_fast_publication_ipc.py",
)
_PUBLICATION_PAYLOAD_TESTS = (
    "tests/test_fast_publication_queue_contracts.py",
    "tests/test_publication_mailbox_contracts.py",
    "tests/test_ipc_publication_edges.py",
)
_PUBLICATION_DEADLINE_TESTS = (
    "tests/test_ipc_deadline_contracts.py",
    "tests/test_fast_publication_ipc.py",
    "tests/test_fast_publication_scheduler_contracts.py",
)
_PUBLICATION_SNAPSHOT_TESTS = (
    "tests/test_publication_mailbox_contracts.py",
    "tests/test_fast_publication_scheduler_contracts.py",
    "tests/test_fast_publication_ipc.py",
)


FOCUSED_TEST_SELECTIONS_GATEWAY: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("scripts/dev/architecture_suppression_contracts.py", _ARCHITECTURE_TESTS),
    ("scripts/dev/check_architecture_contracts.py", _ARCHITECTURE_TESTS),
    ("scripts/ops/gateway_cache_read.py", _CACHE_SCRIPT_TESTS),
    (
        "venus_evcharger/dbus_adapter/health/state.py",
        ("tests/test_dbus_adapter_health_state_contracts.py",),
    ),
    ("venus_evcharger/dbus_adapter/process/adapter.py", (*_PROCESS_CONTRACT_TESTS, "tests/test_fast_publication_ipc.py")),
    (
        "venus_evcharger/dbus_adapter/process/config.py",
        (
            "tests/test_dbus_adapter_process_config_contracts.py",
            "tests/test_dbus_adapter_process_contracts.py",
            "tests/test_energy_binary_ipc_contracts.py",
        ),
    ),
    ("venus_evcharger/dbus_adapter/process/diagnostics.py", _DIAGNOSTICS_TESTS),
    (
        "venus_evcharger/dbus_adapter/process/diagnostics_summary.py",
        _DIAGNOSTICS_TESTS,
    ),
    (
        "venus_evcharger/dbus_adapter/process/diagnostics_values.py",
        _DIAGNOSTICS_TESTS,
    ),
    (
        "venus_evcharger/dbus_adapter/process/health.py",
        (
            "tests/test_dbus_gateway_adapter_scheduler.py",
            "tests/test_dbus_adapter_resource_contracts.py",
            "tests/test_gateway_diagnostics_adapter_contracts.py",
            "tests/test_publication_mailbox_contracts.py",
        ),
    ),
    (
        "venus_evcharger/dbus_adapter/process/introspection.py",
        ("tests/test_dbus_gateway_adapter_scheduler.py", "tests/test_dbus_adapter_introspection_snapshot_contracts.py"),
    ),
    (
        "venus_evcharger/dbus_adapter/process/introspection_snapshot.py",
        ("tests/test_dbus_adapter_introspection_snapshot_contracts.py",),
    ),
    (
        "venus_evcharger/dbus_adapter/process/io.py",
        ("tests/test_dbus_gateway_adapter_scheduler.py", "tests/test_energy_binary_ipc_contracts.py"),
    ),
    (
        "venus_evcharger/dbus_adapter/process/loop.py",
        (
            "tests/test_dbus_gateway_adapter_scheduler.py",
            "tests/test_dbus_adapter_process_contracts.py",
            "tests/test_dbus_adapter_loop_mutation_contracts.py",
            "tests/test_publication_mailbox_contracts.py",
        ),
    ),
    (
        "venus_evcharger/dbus_adapter/process/publication.py",
        ("tests/test_dbus_adapter_process_contracts.py", "tests/test_dbus_adapter_publication_boundary_contracts.py"),
    ),
    ("venus_evcharger/dbus_adapter/process/runtime.py", _PROCESS_CONTRACT_TESTS),
    ("venus_evcharger/dbus_adapter/process/socket.py", _PROCESS_IPC_TESTS),
    (
        "venus_evcharger/dbus_adapter/process/write_context.py",
        (*_PROCESS_CONTRACT_TESTS, *_GATEWAY_SCHEDULER_TESTS),
    ),
    (
        "venus_evcharger/dbus_adapter/publication/registry.py",
        _PUBLICATION_REGISTRY_TESTS,
    ),
    (
        "venus_evcharger/dbus_adapter/resources.py",
        _RESOURCE_TESTS,
    ),
    (
        "venus_evcharger/dbus_adapter/read/pv_discovery.py",
        (
            "tests/test_dbus_adapter_pv_dormancy_contracts.py",
            "tests/test_dbus_adapter_discovery_mutation_contracts.py",
        ),
    ),
    (
        "venus_evcharger/dbus_adapter/read/pv_dormancy.py",
        (
            "tests/test_dbus_adapter_pv_dormancy_contracts.py",
            "tests/test_dbus_adapter_discovery_mutation_contracts.py",
        ),
    ),
    ("venus_evcharger/dbus_adapter/resource_metrics.py", _RESOURCE_TESTS),
    ("venus_evcharger/dbus_adapter/resource_pressure.py", _RESOURCE_TESTS),
    ("venus_evcharger/dbus_adapter/resource_procfs.py", _RESOURCE_TESTS),
    ("venus_evcharger/dbus_adapter/tick_health.py", _RESOURCE_TESTS),
    (
        "venus_evcharger/dbus_gateway_latency.py",
        ("tests/test_dbus_gateway_primitives.py",),
    ),
    (
        "venus_evcharger/dbus_adapter/write/core.py",
        (
            *_GATEWAY_SCHEDULER_TESTS,
            "tests/test_dbus_adapter_write_core_mutation_contracts.py",
            "tests/test_dbus_adapter_write_core_lifecycle_mutation_contracts.py",
            "tests/test_fast_publication_ipc.py",
            "tests/test_fast_publication_scheduler_contracts.py",
            "tests/test_ipc_deadline_contracts.py",
        ),
    ),
    (
        "venus_evcharger/dbus_adapter/write/generic_shelly.py",
        (
            "tests/test_generic_shelly_gateway_configuration.py",
            "tests/test_dbus_adapter_write_semantic_mutation_contracts.py",
        ),
    ),
    (
        "venus_evcharger/dbus_adapter/write/health.py",
        (*_GATEWAY_SCHEDULER_TESTS, "tests/test_fast_publication_ipc.py"),
    ),
    ("venus_evcharger/dbus_adapter/write/publish.py", _WRITE_PUBLISH_TESTS),
    (
        "venus_evcharger/dbus_adapter/write/scheduler.py",
        (
            *_GATEWAY_SCHEDULER_TESTS,
            "tests/test_fast_publication_ipc.py",
            "tests/test_fast_publication_scheduler_contracts.py",
            "tests/test_publication_mailbox_contracts.py",
        ),
    ),
    ("venus_evcharger/dbus_adapter/write/semantic.py", _WRITE_SEMANTIC_TESTS),
    (
        "venus_evcharger/dbus_adapter/write/support.py",
        (
            *_GATEWAY_SCHEDULER_TESTS,
            "tests/test_fast_publication_ipc.py",
            "tests/test_fast_publication_scheduler_contracts.py",
            "tests/test_ipc_deadline_contracts.py",
        ),
    ),
    (
        "venus_evcharger/dbus_gateway_cache.py",
        _CACHE_TESTS,
    ),
    ("venus_evcharger/dbus_gateway_cache_io.py", _CACHE_TESTS),
    ("venus_evcharger/dbus_gateway_cache_metadata.py", _CACHE_TESTS),
    ("venus_evcharger/dbus_gateway_cache_snapshot.py", _CACHE_TESTS),
    (
        "venus_evcharger/dbus_gateway_client.py",
        (
            "tests/test_dbus_gateway_primitives.py",
            "tests/test_disable_generic_shelly_once_contracts.py",
            "tests/test_energy_binary_ipc_contracts.py",
            "tests/test_fast_publication_ipc.py",
            "tests/test_gateway_boundary_edge_contracts.py",
            "tests/test_gateway_semantic_operations.py",
            "tests/test_publication_order_contracts.py",
            "tests/test_publication_mailbox_contracts.py",
        ),
    ),
    (
        "venus_evcharger/dbus_gateway_policy.py",
        (
            "tests/test_dbus_gateway_primitives.py",
            "tests/test_dbus_gateway_policy_mutation_contracts.py",
            "tests/test_fast_publication_scheduler_contracts.py",
            "tests/test_gateway_semantic_operations.py",
            "tests/test_generic_shelly_gateway_configuration.py",
        ),
    ),
    (
        "venus_evcharger/inputs/helper/config_runtime.py",
        ("tests/test_auto_input_helper_config_contracts.py", "tests/test_auto_input_external_energy_scenario.py"),
    ),
    ("venus_evcharger/inputs/helper/external_contracts.py", _EXTERNAL_ENERGY_TESTS),
    ("venus_evcharger/inputs/helper/external_scheduler.py", _EXTERNAL_ENERGY_TESTS),
    ("venus_evcharger/inputs/helper/external_sources.py", _EXTERNAL_ENERGY_TESTS),
    (
        "venus_evcharger/inputs/helper/sources.py",
        ("tests/test_auto_input_helper_sources_contracts.py", *_EXTERNAL_ENERGY_TESTS),
    ),
    (
        "venus_evcharger/ipc/deadline.py",
        _PUBLICATION_DEADLINE_TESTS,
    ),
    (
        "venus_evcharger/ipc/fast_publication.py",
        (
            "tests/test_dbus_adapter_process_ipc_contracts.py",
            *_PUBLICATION_QUEUE_TESTS,
            "tests/test_fast_publication_queue_enqueue_mutation_contracts.py",
            "tests/test_fast_publication_queue_ordering_mutation_contracts.py",
            "tests/test_fast_publication_queue_payload_mutation_contracts.py",
        ),
    ),
    (
        "venus_evcharger/ipc/fast_publication_ordering.py",
        (
            *_PUBLICATION_ORDER_TESTS,
            "tests/test_fast_publication_ordering_mutation_contracts.py",
        ),
    ),
    (
        "venus_evcharger/ipc/fast_publication_wire.py",
        (
            "tests/test_fast_publication_wire_contracts.py",
            "tests/test_fast_publication_wire_mutation_contracts.py",
            "tests/test_fast_publication_ipc.py",
            "tests/test_dbus_adapter_process_ipc_contracts.py",
        ),
    ),
    (
        "venus_evcharger/ipc/fast_publication_metrics.py",
        _PUBLICATION_QUEUE_TESTS,
    ),
    (
        "venus_evcharger/ipc/fast_publication_policy.py",
        _PUBLICATION_QUEUE_TESTS,
    ),
    (
        "venus_evcharger/ipc/fast_publication_work.py",
        _PUBLICATION_QUEUE_TESTS,
    ),
    (
        "venus_evcharger/ipc/pending_snapshot.py",
        _PUBLICATION_SNAPSHOT_TESTS,
    ),
    (
        "venus_evcharger/ipc/publication_order.py",
        _PUBLICATION_ORDER_TESTS,
    ),
    (
        "venus_evcharger/ipc/publication_order_state.py",
        (*_PUBLICATION_ORDER_TESTS, "tests/test_publication_order_state_resilience.py"),
    ),
    (
        "venus_evcharger/ipc/publication_payload.py",
        _PUBLICATION_PAYLOAD_TESTS,
    ),
    ("venus_evcharger/ops/forensic_observer.py", _FORENSIC_OBSERVER_TESTS),
    (
        "venus_evcharger/ops/forensic_observer_artifacts.py",
        _FORENSIC_OBSERVER_TESTS,
    ),
    (
        "venus_evcharger/ops/forensic_observer_probe.py",
        ("tests/test_forensic_observer_probe_contracts.py",),
    ),
    ("venus_evcharger/ops/forensic_observer_schema.py", _FORENSIC_OBSERVER_TESTS),
    (
        "venus_evcharger/ops/removable_storage_coordination.py",
        ("tests/test_removable_storage_coordination.py",),
    ),
    (
        "venus_evcharger/ports/gateway_diagnostic_discovery.py",
        _GATEWAY_DIAGNOSTIC_PORT_TESTS,
    ),
    (
        "venus_evcharger/ports/gateway_diagnostic_health.py",
        _GATEWAY_DIAGNOSTIC_PORT_TESTS,
    ),
    (
        "venus_evcharger/ports/gateway_diagnostic_values.py",
        _GATEWAY_DIAGNOSTIC_PORT_TESTS,
    ),
    ("venus_evcharger/ports/gateway_diagnostics.py", _GATEWAY_DIAGNOSTIC_PORT_TESTS),
    (
        "venus_evcharger/ports/gateway_diagnostics_validation.py",
        _GATEWAY_DIAGNOSTIC_PORT_TESTS,
    ),
)

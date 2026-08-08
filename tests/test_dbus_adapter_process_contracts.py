#!/usr/bin/env python3
"""Construction contracts for the dedicated DBus adapter process."""

from __future__ import annotations

import configparser
import importlib
import logging
import sys
import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from tests.dbus_adapter_venus_stubs import install_venus_adapter_stubs

install_venus_adapter_stubs()

import venus_evcharger.dbus_adapter.process.adapter as process
from venus_evcharger.dbus_adapter.contracts import CommandExecution
from venus_evcharger.dbus_adapter.process.config import (
    CasePreservingConfigParser,
    GatewayAdapterSettings,
    GatewayFileSettings,
    GatewayIntrospectionSettings,
    GatewayRateSettings,
    GatewayResourceSettings,
    GatewaySloSettings,
    GatewayTimingSettings,
    logging_level_from_config,
)
from venus_evcharger.dbus_gateway import gateway_paths


def adapter_settings() -> GatewayAdapterSettings:
    paths = gateway_paths("/run/test-gateway")
    return GatewayAdapterSettings(
        paths=paths,
        service_name="com.victronenergy.evcharger.http_61",
        device_instance=61,
        read_specs={"grid_power_w": {"service": "system", "path": "/Grid"}},
        rates=GatewayRateSettings(0.21, 0.31, 2.1),
        timing=GatewayTimingSettings(
            0.11,
            0.91,
            31.0,
            17.0,
            0.21,
            0.31,
            4.1,
            0.41,
        ),
        slo=GatewaySloSettings(2.1, 5.1, 10.1, 501.0),
        files=GatewayFileSettings("/run/lifecycle", 101, "/run/health", 3.1, 202),
        introspection=GatewayIntrospectionSettings("/run/intro", True),
        resources=GatewayResourceSettings(2.1, 9.1),
        stale_after_seconds=12.5,
    )


class DbusAdapterProcessContractTests(unittest.TestCase):
    def test_adapter_module_import_does_not_duplicate_existing_velib_path(self) -> None:
        velib_path = process._VELIB_PYTHON_PATH
        self.assertIn(velib_path, sys.path)
        occurrences = sys.path.count(velib_path)

        self.assertIs(importlib.reload(process), process)

        self.assertEqual(sys.path.count(velib_path), occurrences)

    def test_logging_level_is_normalized_and_invalid_values_fall_back(self) -> None:
        config = CasePreservingConfigParser()
        self.assertEqual(logging_level_from_config(config), logging.INFO)
        config["DEFAULT"]["Logging"] = " debug "
        self.assertEqual(logging_level_from_config(config), logging.DEBUG)
        config["DEFAULT"]["Logging"] = "warning"
        self.assertEqual(logging_level_from_config(config), logging.WARNING)
        config["DEFAULT"]["Logging"] = "not-a-level"
        self.assertEqual(logging_level_from_config(config), logging.INFO)

    def test_adapter_initializes_every_component_and_runtime_field_from_settings(self) -> None:
        config = CasePreservingConfigParser()
        config["DEFAULT"]["AutoPvMaxServices"] = "0"
        settings = adapter_settings()
        component_names = (
            "DbusConnectionManager",
            "DbusRateLimiter",
            "DbusCircuitBreaker",
            "DbusAsyncOperationBroker",
            "DbusCacheStore",
            "DbusGatewayCommandInbox",
            "FastPublicationQueue",
            "CoreCommandMailbox",
            "GatewayPublicationRegistry",
            "DbusWriteScheduler",
            "DbusReadScheduler",
            "DbusEnergyDiscoveryManager",
            "DbusReadExecutor",
            "DbusDiscoveryManager",
            "AtomicJsonWriter",
            "ResourceMonitor",
            "TickHealth",
            "DbusAdapterRuntime",
            "DbusAdapterIo",
            "DbusAdapterIntrospection",
            "DbusAdapterIntrospectionSnapshot",
            "DbusAdapterDiagnostics",
            "DbusAdapterPublication",
            "DbusAdapterHealth",
            "DbusAdapterSocket",
            "DbusAdapterLoop",
        )
        mocks = {name: MagicMock(name=name) for name in component_names}
        with ExitStack() as stack:
            load_config = stack.enter_context(patch.object(process, "load_adapter_config", return_value=config))
            build_settings = stack.enter_context(patch.object(process, "adapter_settings", return_value=settings))
            for name, mock in mocks.items():
                stack.enter_context(patch.object(process, name, mock))
            adapter = process.DbusAdapter("/etc/evcharger.ini", paths=settings.paths)

        load_config.assert_called_once_with("/etc/evcharger.ini")
        build_settings.assert_called_once_with(config["DEFAULT"], explicit_paths=settings.paths)
        mocks["DbusRateLimiter"].assert_called_once_with(
            read_interval_seconds=0.21,
            write_interval_seconds=0.31,
            introspection_interval_seconds=2.1,
        )
        mocks["DbusAsyncOperationBroker"].assert_called_once_with(
            mocks["DbusRateLimiter"].return_value,
            mocks["DbusCircuitBreaker"].return_value,
        )
        mocks["DbusCacheStore"].assert_called_once_with(settings.paths, stale_after_seconds=12.5)
        mocks["DbusGatewayCommandInbox"].assert_called_once_with(settings.paths.command_dir)
        mocks["FastPublicationQueue"].assert_called_once_with(
            order_state_path=process.os.path.join(
                settings.paths.run_dir,
                process.PUBLICATION_ORDER_STATE_NAME,
            ),
        )
        mocks["CoreCommandMailbox"].assert_called_once_with(settings.paths.core_command_dir)
        registry_call = mocks["GatewayPublicationRegistry"].call_args
        self.assertEqual(registry_call.args, (config,))
        self.assertEqual(registry_call.kwargs["evcs_service_name"], settings.service_name)
        self.assertIs(registry_call.kwargs["cache"], mocks["DbusCacheStore"].return_value)
        self.assertIs(registry_call.kwargs["core_commands"], mocks["CoreCommandMailbox"].return_value)
        self.assertTrue(callable(registry_call.kwargs["timed_publish"]))
        mocks["DbusWriteScheduler"].assert_called_once()
        write_context = mocks["DbusWriteScheduler"].call_args.args[0]
        self.assertIsInstance(write_context, process.DbusAdapterWriteContext)
        self.assertIsNot(write_context, adapter)
        self.assertIs(write_context.cache, adapter.cache)
        self.assertIs(write_context.circuit, adapter.circuit)
        self.assertIs(write_context.commands, adapter.commands)
        self.assertEqual(
            write_context.command_lifecycle_path,
            adapter.command_lifecycle_path,
        )
        self.assertEqual(
            write_context.command_lifecycle_max_bytes,
            adapter.command_lifecycle_max_bytes,
        )
        self.assertIs(write_context.config, adapter.config)
        self.assertIs(write_context.connection, adapter.connection)
        self.assertIs(write_context.operation_broker, adapter.operation_broker)
        self.assertIs(
            write_context.core_command_mailbox,
            adapter.core_command_mailbox,
        )
        self.assertIs(write_context.fast_publications, adapter.fast_publications)
        self.assertIs(write_context.publication_registry, adapter.publication_registry)
        self.assertEqual(write_context.service_name, adapter.service_name)
        self.assertIs(write_context.publication_role, adapter.publication_role)
        self.assertIs(write_context.introspection_role, adapter.introspection_role)
        self.assertIs(write_context.io_role, adapter.io_role)
        self.assertFalse(hasattr(write_context, "loop_role"))
        self.assertFalse(hasattr(write_context, "health_role"))
        write_context.publication_role.evcs_service_registered = True
        self.assertTrue(write_context.evcs_service_registered)
        write_context.introspection_role.schedule_non_write_command.return_value = CommandExecution.immediate("dropped")
        command = {"kind": "unknown"}
        command_file = "/run/test-gateway/commands/unknown.json"
        completion = MagicMock()
        self.assertEqual(
            write_context.schedule_non_write_command(command, command_file, completion),
            CommandExecution.immediate("dropped"),
        )
        write_context.introspection_role.schedule_non_write_command.assert_called_once_with(
            command,
            command_file,
            completion,
        )
        local_result = object()
        write_context.io_role.timed_local_publish.return_value = local_result
        operation = MagicMock()
        self.assertIs(write_context.timed_local_publish(operation), local_result)
        write_context.io_role.timed_local_publish.assert_called_once_with(operation)
        mocks["DbusReadScheduler"].assert_called_once_with(settings.read_specs)
        mocks["DbusEnergyDiscoveryManager"].assert_called_once_with(
            settings.read_specs,
            max_prefix_services=1,
        )
        mocks["DbusReadExecutor"].assert_called_once_with(adapter)
        mocks["DbusDiscoveryManager"].assert_called_once_with(
            interval_seconds=31.0,
            missing_pv_interval_seconds=17.0,
        )
        mocks["ResourceMonitor"].assert_called_once_with(
            settings=process.ResourceMonitorSettings(
                sample_interval_seconds=2.1,
                recovery_hold_seconds=9.1,
            ),
        )
        for role_name in (
            "DbusAdapterRuntime",
            "DbusAdapterIo",
            "DbusAdapterIntrospection",
            "DbusAdapterIntrospectionSnapshot",
            "DbusAdapterDiagnostics",
            "DbusAdapterPublication",
            "DbusAdapterHealth",
            "DbusAdapterSocket",
            "DbusAdapterLoop",
        ):
            mocks[role_name].assert_called_once_with(adapter)
        for attribute, component_name in (
            ("connection", "DbusConnectionManager"),
            ("rate_limiter", "DbusRateLimiter"),
            ("circuit", "DbusCircuitBreaker"),
            ("operation_broker", "DbusAsyncOperationBroker"),
            ("cache", "DbusCacheStore"),
            ("commands", "DbusGatewayCommandInbox"),
            ("fast_publications", "FastPublicationQueue"),
            ("core_command_mailbox", "CoreCommandMailbox"),
            ("publication_registry", "GatewayPublicationRegistry"),
            ("write_scheduler", "DbusWriteScheduler"),
            ("read_scheduler", "DbusReadScheduler"),
            ("energy_discovery", "DbusEnergyDiscoveryManager"),
            ("read_executor", "DbusReadExecutor"),
            ("discovery", "DbusDiscoveryManager"),
            ("json_writer", "AtomicJsonWriter"),
            ("resource_monitor", "ResourceMonitor"),
            ("tick_health", "TickHealth"),
            ("runtime_role", "DbusAdapterRuntime"),
            ("io_role", "DbusAdapterIo"),
            ("introspection_role", "DbusAdapterIntrospection"),
            ("introspection_snapshot_role", "DbusAdapterIntrospectionSnapshot"),
            ("diagnostics_role", "DbusAdapterDiagnostics"),
            ("publication_role", "DbusAdapterPublication"),
            ("health_role", "DbusAdapterHealth"),
            ("socket_role", "DbusAdapterSocket"),
            ("loop_role", "DbusAdapterLoop"),
        ):
            with self.subTest(attribute=attribute):
                self.assertIs(getattr(adapter, attribute), mocks[component_name].return_value)

        self.assertEqual(adapter.config_path, "/etc/evcharger.ini")
        self.assertIs(adapter.config, config)
        self.assertIs(adapter.paths, settings.paths)
        self.assertEqual(adapter.service_name, settings.service_name)
        self.assertFalse(hasattr(adapter, "_dbusservice"))
        self.assertFalse(hasattr(adapter, "_dbusservice_registered"))
        self.assertIs(adapter._stop, False)
        self.assertIsNone(adapter._server)
        self.assertIsNone(adapter._main_loop)
        self.assertEqual(adapter.min_tick_seconds, 0.11)
        self.assertEqual(adapter.max_tick_seconds, 0.91)
        self.assertEqual(adapter.tick_seconds, 0.11)
        self.assertEqual(adapter.energy_publish_interval_seconds, 0.21)
        self.assertEqual(adapter.health_publish_interval_seconds, 0.31)
        self.assertEqual(adapter.cache_publish_interval_seconds, 4.1)
        self.assertEqual(adapter.cache_dirty_publish_interval_seconds, 0.41)
        self.assertEqual(adapter.command_lifecycle_path, "/run/lifecycle")
        self.assertEqual(adapter.command_lifecycle_max_bytes, 101)
        self.assertEqual(adapter.slo_gui_max_age_seconds, 2.1)
        self.assertEqual(adapter.slo_core_read_max_age_seconds, 5.1)
        self.assertEqual(adapter.slo_queue_max_age_seconds, 10.1)
        self.assertEqual(adapter.slo_mainloop_gap_max_ms, 501.0)
        self.assertEqual(adapter.health_log_path, "/run/health")
        self.assertEqual(adapter.health_log_interval_seconds, 3.1)
        self.assertEqual(adapter.health_log_max_bytes, 202)
        self.assertEqual(adapter.dbus_introspection_snapshot_path, "/run/intro")
        self.assertIs(adapter.dbus_introspection_enabled, True)
        self.assertEqual(adapter._next_work_tick_monotonic, 0.0)
        self.assertEqual(adapter._last_resource_snapshot, {})
        self.assertEqual(adapter._last_introspection_full_scan_at, 0.0)
        self.assertEqual(adapter._introspection_queue_depth, 0)
        self.assertEqual(adapter._last_energy_publish_monotonic, 0.0)
        self.assertEqual(adapter._last_health_publish_monotonic, 0.0)
        self.assertEqual(adapter._last_cache_publish_monotonic, 0.0)
        self.assertEqual(adapter._last_cache_publish_sequence, -1)
        self.assertEqual(adapter._last_topology_generation, -1)
        self.assertEqual(adapter._last_health_log_monotonic, 0.0)
        self.assertEqual(adapter._last_tick_at, 0.0)
        self.assertEqual(adapter._last_tick_monotonic, 0.0)
        self.assertEqual(adapter._last_tick_duration_ms, 0.0)
        self.assertIs(adapter._prefer_read_next, True)

    def test_composition_root_facade_starts_and_ticks_through_loop_role(self) -> None:
        config = configparser.ConfigParser()
        settings = adapter_settings()
        with (
            patch.object(process, "load_adapter_config", return_value=config),
            patch.object(process, "adapter_settings", return_value=settings),
        ):
            adapter = process.DbusAdapter("/etc/evcharger.ini", paths=settings.paths)
        with (
            patch.object(adapter.loop_role, "run") as run,
            patch.object(adapter.loop_role, "tick", return_value=True) as tick,
        ):
            adapter.run()
            self.assertTrue(adapter.tick())
        run.assert_called_once_with()
        tick.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()

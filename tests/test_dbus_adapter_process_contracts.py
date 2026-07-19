#!/usr/bin/env python3
"""Construction contracts for the dedicated DBus adapter process."""

from __future__ import annotations

import configparser
import logging
import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from tests.dbus_adapter_venus_stubs import install_venus_adapter_stubs

install_venus_adapter_stubs()

import venus_evcharger.dbus_adapter.process.adapter as process
from venus_evcharger.dbus_adapter.process.config import (
    CasePreservingConfigParser,
    GatewayAdapterSettings,
    GatewayFileSettings,
    GatewayIntrospectionSettings,
    GatewayRateSettings,
    GatewaySloSettings,
    GatewayTimingSettings,
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
        timing=GatewayTimingSettings(0.11, 0.91, 31.0, 0.41),
        slo=GatewaySloSettings(2.1, 5.1, 10.1, 501.0),
        files=GatewayFileSettings("/run/lifecycle", 101, "/run/health", 3.1, 202),
        introspection=GatewayIntrospectionSettings("/run/intro", "/run/request", True),
        stale_after_seconds=12.5,
    )


class DbusAdapterProcessContractTests(unittest.TestCase):
    def test_logging_level_is_normalized_and_invalid_values_fall_back(self) -> None:
        config = CasePreservingConfigParser()
        self.assertEqual(process._logging_level_from_config(config), logging.INFO)
        config["DEFAULT"]["Logging"] = " debug "
        self.assertEqual(process._logging_level_from_config(config), logging.DEBUG)
        config["DEFAULT"]["Logging"] = "warning"
        self.assertEqual(process._logging_level_from_config(config), logging.WARNING)
        config["DEFAULT"]["Logging"] = "not-a-level"
        self.assertEqual(process._logging_level_from_config(config), logging.INFO)

    def test_adapter_initializes_every_component_and_runtime_field_from_settings(self) -> None:
        config = configparser.ConfigParser()
        settings = adapter_settings()
        component_names = (
            "DbusConnectionManager",
            "DbusRateLimiter",
            "DbusCircuitBreaker",
            "DbusCacheStore",
            "DbusCommandInbox",
            "DbusWriteScheduler",
            "DbusReadScheduler",
            "DbusReadExecutor",
            "DbusDiscoveryManager",
            "AtomicJsonWriter",
            "ResourceMonitor",
            "TickHealth",
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
        mocks["DbusCacheStore"].assert_called_once_with(settings.paths, stale_after_seconds=12.5)
        self.assertEqual(
            [call.args[0] for call in mocks["DbusCommandInbox"].call_args_list],
            [settings.paths.command_dir, settings.paths.core_command_dir],
        )
        mocks["DbusWriteScheduler"].assert_called_once_with(adapter)
        mocks["DbusReadScheduler"].assert_called_once_with(settings.read_specs)
        mocks["DbusReadExecutor"].assert_called_once_with(adapter)
        mocks["DbusDiscoveryManager"].assert_called_once_with(interval_seconds=31.0)
        for attribute, component_name in (
            ("connection", "DbusConnectionManager"),
            ("rate_limiter", "DbusRateLimiter"),
            ("circuit", "DbusCircuitBreaker"),
            ("cache", "DbusCacheStore"),
            ("write_scheduler", "DbusWriteScheduler"),
            ("read_scheduler", "DbusReadScheduler"),
            ("read_executor", "DbusReadExecutor"),
            ("discovery", "DbusDiscoveryManager"),
            ("json_writer", "AtomicJsonWriter"),
            ("resource_monitor", "ResourceMonitor"),
            ("tick_health", "TickHealth"),
        ):
            with self.subTest(attribute=attribute):
                self.assertIs(getattr(adapter, attribute), mocks[component_name].return_value)

        self.assertEqual(adapter.config_path, "/etc/evcharger.ini")
        self.assertIs(adapter.config, config)
        self.assertIs(adapter.paths, settings.paths)
        self.assertEqual(adapter.service_name, settings.service_name)
        self.assertIsNone(adapter._dbusservice)
        self.assertIs(adapter._dbusservice_registered, False)
        self.assertIs(adapter._stop, False)
        self.assertIsNone(adapter._server)
        self.assertIsNone(adapter._main_loop)
        self.assertEqual(adapter.min_tick_seconds, 0.11)
        self.assertEqual(adapter.max_tick_seconds, 0.91)
        self.assertEqual(adapter.tick_seconds, 0.11)
        self.assertEqual(adapter.cache_publish_interval_seconds, 0.41)
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
        self.assertEqual(adapter.dbus_introspection_request_path, "/run/request")
        self.assertIs(adapter.dbus_introspection_enabled, True)
        self.assertEqual(adapter._next_work_tick_monotonic, 0.0)
        self.assertEqual(adapter._last_resource_snapshot, {})
        self.assertEqual(adapter._last_introspection_full_scan_at, 0.0)
        self.assertEqual(adapter._introspection_queue_depth, 0)
        self.assertEqual(adapter._last_cache_publish_monotonic, 0.0)
        self.assertEqual(adapter._last_cache_publish_sequence, -1)
        self.assertEqual(adapter._last_health_log_monotonic, 0.0)
        self.assertEqual(adapter._last_tick_at, 0.0)
        self.assertEqual(adapter._last_tick_monotonic, 0.0)
        self.assertEqual(adapter._last_tick_duration_ms, 0.0)
        self.assertIs(adapter._prefer_read_next, True)


if __name__ == "__main__":
    unittest.main()

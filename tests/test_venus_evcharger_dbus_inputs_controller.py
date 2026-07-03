# SPDX-License-Identifier: GPL-3.0-or-later
import os
import tempfile
import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch

import venus_evcharger.inputs.dbus as wallbox_dbus_inputs
from venus_evcharger.inputs.dbus import DbusInputController
from venus_evcharger.dbus_gateway import (
    BATTERY_SOC_READ_KEY,
    DEFAULT_GATEWAY_RUN_DIR,
    GRID_POWER_READ_KEY,
    PV_POWER_READ_KEY,
    DbusCacheStore,
    DbusCommandInbox,
    GatewayPaths,
    dbus_path_key,
    gateway_paths,
)
from venus_evcharger.energy import EnergyClusterSnapshot, EnergyLearningProfile, EnergySourceDefinition, EnergySourceSnapshot


class TestDbusInputController(unittest.TestCase):
    @staticmethod
    def _make_service() -> SimpleNamespace:
        service = SimpleNamespace(
            auto_pv_service="",
            auto_pv_service_prefix="com.victronenergy.pvinverter",
            auto_pv_path="/Ac/Power",
            auto_pv_max_services=2,
            auto_pv_scan_interval_seconds=60.0,
            auto_use_dc_pv=True,
            auto_dc_pv_service="com.victronenergy.system",
            auto_dc_pv_path="/Dc/Pv/Power",
            auto_battery_service="com.victronenergy.battery.socketcan_can1",
            auto_battery_soc_path="/Soc",
            auto_battery_service_prefix="com.victronenergy.battery",
            auto_battery_scan_interval_seconds=60.0,
            auto_grid_service="com.victronenergy.system",
            auto_grid_l1_path="/Ac/Grid/L1/Power",
            auto_grid_l2_path="/Ac/Grid/L2/Power",
            auto_grid_l3_path="/Ac/Grid/L3/Power",
            auto_grid_require_all_phases=True,
            auto_dbus_backoff_base_seconds=5.0,
            auto_dbus_backoff_max_seconds=60.0,
            dbus_method_timeout_seconds=1.0,
            _resolved_auto_pv_services=[],
            _auto_pv_last_scan=0.0,
            _resolved_auto_battery_service=None,
            _auto_battery_last_scan=0.0,
            _dbus_list_backoff_until=0.0,
            _dbus_list_failures=0,
            _last_dbus_ok_at=None,
            _source_retry_after={},
            _source_retry_ready=MagicMock(return_value=True),
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _delay_source_retry=MagicMock(),
            _warning_throttled=MagicMock(),
            _reset_system_bus=MagicMock(),
            _get_system_bus=MagicMock(),
            _get_dbus_value=MagicMock(return_value=None),
            _list_dbus_services=MagicMock(return_value=[]),
            _resolve_auto_pv_services=MagicMock(return_value=[]),
            _invalidate_auto_pv_services=MagicMock(),
            _resolve_auto_battery_service=MagicMock(return_value=""),
            _invalidate_auto_battery_service=MagicMock(),
        )
        service.mark_failure = lambda source: service._mark_failure(source)
        service.mark_recovery = lambda source, detail, *args: service._mark_recovery(source, detail, *args)
        service.delay_source_retry = lambda source, now: service._delay_source_retry(source, now)
        service.warning_throttled = lambda key, *args: service._warning_throttled(key, *args)
        service.reset_system_bus = lambda: service._reset_system_bus()
        service.get_system_bus = lambda: service._get_system_bus()
        service.get_dbus_value = lambda service_name, path: service._get_dbus_value(service_name, path)
        service.list_dbus_services = lambda: service._list_dbus_services()
        service.source_retry_ready = lambda source, now: service._source_retry_ready(source, now)
        service.resolve_auto_pv_services = lambda: service._resolve_auto_pv_services()
        service.invalidate_auto_pv_services = lambda: service._invalidate_auto_pv_services()
        service.resolve_auto_battery_service = lambda: service._resolve_auto_battery_service()
        service.invalidate_auto_battery_service = lambda: service._invalidate_auto_battery_service()
        return service

    def _prepare_gateway(self, service: SimpleNamespace) -> GatewayPaths:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        paths = gateway_paths(os.path.join(tempdir.name, "run"))
        service.dbus_gateway_run_dir = paths.run_dir
        service.dbus_gateway_cache_path = paths.cache_path
        return paths

    def _write_gateway_cache(
        self,
        service: SimpleNamespace,
        *,
        values: dict[tuple[str, str], object] | None = None,
        key_values: dict[str, object] | None = None,
        services: list[str] | None = None,
    ) -> GatewayPaths:
        paths = self._prepare_gateway(service)
        store = DbusCacheStore(paths)
        for (service_name, path), value in (values or {}).items():
            store.update_value(dbus_path_key(service_name, path), value, source=f"{service_name}{path}")
        for key, value in (key_values or {}).items():
            store.update_value(str(key), value, source=f"read-key:{key}")
        if services is not None:
            store.update_services(list(services))
        store.write_snapshot_files()
        return paths

    def test_get_dbus_value_and_list_services_cover_retry_and_failure_paths(self) -> None:
        service = self._make_service()
        controller = DbusInputController(service)

        paths = self._prepare_gateway(service)
        self.assertIsNone(controller.get_dbus_value("svc", "/Path"))
        service._reset_system_bus.assert_not_called()
        commands = [command for _, command in DbusCommandInbox(paths.command_dir).load_pending()]
        self.assertEqual(commands[0]["kind"], "refresh_value")

        self._write_gateway_cache(service, values={("svc", "/Path"): 42.0})
        self.assertEqual(controller.get_dbus_value("svc", "/Path"), 42.0)

        self._write_gateway_cache(service, values={("svc", "/Text"): "not-a-number"})
        self.assertIsNone(controller.get_dbus_value("svc", "/Text"))

        with patch("venus_evcharger.inputs.dbus.time.time", return_value=10.0):
            service._dbus_list_backoff_until = 20.0
            with self.assertRaises(RuntimeError):
                controller.list_dbus_services()

        service._dbus_list_backoff_until = 0.0
        service._dbus_list_failures = 0
        paths = self._prepare_gateway(service)
        with patch("venus_evcharger.inputs.dbus.time.time", return_value=100.0):
            self.assertEqual(controller.list_dbus_services(), [])

        self.assertEqual(service._dbus_list_failures, 1)
        self.assertEqual(service._dbus_list_backoff_until, 105.0)
        commands = [command for _, command in DbusCommandInbox(paths.command_dir).load_pending()]
        self.assertEqual(commands[0]["kind"], "refresh_services")

        service._dbus_list_backoff_until = 0.0
        service._dbus_list_failures = 1
        self._write_gateway_cache(service, services=["com.victronenergy.system"])
        self.assertEqual(controller.list_dbus_services(), ["com.victronenergy.system"])
        self.assertEqual(service._dbus_list_failures, 0)

    def test_get_dbus_value_rejects_stale_gateway_cache_and_requests_refresh(self) -> None:
        service = self._make_service()
        controller = DbusInputController(service)
        paths = self._prepare_gateway(service)
        store = DbusCacheStore(paths, stale_after_seconds=1.0)
        store.update_value(dbus_path_key("svc", "/Path"), 42.0, source="svc/Path", now=100.0)
        with patch("venus_evcharger.dbus_gateway_cache._now", return_value=200.0):
            store.write_snapshot_files()

        self.assertIsNone(controller.get_dbus_value("svc", "/Path"))
        commands = [command for _, command in DbusCommandInbox(paths.command_dir).load_pending()]
        self.assertEqual(commands[0]["kind"], "refresh_value")
        self.assertEqual(commands[0]["service"], "svc")
        self.assertEqual(commands[0]["path"], "/Path")

    def test_gateway_input_reader_contracts_for_semantic_cache_reads(self) -> None:
        service = self._make_service()
        controller = DbusInputController(service)
        paths = self._write_gateway_cache(service, key_values={GRID_POWER_READ_KEY: -42.5})
        service.dbus_gateway_max_age_seconds = 3.5

        with patch("venus_evcharger.inputs.gateway_read.time.time", return_value=123.0):
            self.assertEqual(
                controller.get_gateway_read_value(GRID_POWER_READ_KEY, reason="main semantic grid power read"),
                -42.5,
            )

        self.assertEqual(service._last_dbus_ok_at, 123.0)
        service._mark_recovery.assert_called_once_with("dbus", "DBus reads recovered")
        service._mark_failure.assert_not_called()
        self.assertEqual(controller._gateway_cache_max_age_seconds(), 3.5)
        self.assertEqual(controller._gateway_client().paths.run_dir, paths.run_dir)
        with self.assertRaises(RuntimeError) as direct_dbus_error:
            controller._dbus_module()
        self.assertEqual(
            str(direct_dbus_error.exception),
            "Direct DBus access is disabled; use the DBus gateway adapter",
        )

    def test_gateway_input_reader_contracts_for_missing_invalid_and_fallback_cache_paths(self) -> None:
        service = self._make_service()
        controller = DbusInputController(service)
        paths = self._write_gateway_cache(service, key_values={PV_POWER_READ_KEY: True})
        service.dbus_gateway_cache_path = ""
        service.dbus_gateway_run_dir = paths.run_dir
        service.dbus_gateway_max_age_seconds = 0.5

        self.assertEqual(controller._gateway_snapshot()["values"][PV_POWER_READ_KEY]["value"], True)
        self.assertEqual(controller._gateway_cache_max_age_seconds(), 0.5)
        self.assertIsNone(controller._coerce_dbus_value(True))
        with patch("venus_evcharger.inputs.gateway_read.coerce_dbus_numeric", return_value=True):
            self.assertIsNone(controller._coerce_dbus_value("truthy-but-not-a-number"))
        self.assertIsNone(controller.get_gateway_read_value(PV_POWER_READ_KEY, reason="invalid boolean pv"))

        pending = [command for _, command in DbusCommandInbox(paths.command_dir).load_pending()]
        self.assertEqual(pending[0]["kind"], "refresh_value")
        self.assertEqual(pending[0]["key"], PV_POWER_READ_KEY)
        self.assertEqual(pending[0]["priority"], "read")
        self.assertEqual(pending[0]["source"], "evcharger-inputs")
        self.assertEqual(pending[0]["reason"], "invalid boolean pv")
        service._mark_failure.assert_called_once_with("dbus")

        fake_client = SimpleNamespace(request_read_key=MagicMock())
        with patch.object(controller, "_gateway_snapshot", return_value={"values": {}}), patch.object(
            controller,
            "_gateway_client",
            return_value=fake_client,
        ):
            self.assertIsNone(controller.get_gateway_read_value(GRID_POWER_READ_KEY, reason="missing grid"))
        fake_client.request_read_key.assert_called_once_with(
            GRID_POWER_READ_KEY,
            priority="read",
            reason="missing grid",
            source="evcharger-inputs",
        )

        with patch.object(controller, "_gateway_snapshot", return_value={"values": {}}), patch.object(
            controller,
            "_gateway_client",
            return_value=SimpleNamespace(request_read_key=MagicMock(side_effect=OSError("gateway offline"))),
        ):
            self.assertIsNone(controller.get_gateway_read_value(BATTERY_SOC_READ_KEY, reason="missing soc"))
        self.assertEqual(service._mark_failure.call_count, 3)

    def test_gateway_input_reader_cache_path_and_default_run_dir_contracts(self) -> None:
        service = self._make_service()
        controller = DbusInputController(service)
        explicit_paths = self._write_gateway_cache(service, key_values={GRID_POWER_READ_KEY: 11.0})
        fallback_tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(fallback_tempdir.cleanup)
        fallback_paths = gateway_paths(os.path.join(fallback_tempdir.name, "fallback-run"))
        fallback_store = DbusCacheStore(fallback_paths)
        fallback_store.update_value(GRID_POWER_READ_KEY, 22.0, source="fallback")
        fallback_store.write_snapshot_files()
        service.dbus_gateway_cache_path = f" {explicit_paths.cache_path} "
        service.dbus_gateway_run_dir = fallback_paths.run_dir

        snapshot = controller._gateway_snapshot()
        self.assertEqual(snapshot["values"][GRID_POWER_READ_KEY]["value"], 11.0)

        del service.dbus_gateway_cache_path
        service.dbus_gateway_run_dir = f" {fallback_paths.run_dir} "
        snapshot = controller._gateway_snapshot()
        self.assertEqual(snapshot["values"][GRID_POWER_READ_KEY]["value"], 22.0)

        del service.dbus_gateway_run_dir
        self.assertEqual(controller._gateway_client().paths.run_dir, DEFAULT_GATEWAY_RUN_DIR)
        with patch("venus_evcharger.inputs.gateway_read.DbusCacheStore.load_snapshot", return_value={}) as load_snapshot:
            self.assertEqual(controller._gateway_snapshot(), {})
        load_snapshot.assert_called_once_with(gateway_paths(None).cache_path)
        self.assertEqual(controller._gateway_cache_max_age_seconds(), 10.0)

    def test_pv_resolution_and_missing_pv_paths_cover_explicit_cached_and_rescan_failures(self) -> None:
        service = self._make_service()
        controller = DbusInputController(service)

        service.auto_pv_service = "explicit-pv"
        self.assertEqual(controller.resolve_auto_pv_services(), ["explicit-pv"])

        service.auto_pv_service = ""
        service._resolved_auto_pv_services = ["cached-pv"]
        service._auto_pv_last_scan = 100.0
        with patch("venus_evcharger.inputs.dbus.time.time", return_value=120.0):
            self.assertEqual(controller.resolve_auto_pv_services(), ["cached-pv"])

        service._resolved_auto_pv_services = "malformed-cache"
        service._list_dbus_services = MagicMock(return_value=["com.victronenergy.pvinverter.http_40"])
        with patch("venus_evcharger.inputs.dbus.time.time", return_value=121.0):
            self.assertEqual(controller.resolve_auto_pv_services(), ["com.victronenergy.pvinverter.http_40"])

        service._resolved_auto_pv_services = []
        service._list_dbus_services = MagicMock(return_value=["com.victronenergy.system"])
        with patch("venus_evcharger.inputs.dbus.time.time", return_value=200.0):
            with self.assertRaises(ValueError):
                controller.resolve_auto_pv_services()

        self._write_gateway_cache(service, key_values={PV_POWER_READ_KEY: 0.0})
        service._get_dbus_value.reset_mock()
        self.assertEqual(controller.get_pv_power(), 0.0)
        service._get_dbus_value.assert_not_called()

    def test_pv_source_retry_recovery_and_failure_contracts(self) -> None:
        service = self._make_service()
        controller = DbusInputController(service)
        service._source_retry_ready = MagicMock(side_effect=lambda source, now: source == "pv" and now == 12.5)

        self.assertTrue(controller._source_retry_ready("pv", 12.5))
        self.assertFalse(controller._source_retry_ready("pv", 13.0))
        service._source_retry_ready.assert_any_call("pv", 12.5)
        service._source_retry_ready.assert_any_call("pv", 13.0)

        controller._mark_source_recovery("pv", "PV readings recovered")
        service._mark_recovery.assert_called_once_with("pv", "PV readings recovered")
        service._mark_recovery.reset_mock()
        controller._mark_source_recovery("pv", "PV recovered from %s", "gateway")
        service._mark_recovery.assert_called_once_with("pv", "PV recovered from %s", "gateway")

        self.assertIsNone(
            controller._handle_source_failure(
                "pv",
                44.0,
                "pv-missing",
                60.0,
                "PV missing from %s",
                "gateway",
            )
        )
        service._mark_failure.assert_called_once_with("pv")
        service._delay_source_retry.assert_called_once_with("pv", 44.0)
        service._warning_throttled.assert_called_once_with(
            "pv-missing",
            60.0,
            "PV missing from %s",
            "gateway",
        )

    def test_pv_raw_value_reader_requests_gateway_refresh_on_missing_or_stale_values(self) -> None:
        service = self._make_service()
        controller = DbusInputController(service)
        self._write_gateway_cache(service, values={("svc", "/Ac/Power"): 42.0})

        with patch("venus_evcharger.inputs.gateway_read.time.time", return_value=222.0):
            self.assertEqual(controller.get_dbus_value("svc", "/Ac/Power"), 42.0)
        self.assertEqual(service._last_dbus_ok_at, 222.0)
        service._mark_recovery.assert_called_once_with("dbus", "DBus reads recovered")

        fake_client = SimpleNamespace(request_raw_value=MagicMock())
        stale_snapshot = {
            "values": {
                dbus_path_key("svc", "/Ac/Power"): {
                    "status": "stale",
                    "value": 99.0,
                    "age_s": 99.0,
                }
            }
        }
        with patch.object(controller, "_gateway_snapshot", return_value=stale_snapshot), patch.object(
            controller,
            "_gateway_client",
            return_value=fake_client,
        ):
            self.assertIsNone(controller.get_dbus_value("svc", "/Ac/Power"))
        fake_client.request_raw_value.assert_called_once_with(
            "svc",
            "/Ac/Power",
            priority="read",
            reason="main input cache miss",
            source="evcharger-inputs",
        )
        service._mark_failure.assert_called_once_with("dbus")

    def test_pv_service_listing_backoff_discovery_and_invalidation_contracts(self) -> None:
        service = self._make_service()
        controller = DbusInputController(service)

        service._dbus_list_backoff_until = 30.0
        with patch("venus_evcharger.inputs.pv.time.time", return_value=20.0):
            with self.assertRaises(RuntimeError) as backoff_error:
                controller.list_dbus_services()
        self.assertEqual(str(backoff_error.exception), "DBus list backoff active")

        with patch.object(controller, "_gateway_snapshot", return_value={"services": ["svc-at-boundary"]}), patch(
            "venus_evcharger.inputs.gateway_read.time.time",
            return_value=30.0,
        ), patch("venus_evcharger.inputs.pv.time.time", return_value=30.0):
            self.assertEqual(controller.list_dbus_services(), ["svc-at-boundary"])

        service._dbus_list_backoff_until = 0.0
        service._dbus_list_failures = 1
        service.auto_dbus_backoff_base_seconds = 5.0
        service.auto_dbus_backoff_max_seconds = 60.0
        fake_client = SimpleNamespace(enqueue_command=MagicMock())
        with patch.object(controller, "_gateway_snapshot", return_value={"services": []}), patch.object(
            controller,
            "_gateway_client",
            return_value=fake_client,
        ), patch("venus_evcharger.inputs.pv.time.time", return_value=100.0):
            self.assertEqual(controller.list_dbus_services(), [])
        fake_client.enqueue_command.assert_called_once_with(
            {"kind": "refresh_services", "source": "evcharger-inputs", "priority": "read"}
        )
        self.assertEqual(service._dbus_list_failures, 2)
        self.assertEqual(service._dbus_list_backoff_until, 110.0)
        service._mark_failure.assert_called_once_with("dbus")

        service._dbus_list_failures = 3
        service._dbus_list_backoff_until = 50.0
        with patch.object(
            controller,
            "_gateway_snapshot",
            return_value={"services": {"svc.b": {}, "svc.a": {}}},
        ), patch("venus_evcharger.inputs.gateway_read.time.time", return_value=111.0), patch(
            "venus_evcharger.inputs.pv.time.time",
            return_value=111.0,
        ):
            self.assertEqual(controller.list_dbus_services(), ["svc.b", "svc.a"])
        self.assertEqual(service._dbus_list_failures, 0)
        self.assertEqual(service._dbus_list_backoff_until, 0.0)
        service._mark_recovery.assert_called_with("dbus", "DBus reads recovered")

        service._resolved_auto_pv_services = ["pv"]
        service._auto_pv_last_scan = 99.0
        controller.invalidate_auto_pv_services()
        self.assertEqual(service._resolved_auto_pv_services, [])
        self.assertEqual(service._auto_pv_last_scan, 0.0)

        service._resolved_auto_battery_service = "battery"
        service._auto_battery_last_scan = 88.0
        controller.invalidate_auto_battery_service()
        self.assertIsNone(service._resolved_auto_battery_service)
        self.assertEqual(service._auto_battery_last_scan, 0.0)

    def test_pv_service_listing_state_defaults_and_name_contracts(self) -> None:
        service = self._make_service()
        controller = DbusInputController(service)
        for attr_name in (
            "_dbus_list_backoff_until",
            "_dbus_list_failures",
            "auto_dbus_backoff_base_seconds",
            "auto_dbus_backoff_max_seconds",
            "dbus_method_timeout_seconds",
        ):
            delattr(service, attr_name)

        controller._ensure_dbus_list_state()

        self.assertEqual(service._dbus_list_backoff_until, 0.0)
        self.assertEqual(service._dbus_list_failures, 0)
        self.assertEqual(service.auto_dbus_backoff_base_seconds, 5.0)
        self.assertEqual(service.auto_dbus_backoff_max_seconds, 60.0)
        self.assertEqual(service.dbus_method_timeout_seconds, 1.0)

        service._dbus_list_backoff_until = 12.0
        service._dbus_list_failures = 3
        service.auto_dbus_backoff_base_seconds = 7.0
        service.auto_dbus_backoff_max_seconds = 77.0
        service.dbus_method_timeout_seconds = 4.0
        controller._ensure_dbus_list_state()
        self.assertEqual(service._dbus_list_backoff_until, 12.0)
        self.assertEqual(service._dbus_list_failures, 3)
        self.assertEqual(service.auto_dbus_backoff_base_seconds, 7.0)
        self.assertEqual(service.auto_dbus_backoff_max_seconds, 77.0)
        self.assertEqual(service.dbus_method_timeout_seconds, 4.0)

        with patch.object(controller, "_gateway_snapshot", return_value={}):
            self.assertEqual(controller._list_dbus_names(), [])
        with patch.object(controller, "_gateway_snapshot", return_value={"services": [123, "svc"]}):
            self.assertEqual(controller._list_dbus_names(), ["123", "svc"])
        with patch.object(controller, "_gateway_snapshot", return_value={"services": {"svc.b": {}, "svc.a": {}}}):
            self.assertEqual(controller._list_dbus_names(), ["svc.b", "svc.a"])

    def test_pv_service_resolution_and_power_contracts(self) -> None:
        service = self._make_service()
        controller = DbusInputController(service)

        service.auto_pv_service = " explicit-pv "
        self.assertEqual(controller.resolve_auto_pv_services(), [" explicit-pv "])

        service.auto_pv_service = ""
        service._resolved_auto_pv_services = ["cached-pv"]
        service._auto_pv_last_scan = 100.0
        service.auto_pv_scan_interval_seconds = 60.0
        service.list_dbus_services = MagicMock(return_value=["com.victronenergy.pvinverter.http_2"])
        with patch("venus_evcharger.inputs.pv.time.time", return_value=120.0):
            self.assertEqual(controller.resolve_auto_pv_services(), ["cached-pv"])
        service.list_dbus_services.assert_not_called()

        service._resolved_auto_pv_services = ["stale-cached-pv"]
        service._auto_pv_last_scan = 100.0
        service.list_dbus_services = MagicMock(return_value=["com.victronenergy.pvinverter.http_3"])
        service.auto_pv_max_services = 2
        with patch("venus_evcharger.inputs.pv.time.time", return_value=200.0):
            self.assertEqual(controller.resolve_auto_pv_services(), ["com.victronenergy.pvinverter.http_3"])
        service.list_dbus_services.assert_called_once_with()

        service._resolved_auto_pv_services = ["", 123]
        service._auto_pv_last_scan = 190.0
        service.list_dbus_services = MagicMock(
            return_value=[
                "com.victronenergy.pvinverter.http_2",
                "com.victronenergy.pvinverter.http_1",
                "com.victronenergy.system",
            ]
        )
        service.auto_pv_max_services = 1
        with patch("venus_evcharger.inputs.pv.time.time", return_value=200.0):
            self.assertEqual(controller.resolve_auto_pv_services(), ["com.victronenergy.pvinverter.http_1"])
        service.list_dbus_services.assert_called_once_with()
        self.assertEqual(service._resolved_auto_pv_services, ["com.victronenergy.pvinverter.http_1"])
        self.assertEqual(service._auto_pv_last_scan, 200.0)

        service.list_dbus_services = MagicMock(return_value=["com.victronenergy.system"])
        service._resolved_auto_pv_services = []
        with patch("venus_evcharger.inputs.pv.time.time", return_value=300.0):
            with self.assertRaisesRegex(ValueError, "No DBus service found with prefix"):
                controller.resolve_auto_pv_services()

        service._source_retry_ready = MagicMock(return_value=True)
        controller_any = cast(Any, controller)
        controller_any.get_gateway_read_value = MagicMock(return_value=456)
        controller_any._mark_source_recovery = MagicMock()
        service._last_pv_missing_warning = "old"
        with patch("venus_evcharger.inputs.pv.time.time", return_value=400.0):
            self.assertEqual(controller.get_pv_power(), 456.0)
        service._source_retry_ready.assert_called_with("pv", 400.0)
        controller_any.get_gateway_read_value.assert_called_once_with(
            PV_POWER_READ_KEY,
            reason="main semantic PV power read",
        )
        controller_any._mark_source_recovery.assert_called_once_with("pv", "PV readings recovered")
        self.assertIsNone(service._last_pv_missing_warning)

        controller_any.get_gateway_read_value = MagicMock(return_value=None)
        controller_any._handle_source_failure = MagicMock(return_value=None)
        with patch("venus_evcharger.inputs.pv.time.time", return_value=500.0):
            self.assertIsNone(controller.get_pv_power())
        controller_any._handle_source_failure.assert_called_once_with(
            "pv",
            500.0,
            "pv-missing",
            service.auto_pv_scan_interval_seconds,
            "Auto mode could not read PV power from the DBus gateway read contract.",
        )

        service._source_retry_ready = MagicMock(return_value=False)
        controller_any.get_gateway_read_value = MagicMock(side_effect=AssertionError("should not read during retry"))
        with patch("venus_evcharger.inputs.pv.time.time", return_value=600.0):
            self.assertIsNone(controller.get_pv_power())

    def test_battery_and_grid_paths_cover_override_cache_failures_and_missing_values(self) -> None:
        service = self._make_service()
        controller = DbusInputController(service)

        service._get_dbus_value = MagicMock(side_effect=RuntimeError("offline"))
        self.assertFalse(controller._battery_service_has_soc("battery"))

        service.auto_battery_service = "configured-battery"
        with patch.object(controller, "_battery_service_has_soc", return_value=False):
            self.assertIsNone(controller._resolve_battery_service_override())

        service._resolved_auto_battery_service = "cached-battery"
        service._auto_battery_last_scan = 100.0
        with patch("venus_evcharger.inputs.dbus.time.time", return_value=120.0):
            self.assertEqual(controller._cached_auto_battery_service(120.0), "cached-battery")

        service._resolved_auto_battery_service = 123
        service._auto_battery_last_scan = 100.0
        self.assertIsNone(controller._cached_auto_battery_service(120.0))

        service._list_dbus_services = MagicMock(return_value=["com.victronenergy.system"])
        with patch.object(controller, "_battery_service_has_soc", return_value=False):
            with self.assertRaises(ValueError):
                controller._scan_auto_battery_service(200.0)

        service._source_retry_ready = MagicMock(return_value=False)
        self.assertIsNone(controller.get_battery_soc())

        service._source_retry_ready = MagicMock(return_value=True)
        service._resolve_auto_battery_service = MagicMock(return_value="battery")
        service._get_dbus_value = MagicMock(return_value="bad")
        with patch.object(controller, "_handle_source_failure", return_value=None) as handle_failure:
            self.assertIsNone(controller.get_battery_soc())
        handle_failure.assert_called_once()

        service._source_retry_ready = MagicMock(return_value=True)
        service.auto_grid_l1_path = ""
        service.auto_grid_l2_path = ""
        service.auto_grid_l3_path = ""
        self.assertIsNone(controller.get_grid_power())

        self._write_gateway_cache(service, key_values={GRID_POWER_READ_KEY: 100.0})
        service._get_dbus_value.reset_mock()
        self.assertEqual(controller.get_grid_power(), 100.0)
        service._get_dbus_value.assert_not_called()

        with patch.object(controller, "_handle_source_failure", return_value=None) as handle_failure:
            self.assertIsNone(controller._handle_missing_grid_values(False, [], 100.0))
        handle_failure.assert_called_once()

    def test_introspection_map_skip_paths_cover_main_storage_and_grid_reads(self) -> None:
        service = self._make_service()
        service.dbus_introspection_request_path = "/tmp/request.json"
        service.dbus_introspection_snapshot_path = "/tmp/map.json"
        service._get_dbus_value = MagicMock(return_value=12.0)
        controller = DbusInputController(service)

        paths = self._prepare_gateway(service)
        self.assertIsNone(controller.get_dbus_value("svc", "/Path"))
        commands = [command for _, command in DbusCommandInbox(paths.command_dir).load_pending()]
        self.assertEqual(commands[0]["kind"], "refresh_value")

        with (
            patch("venus_evcharger.inputs.storage_support.owner_path_unusable", return_value=(True, "known-missing")),
            patch("venus_evcharger.inputs.storage_support.request_owner_introspection") as request_storage,
        ):
            self.assertFalse(controller._battery_service_has_soc("battery"))
            self.assertFalse(controller._energy_service_has_readable_field("battery", "/Soc"))
            self.assertIsNone(controller._read_optional_energy_value("battery", "/Soc"))
            self.assertEqual(controller._read_optional_energy_text("battery", "/Mode"), "")

        self.assertGreaterEqual(request_storage.call_count, 4)

    def test_battery_override_cached_resolution_and_nonnumeric_grid_values(self) -> None:
        service = self._make_service()
        controller = DbusInputController(service)

        with patch.object(controller, "_battery_service_has_soc", return_value=True):
            self.assertEqual(controller._resolve_battery_service_override(), service.auto_battery_service)

        with patch.object(controller, "_resolve_battery_service_override", return_value="override-battery"):
            self.assertEqual(controller.resolve_auto_battery_service(), "override-battery")

        with (
            patch.object(controller, "_resolve_battery_service_override", return_value=None),
            patch.object(controller, "_cached_auto_battery_service", return_value="cached-battery"),
        ):
            self.assertEqual(controller.resolve_auto_battery_service(), "cached-battery")

        self._write_gateway_cache(service, key_values={GRID_POWER_READ_KEY: ["bad"]})
        with patch.object(controller, "_handle_source_failure", return_value=None) as handle_failure:
            self.assertIsNone(controller.get_grid_power())
        handle_failure.assert_called_once()

    def test_scan_auto_battery_service_propagates_dbus_listing_failures(self) -> None:
        service = self._make_service()
        controller = DbusInputController(service)
        service._list_dbus_services = MagicMock(side_effect=RuntimeError("dbus down"))

        with self.assertRaisesRegex(RuntimeError, "dbus down"):
            controller._scan_auto_battery_service(100.0)

    def test_resolve_energy_source_service_and_battery_snapshot_cover_dynamic_sources(self) -> None:
        service = self._make_service()
        controller = DbusInputController(service)
        controller_any = cast(Any, controller)
        service.auto_energy_sources = (
            EnergySourceDefinition(
                source_id="primary_battery",
                role="battery",
                connector_type="dbus",
                service_name="configured-primary",
                service_prefix="com.victronenergy.battery",
                soc_path="/Soc",
            ),
            EnergySourceDefinition(
                source_id="hybrid",
                role="hybrid-inverter",
                connector_type="dbus",
                service_name="configured-hybrid",
                service_prefix="com.victronenergy.hybrid",
                soc_path="/Soc",
                battery_power_path="/Dc/0/Power",
            ),
        )
        service._resolved_auto_energy_services = {}
        service._auto_energy_last_scan = {}
        service._resolve_auto_battery_service = MagicMock(return_value="resolved-primary")
        controller_any._energy_source_has_readable_data = MagicMock(side_effect=[True, True])

        self.assertEqual(controller._resolve_energy_source_service(service.auto_energy_sources[0]), "resolved-primary")
        self.assertEqual(controller._resolve_energy_source_service(service.auto_energy_sources[1]), "configured-hybrid")

        service._resolved_auto_energy_services = {"hybrid": "cached-hybrid"}
        service._auto_energy_last_scan = {"hybrid": 100.0}
        cached_source = EnergySourceDefinition(
            source_id="hybrid",
            role="hybrid-inverter",
            connector_type="dbus",
            service_prefix="com.victronenergy.hybrid",
            soc_path="/Soc",
            battery_power_path="/Dc/0/Power",
        )
        with patch("venus_evcharger.inputs.storage.time.time", return_value=120.0):
            self.assertEqual(controller._resolve_energy_source_service(cached_source), "cached-hybrid")

        service._resolved_auto_energy_services = {"hybrid": 123}
        service._auto_energy_last_scan = {"hybrid": 100.0}
        self.assertIsNone(controller._energy_cache_valid("hybrid", 120.0))

        missing_source = EnergySourceDefinition(source_id="missing", role="battery", connector_type="dbus")
        with self.assertRaisesRegex(ValueError, "No readable DBus service configured"):
            controller._resolve_energy_source_service(missing_source)

        prefixed_source = EnergySourceDefinition(
            source_id="prefixed",
            role="battery",
            connector_type="dbus",
            service_prefix="com.victronenergy.battery",
        )
        service._list_dbus_services = MagicMock(return_value=["com.victronenergy.system"])
        with self.assertRaisesRegex(ValueError, "No DBus service found"):
            controller._resolve_energy_source_service(prefixed_source)

        service.auto_energy_sources = ()
        default_sources = controller._battery_snapshot_sources()
        self.assertEqual(len(default_sources), 1)
        self.assertEqual(default_sources[0].source_id, "primary_battery")

        service.get_dbus_value = MagicMock(return_value="12.5")
        self.assertEqual(controller._read_optional_energy_value("svc", "/Soc"), 12.5)
        self.assertIsNone(controller._battery_soc_numeric("bad"))
        self.assertEqual(controller._battery_soc_numeric("34.5"), 34.5)
        with self.assertRaisesRegex(TypeError, "Battery SOC is not numeric"):
            controller._battery_snapshot_validate_soc(
                None,
                EnergyClusterSnapshot(
                    sources=(EnergySourceSnapshot(source_id="primary_battery", role="battery", service_name="svc"),)
                ),
            )

        primary_source = EnergySourceDefinition(
            source_id="primary_battery",
            role="battery",
            connector_type="dbus",
            service_name="primary",
            soc_path="/Soc",
            battery_power_path="/Power",
            operating_mode_path="/Mode",
        )
        service.invalidate_auto_battery_service = MagicMock()
        controller_any._resolve_energy_source_service = MagicMock(return_value="primary")
        controller_any._read_optional_energy_value = MagicMock(
            side_effect=[OSError("offline"), 45.0, -100.0, None, None, None]
        )
        controller_any._read_optional_energy_text = MagicMock(return_value="mode")
        snapshot = controller._dbus_energy_source_snapshot(primary_source, 123.0)
        self.assertEqual(snapshot.soc, 45.0)
        self.assertEqual(snapshot.net_battery_power_w, -100.0)
        service.invalidate_auto_battery_service.assert_called_once_with()

    def test_get_battery_snapshot_returns_forecast_payload_and_failure_fallback(self) -> None:
        service = self._make_service()
        controller = DbusInputController(service)
        controller_any = cast(Any, controller)
        service.auto_energy_sources = (
            EnergySourceDefinition(source_id="primary_battery", role="battery", connector_type="dbus"),
        )
        service._handle_source_failure = MagicMock(return_value=None)
        service._source_retry_ready = MagicMock(return_value=True)
        service._service = SimpleNamespace(_last_energy_learning_profiles={})
        controller_any._source_retry_ready = service._source_retry_ready
        controller_any._handle_source_failure = service._handle_source_failure
        controller_any._mark_source_recovery = MagicMock()

        with patch(
            "venus_evcharger.inputs.storage.read_energy_source_snapshot",
            return_value=EnergySourceSnapshot(
                source_id="primary_battery",
                role="battery",
                service_name="svc",
                soc=55.0,
                usable_capacity_wh=5000.0,
                net_battery_power_w=-500.0,
                grid_interaction_w=-100.0,
                online=True,
                confidence=0.8,
                captured_at=100.0,
            ),
        ), patch("venus_evcharger.inputs.storage.time.time", return_value=100.0):
            snapshot = controller.get_battery_snapshot()

        self.assertEqual(snapshot["battery_soc"], 55.0)
        self.assertEqual(snapshot["battery_combined_soc"], 55.0)
        self.assertEqual(snapshot["battery_combined_charge_power_w"], 500.0)
        self.assertEqual(snapshot["battery_headroom_charge_w"], 0.0)
        self.assertEqual(snapshot["expected_near_term_export_w"], 475.0)
        self.assertEqual(snapshot["battery_source_count"], 1)

        service._source_retry_ready = MagicMock(return_value=False)
        controller_any._source_retry_ready = service._source_retry_ready
        self.assertEqual(controller.get_battery_snapshot(), {"battery_soc": None})

        service._source_retry_ready = MagicMock(return_value=True)
        controller_any._source_retry_ready = service._source_retry_ready
        with patch("venus_evcharger.inputs.storage.read_energy_source_snapshot", side_effect=RuntimeError("boom")):
            failed = controller.get_battery_snapshot()

        self.assertIsNone(failed["battery_soc"])
        self.assertEqual(failed["battery_source_count"], 0)

        service._handle_source_failure.reset_mock()
        with patch("venus_evcharger.inputs.storage_support.logging.debug") as debug_log:
            self.assertIsNone(controller._handle_missing_grid_values(True, ["/L2"], 100.0))
        debug_log.assert_called_once()
        service._handle_source_failure.assert_called_once()

    def test_get_battery_snapshot_includes_discharge_balance_diagnostics(self) -> None:
        service = self._make_service()
        controller = DbusInputController(service)
        controller_any = cast(Any, controller)
        service.auto_energy_sources = (
            EnergySourceDefinition(source_id="victron", profile_name="dbus-battery", role="battery", connector_type="dbus"),
            EnergySourceDefinition(
                source_id="huawei",
                profile_name="huawei_ma_native_ap",
                role="hybrid-inverter",
                connector_type="dbus",
            ),
        )
        service._source_retry_ready = MagicMock(return_value=True)
        service._service = SimpleNamespace(
            _last_energy_learning_profiles={
                "victron": EnergyLearningProfile(source_id="victron", observed_min_discharge_soc=40.0),
                "huawei": EnergyLearningProfile(source_id="huawei", observed_min_discharge_soc=20.0),
            }
        )
        controller_any._source_retry_ready = service._source_retry_ready
        controller_any._handle_source_failure = MagicMock(return_value=None)
        controller_any._mark_source_recovery = MagicMock()

        with patch(
            "venus_evcharger.inputs.storage.read_energy_source_snapshot",
            side_effect=[
                EnergySourceSnapshot(
                    source_id="victron",
                    role="battery",
                    service_name="svc-victron",
                    soc=60.0,
                    usable_capacity_wh=10000.0,
                    net_battery_power_w=1500.0,
                    online=True,
                    confidence=1.0,
                    captured_at=100.0,
                ),
                EnergySourceSnapshot(
                    source_id="huawei",
                    role="hybrid-inverter",
                    service_name="svc-huawei",
                    soc=60.0,
                    usable_capacity_wh=5000.0,
                    net_battery_power_w=0.0,
                    online=True,
                    confidence=1.0,
                    captured_at=100.0,
                ),
            ],
        ), patch("venus_evcharger.inputs.storage.time.time", return_value=100.0):
            snapshot = controller.get_battery_snapshot()

        battery_sources = cast(list[dict[str, Any]], snapshot["battery_sources"])
        self.assertEqual(snapshot["battery_discharge_balance_mode"], "capacity_reserve_weighted")
        self.assertEqual(snapshot["battery_discharge_balance_target_distribution_mode"], "capacity_reserve_weighted")
        self.assertEqual(snapshot["battery_discharge_balance_error_w"], 500.0)
        self.assertEqual(snapshot["battery_discharge_balance_active_source_count"], 1)
        self.assertEqual(snapshot["battery_discharge_balance_control_candidate_count"], 1)
        self.assertEqual(snapshot["battery_discharge_balance_control_ready_count"], 1)
        self.assertEqual(battery_sources[0]["discharge_balance_target_power_w"], 1000.0)
        self.assertEqual(battery_sources[1]["discharge_balance_target_power_w"], 500.0)
        self.assertEqual(battery_sources[0]["discharge_balance_weight_basis"], "available_energy_above_reserve")
        self.assertEqual(battery_sources[1]["discharge_balance_weight_basis"], "available_energy_above_reserve")
        self.assertEqual(battery_sources[0]["discharge_balance_control_support"], "unsupported")
        self.assertEqual(battery_sources[1]["discharge_balance_control_support"], "experimental")
        self.assertFalse(battery_sources[0]["discharge_balance_control_candidate"])
        self.assertTrue(battery_sources[1]["discharge_balance_control_candidate"])

    def test_storage_helpers_cover_empty_paths_retry_and_non_primary_failure(self) -> None:
        service = self._make_service()
        controller = DbusInputController(service)
        controller_any = cast(Any, controller)

        self.assertIsNone(controller._read_optional_energy_value("svc", ""))
        self.assertEqual(controller._read_optional_energy_text("svc", ""), "")
        self.assertEqual(controller._failure_soc_value(42), 42.0)
        self.assertIsNone(controller._failure_soc_value(True))
        self.assertIsNone(controller._failure_soc_value("bad"))

        self._write_gateway_cache(service, key_values={BATTERY_SOC_READ_KEY: 44.0})
        service._get_dbus_value.reset_mock()
        self.assertEqual(controller.get_battery_soc(), 44.0)
        service._get_dbus_value.assert_not_called()

        controller_any._resolve_energy_source_service = MagicMock(return_value="svc")
        controller_any._primary_energy_source = MagicMock(
            return_value=EnergySourceDefinition(source_id="primary_battery", role="battery", connector_type="dbus")
        )
        controller_any._read_optional_energy_value = MagicMock(side_effect=RuntimeError("offline"))
        source = EnergySourceDefinition(
            source_id="secondary",
            role="hybrid-inverter",
            connector_type="dbus",
            service_name="svc",
            soc_path="/Soc",
        )
        with self.assertRaisesRegex(RuntimeError, "offline"):
            controller._dbus_energy_source_snapshot(source, 10.0)

    def test_storage_energy_resolution_text_and_soc_validation_cover_remaining_edges(self) -> None:
        service = self._make_service()
        controller = DbusInputController(service)
        controller_any = cast(Any, controller)

        prefixed_source = EnergySourceDefinition(
            source_id="prefixed",
            role="hybrid-inverter",
            connector_type="dbus",
            service_prefix="com.victronenergy.hybrid",
            soc_path="/Soc",
        )
        service._resolved_auto_energy_services = {}
        service._auto_energy_last_scan = {}
        service._list_dbus_services = MagicMock(return_value=["com.victronenergy.hybrid.demo"])
        controller_any._energy_source_has_readable_data = MagicMock(return_value=True)
        with patch("venus_evcharger.inputs.storage.time.time", return_value=50.0):
            self.assertEqual(controller._resolve_energy_source_service(prefixed_source), "com.victronenergy.hybrid.demo")

        service._get_dbus_value = MagicMock(return_value="support")
        self.assertEqual(controller._read_optional_energy_text("svc", "/Mode"), "support")
        service._get_dbus_value = MagicMock(return_value=None)
        self.assertEqual(controller._read_optional_energy_text("svc", "/Mode"), "")

        invalid_soc_source = EnergySourceDefinition(
            source_id="primary_battery",
            role="battery",
            connector_type="dbus",
            soc_path="/Soc",
            operating_mode_path="/Mode",
        )
        controller_any._resolve_energy_source_service = MagicMock(return_value="svc")
        controller_any._primary_energy_source = MagicMock(return_value=invalid_soc_source)
        values = iter([150.0, None, None, None, None])
        def _next_value(_service_name: str, _path: str) -> float | None:
            return next(values)

        controller_any._read_optional_energy_value = MagicMock(side_effect=_next_value)
        controller_any._read_optional_energy_text = MagicMock(return_value="idle")
        snapshot = controller._dbus_energy_source_snapshot(invalid_soc_source, 10.0)
        self.assertIsNone(snapshot.soc)

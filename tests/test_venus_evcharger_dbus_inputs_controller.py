# SPDX-License-Identifier: GPL-3.0-or-later
import os
import tempfile
import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, call, patch

import venus_evcharger.inputs.dbus as wallbox_dbus_inputs
from venus_evcharger.inputs import storage_support as storage_support_module
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
    def _energy_services_cache(service: SimpleNamespace) -> dict[str, str]:
        cache = service._resolved_auto_energy_services
        if not isinstance(cache, dict):
            raise AssertionError("_resolved_auto_energy_services must be restored to a dict")
        return cast(dict[str, str], cache)

    @staticmethod
    def _energy_scan_cache(service: SimpleNamespace) -> dict[str, float]:
        cache = service._auto_energy_last_scan
        if not isinstance(cache, dict):
            raise AssertionError("_auto_energy_last_scan must be restored to a dict")
        return cast(dict[str, float], cache)

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

    def test_storage_support_battery_service_resolution_contracts_are_exact(self) -> None:
        service = self._make_service()
        controller = DbusInputController(service)
        controller_any = cast(Any, controller)
        service.auto_battery_service = "configured-battery"
        service.auto_battery_service_prefix = "com.example.battery"
        service.auto_battery_soc_path = "/SocCustom"
        service.auto_battery_capacity_wh = 12345.0
        service.auto_battery_power_path = "/Dc/Battery/Power"
        service.auto_battery_ac_power_path = "/Ac/Power"
        service.auto_battery_pv_power_path = "/Pv/Power"
        service.auto_battery_grid_interaction_path = "/Grid/Power"
        service.auto_battery_operating_mode_path = "/Mode"

        source = controller._default_primary_energy_source()
        self.assertEqual(source.source_id, "primary_battery")
        self.assertEqual(source.role, "battery")
        self.assertEqual(source.service_name, "configured-battery")
        self.assertEqual(source.service_prefix, "com.example.battery")
        self.assertEqual(source.soc_path, "/SocCustom")
        self.assertEqual(source.usable_capacity_wh, 12345.0)
        self.assertEqual(source.battery_power_path, "/Dc/Battery/Power")
        self.assertEqual(source.ac_power_path, "/Ac/Power")
        self.assertEqual(source.pv_power_path, "/Pv/Power")
        self.assertEqual(source.grid_interaction_path, "/Grid/Power")
        self.assertEqual(source.operating_mode_path, "/Mode")

        configured_source = EnergySourceDefinition(source_id="configured", role="hybrid-inverter", service_name="svc")
        service.auto_energy_sources = (configured_source,)
        self.assertEqual(controller._configured_primary_energy_sources(), (configured_source,))
        self.assertIs(controller._primary_energy_source(), configured_source)
        service.auto_energy_sources = ()
        self.assertEqual(controller._configured_primary_energy_sources(), ())
        delattr(service, "auto_energy_sources")
        self.assertEqual(controller._configured_primary_energy_sources(), ())

        for attr in (
            "auto_battery_service",
            "auto_battery_service_prefix",
            "auto_battery_soc_path",
            "auto_battery_capacity_wh",
            "auto_battery_power_path",
            "auto_battery_ac_power_path",
            "auto_battery_pv_power_path",
            "auto_battery_grid_interaction_path",
            "auto_battery_operating_mode_path",
        ):
            delattr(service, attr)
        fallback_source = controller._default_primary_energy_source()
        self.assertEqual(fallback_source.service_name, "")
        self.assertEqual(fallback_source.service_prefix, "")
        self.assertEqual(fallback_source.soc_path, "/Soc")
        self.assertIsNone(fallback_source.usable_capacity_wh)
        self.assertEqual(fallback_source.battery_power_path, "")
        self.assertEqual(fallback_source.ac_power_path, "")
        self.assertEqual(fallback_source.pv_power_path, "")
        self.assertEqual(fallback_source.grid_interaction_path, "")
        self.assertEqual(fallback_source.operating_mode_path, "")
        self.assertEqual(storage_support_module._text_attr(SimpleNamespace(value=0), "value", "fallback"), "fallback")
        self.assertEqual(storage_support_module._text_attr(SimpleNamespace(value=""), "value", "fallback"), "fallback")
        self.assertEqual(storage_support_module._text_attr(SimpleNamespace(value="actual"), "value", "fallback"), "actual")
        self.assertEqual(storage_support_module._text_attr(SimpleNamespace(), "missing", "fallback"), "fallback")
        self.assertEqual(storage_support_module._float_attr(SimpleNamespace(value=12), "value"), 12.0)
        self.assertIsNone(storage_support_module._float_attr(SimpleNamespace(value=True), "value"))
        self.assertIsNone(storage_support_module._float_attr(SimpleNamespace(value="12"), "value"))
        self.assertEqual(storage_support_module._dict_attr(SimpleNamespace(value={"a": 1}), "value"), {"a": 1})
        self.assertIsNone(storage_support_module._dict_attr(SimpleNamespace(value=[]), "value"))
        self.assertIsNone(storage_support_module._dict_attr(SimpleNamespace(), "missing"))
        self.assertEqual(storage_support_module._numeric_mapping_value({"a": 1}, "a"), 1.0)
        self.assertIsNone(storage_support_module._numeric_mapping_value({"a": True}, "a"))
        self.assertIsNone(storage_support_module._numeric_mapping_value({"a": "1"}, "a"))
        self.assertEqual(
            storage_support_module._energy_cache_entry(
                SimpleNamespace(_resolved_auto_energy_services={"aux": "cached"}, _auto_energy_last_scan={"aux": 12.0}),
                "aux",
            ),
            ("cached", 12.0),
        )
        self.assertIsNone(
            storage_support_module._energy_cache_entry(
                SimpleNamespace(_resolved_auto_energy_services={"aux": ""}, _auto_energy_last_scan={"aux": 12.0}),
                "aux",
            )
        )
        self.assertIsNone(
            storage_support_module._energy_cache_entry(
                SimpleNamespace(_resolved_auto_energy_services={"aux": "cached"}, _auto_energy_last_scan={"aux": "bad"}),
                "aux",
            )
        )

        service.auto_battery_soc_path = "/Soc"
        service.auto_battery_service = "explicit-battery"
        service.auto_battery_service_prefix = "com.example.battery"
        controller_any._introspection_says_skip = MagicMock(return_value=False)
        service._get_dbus_value = MagicMock(return_value=55.0)
        self.assertTrue(controller._battery_service_has_soc("battery-a"))
        service._get_dbus_value.assert_called_once_with("battery-a", "/Soc")
        controller_any._introspection_says_skip.assert_called_once_with("battery-a", "/Soc", priority=80)

        controller_any._introspection_says_skip = MagicMock(return_value=True)
        service._get_dbus_value.reset_mock()
        self.assertFalse(controller._battery_service_has_soc("battery-a"))
        service._get_dbus_value.assert_not_called()

        controller_any._introspection_says_skip = MagicMock(return_value=False)
        service._get_dbus_value = MagicMock(side_effect=RuntimeError("offline"))
        with patch.object(controller, "_request_introspection") as request:
            self.assertFalse(controller._battery_service_has_soc("battery-a"))
        request.assert_called_once_with("battery-a", "/Soc", priority=95, reason="battery SOC probe failed")

        readable_source = EnergySourceDefinition(
            source_id="aux",
            role="battery",
            service_name="configured-aux",
            service_prefix="com.example.aux",
            soc_path="/Soc",
            battery_power_path="/Dc/Power",
        )
        service.auto_energy_sources = (readable_source,)
        controller_any._battery_service_has_soc = MagicMock(return_value=False)
        controller_any._energy_source_has_readable_data = MagicMock(return_value=True)
        self.assertEqual(controller._resolve_battery_service_override(), "configured-aux")
        controller_any._battery_service_has_soc.assert_called_once_with("configured-aux")
        controller_any._energy_source_has_readable_data.assert_called_once_with(readable_source, "configured-aux")
        controller_any._energy_source_has_readable_data.reset_mock()
        self.assertEqual(controller._configured_energy_source_service(readable_source, 10.0), "configured-aux")
        controller_any._energy_source_has_readable_data.assert_called_once_with(readable_source, "configured-aux")
        self.assertEqual(self._energy_services_cache(service)["aux"], "configured-aux")
        self.assertEqual(self._energy_scan_cache(service)["aux"], 10.0)

        controller_any._battery_service_has_soc = MagicMock(return_value=True)
        controller_any._energy_source_has_readable_data = MagicMock(side_effect=AssertionError("not needed after SOC"))
        self.assertEqual(controller._resolve_battery_service_override(), "configured-aux")
        controller_any._energy_source_has_readable_data.assert_not_called()

        service.auto_energy_sources = (EnergySourceDefinition(source_id="blank", role="battery", service_name=""),)
        controller_any._battery_service_has_soc = MagicMock(side_effect=AssertionError("blank service should not probe"))
        controller_any._energy_source_has_readable_data = MagicMock(side_effect=AssertionError("blank service should not probe"))
        self.assertIsNone(controller._resolve_battery_service_override())

        service.auto_energy_sources = (readable_source,)
        controller_any._battery_service_has_soc = MagicMock(return_value=False)
        controller_any._energy_source_has_readable_data = MagicMock(return_value=False)
        with patch("venus_evcharger.inputs.storage_support.logging.debug") as debug_log:
            self.assertIsNone(controller._resolve_battery_service_override())
        debug_log.assert_called_once_with(
            "Auto battery service override %s missing SOC, falling back to prefix scan.",
            "configured-aux",
        )
        self.assertIsNone(controller._configured_energy_source_service(readable_source, 20.0))
        service._resolved_auto_energy_services = {"aux": "cached-aux"}
        service._auto_energy_last_scan = {"aux": 15.0}
        service.auto_battery_scan_interval_seconds = 60.0
        service._resolved_auto_battery_service = "cached-battery"
        service._auto_battery_last_scan = 15.0
        self.assertEqual(controller._cached_auto_battery_service(30.0), "cached-battery")
        self.assertIsNone(controller._cached_auto_battery_service(100.0))
        self.assertEqual(controller._energy_cache_valid("aux", 30.0), "cached-aux")
        self.assertIsNone(controller._energy_cache_valid("aux", 100.0))
        service._resolved_auto_energy_services = {"aux": ""}
        service._auto_energy_last_scan = {"aux": 15.0}
        self.assertIsNone(controller._energy_cache_valid("aux", 30.0))
        service._resolved_auto_energy_services = {"aux": "cached-aux"}
        service._auto_energy_last_scan = {"other": 15.0}
        self.assertIsNone(controller._energy_cache_valid("aux", 30.0))
        self.assertIsNone(controller._energy_cache_valid("aux", 100.0))
        service._resolved_auto_energy_services = {"aux": "cached-aux"}
        service._auto_energy_last_scan = {"aux": True}
        self.assertIsNone(controller._energy_cache_valid("aux", 30.0))
        service._resolved_auto_energy_services = {"aux": "cached-aux"}
        service._auto_energy_last_scan = None
        self.assertIsNone(controller._energy_cache_valid("aux", 30.0))
        service._resolved_auto_energy_services = "bad"
        service._auto_energy_last_scan = "bad"
        self.assertIsNone(controller._energy_cache_valid("aux", 30.0))
        self.assertEqual(controller._remember_energy_service("aux", "remembered", 40.0), "remembered")
        self.assertEqual(self._energy_services_cache(service)["aux"], "remembered")
        self.assertEqual(self._energy_scan_cache(service)["aux"], 40.0)

        service._list_dbus_services = MagicMock(return_value=["com.example.aux.1", "com.example.other"])
        probe_calls: list[tuple[EnergySourceDefinition, str]] = []

        def _readable_probe(probed_source: EnergySourceDefinition, candidate: str) -> bool:
            probe_calls.append((probed_source, candidate))
            return probed_source is readable_source and candidate.endswith(".1")

        controller_any._energy_source_has_readable_data = MagicMock(side_effect=_readable_probe)
        self.assertEqual(controller._discovered_energy_source_service(readable_source, 50.0), "com.example.aux.1")
        self.assertEqual(self._energy_services_cache(service)["aux"], "com.example.aux.1")
        self.assertEqual(self._energy_scan_cache(service)["aux"], 50.0)
        self.assertEqual(probe_calls, [(readable_source, "com.example.aux.1")])
        with self.assertRaisesRegex(ValueError, "No readable DBus service configured"):
            controller._discovered_energy_source_service(
                EnergySourceDefinition(source_id="missing-prefix", role="battery"),
                60.0,
            )

        delattr(controller, "_introspection_says_skip")
        with (
            patch(
                "venus_evcharger.inputs.storage_support.owner_path_unusable",
                side_effect=lambda svc, owner, path: (True, "not-there")
                if (svc is service and owner == "svc" and path == "/Missing")
                else (False, "wrong-call"),
            ) as owner_unusable,
            patch("venus_evcharger.inputs.storage_support.request_owner_introspection") as request_owner,
            patch("venus_evcharger.inputs.storage_support.logging.debug") as log_debug,
        ):
            self.assertTrue(controller._introspection_says_skip("svc", "/Missing", priority=70))
        owner_unusable.assert_called_once_with(service, "svc", "/Missing")
        log_debug.assert_called_once_with(
            "Skipping %s %s from DBus introspection cache: %s",
            "svc",
            "/Missing",
            "not-there",
        )
        request_owner.assert_called_once_with(
            service,
            "svc",
            "/Missing",
            priority=70,
            reason="known-unusable input path",
            source="evcharger-inputs",
        )
        with patch("venus_evcharger.inputs.storage_support.owner_path_unusable", return_value=(False, "")):
            self.assertFalse(controller._introspection_says_skip("svc", "/Present", priority=70))

        controller_any._handle_source_failure = MagicMock(return_value=None)
        with patch("venus_evcharger.inputs.storage_support.logging.debug") as grid_debug:
            self.assertIsNone(controller._handle_missing_grid_values(True, ["/L2", "/L3"], 77.0))
        grid_debug.assert_called_once_with(
            "Auto grid readings incomplete for %s, missing paths: %s",
            service.auto_grid_service,
            "/L2, /L3",
        )
        controller_any._handle_source_failure.assert_called_once_with(
            "grid",
            77.0,
            "grid-missing",
            service.auto_pv_scan_interval_seconds,
            "Auto mode could not read grid power from %s.",
            service.auto_grid_service,
        )
        controller_any._handle_source_failure.reset_mock()
        with patch("venus_evcharger.inputs.storage_support.logging.debug") as grid_debug:
            self.assertIsNone(controller._handle_missing_grid_values(False, [], 78.0))
        grid_debug.assert_not_called()
        controller_any._handle_source_failure.assert_called_once()
        controller_any._handle_source_failure.reset_mock()
        with patch("venus_evcharger.inputs.storage_support.logging.debug") as grid_debug:
            self.assertIsNone(controller._handle_missing_grid_values(False, ["/L1"], 79.0))
        grid_debug.assert_not_called()
        controller_any._handle_source_failure.assert_called_once()

    def test_storage_support_readability_probe_scan_and_resolution_edges_are_exact(self) -> None:
        service = self._make_service()
        controller = DbusInputController(service)
        controller_any = cast(Any, controller)
        source = EnergySourceDefinition(
            source_id="primary_battery",
            role="battery",
            service_prefix="com.example.battery",
            soc_path="/Soc",
            battery_power_path="/Dc/Power",
            ac_power_path="/Ac/Power",
            pv_power_path="/Pv/Power",
            grid_interaction_path="/Grid/Power",
            operating_mode_path="/Mode",
        )

        controller_any._introspection_says_skip = MagicMock(return_value=False)
        service._get_dbus_value = MagicMock(return_value=None)
        self.assertFalse(controller._energy_service_has_readable_field("svc", ""))
        controller_any._introspection_says_skip.assert_not_called()
        service._get_dbus_value.assert_not_called()

        controller_any._introspection_says_skip = MagicMock(return_value=True)
        self.assertFalse(controller._energy_service_has_readable_field("svc", "/Soc"))
        controller_any._introspection_says_skip.assert_called_once_with("svc", "/Soc", priority=85)
        service._get_dbus_value.assert_not_called()

        controller_any._introspection_says_skip = MagicMock(return_value=False)
        service._get_dbus_value = MagicMock(return_value=0.0)
        self.assertTrue(controller._energy_service_has_readable_field("svc", "/Soc"))
        service._get_dbus_value.assert_called_once_with("svc", "/Soc")

        service._get_dbus_value = MagicMock(return_value=None)
        self.assertFalse(controller._energy_service_has_readable_field("svc", "/Soc"))
        with patch.object(controller, "_request_introspection") as request:
            service._get_dbus_value = MagicMock(side_effect=RuntimeError("offline"))
            self.assertFalse(controller._energy_service_has_readable_field("svc", "/Soc"))
        request.assert_called_once_with("svc", "/Soc", priority=95, reason="energy-source field probe failed")

        controller_any._introspection_says_skip = MagicMock(return_value=False)
        service._get_dbus_value = MagicMock(side_effect=lambda _svc, path: 1.0 if path == "/Pv/Power" else None)
        self.assertTrue(controller._energy_source_has_readable_data(source, "svc"))
        self.assertEqual(
            [call_args.args for call_args in service._get_dbus_value.call_args_list],
            [
                ("svc", "/Soc"),
                ("svc", "/Dc/Power"),
                ("svc", "/Ac/Power"),
                ("svc", "/Pv/Power"),
            ],
        )

        service._get_dbus_value = MagicMock(return_value=None)
        self.assertFalse(controller._energy_source_has_readable_data(source, "svc"))
        self.assertEqual(service._get_dbus_value.call_count, 6)

        service.auto_battery_service = ""
        service._resolved_auto_battery_service = None
        service._auto_battery_last_scan = 0.0
        service.auto_energy_sources = (source,)
        service._list_dbus_services = MagicMock(
            return_value=["com.example.other", "com.example.battery.2", "com.example.battery.1"]
        )
        service._get_dbus_value = MagicMock(side_effect=lambda svc, _path: 42.0 if svc.endswith(".1") else None)
        with patch("venus_evcharger.inputs.storage_support.time.time", return_value=300.0), patch(
            "venus_evcharger.inputs.storage_support.logging.debug"
        ) as log_debug:
            self.assertEqual(controller.resolve_auto_battery_service(), "com.example.battery.1")
        self.assertEqual(service._resolved_auto_battery_service, "com.example.battery.1")
        self.assertEqual(service._auto_battery_last_scan, 300.0)
        self.assertEqual(service._resolved_auto_energy_services["primary_battery"], "com.example.battery.1")
        self.assertEqual(service._auto_energy_last_scan["primary_battery"], 300.0)
        log_debug.assert_called_once_with("Auto battery service resolved: %s", "com.example.battery.1")

        fallback_source = EnergySourceDefinition(
            source_id="primary_battery",
            role="battery",
            service_prefix="",
            soc_path="/Soc",
        )
        service.auto_energy_sources = (fallback_source,)
        service.auto_battery_service_prefix = "com.example.fallback"
        service._list_dbus_services = MagicMock(return_value=["com.example.other", "com.example.fallback.1"])
        service._get_dbus_value = MagicMock(side_effect=lambda svc, _path: 7.0 if svc.endswith(".1") else None)
        self.assertEqual(controller._scan_auto_battery_service(320.0), "com.example.fallback.1")
        self.assertEqual(service._resolved_auto_battery_service, "com.example.fallback.1")
        self.assertEqual(service._auto_battery_last_scan, 320.0)

        service.auto_battery_service_prefix = ""
        with self.assertRaisesRegex(ValueError, "No DBus service prefix configured"):
            controller._scan_auto_battery_service(330.0)

        service.auto_energy_sources = (source,)
        service.auto_battery_service_prefix = "com.example.none"
        service._list_dbus_services = MagicMock(return_value=["com.example.other"])
        with self.assertRaisesRegex(ValueError, "No DBus service found with prefix 'com.example.battery'"):
            controller._scan_auto_battery_service(340.0)

        with patch.object(controller, "_resolve_battery_service_override", return_value=None), patch.object(
            controller,
            "_cached_auto_battery_service",
            side_effect=lambda now: None if now == 350.0 else (_ for _ in ()).throw(AssertionError(f"bad now {now!r}")),
        ), patch.object(controller, "_scan_auto_battery_service", return_value="scanned") as scan:
            with patch("venus_evcharger.inputs.storage_support.time.time", return_value=350.0):
                self.assertEqual(controller.resolve_auto_battery_service(), "scanned")
        scan.assert_called_once_with(350.0)

    def test_scan_auto_battery_service_propagates_dbus_listing_failures(self) -> None:
        service = self._make_service()
        controller = DbusInputController(service)
        service._list_dbus_services = MagicMock(side_effect=RuntimeError("dbus down"))

        with self.assertRaisesRegex(RuntimeError, "dbus down"):
            controller._scan_auto_battery_service(100.0)

    def test_storage_support_cache_and_resolution_time_contracts_are_exact(self) -> None:
        service = self._make_service()
        controller = DbusInputController(service)
        controller_any = cast(Any, controller)

        service._resolved_auto_battery_service = "cached-battery"
        delattr(service, "_auto_battery_last_scan")
        self.assertIsNone(controller._cached_auto_battery_service(1.0))
        service._auto_battery_last_scan = True
        self.assertIsNone(controller._cached_auto_battery_service(1.0))

        secondary = EnergySourceDefinition(
            source_id="secondary",
            role="battery",
            service_name="configured-secondary",
            service_prefix="com.example.secondary",
        )
        service.auto_energy_sources = (EnergySourceDefinition(source_id="primary_battery", role="battery"),)

        def _configured_with_exact_time(source: EnergySourceDefinition, now: float) -> str:
            self.assertIs(source, secondary)
            self.assertEqual(now, 444.0)
            return "configured-secondary"

        controller_any._configured_energy_source_service = MagicMock(side_effect=_configured_with_exact_time)
        controller_any._energy_cache_valid = MagicMock(side_effect=AssertionError("cache should not run"))
        controller_any._discovered_energy_source_service = MagicMock(side_effect=AssertionError("discovery should not run"))
        with patch("venus_evcharger.inputs.storage_support.time.time", return_value=444.0):
            self.assertEqual(controller._resolve_energy_source_service(secondary), "configured-secondary")

        def _discover_with_exact_time(source: EnergySourceDefinition, now: float) -> str:
            self.assertIs(source, secondary)
            self.assertEqual(now, 555.0)
            return "discovered-secondary"

        controller_any._configured_energy_source_service = MagicMock(return_value=None)
        controller_any._energy_cache_valid = MagicMock(return_value=None)
        controller_any._discovered_energy_source_service = MagicMock(side_effect=_discover_with_exact_time)
        with patch("venus_evcharger.inputs.storage_support.time.time", return_value=555.0):
            self.assertEqual(controller._resolve_energy_source_service(secondary), "discovered-secondary")

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
        delattr(service, "auto_energy_sources")
        missing_config_sources = controller._battery_snapshot_sources()
        self.assertEqual(len(missing_config_sources), 1)
        self.assertEqual(missing_config_sources[0].source_id, "primary_battery")

        service.get_dbus_value = MagicMock(return_value="12.5")
        self.assertEqual(controller._read_optional_energy_value("svc", "/Soc"), 12.5)
        self.assertIsNone(controller._battery_soc_numeric("bad"))
        self.assertEqual(controller._battery_soc_numeric("34.5"), 34.5)
        with self.assertRaises(TypeError) as soc_error:
            controller._battery_snapshot_validate_soc(
                None,
                EnergyClusterSnapshot(
                    sources=(EnergySourceSnapshot(source_id="primary_battery", role="battery", service_name="svc"),)
                ),
            )
        self.assertEqual(str(soc_error.exception), "Battery SOC is not numeric")
        controller._battery_snapshot_validate_soc(
            None,
            EnergyClusterSnapshot(
                sources=(EnergySourceSnapshot(source_id="primary_battery", role="battery", service_name="svc", soc=20.0),)
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
        self.assertEqual(
            controller_any._resolve_energy_source_service.call_args_list,
            [call(primary_source), call(primary_source)],
        )
        self.assertEqual(
            controller_any._read_optional_energy_value.call_args_list,
            [
                call("primary", "/Soc"),
                call("primary", "/Soc"),
                call("primary", "/Power"),
                call("primary", ""),
                call("primary", ""),
                call("primary", ""),
            ],
        )
        controller_any._read_optional_energy_text.assert_called_once_with("primary", "/Mode")

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

        boundary_soc_source = EnergySourceDefinition(
            source_id="primary_battery",
            role="battery",
            connector_type="dbus",
            soc_path="/Soc",
        )
        controller_any._resolve_energy_source_service = MagicMock(return_value="svc")
        controller_any._read_optional_energy_value = MagicMock(side_effect=[0.0, None, None, None, None])
        controller_any._read_optional_energy_text = MagicMock(return_value="")
        self.assertEqual(controller._dbus_energy_source_snapshot(boundary_soc_source, 10.5).soc, 0.0)
        controller_any._read_optional_energy_value = MagicMock(side_effect=[1.0, None, None, None, None])
        self.assertEqual(controller._dbus_energy_source_snapshot(boundary_soc_source, 11.0).soc, 1.0)
        controller_any._read_optional_energy_value = MagicMock(side_effect=[100.0, None, None, None, None])
        self.assertEqual(controller._dbus_energy_source_snapshot(boundary_soc_source, 12.0).soc, 100.0)
        controller_any._read_optional_energy_value = MagicMock(side_effect=[100.5, None, None, None, None])
        self.assertIsNone(controller._dbus_energy_source_snapshot(boundary_soc_source, 13.0).soc)

    def test_semantic_battery_soc_gateway_read_contract_is_exact(self) -> None:
        service = self._make_service()
        service.auto_battery_scan_interval_seconds = 17.0
        controller = DbusInputController(service)
        controller_any = cast(Any, controller)
        controller_any._source_retry_ready = MagicMock(return_value=True)
        controller_any.get_gateway_read_value = MagicMock(return_value=100.0)
        controller_any._mark_source_recovery = MagicMock()
        controller_any._handle_source_failure = MagicMock(return_value=None)

        with patch("venus_evcharger.inputs.storage.time.time", return_value=123.0):
            self.assertEqual(controller.get_battery_soc(), 100.0)

        controller_any._source_retry_ready.assert_called_once_with("battery", 123.0)
        controller_any.get_gateway_read_value.assert_called_once_with(
            BATTERY_SOC_READ_KEY,
            reason="main semantic battery SOC read",
        )
        controller_any._mark_source_recovery.assert_called_once_with("battery", "Battery SOC readings recovered")
        controller_any._handle_source_failure.assert_not_called()

        controller_any.get_gateway_read_value = MagicMock(return_value=0.0)
        controller_any._mark_source_recovery = MagicMock()
        with patch("venus_evcharger.inputs.storage.time.time", return_value=123.5):
            self.assertEqual(controller.get_battery_soc(), 0.0)
        controller_any._mark_source_recovery.assert_called_once_with("battery", "Battery SOC readings recovered")

        for invalid_soc in (-0.01, 100.01, True, "100"):
            with self.subTest(invalid_soc=invalid_soc):
                controller_any.get_gateway_read_value = MagicMock(return_value=invalid_soc)
                controller_any._handle_source_failure = MagicMock(return_value=None)
                controller_any._mark_source_recovery = MagicMock()
                with patch("venus_evcharger.inputs.storage.time.time", return_value=124.0):
                    self.assertIsNone(controller.get_battery_soc())
                controller_any._handle_source_failure.assert_called_once_with(
                    "battery",
                    124.0,
                    "battery-missing",
                    17.0,
                    "Auto mode could not read battery SOC from the DBus gateway read contract.",
                )
                controller_any._mark_source_recovery.assert_not_called()

        controller_any._source_retry_ready = MagicMock(return_value=False)
        controller_any.get_gateway_read_value = MagicMock(return_value=50.0)
        with patch("venus_evcharger.inputs.storage.time.time", return_value=125.0):
            self.assertIsNone(controller.get_battery_soc())
        controller_any._source_retry_ready.assert_called_once_with("battery", 125.0)
        controller_any.get_gateway_read_value.assert_not_called()

    def test_semantic_grid_power_gateway_read_contract_is_exact(self) -> None:
        service = self._make_service()
        controller = DbusInputController(service)
        controller_any = cast(Any, controller)
        controller_any._source_retry_ready = MagicMock(return_value=True)
        controller_any.get_gateway_read_value = MagicMock(return_value=-12.25)
        controller_any._mark_source_recovery = MagicMock()
        controller_any._handle_missing_grid_values = MagicMock(return_value=None)

        with patch("venus_evcharger.inputs.storage.time.time", return_value=456.0):
            self.assertEqual(controller.get_grid_power(), -12.25)

        controller_any._source_retry_ready.assert_called_once_with("grid", 456.0)
        controller_any.get_gateway_read_value.assert_called_once_with(
            GRID_POWER_READ_KEY,
            reason="main semantic grid power read",
        )
        controller_any._mark_source_recovery.assert_called_once_with("grid", "Grid readings recovered")
        controller_any._handle_missing_grid_values.assert_not_called()

        for invalid_power in (True, "bad", None):
            with self.subTest(invalid_power=invalid_power):
                controller_any.get_gateway_read_value = MagicMock(return_value=invalid_power)
                controller_any._handle_missing_grid_values = MagicMock(return_value=None)
                controller_any._mark_source_recovery = MagicMock()
                with patch("venus_evcharger.inputs.storage.time.time", return_value=457.0):
                    self.assertIsNone(controller.get_grid_power())
                controller_any._handle_missing_grid_values.assert_called_once_with(False, [], 457.0)
                controller_any._mark_source_recovery.assert_not_called()

        controller_any._source_retry_ready = MagicMock(return_value=False)
        controller_any.get_gateway_read_value = MagicMock(return_value=1.0)
        with patch("venus_evcharger.inputs.storage.time.time", return_value=458.0):
            self.assertIsNone(controller.get_grid_power())
        controller_any._source_retry_ready.assert_called_once_with("grid", 458.0)
        controller_any.get_gateway_read_value.assert_not_called()

    def test_storage_optional_energy_reads_use_exact_service_path_contract(self) -> None:
        service = self._make_service()
        controller = DbusInputController(service)
        controller_any = cast(Any, controller)
        controller_any._introspection_says_skip = MagicMock(return_value=False)
        service._get_dbus_value = MagicMock(side_effect=["12.5", " support "])

        self.assertEqual(controller._read_optional_energy_value("svc", "/Soc"), 12.5)
        self.assertEqual(controller._read_optional_energy_text("svc", "/Mode"), "support")

        controller_any._introspection_says_skip.assert_any_call("svc", "/Soc", priority=85)
        controller_any._introspection_says_skip.assert_any_call("svc", "/Mode", priority=85)
        self.assertEqual(
            service._get_dbus_value.call_args_list,
            [call("svc", "/Soc"), call("svc", "/Mode")],
        )

        controller_any._introspection_says_skip = MagicMock(return_value=True)
        service._get_dbus_value.reset_mock()
        self.assertIsNone(controller._read_optional_energy_value("svc", "/SkippedSoc"))
        self.assertEqual(controller._read_optional_energy_text("svc", "/SkippedMode"), "")
        service._get_dbus_value.assert_not_called()

    def test_storage_energy_snapshot_reads_exact_paths_and_service_contract(self) -> None:
        service = self._make_service()
        controller = DbusInputController(service)
        controller_any = cast(Any, controller)
        source = EnergySourceDefinition(
            source_id="primary_battery",
            role="battery",
            connector_type="dbus",
            soc_path="/Soc",
            battery_power_path="/Dc/0/Power",
            ac_power_path="/Ac/Power",
            pv_power_path="/Pv/Power",
            grid_interaction_path="/Grid/Power",
            operating_mode_path="/Mode",
            usable_capacity_wh=9876.0,
        )
        controller_any._resolve_energy_source_service = MagicMock(return_value="battery.svc")
        controller_any._introspection_says_skip = MagicMock(return_value=False)
        values = {
            "/Soc": 55.0,
            "/Dc/0/Power": -123.0,
            "/Ac/Power": 456.0,
            "/Pv/Power": 78.0,
            "/Grid/Power": -9.0,
            "/Mode": "support",
        }
        service._get_dbus_value = MagicMock(side_effect=lambda _service, path: values[path])

        snapshot = controller._dbus_energy_source_snapshot(source, 42.0)

        self.assertEqual(snapshot.source_id, "primary_battery")
        self.assertEqual(snapshot.role, "battery")
        self.assertEqual(snapshot.service_name, "battery.svc")
        self.assertEqual(snapshot.soc, 55.0)
        self.assertEqual(snapshot.net_battery_power_w, -123.0)
        self.assertEqual(snapshot.ac_power_w, 456.0)
        self.assertEqual(snapshot.pv_input_power_w, 78.0)
        self.assertEqual(snapshot.grid_interaction_w, -9.0)
        self.assertEqual(snapshot.operating_mode, "support")
        self.assertEqual(snapshot.usable_capacity_wh, 9876.0)
        self.assertTrue(snapshot.online)
        self.assertEqual(snapshot.confidence, 1.0)
        self.assertEqual(snapshot.captured_at, 42.0)
        self.assertEqual(
            service._get_dbus_value.call_args_list,
            [
                call("battery.svc", "/Soc"),
                call("battery.svc", "/Dc/0/Power"),
                call("battery.svc", "/Ac/Power"),
                call("battery.svc", "/Pv/Power"),
                call("battery.svc", "/Grid/Power"),
                call("battery.svc", "/Mode"),
            ],
        )

    def test_storage_effective_soc_and_payload_keys_are_exact_contracts(self) -> None:
        service = self._make_service()
        controller = DbusInputController(service)

        cluster_for_soc = EnergyClusterSnapshot(
            sources=(
                EnergySourceSnapshot("primary", "battery", "svc-primary", soc=33.0),
                EnergySourceSnapshot("secondary", "battery", "svc-secondary", soc=66.0),
            ),
            effective_soc=55.0,
        )
        service.auto_use_combined_battery_soc = True
        self.assertEqual(controller._battery_snapshot_effective_soc(cluster_for_soc), 55.0)
        service.auto_use_combined_battery_soc = False
        self.assertEqual(controller._battery_snapshot_effective_soc(cluster_for_soc), 33.0)
        self.assertIsNone(controller._battery_snapshot_effective_soc(EnergyClusterSnapshot(effective_soc=44.0)))
        delattr(service, "auto_use_combined_battery_soc")
        self.assertEqual(controller._battery_snapshot_effective_soc(cluster_for_soc), 55.0)

        service._service = SimpleNamespace(name="inner-cache-owner")
        self.assertIs(controller._battery_snapshot_cache_owner(), service._service)
        delattr(service, "_service")
        self.assertIs(controller._battery_snapshot_cache_owner(), service)

        cluster = EnergyClusterSnapshot(
            combined_soc=55.0,
            combined_usable_capacity_wh=5000.0,
            combined_charge_power_w=100.0,
            combined_discharge_power_w=200.0,
            combined_net_battery_power_w=-100.0,
            combined_ac_power_w=300.0,
            combined_pv_input_power_w=400.0,
            combined_grid_interaction_w=-50.0,
            average_confidence=0.9,
            source_count=2,
            online_source_count=2,
            valid_soc_source_count=1,
            battery_source_count=1,
            hybrid_inverter_source_count=1,
            inverter_source_count=0,
        )
        cache_owner = SimpleNamespace(_last_energy_learning_profiles={"a": EnergyLearningProfile(source_id="a")})
        forecast: dict[str, object] = {
            "battery_headroom_charge_w": 1.0,
            "battery_headroom_discharge_w": 2.0,
            "expected_near_term_export_w": 3.0,
            "expected_near_term_import_w": 4.0,
        }
        discharge_balance: dict[str, object] = {
            "mode": "mode",
            "target_distribution_mode": "target",
            "error_w": 5.0,
            "max_abs_error_w": 6.0,
            "total_discharge_w": 7.0,
            "eligible_source_count": 8,
            "active_source_count": 9,
        }
        discharge_control: dict[str, object] = {
            "control_candidate_count": 10,
            "control_ready_count": 11,
            "supported_control_source_count": 12,
            "experimental_control_source_count": 13,
        }
        payload = controller._battery_snapshot_payload(
            cache_owner,
            54.0,
            cluster,
            forecast,
            discharge_balance,
            discharge_control,
            [{"source_id": "a"}],
        )
        expected_keys = {
            "battery_soc",
            "battery_combined_soc",
            "battery_combined_usable_capacity_wh",
            "battery_combined_charge_power_w",
            "battery_combined_discharge_power_w",
            "battery_combined_net_power_w",
            "battery_combined_ac_power_w",
            "battery_combined_pv_input_power_w",
            "battery_combined_grid_interaction_w",
            "battery_headroom_charge_w",
            "battery_headroom_discharge_w",
            "expected_near_term_export_w",
            "expected_near_term_import_w",
            "battery_discharge_balance_mode",
            "battery_discharge_balance_target_distribution_mode",
            "battery_discharge_balance_error_w",
            "battery_discharge_balance_max_abs_error_w",
            "battery_discharge_balance_total_discharge_w",
            "battery_discharge_balance_eligible_source_count",
            "battery_discharge_balance_active_source_count",
            "battery_discharge_balance_control_candidate_count",
            "battery_discharge_balance_control_ready_count",
            "battery_discharge_balance_supported_control_source_count",
            "battery_discharge_balance_experimental_control_source_count",
            "battery_average_confidence",
            "battery_source_count",
            "battery_online_source_count",
            "battery_valid_soc_source_count",
            "battery_battery_source_count",
            "battery_hybrid_inverter_source_count",
            "battery_inverter_source_count",
            "battery_sources",
            "battery_learning_profiles",
        }
        expected_payload = {
            "battery_soc": 54.0,
            "battery_combined_soc": 55.0,
            "battery_combined_usable_capacity_wh": 5000.0,
            "battery_combined_charge_power_w": 100.0,
            "battery_combined_discharge_power_w": 200.0,
            "battery_combined_net_power_w": -100.0,
            "battery_combined_ac_power_w": 300.0,
            "battery_combined_pv_input_power_w": 400.0,
            "battery_combined_grid_interaction_w": -50.0,
            "battery_headroom_charge_w": 1.0,
            "battery_headroom_discharge_w": 2.0,
            "expected_near_term_export_w": 3.0,
            "expected_near_term_import_w": 4.0,
            "battery_discharge_balance_mode": "mode",
            "battery_discharge_balance_target_distribution_mode": "target",
            "battery_discharge_balance_error_w": 5.0,
            "battery_discharge_balance_max_abs_error_w": 6.0,
            "battery_discharge_balance_total_discharge_w": 7.0,
            "battery_discharge_balance_eligible_source_count": 8,
            "battery_discharge_balance_active_source_count": 9,
            "battery_discharge_balance_control_candidate_count": 10,
            "battery_discharge_balance_control_ready_count": 11,
            "battery_discharge_balance_supported_control_source_count": 12,
            "battery_discharge_balance_experimental_control_source_count": 13,
            "battery_average_confidence": 0.9,
            "battery_source_count": 2,
            "battery_online_source_count": 2,
            "battery_valid_soc_source_count": 1,
            "battery_battery_source_count": 1,
            "battery_hybrid_inverter_source_count": 1,
            "battery_inverter_source_count": 0,
            "battery_sources": [{"source_id": "a"}],
            "battery_learning_profiles": {
                "a": {
                    "source_id": "a",
                    "sample_count": 0,
                    "active_sample_count": 0,
                    "charge_sample_count": 0,
                    "discharge_sample_count": 0,
                    "import_support_sample_count": 0,
                    "import_charge_sample_count": 0,
                    "export_charge_sample_count": 0,
                    "export_discharge_sample_count": 0,
                    "export_idle_sample_count": 0,
                    "day_active_sample_count": 0,
                    "night_active_sample_count": 0,
                    "day_charge_sample_count": 0,
                    "night_charge_sample_count": 0,
                    "day_discharge_sample_count": 0,
                    "night_discharge_sample_count": 0,
                    "response_sample_count": 0,
                    "smoothing_sample_count": 0,
                    "observed_max_charge_power_w": None,
                    "observed_max_discharge_power_w": None,
                    "observed_max_ac_power_w": None,
                    "observed_max_pv_input_power_w": None,
                    "observed_max_grid_import_w": None,
                    "observed_max_grid_export_w": None,
                    "observed_min_discharge_soc": None,
                    "observed_max_charge_soc": None,
                    "reserve_band_floor_soc": None,
                    "reserve_band_ceiling_soc": None,
                    "reserve_band_width_soc": None,
                    "average_active_charge_power_w": None,
                    "average_active_discharge_power_w": None,
                    "average_active_power_delta_w": None,
                    "typical_response_delay_seconds": None,
                    "power_smoothing_ratio": None,
                    "support_bias": None,
                    "import_support_bias": None,
                    "export_bias": None,
                    "battery_first_export_bias": None,
                    "day_support_bias": None,
                    "night_support_bias": None,
                    "direction_change_count": 0,
                    "last_direction": "idle",
                    "last_activity_state": "idle",
                    "last_active_at": None,
                    "last_inactive_at": None,
                    "last_change_at": None,
                }
            },
        }
        self.assertEqual(set(payload), expected_keys)
        self.assertEqual(payload, expected_payload)

        empty_payload = controller._empty_battery_snapshot_payload(None)
        expected_empty_payload: dict[str, object] = {
            "battery_soc": None,
            "battery_combined_soc": None,
            "battery_combined_usable_capacity_wh": None,
            "battery_combined_charge_power_w": None,
            "battery_combined_discharge_power_w": None,
            "battery_combined_net_power_w": None,
            "battery_combined_ac_power_w": None,
            "battery_combined_pv_input_power_w": None,
            "battery_combined_grid_interaction_w": None,
            "battery_headroom_charge_w": None,
            "battery_headroom_discharge_w": None,
            "expected_near_term_export_w": None,
            "expected_near_term_import_w": None,
            "battery_discharge_balance_mode": "",
            "battery_discharge_balance_target_distribution_mode": "",
            "battery_discharge_balance_error_w": None,
            "battery_discharge_balance_max_abs_error_w": None,
            "battery_discharge_balance_total_discharge_w": None,
            "battery_discharge_balance_eligible_source_count": 0,
            "battery_discharge_balance_active_source_count": 0,
            "battery_discharge_balance_control_candidate_count": 0,
            "battery_discharge_balance_control_ready_count": 0,
            "battery_discharge_balance_supported_control_source_count": 0,
            "battery_discharge_balance_experimental_control_source_count": 0,
            "battery_average_confidence": None,
            "battery_source_count": 0,
            "battery_online_source_count": 0,
            "battery_valid_soc_source_count": 0,
            "battery_battery_source_count": 0,
            "battery_hybrid_inverter_source_count": 0,
            "battery_inverter_source_count": 0,
            "battery_sources": [],
            "battery_learning_profiles": {},
        }
        self.assertEqual(set(empty_payload), expected_keys)
        self.assertEqual(empty_payload, expected_empty_payload)

        defaulted_payload = controller._battery_snapshot_payload(
            SimpleNamespace(_last_energy_learning_profiles={}),
            None,
            cluster,
            forecast,
            {},
            {},
            [],
        )
        self.assertEqual(defaulted_payload["battery_discharge_balance_eligible_source_count"], 0)
        self.assertEqual(defaulted_payload["battery_discharge_balance_active_source_count"], 0)
        self.assertEqual(defaulted_payload["battery_discharge_balance_control_candidate_count"], 0)
        self.assertEqual(defaulted_payload["battery_discharge_balance_control_ready_count"], 0)
        self.assertEqual(defaulted_payload["battery_discharge_balance_supported_control_source_count"], 0)
        self.assertEqual(defaulted_payload["battery_discharge_balance_experimental_control_source_count"], 0)
        missing_learning_payload = controller._battery_snapshot_payload(
            SimpleNamespace(),
            None,
            cluster,
            forecast,
            {},
            {},
            [],
        )
        self.assertEqual(missing_learning_payload["battery_learning_profiles"], {})

    def test_storage_source_payloads_merge_balance_and_control_by_source_id(self) -> None:
        cluster = EnergyClusterSnapshot(
            sources=(
                EnergySourceSnapshot("a", "battery", "svc-a", soc=50.0),
                EnergySourceSnapshot("b", "hybrid-inverter", "svc-b", soc=60.0),
                EnergySourceSnapshot("c", "inverter", "svc-c", soc=None),
            )
        )

        payloads = DbusInputController._battery_snapshot_source_payloads(
            cluster,
            {"sources": {"a": {"balance": 1}, "b": "ignored", "missing": {"balance": 9}}},
            {"sources": {"a": {"control": 2}, "b": {"control": 3}}},
        )

        self.assertEqual(payloads[0]["source_id"], "a")
        self.assertEqual(payloads[0]["role"], "battery")
        self.assertEqual(payloads[0]["service_name"], "svc-a")
        self.assertEqual(payloads[0]["soc"], 50.0)
        self.assertEqual(payloads[0]["balance"], 1)
        self.assertEqual(payloads[0]["control"], 2)
        self.assertEqual(payloads[1]["source_id"], "b")
        self.assertNotIn("balance", payloads[1])
        self.assertEqual(payloads[1]["control"], 3)
        self.assertEqual(payloads[2]["source_id"], "c")
        self.assertNotIn("balance", payloads[2])
        self.assertNotIn("control", payloads[2])

    def test_storage_cluster_reads_each_source_with_owner_and_now(self) -> None:
        service = self._make_service()
        controller = DbusInputController(service)
        source_a = EnergySourceDefinition(source_id="a", role="battery", connector_type="dbus")
        source_b = EnergySourceDefinition(source_id="b", role="hybrid-inverter", connector_type="dbus")
        snapshot_a = EnergySourceSnapshot("a", "battery", "svc-a", soc=50.0)
        snapshot_b = EnergySourceSnapshot("b", "hybrid-inverter", "svc-b", soc=60.0)
        aggregate = EnergyClusterSnapshot(sources=(snapshot_a, snapshot_b), combined_soc=55.0)

        with (
            patch.object(controller, "_battery_snapshot_sources", return_value=(source_a, source_b)) as sources,
            patch(
                "venus_evcharger.inputs.storage.read_energy_source_snapshot",
                side_effect=[snapshot_a, snapshot_b],
            ) as read_snapshot,
            patch("venus_evcharger.inputs.storage.aggregate_energy_sources", return_value=aggregate) as aggregate_mock,
        ):
            cluster, source_defs, snapshots = controller._battery_snapshot_cluster(99.0)

        self.assertIs(cluster, aggregate)
        self.assertEqual(source_defs, (source_a, source_b))
        self.assertEqual(snapshots, [snapshot_a, snapshot_b])
        sources.assert_called_once_with()
        self.assertEqual(read_snapshot.call_args_list, [call(controller, source_a, 99.0), call(controller, source_b, 99.0)])
        aggregate_mock.assert_called_once_with([snapshot_a, snapshot_b])

    def test_storage_learning_bundle_uses_exact_energy_contracts(self) -> None:
        service = self._make_service()
        controller = DbusInputController(service)
        cache_owner = SimpleNamespace(_last_energy_learning_profiles={"old": {"sample_count": 1}})
        source = EnergySourceSnapshot(source_id="battery", role="battery", service_name="svc", soc=66.0)
        cluster = EnergyClusterSnapshot(
            sources=(source,),
            combined_charge_power_w=11.0,
            combined_discharge_power_w=22.0,
            combined_charge_limit_power_w=33.0,
            combined_discharge_limit_power_w=44.0,
            combined_grid_interaction_w=55.0,
        )
        updated_profiles = {"battery": EnergyLearningProfile(source_id="battery", sample_count=2)}

        with (
            patch("venus_evcharger.inputs.storage.learning_profiles", return_value={"old": {"sample_count": 1}}) as parse,
            patch(
                "venus_evcharger.inputs.storage.update_energy_learning_profiles",
                return_value=updated_profiles,
            ) as update,
            patch("venus_evcharger.inputs.storage.summarize_energy_learning_profiles", return_value={"summary": 1}) as summarize,
            patch("venus_evcharger.inputs.storage.derive_discharge_balance_metrics", return_value={"balance": 2}) as balance,
            patch("venus_evcharger.inputs.storage.derive_energy_forecast", return_value={"forecast": 3}) as forecast,
        ):
            learning_summary, discharge_balance, energy_forecast = controller._battery_snapshot_learning_bundle(
                cache_owner,
                cluster,
                222.0,
            )

        self.assertEqual(cache_owner._last_energy_learning_profiles, updated_profiles)
        self.assertEqual(learning_summary, {"summary": 1})
        self.assertEqual(discharge_balance, {"balance": 2})
        self.assertEqual(energy_forecast, {"forecast": 3})
        parse.assert_called_once_with({"old": {"sample_count": 1}})
        update.assert_called_once_with({"old": {"sample_count": 1}}, (source,), 222.0)
        summarize.assert_called_once_with(updated_profiles)
        balance.assert_called_once_with((source,), updated_profiles)
        forecast.assert_called_once_with(
            {
                "battery_combined_charge_power_w": 11.0,
                "battery_combined_discharge_power_w": 22.0,
                "BATTERY_COMBINED_CHARGE_LIMIT_POWER_W": 33.0,
                "battery_combined_discharge_limit_power_w": 44.0,
                "battery_combined_grid_interaction_w": 55.0,
            },
            {"summary": 1},
        )

        missing_owner = SimpleNamespace()
        with (
            patch("venus_evcharger.inputs.storage.learning_profiles", return_value={}) as missing_parse,
            patch("venus_evcharger.inputs.storage.update_energy_learning_profiles", return_value={}) as missing_update,
            patch("venus_evcharger.inputs.storage.summarize_energy_learning_profiles", return_value={}),
            patch("venus_evcharger.inputs.storage.derive_discharge_balance_metrics", return_value={}),
            patch("venus_evcharger.inputs.storage.derive_energy_forecast", return_value={}),
        ):
            controller._battery_snapshot_learning_bundle(missing_owner, cluster, 223.0)
        missing_parse.assert_called_once_with(None)
        missing_update.assert_called_once_with({}, (source,), 223.0)

    def test_successful_and_failed_battery_snapshot_payloads_have_exact_flow_contracts(self) -> None:
        service = self._make_service()
        service.auto_battery_service = "battery.svc"
        service.auto_battery_service_prefix = "com.victronenergy.battery"
        service.auto_battery_soc_path = "/Soc"
        service.auto_battery_scan_interval_seconds = 17.0
        controller = DbusInputController(service)
        controller_any = cast(Any, controller)
        cluster = SimpleNamespace(sources=(EnergySourceSnapshot("battery", "battery", "svc", soc=77.0),))
        sources = (EnergySourceDefinition(source_id="battery", role="battery", connector_type="dbus"),)
        cache_owner = SimpleNamespace()
        controller_any._battery_snapshot_cluster = MagicMock(return_value=(cluster, sources, list(cluster.sources)))
        controller_any._battery_snapshot_effective_soc = MagicMock(return_value=77.0)
        controller_any._battery_snapshot_validate_soc = MagicMock()
        controller_any._battery_snapshot_cache_owner = MagicMock(return_value=cache_owner)
        controller_any._battery_snapshot_learning_bundle = MagicMock(
            return_value=({"summary": 1}, {"balance": 2}, {"forecast": 3})
        )
        controller_any._battery_snapshot_discharge_control = MagicMock(return_value={"control": 4})
        controller_any._battery_snapshot_source_payloads = MagicMock(return_value=[{"source_id": "battery"}])
        controller_any._mark_source_recovery = MagicMock()
        controller_any._battery_snapshot_payload = MagicMock(return_value={"battery_soc": 77.0})

        self.assertEqual(controller._successful_battery_snapshot_payload(333.0), {"battery_soc": 77.0})

        controller_any._battery_snapshot_cluster.assert_called_once_with(333.0)
        controller_any._battery_snapshot_effective_soc.assert_called_once_with(cluster)
        controller_any._battery_snapshot_validate_soc.assert_called_once_with(77.0, cluster)
        controller_any._battery_snapshot_cache_owner.assert_called_once_with()
        controller_any._battery_snapshot_learning_bundle.assert_called_once_with(cache_owner, cluster, 333.0)
        controller_any._battery_snapshot_discharge_control.assert_called_once_with(cluster, sources)
        controller_any._battery_snapshot_source_payloads.assert_called_once_with(cluster, {"balance": 2}, {"control": 4})
        controller_any._mark_source_recovery.assert_called_once_with("battery", "Battery SOC readings recovered")
        controller_any._battery_snapshot_payload.assert_called_once_with(
            cache_owner,
            77.0,
            cluster,
            {"forecast": 3},
            {"balance": 2},
            {"control": 4},
            [{"source_id": "battery"}],
        )
        self.assertEqual(cache_owner._last_energy_cluster, {"battery_soc": 77.0})

        controller_any._handle_source_failure = MagicMock(return_value=55.0)
        controller_any._empty_battery_snapshot_payload = MagicMock(return_value={"battery_soc": 55.0})
        error = RuntimeError("offline")
        self.assertEqual(controller._failed_battery_snapshot_payload(444.0, error), {"battery_soc": 55.0})
        controller_any._handle_source_failure.assert_called_once_with(
            "battery",
            444.0,
            "battery-missing",
            17.0,
            "Auto mode could not read battery SOC from %s %s: %s",
            "battery.svc",
            "/Soc",
            error,
        )
        controller_any._empty_battery_snapshot_payload.assert_called_once_with(55.0)

        delattr(service, "auto_battery_service")
        delattr(service, "auto_battery_service_prefix")
        controller_any._handle_source_failure = MagicMock(return_value=None)
        controller_any._empty_battery_snapshot_payload = MagicMock(return_value={"battery_soc": None})
        self.assertEqual(controller._failed_battery_snapshot_payload(445.0, error), {"battery_soc": None})
        controller_any._handle_source_failure.assert_called_once_with(
            "battery",
            445.0,
            "battery-missing",
            17.0,
            "Auto mode could not read battery SOC from %s %s: %s",
            "",
            "/Soc",
            error,
        )

        service.auto_battery_service_prefix = "com.victronenergy.battery"
        controller_any._handle_source_failure = MagicMock(return_value=None)
        controller_any._empty_battery_snapshot_payload = MagicMock(return_value={"battery_soc": None})
        self.assertEqual(controller._failed_battery_snapshot_payload(446.0, error), {"battery_soc": None})
        controller_any._handle_source_failure.assert_called_once_with(
            "battery",
            446.0,
            "battery-missing",
            17.0,
            "Auto mode could not read battery SOC from %s %s: %s",
            "com.victronenergy.battery",
            "/Soc",
            error,
        )

    def test_optional_text_attr_contract(self) -> None:
        owner = SimpleNamespace(non_empty="value", empty="", none=None, zero=0, false=False)

        self.assertEqual(DbusInputController._optional_text_attr(owner, "non_empty"), "value")
        self.assertEqual(DbusInputController._optional_text_attr(owner, "empty"), "")
        self.assertEqual(DbusInputController._optional_text_attr(owner, "none"), "")
        self.assertEqual(DbusInputController._optional_text_attr(owner, "zero"), "")
        self.assertEqual(DbusInputController._optional_text_attr(owner, "false"), "")
        self.assertEqual(DbusInputController._optional_text_attr(owner, "missing"), "")

    def test_get_battery_snapshot_uses_retry_time_and_failure_contracts(self) -> None:
        service = self._make_service()
        controller = DbusInputController(service)
        controller_any = cast(Any, controller)
        controller_any._source_retry_ready = MagicMock(return_value=True)
        controller_any._successful_battery_snapshot_payload = MagicMock(return_value={"battery_soc": 88.0})
        controller_any._failed_battery_snapshot_payload = MagicMock(return_value={"battery_soc": None})

        with patch("venus_evcharger.inputs.storage.time.time", return_value=501.0):
            self.assertEqual(controller.get_battery_snapshot(), {"battery_soc": 88.0})

        controller_any._source_retry_ready.assert_called_once_with("battery", 501.0)
        controller_any._successful_battery_snapshot_payload.assert_called_once_with(501.0)
        controller_any._failed_battery_snapshot_payload.assert_not_called()

        controller_any._source_retry_ready = MagicMock(return_value=False)
        controller_any._successful_battery_snapshot_payload = MagicMock(return_value={"battery_soc": 88.0})
        with patch("venus_evcharger.inputs.storage.time.time", return_value=502.0):
            self.assertEqual(controller.get_battery_snapshot(), {"battery_soc": None})
        controller_any._source_retry_ready.assert_called_once_with("battery", 502.0)
        controller_any._successful_battery_snapshot_payload.assert_not_called()

        error = RuntimeError("snapshot failed")
        controller_any._source_retry_ready = MagicMock(return_value=True)
        controller_any._successful_battery_snapshot_payload = MagicMock(side_effect=error)
        controller_any._failed_battery_snapshot_payload = MagicMock(return_value={"battery_soc": None})
        with patch("venus_evcharger.inputs.storage.time.time", return_value=503.0):
            self.assertEqual(controller.get_battery_snapshot(), {"battery_soc": None})
        controller_any._failed_battery_snapshot_payload.assert_called_once_with(503.0, error)

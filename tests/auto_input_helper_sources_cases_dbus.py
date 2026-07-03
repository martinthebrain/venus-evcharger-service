# SPDX-License-Identifier: GPL-3.0-or-later
from tests.auto_input_helper_sources_cases_common import *
import json
import os
import tempfile
from venus_evcharger.core.shared import compact_json
from venus_evcharger.dbus_gateway import (
    BATTERY_SOC_READ_KEY,
    GRID_POWER_READ_KEY,
    PV_POWER_READ_KEY,
    DbusCacheStore,
    DbusCommandInbox,
    GatewayClient,
    dbus_path_key,
    gateway_paths,
)


class _AutoInputHelperSourcesDbusCases:
    def _prepare_gateway(self, helper):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        paths = gateway_paths(os.path.join(tempdir.name, "run"))
        helper.dbus_gateway_run_dir = paths.run_dir
        helper.dbus_gateway_cache_path = paths.cache_path
        helper.dbus_gateway_max_age_seconds = 30.0
        helper._gateway_client_instance = None
        return paths

    def _write_gateway_cache(self, helper, *, values=None, key_values=None, services=None):
        paths = self._prepare_gateway(helper)
        store = DbusCacheStore(paths)
        for (service_name, path), value in (values or {}).items():
            store.update_value(dbus_path_key(service_name, path), value, source=f"{service_name}{path}")
        for key, value in (key_values or {}).items():
            store.update_value(str(key), value, source=f"read-key:{key}")
        if services is not None:
            store.update_services(list(services))
        store.write_snapshot_files()
        return paths

    def _gateway_commands(self, paths):
        return [command for _, command in DbusCommandInbox(paths.command_dir).load_pending()]

    def test_parent_watchdog_quits_mainloop_when_parent_disappears(self):
        helper = self._make_helper()
        helper._stop_requested = False
        helper._parent_alive = MagicMock(return_value=False)
        helper._main_loop = MagicMock()

        self.assertFalse(helper._parent_watchdog())
        helper._main_loop.quit.assert_called_once_with()

        helper = self._make_helper()
        helper._stop_requested = True
        helper._parent_alive = MagicMock(return_value=False)
        self.assertFalse(helper._parent_watchdog())

    def test_list_dbus_services_returns_empty_and_sets_backoff_on_failure(self):
        helper = self._make_helper()
        paths = self._prepare_gateway(helper)
        with unittest.mock.patch("venus_evcharger_auto_input_helper.time.time", return_value=100.0):
            self.assertEqual(helper._list_dbus_services(), [])

        commands = self._gateway_commands(paths)
        self.assertEqual(commands[0]["kind"], "refresh_services")
        self.assertEqual(helper._dbus_list_failures, 1)
        self.assertEqual(helper._dbus_list_backoff_until, 105.0)

    def test_list_dbus_services_skips_backoff_cap_when_maximum_is_disabled(self):
        helper = self._make_helper()
        helper.auto_dbus_backoff_base_seconds = 5.0
        helper.auto_dbus_backoff_max_seconds = 0.0
        self._prepare_gateway(helper)
        with unittest.mock.patch("venus_evcharger_auto_input_helper.time.time", return_value=100.0):
            self.assertEqual(helper._list_dbus_services(), [])

        self.assertEqual(helper._dbus_list_backoff_until, 105.0)

    def test_get_system_bus_is_disabled_for_helper_process(self):
        helper = self._make_helper()
        with self.assertRaisesRegex(RuntimeError, "Direct DBus access is disabled"):
            helper._get_system_bus()

    def test_get_dbus_value_reads_gateway_cache_and_child_nodes_use_introspection_snapshot(self):
        helper = self._make_helper()
        self._write_gateway_cache(helper, values={("svc", "/Path"): 42.0})
        helper._gateway_client_instance = object()
        self.assertIsInstance(helper._gateway_client(), GatewayClient)
        self.assertIs(helper._gateway_client(), helper._gateway_client_instance)
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as handle:
            handle.write(
                compact_json(
                    {
                        "schema_version": 1,
                        "captured_at": 100.0,
                        "heartbeat_at": 100.0,
                        "services": {
                            "svc": {
                                "paths": {
                                    "/Ac/Grid": {
                                        "status": "fresh",
                                        "children": ["L1", "L2"],
                                    }
                                }
                            }
                        },
                    }
                )
            )
        try:
            self.assertEqual(helper._get_dbus_value("svc", "/Path"), 42.0)
            helper.dbus_introspection_snapshot_path = handle.name
            helper._dbus_introspection_snapshot_loaded_at = 0.0
            with patch("venus_evcharger.dbus_introspection.time.time", return_value=120.0):
                self.assertEqual(helper._get_dbus_child_nodes("svc", "/Ac/Grid"), ["L1", "L2"])
        finally:
            os.unlink(helper.dbus_introspection_snapshot_path)

    def test_get_dbus_value_ignores_fresh_non_numeric_gateway_values(self):
        helper = self._make_helper()
        paths = self._write_gateway_cache(helper, values={("svc", "/Path"): "not-numeric"})

        self.assertIsNone(helper._get_dbus_value("svc", "/Path"))
        self.assertEqual(self._gateway_commands(paths), [])

    def test_get_dbus_value_rejects_stale_gateway_cache_and_requests_refresh(self):
        helper = self._make_helper()
        paths = self._prepare_gateway(helper)
        store = DbusCacheStore(paths, stale_after_seconds=1.0)
        store.update_value(dbus_path_key("svc", "/Path"), 42.0, source="svc/Path", now=100.0)
        with patch("venus_evcharger.dbus_gateway_cache._now", return_value=200.0):
            store.write_snapshot_files()

        self.assertIsNone(helper._get_dbus_value("svc", "/Path"))
        commands = self._gateway_commands(paths)
        self.assertEqual(commands[0]["kind"], "refresh_value")
        self.assertEqual(commands[0]["service"], "svc")
        self.assertEqual(commands[0]["path"], "/Path")

    def test_semantic_pv_read_uses_gateway_key_not_raw_dbus_paths(self):
        helper = self._make_helper()
        helper._get_dbus_value = MagicMock(return_value=123.0)
        self._write_gateway_cache(helper, key_values={PV_POWER_READ_KEY: 123.0})

        self.assertEqual(helper._get_pv_power(), 123.0)
        helper._get_dbus_value.assert_not_called()

    def test_semantic_pv_and_grid_reads_use_exact_gateway_contracts(self):
        helper = self._make_helper()
        helper._get_gateway_read_value = MagicMock(return_value=123)
        helper._delay_source_retry = MagicMock()

        self.assertEqual(helper._get_pv_power(), 123.0)
        helper._get_gateway_read_value.assert_called_once_with(
            PV_POWER_READ_KEY,
            reason="helper semantic PV power read",
        )
        helper._delay_source_retry.assert_not_called()

        helper = self._make_helper()
        helper._get_gateway_read_value = MagicMock(return_value=-45)
        helper._delay_source_retry = MagicMock()

        self.assertEqual(helper._get_grid_power(), -45.0)
        helper._get_gateway_read_value.assert_called_once_with(
            GRID_POWER_READ_KEY,
            reason="helper semantic grid power read",
        )
        helper._delay_source_retry.assert_not_called()

    def test_semantic_pv_and_grid_reads_delay_only_the_missing_source(self):
        helper = self._make_helper()
        helper._get_gateway_read_value = MagicMock(return_value=None)
        helper._delay_source_retry = MagicMock()

        self.assertIsNone(helper._get_pv_power())
        helper._get_gateway_read_value.assert_called_once_with(
            PV_POWER_READ_KEY,
            reason="helper semantic PV power read",
        )
        helper._delay_source_retry.assert_called_once_with("pv")

        helper = self._make_helper()
        helper._get_gateway_read_value = MagicMock(return_value=None)
        helper._delay_source_retry = MagicMock()

        self.assertIsNone(helper._get_grid_power())
        helper._get_gateway_read_value.assert_called_once_with(
            GRID_POWER_READ_KEY,
            reason="helper semantic grid power read",
        )
        helper._delay_source_retry.assert_called_once_with("grid")

    def test_semantic_gateway_read_suppresses_fresh_cached_errors(self):
        helper = self._make_helper()
        paths = self._prepare_gateway(helper)
        store = DbusCacheStore(paths)
        store.mark_error(PV_POWER_READ_KEY, source="read-key:pv", error="sleeping", now=100.0)
        store.write_snapshot_files()

        with patch("venus_evcharger.inputs.helper.sources_dbus_gateway.time.time", return_value=101.0):
            self.assertIsNone(helper._get_gateway_read_value(PV_POWER_READ_KEY, reason="pv retry"))

        self.assertEqual(self._gateway_commands(paths), [])

    def test_semantic_pv_and_grid_reads_do_not_query_gateway_during_retry_cooldown(self):
        helper = self._make_helper()
        helper._source_retry_after["pv"] = 200.0
        helper._get_gateway_read_value = MagicMock(side_effect=AssertionError("PV read should be skipped"))
        helper._delay_source_retry = MagicMock()
        with patch("venus_evcharger_auto_input_helper.time.time", return_value=100.0):
            self.assertIsNone(helper._get_pv_power())
        helper._get_gateway_read_value.assert_not_called()
        helper._delay_source_retry.assert_not_called()

        helper = self._make_helper()
        helper._source_retry_after["grid"] = 200.0
        helper._get_gateway_read_value = MagicMock(side_effect=AssertionError("Grid read should be skipped"))
        helper._delay_source_retry = MagicMock()
        with patch("venus_evcharger_auto_input_helper.time.time", return_value=100.0):
            self.assertIsNone(helper._get_grid_power())
        helper._get_gateway_read_value.assert_not_called()
        helper._delay_source_retry.assert_not_called()

    def test_get_dbus_value_skips_known_unusable_introspection_finding(self):
        helper = self._make_helper()
        paths = self._prepare_gateway(helper)
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as snapshot:
            snapshot.write(
                compact_json(
                    {
                        "schema_version": 1,
                        "captured_at": 100.0,
                        "heartbeat_at": 100.0,
                        "services": {
                            "svc.dead": {
                                "paths": {
                                    "/Soc": {
                                        "status": "known-missing",
                                        "retry_after": 0.0,
                                    }
                                }
                            }
                        },
                    }
                )
            )
            helper.dbus_introspection_snapshot_path = snapshot.name
        try:
            with patch("venus_evcharger.dbus_introspection.time.time", return_value=120.0):
                self.assertIsNone(helper._get_dbus_value("svc.dead", "/Soc"))
        finally:
            os.unlink(snapshot.name)
        commands = self._gateway_commands(paths)
        self.assertEqual(commands[0]["source"], "auto-input-helper")
        self.assertEqual(commands[0]["kind"], "introspect")

    def test_get_dbus_value_and_child_nodes_do_not_reset_on_missing_services(self):
        helper = self._make_helper()
        helper._reset_system_bus = MagicMock()
        paths = self._prepare_gateway(helper)
        self.assertIsNone(helper._get_dbus_value("com.example.missing", "/Soc"))
        self.assertEqual(helper._get_dbus_child_nodes("com.example.missing", "/"), [])

        helper._reset_system_bus.assert_not_called()
        kinds = [command["kind"] for command in self._gateway_commands(paths)]
        self.assertCountEqual(kinds, ["refresh_value", "introspect"])

    def test_get_dbus_value_and_child_nodes_request_gateway_on_cache_miss(self):
        helper = self._make_helper()
        helper._reset_system_bus = MagicMock()
        paths = self._prepare_gateway(helper)
        self.assertIsNone(helper._get_dbus_value("svc", "/Path"))
        self.assertEqual(helper._get_dbus_child_nodes("svc", "/Path"), [])

        helper._reset_system_bus.assert_not_called()
        kinds = [command["kind"] for command in self._gateway_commands(paths)]
        self.assertCountEqual(kinds, ["refresh_value", "introspect"])

    def test_get_dbus_value_suppresses_recent_gateway_error_until_retry_window(self):
        helper = self._make_helper()
        paths = self._prepare_gateway(helper)
        store = DbusCacheStore(paths)
        cache_key = dbus_path_key("svc", "/Missing")
        store.mark_error(cache_key, source="svc/Missing", error="missing", now=100.0)
        store.write_snapshot_files()
        helper.dbus_gateway_error_retry_seconds = 30.0

        with unittest.mock.patch("venus_evcharger.inputs.helper.sources_dbus_gateway.time.time", return_value=120.0):
            self.assertIsNone(helper._get_dbus_value("svc", "/Missing"))
        self.assertEqual(self._gateway_commands(paths), [])
        self.assertFalse(helper._gateway_error_recent({"status": "fresh", "error_at": 100.0}))
        self.assertFalse(helper._gateway_error_recent({"status": "error", "error_at": 0.0}))

        with unittest.mock.patch("venus_evcharger.inputs.helper.sources_dbus_gateway.time.time", return_value=131.0):
            self.assertIsNone(helper._get_dbus_value("svc", "/Missing"))
        commands = self._gateway_commands(paths)
        self.assertEqual(commands[0]["kind"], "refresh_value")

    def test_list_dbus_services_short_circuits_during_backoff_and_resets_on_success(self):
        helper = self._make_helper()
        with patch("venus_evcharger_auto_input_helper.time.time", return_value=50.0):
            helper._dbus_list_backoff_until = 60.0
            self.assertEqual(helper._list_dbus_services(), [])

        helper._dbus_list_backoff_until = 0.0
        helper._dbus_list_failures = 2
        self._write_gateway_cache(helper, services=["com.victronenergy.system"])
        with patch("venus_evcharger_auto_input_helper.time.time", return_value=100.0):
            self.assertEqual(helper._list_dbus_services(), ["com.victronenergy.system"])

        self.assertEqual(helper._dbus_list_failures, 0)
        self.assertEqual(helper._dbus_list_backoff_until, 0.0)

    def test_source_retry_helpers_and_invalidate_helpers(self):
        helper = self._make_helper()
        with patch("venus_evcharger_auto_input_helper.time.time", side_effect=[100.0, 100.0]):
            self.assertTrue(helper._source_retry_ready("pv"))
            helper._delay_source_retry("pv")
        with patch("venus_evcharger_auto_input_helper.time.time", return_value=100.0):
            self.assertFalse(helper._source_retry_ready("pv"))

        helper._resolved_auto_pv_services = ["svc"]
        helper._auto_pv_last_scan = 100.0
        helper._invalidate_auto_pv_services()
        self.assertEqual(helper._resolved_auto_pv_services, [])
        self.assertEqual(helper._auto_pv_last_scan, 0.0)

        helper._resolved_auto_battery_service = "battery"
        helper._auto_battery_last_scan = 100.0
        helper._invalidate_auto_battery_service()
        self.assertIsNone(helper._resolved_auto_battery_service)
        self.assertEqual(helper._auto_battery_last_scan, 0.0)

    def test_resolve_auto_pv_services_uses_discovery_cache(self):
        helper = self._make_helper()
        helper._list_dbus_services = MagicMock(
            return_value=[
                "com.victronenergy.system",
                "com.victronenergy.pvinverter.http_40",
                "com.victronenergy.pvinverter.http_41",
            ]
        )

        with unittest.mock.patch("venus_evcharger_auto_input_helper.time.time", side_effect=[100.0, 120.0]):
            first = helper._resolve_auto_pv_services()
            second = helper._resolve_auto_pv_services()

        self.assertEqual(first, ["com.victronenergy.pvinverter.http_40", "com.victronenergy.pvinverter.http_41"])
        self.assertEqual(second, first)
        helper._list_dbus_services.assert_called_once_with()

    def test_resolve_auto_pv_services_refreshes_stale_cache_and_preserves_order_limit(self):
        helper = self._make_helper()
        helper._resolved_auto_pv_services = ["stale-pv"]
        helper._auto_pv_last_scan = 100.0
        helper.auto_pv_max_services = 1
        helper._list_dbus_services = MagicMock(
            return_value=[
                "com.victronenergy.system",
                "com.victronenergy.pvinverter.http_2",
                "com.victronenergy.pvinverter.http_1",
            ]
        )

        with unittest.mock.patch("venus_evcharger_auto_input_helper.time.time", return_value=200.0):
            self.assertEqual(helper._resolve_auto_pv_services(), ["com.victronenergy.pvinverter.http_2"])

        helper._list_dbus_services.assert_called_once_with()
        self.assertEqual(helper._resolved_auto_pv_services, ["com.victronenergy.pvinverter.http_2"])
        self.assertEqual(helper._auto_pv_last_scan, 200.0)

    def test_resolve_auto_pv_services_uses_configured_service_directly(self):
        helper = self._make_helper()
        helper.auto_pv_service = "com.victronenergy.pvinverter.http_40"
        self.assertEqual(helper._resolve_auto_pv_services(), ["com.victronenergy.pvinverter.http_40"])

    def test_get_pv_power_covers_retry_guard_and_read_failures(self):
        helper = self._make_helper()
        helper._source_retry_after["pv"] = 200.0
        with patch("venus_evcharger_auto_input_helper.time.time", return_value=100.0):
            self.assertIsNone(helper._get_pv_power())

        helper = self._make_helper()
        helper.auto_use_dc_pv = True
        helper.auto_pv_service = ""
        helper._resolve_auto_pv_services = MagicMock(side_effect=RuntimeError("scan failed"))
        helper._get_dbus_value = MagicMock(return_value=None)
        helper._delay_source_retry = MagicMock()
        self.assertIsNone(helper._get_pv_power())
        helper._delay_source_retry.assert_called_once_with("pv")

        helper = self._make_helper()
        helper.auto_use_dc_pv = True
        helper.auto_pv_service = "configured-pv"
        helper._delay_source_retry = MagicMock()
        self.assertIsNone(helper._get_pv_power())
        helper._delay_source_retry.assert_called_once_with("pv")

    def test_resolve_auto_battery_service_finds_prefixed_service_with_soc(self):
        helper = self._make_helper()
        helper.auto_battery_service = ""
        helper._list_dbus_services = MagicMock(
            return_value=[
                "com.victronenergy.system",
                "com.victronenergy.battery.socketcan_can0",
                "com.victronenergy.battery.socketcan_can1",
            ]
        )
        helper._battery_service_has_soc = MagicMock(side_effect=lambda service_name: service_name.endswith("can1"))

        with unittest.mock.patch("venus_evcharger_auto_input_helper.time.time", return_value=100.0):
            self.assertEqual(helper._resolve_auto_battery_service(), "com.victronenergy.battery.socketcan_can1")

    def test_resolve_auto_battery_service_covers_configured_service_cache_and_failure(self):
        helper = self._make_helper()
        helper._list_dbus_services = MagicMock(return_value=[helper.auto_battery_service])
        helper._get_dbus_value = MagicMock(return_value=60.0)
        with patch("venus_evcharger_auto_input_helper.time.time", return_value=100.0):
            self.assertEqual(helper._resolve_auto_battery_service(), "com.victronenergy.battery.socketcan_can1")
        self.assertEqual(helper._resolved_auto_battery_service, "com.victronenergy.battery.socketcan_can1")

        helper = self._make_helper()
        helper.auto_battery_service = "configured-battery"
        helper._list_dbus_services = MagicMock(return_value=["configured-battery"])
        helper._get_dbus_value = MagicMock(side_effect=RuntimeError("offline"))
        helper._resolved_auto_battery_service = "cached-battery"
        helper._auto_battery_last_scan = 100.0
        with patch("venus_evcharger_auto_input_helper.time.time", return_value=120.0):
            self.assertEqual(helper._resolve_auto_battery_service(), "cached-battery")

        helper = self._make_helper()
        helper.auto_battery_service = ""
        helper._list_dbus_services = MagicMock(return_value=["com.victronenergy.system"])
        helper._battery_service_has_soc = MagicMock(return_value=False)
        with self.assertRaises(ValueError):
            helper._resolve_auto_battery_service()

    def test_battery_service_has_soc_and_get_battery_soc_cover_success_and_failure(self):
        helper = self._make_helper()
        helper._get_dbus_value = MagicMock(side_effect=[55.0, RuntimeError("boom")])
        self.assertTrue(helper._battery_service_has_soc("svc"))
        self.assertFalse(helper._battery_service_has_soc("svc"))

        helper = self._make_helper()
        helper._resolve_auto_battery_service = MagicMock(return_value="battery")
        self._write_gateway_cache(helper, key_values={BATTERY_SOC_READ_KEY: 57.5})
        self.assertEqual(helper._get_battery_soc(), 57.5)

        helper = self._make_helper()
        self._write_gateway_cache(helper, key_values={BATTERY_SOC_READ_KEY: "bad"})
        self.assertIsNone(helper._get_battery_soc())

        helper = self._make_helper()
        self._write_gateway_cache(helper, key_values={BATTERY_SOC_READ_KEY: True})
        self.assertIsNone(helper._get_battery_soc())

        helper = self._make_helper()
        self._write_gateway_cache(helper, key_values={BATTERY_SOC_READ_KEY: 150.0})
        helper._delay_source_retry = MagicMock()
        self.assertIsNone(helper._get_battery_soc())
        helper._delay_source_retry.assert_called_once_with("battery")

        helper = self._make_helper()
        helper._delay_source_retry = MagicMock()
        self.assertIsNone(helper._get_battery_soc())
        helper._delay_source_retry.assert_called_once_with("battery")

    def test_get_battery_soc_respects_source_retry_guard(self):
        helper = self._make_helper()
        helper._source_retry_after["battery"] = 200.0
        with patch("venus_evcharger_auto_input_helper.time.time", return_value=100.0):
            self.assertIsNone(helper._get_battery_soc())

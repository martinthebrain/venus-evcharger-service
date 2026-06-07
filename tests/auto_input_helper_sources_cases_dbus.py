# SPDX-License-Identifier: GPL-3.0-or-later
from tests.auto_input_helper_sources_cases_common import *


class _AutoInputHelperSourcesDbusCases:
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
        helper._reset_system_bus = MagicMock()
        helper._get_system_bus = MagicMock(return_value=MagicMock(get_object=MagicMock(return_value=object())))
        failing_interface = MagicMock()
        failing_interface.ListNames.side_effect = RuntimeError("dbus down")
        original_interface = venus_evcharger_auto_input_helper.dbus.Interface
        venus_evcharger_auto_input_helper.dbus.Interface = MagicMock(return_value=failing_interface)
        try:
            with unittest.mock.patch("venus_evcharger_auto_input_helper.time.time", return_value=100.0):
                self.assertEqual(helper._list_dbus_services(), [])
        finally:
            venus_evcharger_auto_input_helper.dbus.Interface = original_interface

        helper._reset_system_bus.assert_called_once_with()
        self.assertEqual(helper._dbus_list_failures, 1)
        self.assertEqual(helper._dbus_list_backoff_until, 105.0)

    def test_list_dbus_services_skips_backoff_cap_when_maximum_is_disabled(self):
        helper = self._make_helper()
        helper.auto_dbus_backoff_base_seconds = 5.0
        helper.auto_dbus_backoff_max_seconds = 0.0
        helper._reset_system_bus = MagicMock()
        helper._get_system_bus = MagicMock(return_value=MagicMock(get_object=MagicMock(return_value=object())))
        failing_interface = MagicMock()
        failing_interface.ListNames.side_effect = RuntimeError("dbus down")
        original_interface = venus_evcharger_auto_input_helper.dbus.Interface
        venus_evcharger_auto_input_helper.dbus.Interface = MagicMock(return_value=failing_interface)
        try:
            with unittest.mock.patch("venus_evcharger_auto_input_helper.time.time", return_value=100.0):
                self.assertEqual(helper._list_dbus_services(), [])
        finally:
            venus_evcharger_auto_input_helper.dbus.Interface = original_interface

        self.assertEqual(helper._dbus_list_backoff_until, 105.0)

    def test_get_system_bus_always_caches_system_bus(self):
        helper = self._make_helper()
        original_session_bus = venus_evcharger_auto_input_helper.dbus.SessionBus
        original_system_bus = venus_evcharger_auto_input_helper.dbus.SystemBus
        try:
            venus_evcharger_auto_input_helper.dbus.SessionBus = MagicMock(return_value="session-bus")
            venus_evcharger_auto_input_helper.dbus.SystemBus = MagicMock(return_value="system-bus")
            with patch.dict("venus_evcharger_auto_input_helper.os.environ", {"DBUS_SESSION_BUS_ADDRESS": "x"}, clear=True):
                self.assertEqual(helper._get_system_bus(), "system-bus")
                self.assertEqual(helper._get_system_bus(), "system-bus")
            helper._reset_system_bus()
            with patch.dict("venus_evcharger_auto_input_helper.os.environ", {}, clear=True):
                self.assertEqual(helper._get_system_bus(), "system-bus")
            venus_evcharger_auto_input_helper.dbus.SessionBus.assert_not_called()
        finally:
            venus_evcharger_auto_input_helper.dbus.SessionBus = original_session_bus
            venus_evcharger_auto_input_helper.dbus.SystemBus = original_system_bus

    def test_get_dbus_value_and_child_nodes_retry_after_reset(self):
        helper = self._make_helper()
        first_bus = MagicMock()
        second_bus = MagicMock()
        helper._get_system_bus = MagicMock(side_effect=[first_bus, second_bus, first_bus, second_bus])
        helper._reset_system_bus = MagicMock()
        first_bus.get_object.return_value = object()
        second_bus.get_object.return_value = object()
        read_interface = MagicMock()
        read_interface.GetValue.side_effect = [RuntimeError("boom"), 42.0]
        introspect_interface = MagicMock()
        introspect_interface.Introspect.side_effect = [
            RuntimeError("boom"),
            "<node><node name='L1'/><node name='L2'/></node>",
        ]
        original_interface = venus_evcharger_auto_input_helper.dbus.Interface
        venus_evcharger_auto_input_helper.dbus.Interface = MagicMock(
            side_effect=[read_interface, read_interface, introspect_interface, introspect_interface]
        )
        try:
            self.assertEqual(helper._get_dbus_value("svc", "/Path"), 42.0)
            self.assertEqual(helper._get_dbus_child_nodes("svc", "/Ac/Grid"), ["L1", "L2"])
        finally:
            venus_evcharger_auto_input_helper.dbus.Interface = original_interface

        self.assertEqual(helper._reset_system_bus.call_count, 2)

    def test_get_dbus_value_and_child_nodes_do_not_reset_on_missing_services(self):
        class MissingDbusError(Exception):
            def get_dbus_name(self):
                return "org.freedesktop.DBus.Error.ServiceUnknown"

        helper = self._make_helper()
        helper._get_system_bus = MagicMock(return_value=MagicMock(get_object=MagicMock(return_value=object())))
        helper._reset_system_bus = MagicMock()
        missing_error = MissingDbusError("The name com.example.missing was not provided by any .service files")
        failing_interface = MagicMock()
        failing_interface.GetValue.side_effect = missing_error
        failing_interface.Introspect.side_effect = missing_error
        original_interface = venus_evcharger_auto_input_helper.dbus.Interface
        venus_evcharger_auto_input_helper.dbus.Interface = MagicMock(return_value=failing_interface)
        try:
            with self.assertRaises(MissingDbusError):
                helper._get_dbus_value("com.example.missing", "/Soc")
            with self.assertRaises(MissingDbusError):
                helper._get_dbus_child_nodes("com.example.missing", "/")
        finally:
            venus_evcharger_auto_input_helper.dbus.Interface = original_interface

        helper._reset_system_bus.assert_not_called()
        self.assertEqual(helper._get_system_bus.call_count, 2)

    def test_get_dbus_value_and_child_nodes_raise_after_second_failure(self):
        helper = self._make_helper()
        helper._get_system_bus = MagicMock(return_value=MagicMock(get_object=MagicMock(return_value=object())))
        helper._reset_system_bus = MagicMock()
        failing_interface = MagicMock()
        failing_interface.GetValue.side_effect = RuntimeError("dbus read failed")
        failing_interface.Introspect.side_effect = RuntimeError("dbus introspect failed")
        original_interface = venus_evcharger_auto_input_helper.dbus.Interface
        venus_evcharger_auto_input_helper.dbus.Interface = MagicMock(return_value=failing_interface)
        try:
            with self.assertRaises(RuntimeError):
                helper._get_dbus_value("svc", "/Path")
            with self.assertRaises(RuntimeError):
                helper._get_dbus_child_nodes("svc", "/Path")
        finally:
            venus_evcharger_auto_input_helper.dbus.Interface = original_interface

        self.assertEqual(helper._reset_system_bus.call_count, 4)

    def test_list_dbus_services_short_circuits_during_backoff_and_resets_on_success(self):
        helper = self._make_helper()
        with patch("venus_evcharger_auto_input_helper.time.time", return_value=50.0):
            helper._dbus_list_backoff_until = 60.0
            self.assertEqual(helper._list_dbus_services(), [])

        helper._dbus_list_backoff_until = 0.0
        helper._dbus_list_failures = 2
        helper._get_system_bus = MagicMock(return_value=MagicMock(get_object=MagicMock(return_value=object())))
        dbus_iface = MagicMock()
        dbus_iface.ListNames.return_value = ["com.victronenergy.system"]
        original_interface = venus_evcharger_auto_input_helper.dbus.Interface
        venus_evcharger_auto_input_helper.dbus.Interface = MagicMock(return_value=dbus_iface)
        try:
            with patch("venus_evcharger_auto_input_helper.time.time", return_value=100.0):
                self.assertEqual(helper._list_dbus_services(), ["com.victronenergy.system"])
        finally:
            venus_evcharger_auto_input_helper.dbus.Interface = original_interface

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
        helper._resolve_auto_pv_services = MagicMock(return_value=["svc"])
        helper._get_dbus_value = MagicMock(side_effect=[RuntimeError("ac failed"), RuntimeError("dc failed")])
        helper._invalidate_auto_pv_services = MagicMock()
        helper._delay_source_retry = MagicMock()
        self.assertIsNone(helper._get_pv_power())
        helper._invalidate_auto_pv_services.assert_called_once_with()
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
        helper._get_dbus_value = MagicMock(return_value=57.5)
        self.assertEqual(helper._get_battery_soc(), 57.5)

        helper._get_dbus_value = MagicMock(return_value="bad")
        self.assertIsNone(helper._get_battery_soc())

        helper._get_dbus_value = MagicMock(return_value=True)
        self.assertIsNone(helper._get_battery_soc())

        helper._get_dbus_value = MagicMock(return_value=150.0)
        helper._warning_throttled = MagicMock()
        helper._delay_source_retry = MagicMock()
        self.assertIsNone(helper._get_battery_soc())
        helper._warning_throttled.assert_called_once()
        helper._delay_source_retry.assert_called_once_with("battery")

        helper._resolve_auto_battery_service = MagicMock(side_effect=RuntimeError("offline"))
        helper._invalidate_auto_battery_service = MagicMock()
        helper._delay_source_retry = MagicMock()
        self.assertIsNone(helper._get_battery_soc())
        helper._invalidate_auto_battery_service.assert_called_once_with()
        helper._delay_source_retry.assert_called_once_with("battery")

    def test_get_battery_soc_respects_source_retry_guard(self):
        helper = self._make_helper()
        helper._source_retry_after["battery"] = 200.0
        with patch("venus_evcharger_auto_input_helper.time.time", return_value=100.0):
            self.assertIsNone(helper._get_battery_soc())

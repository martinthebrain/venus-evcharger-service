# SPDX-License-Identifier: GPL-3.0-or-later
from tests.auto_input_helper_basic_cases_common import MagicMock, patch


class _AutoInputHelperBasicSubscriptionCases:
    def test_refresh_subscriptions_rebuilds_desired_specs_and_refreshes_sources(self):
        helper = self._make_helper()
        helper._desired_subscription_specs = MagicMock(
            return_value=[
                ("pv", "com.victronenergy.pvinverter.http_40", "/Ac/Power"),
                ("grid", "com.victronenergy.system", "/Ac/Grid/L1/Power"),
            ]
        )
        helper._register_name_owner_subscription = MagicMock()
        helper._subscribe_busitem_path = MagicMock()
        helper._clear_missing_subscriptions = MagicMock()
        helper._refresh_all_sources = MagicMock()
        self.assertFalse(helper._refresh_subscriptions())
        helper._subscribe_busitem_path.assert_any_call("pv", "com.victronenergy.pvinverter.http_40", "/Ac/Power")
        helper._subscribe_busitem_path.assert_any_call("grid", "com.victronenergy.system", "/Ac/Grid/L1/Power")
        helper._clear_missing_subscriptions.assert_called_once()
        helper._refresh_all_sources.assert_called_once_with()

    def test_signal_spec_key_and_subscribe_busitem_path_deduplicate_subscriptions(self):
        helper = self._make_helper()
        helper._request_gateway_value = MagicMock()

        self.assertEqual(helper._signal_spec_key("pv", "com.victronenergy.pvinverter.http_40", "/Ac/Power"), ("pv", "com.victronenergy.pvinverter.http_40", "/Ac/Power"))
        helper._subscribe_busitem_path("pv", "com.victronenergy.pvinverter.http_40", "/Ac/Power")
        helper._subscribe_busitem_path("pv", "com.victronenergy.pvinverter.http_40", "/Ac/Power")
        helper._request_gateway_value.assert_called_once_with(
            "com.victronenergy.pvinverter.http_40",
            "/Ac/Power",
            priority=80,
            reason="pv subscription refresh",
        )
        self.assertIn(("pv", "com.victronenergy.pvinverter.http_40", "/Ac/Power"), helper._signal_matches)
        self.assertIn(("pv", "com.victronenergy.pvinverter.http_40", "/Ac/Power"), helper._monitored_specs)

    def test_busitem_subscription_records_gateway_interest_without_dbus_callback(self):
        helper = self._make_helper()
        helper._dbus_generation = 7
        helper._system_bus_generation = 7
        helper._request_gateway_value = MagicMock()

        helper._subscribe_busitem_path("pv", "com.victronenergy.pvinverter.http_40", "/Ac/Power")

        self.assertEqual(helper._system_bus_generation, 7)
        self.assertEqual(helper._dbus_generation, 7)
        helper._request_gateway_value.assert_called_once()

    def test_clear_missing_subscriptions_removes_stale_entries_and_ignores_remove_errors(self):
        helper = self._make_helper()
        keep_key = ("pv", "svc", "/Ac/Power")
        drop_key = ("grid", "svc", "/Ac/Grid/L1/Power")
        helper._signal_matches = {keep_key: MagicMock(), drop_key: MagicMock(remove=MagicMock(side_effect=RuntimeError("boom")))}
        helper._monitored_specs = {keep_key: {"x": 1}, drop_key: {"y": 2}}
        helper._clear_missing_subscriptions({keep_key})
        self.assertIn(keep_key, helper._signal_matches)
        self.assertNotIn(drop_key, helper._signal_matches)
        self.assertNotIn(drop_key, helper._monitored_specs)

    def test_clear_missing_subscriptions_also_drops_specs_without_live_match(self):
        helper = self._make_helper()
        drop_key = ("grid", "svc", "/Ac/Grid/L1/Power")
        helper._signal_matches = {drop_key: None}
        helper._monitored_specs = {drop_key: {"y": 2}}
        helper._clear_missing_subscriptions(set())
        self.assertEqual(helper._signal_matches, {})
        self.assertEqual(helper._monitored_specs, {})

    def test_reset_system_bus_removes_all_matches_closes_bus_and_invalidates_generation(self):
        helper = self._make_helper()
        bus = MagicMock()
        busitem_match = MagicMock()
        failing_match = MagicMock(remove=MagicMock(side_effect=RuntimeError("remove failed")))
        name_owner_match = MagicMock()
        helper._system_bus = bus
        helper._dbus_generation = 3
        helper._system_bus_generation = 3
        helper._name_owner_match = name_owner_match
        helper._signal_matches = {
            ("pv", "svc", "/Ac/Power"): busitem_match,
            ("grid", "svc", "/Ac/Grid/L1/Power"): failing_match,
        }
        helper._monitored_specs = {
            ("pv", "svc", "/Ac/Power"): {"source": "pv"},
            ("grid", "svc", "/Ac/Grid/L1/Power"): {"source": "grid"},
        }

        helper._reset_system_bus()

        busitem_match.remove.assert_called_once_with()
        failing_match.remove.assert_called_once_with()
        name_owner_match.remove.assert_called_once_with()
        bus.close.assert_called_once_with()
        self.assertIsNone(helper._system_bus)
        self.assertIsNone(helper._name_owner_match)
        self.assertEqual(helper._signal_matches, {})
        self.assertEqual(helper._monitored_specs, {})
        self.assertEqual(helper._system_bus_generation, 0)
        self.assertEqual(helper._dbus_generation, 4)

    def test_register_name_owner_subscription_tracks_match_and_deduplicates(self):
        helper = self._make_helper()
        gateway_client = MagicMock()
        helper._gateway_client = MagicMock(return_value=gateway_client)
        helper._dbus_generation = 5
        helper._system_bus_generation = 5

        helper._register_name_owner_subscription()
        helper._register_name_owner_subscription()

        self.assertIsNotNone(helper._name_owner_match)
        gateway_client.enqueue_command.assert_called_once_with({"kind": "refresh_services", "priority": "normal"})

    def test_desired_subscription_specs_combines_pv_battery_and_grid_sources(self):
        helper = self._make_helper()
        helper.auto_pv_service = "com.victronenergy.pvinverter.http_40"
        helper._resolve_auto_battery_service = MagicMock(return_value="com.victronenergy.battery.socketcan_can1")
        specs = helper._desired_subscription_specs()
        self.assertIn(("pv", "com.victronenergy.pvinverter.http_40", "/Ac/Power"), specs)
        self.assertIn(("pv", "com.victronenergy.system", "/Dc/Pv/Power"), specs)
        self.assertIn(("battery", "com.victronenergy.battery.socketcan_can1", "/Soc"), specs)
        self.assertIn(("grid", "com.victronenergy.system", "/Ac/Grid/L1/Power"), specs)

    def test_desired_subscription_specs_tolerates_resolution_errors(self):
        helper = self._make_helper()
        helper.auto_pv_service = ""
        helper._resolve_auto_pv_services = MagicMock(side_effect=RuntimeError("pv down"))
        helper._resolve_auto_battery_service = MagicMock(side_effect=RuntimeError("battery down"))
        specs = helper._desired_subscription_specs()
        self.assertIn(("pv", "com.victronenergy.system", "/Dc/Pv/Power"), specs)
        self.assertNotIn(("battery", "com.victronenergy.battery.socketcan_can1", "/Soc"), specs)

    def test_desired_grid_subscription_specs_returns_empty_without_grid_service(self):
        helper = self._make_helper()
        helper.auto_grid_service = ""
        self.assertEqual(helper._desired_grid_subscription_specs(), [])

    def test_on_source_signal_logs_throttled_warning_on_refresh_error(self):
        helper = self._make_helper()
        helper._refresh_source = MagicMock(side_effect=RuntimeError("boom"))
        helper._warning_throttled = MagicMock()
        helper._on_source_signal("pv", sender="svc", path="/Ac/Power")
        helper._warning_throttled.assert_called_once()
        args = helper._warning_throttled.call_args[0]
        self.assertEqual(args[0], "auto-helper-source-signal-pv")
        self.assertGreater(helper._dbus_subscription_backoff_until, 0.0)

    def test_on_source_signal_resets_bus_and_schedules_backoff_after_dbus_error(self):
        helper = self._make_helper()
        helper._dbus_generation = 2
        helper._system_bus_generation = 2
        helper._refresh_source = MagicMock(side_effect=AssertionError("dbus connection broken"))
        helper._warning_throttled = MagicMock()
        scheduled = []

        with patch("venus_evcharger.inputs.helper.subscriptions.time.time", return_value=100.0):
            with patch(
                "venus_evcharger.inputs.helper.subscriptions.GLib.timeout_add",
                side_effect=lambda delay, callback: scheduled.append((delay, callback)),
            ):
                helper._on_source_signal("pv", 2, sender="svc", path="/Ac/Power")

        helper._warning_throttled.assert_called_once()
        self.assertIsNone(helper._system_bus)
        self.assertEqual(helper._system_bus_generation, 0)
        self.assertEqual(helper._dbus_generation, 3)
        self.assertEqual(helper._dbus_subscription_backoff_until, 105.0)
        self.assertEqual(len(scheduled), 1)

    def test_refresh_subscriptions_timer_requests_refresh_and_obeys_stop_flag(self):
        helper = self._make_helper()
        helper._schedule_refresh_subscriptions = MagicMock()
        helper._stop_requested = False
        self.assertTrue(helper._refresh_subscriptions_timer())
        helper._schedule_refresh_subscriptions.assert_called_once_with()
        helper._stop_requested = True
        self.assertFalse(helper._refresh_subscriptions_timer())

    def test_parent_watchdog_stops_without_quit_call_when_mainloop_is_missing(self):
        helper = self._make_helper()
        helper._stop_requested = False
        helper._main_loop = None
        helper._parent_alive = MagicMock(return_value=False)
        self.assertFalse(helper._parent_watchdog())

    def test_schedule_refresh_subscriptions_only_schedules_one_idle_callback(self):
        helper = self._make_helper()
        helper._refresh_subscriptions = MagicMock(return_value=False)
        callbacks = []

        with patch(
            "venus_evcharger_auto_input_helper.GLib.idle_add",
            side_effect=lambda callback: callbacks.append(callback),
        ):
            helper._schedule_refresh_subscriptions()
            helper._schedule_refresh_subscriptions()

        self.assertEqual(len(callbacks), 1)
        self.assertTrue(helper._refresh_scheduled)
        self.assertFalse(callbacks[0]())
        helper._refresh_subscriptions.assert_called_once_with()
        self.assertFalse(helper._refresh_scheduled)

    def test_on_name_owner_changed_schedules_refresh_only_for_relevant_services(self):
        helper = self._make_helper()
        helper._schedule_refresh_subscriptions = MagicMock()
        helper._on_name_owner_changed("com.victronenergy.system", "", ":1.5")
        helper._on_name_owner_changed("com.example.unrelated", "", ":1.6")
        helper._schedule_refresh_subscriptions.assert_called_once_with()

    def test_on_name_owner_changed_ignores_stale_dbus_generation(self):
        helper = self._make_helper()
        helper._dbus_generation = 4
        helper._schedule_refresh_subscriptions = MagicMock()

        helper._on_name_owner_changed(3, "com.victronenergy.system", "", ":1.5")
        helper._schedule_refresh_subscriptions.assert_not_called()

        helper._on_name_owner_changed(4, "com.victronenergy.system", "", ":1.6")
        helper._schedule_refresh_subscriptions.assert_called_once_with()

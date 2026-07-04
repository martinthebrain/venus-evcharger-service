# SPDX-License-Identifier: GPL-3.0-or-later
from types import SimpleNamespace

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
        self.assertEqual(
            helper._clear_missing_subscriptions.call_args.args[0],
            {
                ("pv", "com.victronenergy.pvinverter.http_40", "/Ac/Power"),
                ("grid", "com.victronenergy.system", "/Ac/Grid/L1/Power"),
            },
        )
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

    def test_subscribe_busitem_path_contract_records_exact_gateway_spec(self):
        helper = self._make_helper()
        helper._request_gateway_value = MagicMock()

        helper._subscribe_busitem_path(123, 456, 789)

        key = ("123", "456", "789")
        self.assertIn(key, helper._signal_matches)
        self.assertIsNotNone(helper._signal_matches[key])
        self.assertEqual(helper._monitored_specs[key], {"source": 123, "service_name": 456, "path": 789})
        helper._request_gateway_value.assert_called_once_with(
            456,
            789,
            priority=80,
            reason="123 subscription refresh",
        )

    def test_subscribe_busitem_path_without_gateway_client_only_records_local_interest(self):
        helper = self._make_helper()
        helper._request_gateway_value = "not-callable"

        helper._subscribe_busitem_path("grid", "svc", "/P")

        self.assertEqual(helper._monitored_specs[("grid", "svc", "/P")], {"source": "grid", "service_name": "svc", "path": "/P"})

    def test_subscribe_busitem_path_does_not_rewrite_existing_subscription(self):
        helper = self._make_helper()
        helper._ensure_poll_state()
        helper._request_gateway_value = MagicMock()
        key = ("pv", "svc", "/P")
        helper._signal_matches[key] = object()
        helper._monitored_specs[key] = {"source": "old", "service_name": "old", "path": "old"}

        helper._subscribe_busitem_path("pv", "svc", "/P")

        self.assertEqual(helper._monitored_specs[key], {"source": "old", "service_name": "old", "path": "old"})
        helper._request_gateway_value.assert_not_called()

    def test_clear_missing_subscriptions_removes_stale_entries_and_ignores_remove_errors(self):
        helper = self._make_helper()
        keep_key = ("pv", "svc", "/Ac/Power")
        drop_key = ("grid", "svc", "/Ac/Grid/L1/Power")
        drop_match = MagicMock()
        drop_match.remove = MagicMock(side_effect=RuntimeError("boom"))
        helper._signal_matches = {keep_key: MagicMock(), drop_key: drop_match}
        helper._monitored_specs = {keep_key: {"x": 1}, drop_key: {"y": 2}}
        helper._clear_missing_subscriptions({keep_key})
        drop_match.remove.assert_called_once_with()
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

        zero_generation = self._make_helper()
        zero_generation._dbus_generation = 0
        zero_generation._reset_system_bus()
        self.assertEqual(zero_generation._dbus_generation, 1)

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

    def test_resolved_pv_subscription_services_contracts(self):
        helper = self._make_helper()
        helper.auto_pv_service = "configured-pv"
        helper._resolve_auto_pv_services = MagicMock(side_effect=AssertionError("should not scan"))
        self.assertEqual(helper._resolved_pv_subscription_services(), ["configured-pv"])

        helper.auto_pv_service = ""
        helper._resolve_auto_pv_services = MagicMock(return_value=("pv-1", "pv-2"))
        self.assertEqual(helper._resolved_pv_subscription_services(), ["pv-1", "pv-2"])

        helper.auto_pv_service = None
        helper._resolve_auto_pv_services = MagicMock(return_value=["pv-fallback"])
        self.assertEqual(helper._resolved_pv_subscription_services(), ["pv-fallback"])

        helper._resolve_auto_pv_services = MagicMock(side_effect=ValueError("missing"))
        self.assertEqual(helper._resolved_pv_subscription_services(), [])

    def test_dc_pv_subscription_spec_requires_all_configured_parts(self):
        helper = self._make_helper()
        self.assertEqual(helper._dc_pv_subscription_spec(), ("pv", "com.victronenergy.system", "/Dc/Pv/Power"))

        for attr_name in ("auto_use_dc_pv", "auto_dc_pv_service", "auto_dc_pv_path"):
            changed = self._make_helper()
            setattr(changed, attr_name, False if attr_name == "auto_use_dc_pv" else "")
            self.assertIsNone(changed._dc_pv_subscription_spec())

    def test_battery_subscription_specs_use_primary_fallback_and_energy_sources(self):
        helper = self._make_helper()
        source_a = SimpleNamespace(soc_path="/SocA", battery_power_path="/PowerA", ac_power_path="")
        source_b = SimpleNamespace(soc_path="", battery_power_path="/PowerB", ac_power_path="/AcB")
        helper.auto_energy_sources = (source_a, source_b)
        helper._resolve_energy_source_service = MagicMock(side_effect=["svc-a", "svc-b"])

        self.assertEqual(
            helper._desired_battery_subscription_specs(),
            [
                ("battery", "svc-a", "/SocA"),
                ("battery", "svc-a", "/PowerA"),
                ("battery", "svc-b", "/PowerB"),
                ("battery", "svc-b", "/AcB"),
            ],
        )

        fallback = self._make_helper()
        primary = SimpleNamespace(soc_path="/Soc", battery_power_path="", ac_power_path="")
        fallback._primary_energy_source = MagicMock(return_value=primary)
        fallback._resolve_energy_source_service = MagicMock(return_value="primary-svc")
        self.assertEqual(fallback._desired_battery_subscription_specs(), [("battery", "primary-svc", "/Soc")])

        empty_sources = self._make_helper()
        empty_sources.auto_energy_sources = None
        empty_sources._primary_energy_source = MagicMock(return_value=primary)
        empty_sources._resolve_energy_source_service = MagicMock(return_value="primary-svc")
        self.assertEqual(empty_sources._desired_battery_subscription_specs(), [("battery", "primary-svc", "/Soc")])

    def test_battery_subscription_specs_ignore_resolution_failures(self):
        helper = self._make_helper()
        source = SimpleNamespace(soc_path="/Soc", battery_power_path="/Power", ac_power_path="/Ac")
        helper._resolve_energy_source_service = MagicMock(side_effect=ValueError("not available"))
        self.assertEqual(helper._battery_subscription_specs_for_source(source), [])

    def test_desired_grid_subscription_specs_contracts(self):
        helper = self._make_helper()
        helper.auto_grid_l2_path = ""
        self.assertEqual(
            helper._desired_grid_subscription_specs(),
            [
                ("grid", "com.victronenergy.system", "/Ac/Grid/L1/Power"),
                ("grid", "com.victronenergy.system", "/Ac/Grid/L3/Power"),
            ],
        )

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

    def test_on_source_signal_refreshes_only_current_generation(self):
        helper = self._make_helper()
        helper._dbus_generation = 9
        helper._refresh_source = MagicMock()

        helper._on_source_signal("grid", 8)
        helper._refresh_source.assert_not_called()

        helper._on_source_signal("grid", 9)
        helper._refresh_source.assert_called_once_with("grid")

    def test_on_source_signal_error_handler_contract(self):
        helper = self._make_helper()
        failure = TypeError("bad source callback")
        helper._refresh_source = MagicMock(side_effect=failure)
        helper._handle_dbus_callback_error = MagicMock()

        helper._on_source_signal("battery", 0, "ignored-arg", ignored_kwarg=True)

        helper._handle_dbus_callback_error.assert_called_once_with(
            "auto-helper-source-signal-battery",
            "Auto input helper failed to refresh %s after signal: %s",
            "battery",
            failure,
        )

    def test_refresh_subscriptions_timer_requests_refresh_and_obeys_stop_flag(self):
        helper = self._make_helper()
        helper._schedule_refresh_subscriptions = MagicMock()
        helper._stop_requested = False
        self.assertTrue(helper._refresh_subscriptions_timer())
        helper._schedule_refresh_subscriptions.assert_called_once_with()
        helper._stop_requested = True
        self.assertFalse(helper._refresh_subscriptions_timer())

    def test_subscription_refresh_backoff_and_delay_contract(self):
        helper = self._make_helper()
        helper._dbus_subscription_backoff_until = 95.0
        with patch("venus_evcharger.inputs.helper.subscriptions.time.time", return_value=100.0):
            self.assertEqual(helper._subscription_refresh_delay_seconds(), 0.0)
            self.assertFalse(helper._subscription_refresh_backoff_active())

        helper._dbus_subscription_backoff_until = 112.5
        with patch("venus_evcharger.inputs.helper.subscriptions.time.time", return_value=100.0):
            self.assertEqual(helper._subscription_refresh_delay_seconds(), 12.5)
            self.assertTrue(helper._subscription_refresh_backoff_active())

        helper._dbus_subscription_backoff_until = 0.0
        with patch("venus_evcharger.inputs.helper.subscriptions.time.time", return_value=0.0):
            self.assertEqual(helper._subscription_refresh_delay_seconds(), 0.0)

        helper._dbus_subscription_backoff_until = 100.5
        with patch("venus_evcharger.inputs.helper.subscriptions.time.time", return_value=100.0):
            self.assertEqual(helper._subscription_refresh_delay_seconds(), 0.5)
            self.assertTrue(helper._subscription_refresh_backoff_active())

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
        self.assertIs(callbacks[0](), False)
        helper._refresh_subscriptions.assert_called_once_with()
        self.assertIs(helper._refresh_scheduled, False)

    def test_schedule_refresh_subscriptions_uses_timeout_delay_and_honors_stop_flag(self):
        helper = self._make_helper()
        helper._refresh_subscriptions = MagicMock(return_value=False)
        callbacks = []

        with patch("venus_evcharger.inputs.helper.subscriptions.time.time", return_value=100.0):
            helper._dbus_subscription_backoff_until = 100.0005
            with patch(
                "venus_evcharger.inputs.helper.subscriptions.GLib.timeout_add",
                side_effect=lambda delay_ms, callback: callbacks.append((delay_ms, callback)),
            ):
                helper._schedule_refresh_subscriptions()

        self.assertEqual(callbacks[0][0], 1)
        helper._stop_requested = True
        self.assertIs(callbacks[0][1](), False)
        helper._refresh_subscriptions.assert_not_called()
        self.assertIs(helper._refresh_scheduled, False)

        delayed = self._make_helper()
        timeout_calls = []
        with patch("venus_evcharger.inputs.helper.subscriptions.time.time", return_value=100.0):
            delayed._dbus_subscription_backoff_until = 101.0
            with patch(
                "venus_evcharger.inputs.helper.subscriptions.GLib.timeout_add",
                side_effect=lambda delay_ms, callback: timeout_calls.append((delay_ms, callback)),
            ):
                delayed._schedule_refresh_subscriptions()
        self.assertEqual(timeout_calls[0][0], 1000)

    def test_schedule_refresh_subscriptions_skips_when_already_scheduled(self):
        helper = self._make_helper()
        helper._ensure_poll_state()
        helper._refresh_scheduled = True

        with patch("venus_evcharger.inputs.helper.subscriptions.GLib.idle_add") as idle_add:
            helper._schedule_refresh_subscriptions()

        idle_add.assert_not_called()
        self.assertTrue(helper._refresh_scheduled)

    def test_refresh_subscriptions_backoff_only_schedules_retry(self):
        helper = self._make_helper()
        helper._subscription_refresh_backoff_active = MagicMock(return_value=True)
        helper._schedule_refresh_subscriptions = MagicMock()
        helper._register_name_owner_subscription = MagicMock()

        self.assertFalse(helper._refresh_subscriptions())

        helper._schedule_refresh_subscriptions.assert_called_once_with()
        helper._register_name_owner_subscription.assert_not_called()

    def test_refresh_subscriptions_handles_callback_errors_without_clearing_existing_specs(self):
        helper = self._make_helper()
        keep_key = ("pv", "old", "/Old")
        helper._signal_matches = {keep_key: object()}
        helper._monitored_specs = {keep_key: {"source": "pv", "service_name": "old", "path": "/Old"}}
        helper._subscription_refresh_backoff_active = MagicMock(return_value=False)
        helper._register_name_owner_subscription = MagicMock()
        helper._desired_subscription_specs = MagicMock(return_value=[("pv", "svc", "/P")])
        helper._subscribe_busitem_path = MagicMock(side_effect=AssertionError("broken callback"))
        helper._clear_missing_subscriptions = MagicMock()
        helper._handle_dbus_callback_error = MagicMock()

        self.assertFalse(helper._refresh_subscriptions())

        helper._clear_missing_subscriptions.assert_not_called()
        helper._handle_dbus_callback_error.assert_called_once()
        self.assertEqual(helper._monitored_specs[keep_key], {"source": "pv", "service_name": "old", "path": "/Old"})

    def test_refresh_subscriptions_error_passes_contract_to_error_handler(self):
        helper = self._make_helper()
        failure = RuntimeError("source refresh failed")
        helper._subscription_refresh_backoff_active = MagicMock(return_value=False)
        helper._register_name_owner_subscription = MagicMock()
        helper._desired_subscription_specs = MagicMock(return_value=[])
        helper._clear_missing_subscriptions = MagicMock()
        helper._refresh_all_sources = MagicMock(side_effect=failure)
        helper._handle_dbus_callback_error = MagicMock()

        self.assertFalse(helper._refresh_subscriptions())

        helper._handle_dbus_callback_error.assert_called_once_with(
            "auto-helper-refresh-subscriptions",
            "Auto input helper failed to refresh DBus subscriptions: %s",
            failure,
        )

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

    def test_name_owner_changed_handles_callback_errors(self):
        helper = self._make_helper()
        helper._is_relevant_name_owner_change = MagicMock(side_effect=TypeError("bad signal"))
        helper._handle_dbus_callback_error = MagicMock()

        helper._on_name_owner_changed("com.victronenergy.system")

        helper._handle_dbus_callback_error.assert_called_once()

    def test_on_name_owner_changed_uses_parsed_name_for_relevance_and_error_contract(self):
        helper = self._make_helper()
        failure = TypeError("bad signal")
        helper._dbus_generation = 6
        helper._is_relevant_name_owner_change = MagicMock(side_effect=failure)
        helper._handle_dbus_callback_error = MagicMock()

        helper._on_name_owner_changed(6, "svc.name", "", ":1.10")

        helper._is_relevant_name_owner_change.assert_called_once_with("svc.name")
        helper._handle_dbus_callback_error.assert_called_once_with(
            "auto-helper-name-owner-signal",
            "Auto input helper failed to process DBus owner change for %s: %s",
            "svc.name",
            failure,
        )

    def test_parse_name_owner_changed_and_generation_contracts(self):
        helper = self._make_helper()
        helper._dbus_generation = 2

        self.assertEqual(helper._parse_name_owner_changed_args((2, "svc", "", ":1")), (2, "svc"))
        self.assertEqual(helper._parse_name_owner_changed_args(("svc", "", ":1")), (None, "svc"))
        self.assertEqual(helper._parse_name_owner_changed_args(()), (None, ""))
        self.assertTrue(helper._dbus_callback_generation_current(None))
        self.assertTrue(helper._dbus_callback_generation_current(2))
        helper._dbus_generation = "02"
        self.assertTrue(helper._dbus_callback_generation_current(2))
        self.assertFalse(helper._dbus_callback_generation_current(3))

    def test_service_name_matching_contracts(self):
        helper = self._make_helper()
        helper.auto_pv_service = "pv.explicit"
        helper.auto_battery_service = "battery.explicit"
        helper.auto_pv_service_prefix = "pv.prefix"
        helper.auto_battery_service_prefix = "battery.prefix"
        helper.auto_energy_sources = (
            SimpleNamespace(service_name="energy.explicit", service_prefix="energy.prefix"),
            SimpleNamespace(service_name="", service_prefix=""),
        )

        self.assertTrue(helper._matches_explicit_service_name("pv.explicit"))
        self.assertTrue(helper._matches_explicit_service_name("battery.explicit"))
        self.assertTrue(helper._matches_explicit_service_name("energy.explicit"))
        self.assertFalse(helper._matches_explicit_service_name("energy.prefix.1"))
        self.assertTrue(helper._matches_explicit_pv_service_name("pv.explicit"))
        self.assertFalse(helper._matches_explicit_pv_service_name("battery.explicit"))
        self.assertTrue(helper._matches_explicit_battery_service_name("battery.explicit"))
        self.assertFalse(helper._matches_explicit_battery_service_name("pv.explicit"))
        self.assertTrue(helper._matches_explicit_energy_source_service_name("energy.explicit"))
        self.assertFalse(helper._matches_explicit_energy_source_service_name("energy.prefix.1"))
        self.assertTrue(helper._matches_discovery_prefix("pv.prefix.1"))
        self.assertTrue(helper._matches_discovery_prefix("battery.prefix.1"))
        self.assertTrue(helper._matches_discovery_prefix("energy.prefix.1"))
        self.assertFalse(helper._matches_discovery_prefix("other.prefix.1"))
        self.assertFalse(helper._matches_explicit_energy_source_service_name("anything"))
        helper.auto_energy_sources = ()
        self.assertFalse(helper._matches_discovery_prefix("energy.prefix.1"))
        helper.auto_energy_sources = (
            SimpleNamespace(service_name="energy.explicit", service_prefix="energy.prefix"),
            SimpleNamespace(service_name="", service_prefix=""),
        )
        self.assertTrue(helper._is_relevant_name_owner_change(helper.auto_grid_service))
        self.assertTrue(helper._is_relevant_name_owner_change(helper.auto_dc_pv_service))
        self.assertTrue(helper._is_relevant_name_owner_change("pv.explicit"))
        self.assertTrue(helper._is_relevant_name_owner_change("pv.prefix.1"))
        self.assertTrue(helper._is_relevant_name_owner_change("com.victronenergy.system"))
        self.assertFalse(helper._is_relevant_name_owner_change("unrelated"))

        isolated = self._make_helper()
        isolated.auto_grid_service = "grid.unique"
        isolated.auto_dc_pv_service = "dc.unique"
        isolated.auto_pv_service = ""
        isolated.auto_battery_service = ""
        isolated.auto_pv_service_prefix = "pv.unique."
        isolated.auto_battery_service_prefix = "battery.unique."
        isolated.auto_energy_sources = ()
        self.assertTrue(isolated._is_relevant_name_owner_change("grid.unique"))
        self.assertTrue(isolated._is_relevant_name_owner_change("dc.unique"))

    def test_handle_dbus_callback_error_contracts(self):
        helper = self._make_helper()
        helper.auto_dbus_backoff_base_seconds = 0.5
        helper._warning_throttled = MagicMock()
        helper._reset_system_bus = MagicMock()
        helper._schedule_refresh_subscriptions = MagicMock()

        with patch("venus_evcharger.inputs.helper.subscriptions.time.time", return_value=40.0):
            helper._handle_dbus_callback_error("key", "message %s", "arg")

        helper._warning_throttled.assert_called_once_with("key", 5.0, "message %s", "arg")
        helper._reset_system_bus.assert_called_once_with()
        helper._schedule_refresh_subscriptions.assert_called_once_with()
        self.assertEqual(helper._dbus_subscription_backoff_until, 41.0)

        helper.auto_dbus_backoff_base_seconds = 8.0
        helper._warning_throttled.reset_mock()
        helper._reset_system_bus.reset_mock()
        helper._schedule_refresh_subscriptions.reset_mock()
        with patch("venus_evcharger.inputs.helper.subscriptions.time.time", return_value=50.0):
            helper._handle_dbus_callback_error("key2", "message2")

        helper._warning_throttled.assert_called_once_with("key2", 8.0, "message2")
        self.assertEqual(helper._dbus_subscription_backoff_until, 58.0)

        helper.auto_dbus_backoff_base_seconds = 0.0
        helper._warning_throttled.reset_mock()
        helper._reset_system_bus.reset_mock()
        helper._schedule_refresh_subscriptions.reset_mock()
        with patch("venus_evcharger.inputs.helper.subscriptions.time.time", return_value=60.0):
            helper._handle_dbus_callback_error("key3", "message3")

        helper._warning_throttled.assert_called_once_with("key3", 5.0, "message3")
        self.assertEqual(helper._dbus_subscription_backoff_until, 65.0)

    def test_get_system_bus_remains_disabled(self):
        helper = self._make_helper()
        with self.assertRaises(RuntimeError) as exc:
            helper._get_system_bus()
        self.assertEqual(str(exc.exception), "Direct DBus access is disabled; use the DBus gateway adapter")

    def test_register_name_owner_subscription_failure_contracts(self):
        helper = self._make_helper()
        helper._gateway_client = None
        with patch("venus_evcharger.inputs.helper.subscriptions.logging.debug") as debug:
            helper._register_name_owner_subscription()
        debug.assert_called_once_with("Gateway service refresh request skipped; gateway client is unavailable")
        self.assertIsNone(helper._name_owner_match)

        helper._gateway_client = MagicMock(side_effect=RuntimeError("gateway down"))
        with patch("venus_evcharger.inputs.helper.subscriptions.logging.debug") as debug:
            helper._register_name_owner_subscription()
        debug.assert_called_once_with("Gateway service refresh request failed: %s", helper._gateway_client.side_effect)
        self.assertIsNone(helper._name_owner_match)

    def test_clear_all_signal_matches_and_cleanup_helpers_contracts(self):
        helper = self._make_helper()
        first = MagicMock()
        second = MagicMock(remove=MagicMock(side_effect=RuntimeError("ignore")))
        name_owner = MagicMock()
        helper._signal_matches = {
            ("pv", "svc", "/P"): first,
            ("grid", "svc", "/G"): second,
        }
        helper._monitored_specs = {
            ("pv", "svc", "/P"): {"source": "pv"},
            ("grid", "svc", "/G"): {"source": "grid"},
        }
        helper._name_owner_match = name_owner

        helper._clear_all_signal_matches()

        first.remove.assert_called_once_with()
        second.remove.assert_called_once_with()
        name_owner.remove.assert_called_once_with()
        self.assertEqual(helper._signal_matches, {})
        self.assertEqual(helper._monitored_specs, {})
        self.assertIsNone(helper._name_owner_match)

        helper._remove_signal_match(None)
        helper._remove_signal_match(object())
        helper._close_system_bus(None)
        helper._close_system_bus(object())

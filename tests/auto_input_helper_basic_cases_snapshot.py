# SPDX-License-Identifier: GPL-3.0-or-later
from tests.auto_input_helper_basic_cases_common import AutoInputHelper, MagicMock, json, patch, tempfile


class _AutoInputHelperBasicSnapshotCases:
    def test_ensure_poll_state_populates_missing_defaults(self):
        helper = AutoInputHelper.__new__(AutoInputHelper)
        helper.poll_interval_seconds = 1.5

        helper._ensure_poll_state()

        self.assertEqual(helper.auto_pv_poll_interval_seconds, 1.5)
        self.assertEqual(helper.auto_grid_poll_interval_seconds, 1.5)
        self.assertEqual(helper.auto_battery_poll_interval_seconds, 1.5)
        self.assertIn("captured_at", helper._last_snapshot_state)
        self.assertEqual(helper._last_snapshot_state["snapshot_version"], AutoInputHelper.SNAPSHOT_SCHEMA_VERSION)
        self.assertEqual(helper._next_source_poll_at["pv"], 0.0)
        self.assertFalse(helper._refresh_scheduled)
        self.assertEqual(helper.subscription_refresh_seconds, 60.0)
        self.assertEqual(helper.validation_poll_seconds, 30.0)
        self.assertIsNone(helper._main_loop)
        self.assertFalse(helper._stop_requested)

    def test_ensure_poll_state_derives_poll_interval_from_source_intervals(self):
        helper = AutoInputHelper.__new__(AutoInputHelper)
        helper.auto_pv_poll_interval_seconds = 2.0
        helper.auto_grid_poll_interval_seconds = 3.0
        helper.auto_battery_poll_interval_seconds = 5.0
        helper._ensure_poll_state()
        self.assertEqual(helper.poll_interval_seconds, 2.0)

    def test_get_pv_power_returns_zero_when_gateway_reports_zero(self):
        helper = self._make_helper()
        helper._get_gateway_read_value = MagicMock(return_value=0.0)
        self.assertEqual(helper._get_pv_power(), 0.0)
        helper._get_gateway_read_value.assert_called_once()

    def test_get_pv_power_returns_semantic_gateway_value(self):
        helper = self._make_helper()
        helper._get_gateway_read_value = MagicMock(return_value=750.0)
        self.assertEqual(helper._get_pv_power(), 750.0)

    def test_get_grid_power_backs_off_when_gateway_value_is_missing(self):
        helper = self._make_helper()
        helper._get_gateway_read_value = MagicMock(return_value=None)
        helper._delay_source_retry = MagicMock()
        self.assertIsNone(helper._get_grid_power())
        helper._delay_source_retry.assert_called_once_with("grid")

    def test_write_snapshot_uses_atomic_ram_file(self):
        helper = self._make_helper()
        helper.helper_generation = 9
        with tempfile.TemporaryDirectory() as temp_dir:
            helper.snapshot_path = f"{temp_dir}/auto.json"
            with patch("venus_evcharger_auto_input_helper.os.getpid", return_value=12345):
                helper._write_snapshot({"captured_at": 100.0, "pv_power": 0.0})
            with open(helper.snapshot_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)

        self.assertEqual(payload["captured_at"], 100.0)
        self.assertEqual(payload["pv_power"], 0.0)
        self.assertEqual(payload["snapshot_version"], AutoInputHelper.SNAPSHOT_SCHEMA_VERSION)
        self.assertEqual(payload["writer_pid"], 12345)
        self.assertEqual(payload["helper_generation"], 9)

    def test_collect_snapshot_polls_battery_less_often_than_pv_and_grid(self):
        helper = self._make_helper()
        helper._get_pv_power = MagicMock(side_effect=[2100.0, 2200.0])
        helper._get_battery_snapshot = MagicMock(side_effect=[{"battery_soc": 55.0, "battery_source_count": 1}])
        helper._get_grid_power = MagicMock(side_effect=[-800.0, -750.0])

        first = helper._collect_snapshot(100.0)
        second = helper._collect_snapshot(102.0)

        self.assertEqual(first["pv_power"], 2100.0)
        self.assertEqual(first["battery_soc"], 55.0)
        self.assertEqual(first["grid_power"], -800.0)
        self.assertEqual(second["pv_power"], 2200.0)
        self.assertEqual(second["battery_soc"], 55.0)
        self.assertEqual(second["battery_captured_at"], 100.0)
        self.assertEqual(second["grid_power"], -750.0)
        self.assertEqual(second["captured_at"], 102.0)
        self.assertEqual(second["snapshot_version"], AutoInputHelper.SNAPSHOT_SCHEMA_VERSION)
        self.assertIn("writer_pid", second)
        self.assertIn("helper_generation", second)
        self.assertEqual(helper._get_pv_power.call_count, 2)
        self.assertEqual(helper._get_grid_power.call_count, 2)
        self.assertEqual(helper._get_battery_snapshot.call_count, 1)
        self.assertEqual(second["battery_source_count"], 1)

    def test_collect_snapshot_clears_source_fields_when_getter_returns_none(self):
        helper = self._make_helper()
        helper._last_snapshot_state["pv_power"] = 2100.0
        helper._last_snapshot_state["pv_captured_at"] = 90.0
        helper._get_pv_power = MagicMock(return_value=None)
        helper._get_battery_snapshot = MagicMock(return_value={"battery_soc": 55.0})
        helper._get_grid_power = MagicMock(return_value=-800.0)

        snapshot = helper._collect_snapshot(100.0)
        self.assertIsNone(snapshot["pv_power"])
        self.assertIsNone(snapshot["pv_captured_at"])

    def test_configured_auto_battery_service_returns_none_when_soc_is_missing(self):
        helper = self._make_helper()
        helper._list_dbus_services = MagicMock(return_value=[helper.auto_battery_service])
        helper._get_dbus_value = MagicMock(return_value=None)
        self.assertIsNone(helper._configured_auto_battery_service(100.0))
        self.assertIsNone(helper._resolved_auto_battery_service)
        self.assertEqual(helper._auto_battery_last_scan, 0.0)

    def test_configured_auto_battery_service_returns_none_when_read_raises(self):
        helper = self._make_helper()
        helper.auto_battery_service = "configured-battery"
        helper._list_dbus_services = MagicMock(return_value=["configured-battery"])
        helper._get_dbus_value = MagicMock(side_effect=RuntimeError("offline"))
        self.assertIsNone(helper._configured_auto_battery_service(100.0))

    def test_set_source_value_updates_snapshot_and_ignores_unknown_source(self):
        helper = self._make_helper()
        helper._write_snapshot = MagicMock()

        helper._set_source_value("pv", 1200.0, 99.0)
        self.assertEqual(helper._last_snapshot_state["pv_power"], 1200.0)
        self.assertEqual(helper._last_snapshot_state["pv_captured_at"], 99.0)

        helper._set_source_value("battery", 55.0, 99.5)
        self.assertEqual(helper._last_snapshot_state["battery_soc"], 55.0)
        self.assertEqual(helper._last_snapshot_state["battery_captured_at"], 99.5)

        helper._set_source_value("grid", -750.0, 100.0)
        self.assertEqual(helper._last_snapshot_state["grid_power"], -750.0)
        self.assertEqual(helper._last_snapshot_state["grid_captured_at"], 100.0)
        self.assertEqual(helper._write_snapshot.call_count, 3)

        helper._write_snapshot.reset_mock()
        previous_state = dict(helper._last_snapshot_state)
        helper._set_source_value("unknown", 1.0, 101.0)
        self.assertEqual(helper._last_snapshot_state, previous_state)
        helper._write_snapshot.assert_not_called()

    def test_set_source_value_applies_structured_battery_snapshot_payload(self):
        helper = self._make_helper()
        helper._write_snapshot = MagicMock()
        helper._set_source_value(
            "battery",
            {
                "battery_soc": 56.0,
                "battery_combined_soc": 58.0,
                "battery_combined_usable_capacity_wh": 9000.0,
                "battery_combined_charge_power_w": 700.0,
                "battery_combined_discharge_power_w": 0.0,
                "battery_combined_net_power_w": -700.0,
                "battery_combined_ac_power_w": 1500.0,
                "battery_headroom_charge_w": 300.0,
                "battery_headroom_discharge_w": 1100.0,
                "expected_near_term_export_w": 120.0,
                "expected_near_term_import_w": 0.0,
                "battery_source_count": 2,
                "battery_online_source_count": 2,
                "battery_valid_soc_source_count": 2,
                "battery_sources": [{"source_id": "victron"}],
                "battery_learning_profiles": {"victron": {"sample_count": 1}},
            },
            99.5,
        )
        self.assertEqual(helper._last_snapshot_state["battery_soc"], 56.0)
        self.assertEqual(helper._last_snapshot_state["battery_combined_soc"], 58.0)
        self.assertEqual(helper._last_snapshot_state["battery_headroom_charge_w"], 300.0)
        self.assertEqual(helper._last_snapshot_state["expected_near_term_export_w"], 120.0)
        self.assertEqual(helper._last_snapshot_state["battery_sources"], [{"source_id": "victron"}])
        self.assertEqual(helper._last_snapshot_state["battery_learning_profiles"], {"victron": {"sample_count": 1}})
        self.assertEqual(helper._last_snapshot_state["battery_captured_at"], 99.5)

    def test_heartbeat_updates_only_helper_liveness_not_source_timestamps(self):
        helper = self._make_helper()
        helper._last_snapshot_state = {
            "captured_at": 100.0,
            "heartbeat_at": 100.0,
            "pv_captured_at": 100.0,
            "pv_power": 2100.0,
            "battery_captured_at": 100.0,
            "battery_soc": 55.0,
            "grid_captured_at": 100.0,
            "grid_power": -800.0,
        }
        helper._write_snapshot = MagicMock()

        with patch("venus_evcharger_auto_input_helper.time.time", return_value=130.0):
            helper._heartbeat_snapshot()

        self.assertEqual(helper._last_snapshot_state["heartbeat_at"], 130.0)
        self.assertEqual(helper._last_snapshot_state["captured_at"], 100.0)
        self.assertEqual(helper._last_snapshot_state["snapshot_version"], AutoInputHelper.SNAPSHOT_SCHEMA_VERSION)
        self.assertEqual(helper._last_snapshot_state["pv_captured_at"], 100.0)
        self.assertEqual(helper._last_snapshot_state["battery_captured_at"], 100.0)
        self.assertEqual(helper._last_snapshot_state["grid_captured_at"], 100.0)

    def test_refresh_source_and_validation_poll_use_expected_source_getters(self):
        helper = self._make_helper()
        helper._set_source_value = MagicMock()
        helper._get_pv_power = MagicMock(return_value=2100.0)
        battery_payload = {"battery_soc": 56.0, "battery_source_count": 1}
        helper._get_battery_snapshot = MagicMock(return_value=battery_payload)
        helper._get_grid_power = MagicMock(return_value=-800.0)

        helper._refresh_source("pv", 100.0)
        helper._refresh_source("battery", 101.0)
        helper._refresh_source("grid", 102.0)
        helper._refresh_source("unknown", 103.0)

        helper._set_source_value.assert_any_call("pv", 2100.0, 100.0)
        helper._set_source_value.assert_any_call("battery", battery_payload, 101.0)
        helper._set_source_value.assert_any_call("grid", -800.0, 102.0)
        self.assertEqual(helper._set_source_value.call_count, 3)

        helper._refresh_all_sources = MagicMock()
        helper._stop_requested = False
        self.assertTrue(helper._validation_poll())
        helper._refresh_all_sources.assert_called_once_with()
        helper._stop_requested = True
        self.assertFalse(helper._validation_poll())

    def test_refresh_all_sources_uses_current_time_when_now_is_omitted(self):
        helper = self._make_helper()
        helper._refresh_source = MagicMock()
        with patch("venus_evcharger_auto_input_helper.time.time", return_value=123.0):
            helper._refresh_all_sources()
        helper._refresh_source.assert_any_call("pv", 123.0)
        helper._refresh_source.assert_any_call("battery", 123.0)
        helper._refresh_source.assert_any_call("grid", 123.0)
        self.assertEqual(helper._refresh_source.call_count, 3)

    def test_get_pv_power_ignores_legacy_ac_resolution_when_gateway_has_value(self):
        helper = self._make_helper()
        helper._resolve_auto_pv_services = MagicMock(return_value=["com.victronenergy.pvinverter.http_40"])
        helper._get_dbus_value = MagicMock(return_value=999.0)
        helper._get_gateway_read_value = MagicMock(return_value=500.0)
        self.assertEqual(helper._get_pv_power(), 500.0)
        helper._resolve_auto_pv_services.assert_not_called()
        helper._get_dbus_value.assert_not_called()

    def test_get_pv_power_backs_off_when_gateway_value_is_missing(self):
        helper = self._make_helper()
        helper._get_gateway_read_value = MagicMock(return_value=None)
        helper._delay_source_retry = MagicMock()
        self.assertIsNone(helper._get_pv_power())
        helper._delay_source_retry.assert_called_once_with("pv")

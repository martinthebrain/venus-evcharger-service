# SPDX-License-Identifier: GPL-3.0-or-later
from unittest.mock import call

from tests.auto_input_helper_basic_cases_common import AutoInputHelper, MagicMock, json, patch, tempfile


class _RecordingLock:
    def __init__(self):
        self.events = []

    def __enter__(self):
        self.events.append("enter")
        return self

    def __exit__(self, *_args):
        self.events.append("exit")
        return None


class _AutoInputHelperBasicSnapshotCases:
    def test_snapshot_static_contracts_are_stable(self):
        self.assertEqual(AutoInputHelper._default_source_poll_schedule(), {"pv": 0.0, "battery": 0.0, "grid": 0.0})
        self.assertEqual(
            AutoInputHelper._battery_snapshot_field_names(),
            (
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
            ),
        )

    def test_ensure_poll_state_populates_missing_defaults(self):
        helper = AutoInputHelper.__new__(AutoInputHelper)
        helper.poll_interval_seconds = 1.5

        helper._ensure_poll_state()

        self.assertEqual(helper.auto_pv_poll_interval_seconds, 1.5)
        self.assertEqual(helper.auto_grid_poll_interval_seconds, 1.5)
        self.assertEqual(helper.auto_battery_poll_interval_seconds, 1.5)
        self.assertIn("captured_at", helper._last_snapshot_state)
        self.assertEqual(helper._last_snapshot_state["snapshot_version"], AutoInputHelper.SNAPSHOT_SCHEMA_VERSION)
        self.assertEqual(helper._next_source_poll_at, {"pv": 0.0, "battery": 0.0, "grid": 0.0})
        self.assertIsNone(helper._system_bus)
        self.assertEqual(helper._dbus_generation, 0)
        self.assertEqual(helper._system_bus_generation, 0)
        self.assertIsNone(helper._name_owner_match)
        self.assertEqual(helper._dbus_subscription_backoff_until, 0.0)
        self.assertEqual(helper._signal_matches, {})
        self.assertEqual(helper._monitored_specs, {})
        self.assertEqual(helper._auto_battery_capacity_estimates, {})
        self.assertEqual(helper._auto_battery_capacity_startup_recheck_at, 0.0)
        self.assertEqual(helper._auto_battery_capacity_startup_rechecked, {})
        self.assertFalse(helper._refresh_scheduled)
        self.assertEqual(helper.subscription_refresh_seconds, 60.0)
        self.assertEqual(helper.validation_poll_seconds, 30.0)
        self.assertIsNone(helper._main_loop)
        self.assertFalse(helper._stop_requested)
        self.assertEqual(helper.helper_generation, 0)
        self.assertEqual(helper.runtime_instance_id, "")

    def test_ensure_poll_state_derives_poll_interval_from_source_intervals(self):
        helper = AutoInputHelper.__new__(AutoInputHelper)
        helper.auto_pv_poll_interval_seconds = 9.0
        helper.auto_grid_poll_interval_seconds = 1.0
        helper.auto_battery_poll_interval_seconds = 5.0
        helper._ensure_poll_state()
        self.assertEqual(helper.poll_interval_seconds, 1.0)

    def test_ensure_source_poll_interval_contracts(self):
        helper = AutoInputHelper.__new__(AutoInputHelper)
        helper.poll_interval_seconds = 0.1
        helper.auto_grid_poll_interval_seconds = 9.0

        helper._ensure_poll_state()

        self.assertEqual(helper.auto_pv_poll_interval_seconds, 0.2)
        self.assertEqual(helper.auto_grid_poll_interval_seconds, 9.0)
        self.assertEqual(helper.auto_battery_poll_interval_seconds, 0.2)
        self.assertEqual(helper.poll_interval_seconds, 0.1)

        defaulted = AutoInputHelper.__new__(AutoInputHelper)
        defaulted._ensure_poll_state()
        self.assertEqual(defaulted.auto_pv_poll_interval_seconds, 1.0)
        self.assertEqual(defaulted.auto_grid_poll_interval_seconds, 1.0)
        self.assertEqual(defaulted.auto_battery_poll_interval_seconds, 1.0)
        self.assertEqual(defaulted.poll_interval_seconds, 1.0)

        pv_minimum = AutoInputHelper.__new__(AutoInputHelper)
        pv_minimum.auto_pv_poll_interval_seconds = 1.0
        pv_minimum.auto_grid_poll_interval_seconds = 5.0
        pv_minimum.auto_battery_poll_interval_seconds = 9.0
        pv_minimum._ensure_poll_state()
        self.assertEqual(pv_minimum.poll_interval_seconds, 1.0)

        battery_minimum = AutoInputHelper.__new__(AutoInputHelper)
        battery_minimum.auto_pv_poll_interval_seconds = 9.0
        battery_minimum.auto_grid_poll_interval_seconds = 5.0
        battery_minimum.auto_battery_poll_interval_seconds = 1.0
        battery_minimum._ensure_poll_state()
        self.assertEqual(battery_minimum.poll_interval_seconds, 1.0)

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

    def test_collect_snapshot_writes_canonical_liveness_keys_from_empty_state(self):
        helper = self._make_helper()
        helper._last_snapshot_state = {}
        helper._next_source_poll_at = {"pv": 999.0, "battery": 999.0, "grid": 999.0}
        snapshot = helper._collect_snapshot(10.0)

        self.assertEqual(snapshot["captured_at"], 10.0)
        self.assertEqual(snapshot["heartbeat_at"], 10.0)
        self.assertEqual(snapshot["snapshot_version"], AutoInputHelper.SNAPSHOT_SCHEMA_VERSION)
        self.assertNotIn("XXheartbeat_atXX", snapshot)
        self.assertNotIn("HEARTBEAT_AT", snapshot)
        self.assertNotIn("XXsnapshot_versionXX", snapshot)
        self.assertNotIn("SNAPSHOT_VERSION", snapshot)

    def test_due_snapshot_sources_contracts(self):
        helper = self._make_helper()
        helper.auto_pv_poll_interval_seconds = 2.0
        helper.auto_battery_poll_interval_seconds = 10.0
        helper.auto_grid_poll_interval_seconds = 3.0
        helper._next_source_poll_at = {"pv": 100.0, "battery": 200.0, "grid": 99.0}

        due = helper._due_snapshot_sources(100.0)
        self.assertEqual([item[0] for item in due], ["pv", "grid"])
        self.assertEqual([item[1] for item in due], [2.0, 3.0])
        self.assertEqual([item[3] for item in due], ["pv_power", "grid_power"])
        self.assertEqual([item[4] for item in due], ["pv_captured_at", "grid_captured_at"])

        all_due = helper._due_snapshot_sources(200.0)
        self.assertEqual([item[0] for item in all_due], ["pv", "battery", "grid"])
        self.assertEqual([item[3] for item in all_due], ["pv_power", "battery_soc", "grid_power"])
        self.assertEqual([item[4] for item in all_due], ["pv_captured_at", "battery_captured_at", "grid_captured_at"])

        helper._next_source_poll_at = {}
        self.assertEqual([item[0] for item in helper._due_snapshot_sources(0.5)], ["pv", "battery", "grid"])

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
        self.assertEqual(helper._last_snapshot_state["captured_at"], 100.0)
        self.assertEqual(helper._last_snapshot_state["heartbeat_at"], 100.0)
        self.assertEqual(helper._last_snapshot_state["snapshot_version"], AutoInputHelper.SNAPSHOT_SCHEMA_VERSION)
        self.assertEqual(helper._last_snapshot_state["grid_status"], "ok")
        self.assertNotIn("XXcaptured_atXX", helper._last_snapshot_state)
        self.assertNotIn("HEARTBEAT_AT", helper._last_snapshot_state)
        self.assertNotIn("SNAPSHOT_VERSION", helper._last_snapshot_state)

        helper._write_snapshot.reset_mock()
        previous_state = dict(helper._last_snapshot_state)
        helper._set_source_value("unknown", 1.0, 101.0)
        self.assertEqual(helper._last_snapshot_state, previous_state)
        helper._write_snapshot.assert_not_called()

        fresh = self._make_helper()
        fresh._last_snapshot_state = {}
        fresh._write_snapshot = MagicMock()
        fresh._set_source_value("pv", 12.0, 12.5)
        self.assertEqual(
            fresh._last_snapshot_state,
            {
                "pv_power": 12.0,
                "pv_captured_at": 12.5,
                "pv_status": "ok",
                "helper_state": "running",
                "helper_status": "running",
                "captured_at": 12.5,
                "heartbeat_at": 12.5,
                "snapshot_version": AutoInputHelper.SNAPSHOT_SCHEMA_VERSION,
                "writer_pid": fresh._last_snapshot_state["writer_pid"],
                "helper_generation": 0,
                "runtime_instance_id": "",
            },
        )
        fresh._write_snapshot.assert_called_once_with(fresh._last_snapshot_state)
        self.assertNotIn("XXsnapshot_versionXX", fresh._last_snapshot_state)

    def test_set_source_status_contracts(self):
        snapshot = {}
        AutoInputHelper._set_source_status(snapshot, "pv", True)
        self.assertEqual(snapshot, {"pv_status": "ok", "helper_state": "running", "helper_status": "running"})

        AutoInputHelper._set_source_status(snapshot, "pv", False)
        self.assertEqual(snapshot["pv_status"], "missing")
        self.assertEqual(snapshot["helper_state"], "running")
        self.assertEqual(snapshot["helper_status"], "running")

        AutoInputHelper._set_source_status(snapshot, "grid", True)
        self.assertEqual(snapshot["grid_status"], "ok")
        self.assertEqual(snapshot["pv_status"], "missing")

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

        helper._set_source_value("battery", {"battery_soc": 57.0}, 100.5)
        self.assertEqual(helper._last_snapshot_state["battery_soc"], 57.0)
        self.assertIsNone(helper._last_snapshot_state["battery_combined_soc"])
        self.assertIsNone(helper._last_snapshot_state["battery_sources"])
        self.assertEqual(helper._last_snapshot_state["battery_captured_at"], 100.5)
        self.assertEqual(helper._last_snapshot_state["battery_status"], "ok")

        helper._set_source_value("battery", {"battery_soc": None}, 101.5)
        self.assertIsNone(helper._last_snapshot_state["battery_soc"])
        self.assertIsNone(helper._last_snapshot_state["battery_captured_at"])
        self.assertEqual(helper._last_snapshot_state["battery_status"], "missing")

    def test_apply_source_snapshot_value_contracts(self):
        helper = self._make_helper()
        snapshot = {}

        helper._apply_source_snapshot_value(snapshot, "grid", "grid_power", "grid_captured_at", -500.0, 50.0)
        self.assertEqual(
            snapshot,
            {
                "grid_power": -500.0,
                "grid_captured_at": 50.0,
                "grid_status": "ok",
                "helper_state": "running",
                "helper_status": "running",
            },
        )

        helper._apply_source_snapshot_value(snapshot, "grid", "grid_power", "grid_captured_at", None, 60.0)
        self.assertIsNone(snapshot["grid_power"])
        self.assertIsNone(snapshot["grid_captured_at"])
        self.assertEqual(snapshot["grid_status"], "missing")

        battery_snapshot = {}
        helper._apply_source_snapshot_value(
            battery_snapshot,
            "battery",
            "battery_soc",
            "battery_captured_at",
            {"battery_soc": 75.0, "battery_source_count": 3},
            70.0,
        )
        self.assertEqual(battery_snapshot["battery_soc"], 75.0)
        self.assertEqual(battery_snapshot["battery_captured_at"], 70.0)
        self.assertEqual(battery_snapshot["battery_status"], "ok")
        self.assertEqual(battery_snapshot["battery_source_count"], 3)
        self.assertNotIn("XXbattery_socXX", battery_snapshot)

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

        minimal = self._make_helper()
        minimal._last_snapshot_state = {"captured_at": 1.0, "heartbeat_at": 1.0}
        minimal._write_snapshot = MagicMock()
        with patch("venus_evcharger.inputs.helper.snapshot.time.time", return_value=2.0):
            self.assertTrue(minimal._heartbeat_snapshot())
        self.assertEqual(minimal._last_snapshot_state["heartbeat_at"], 2.0)
        self.assertEqual(minimal._last_snapshot_state["helper_state"], "running")
        self.assertEqual(minimal._last_snapshot_state["helper_status"], "running")
        minimal._write_snapshot.assert_called_once_with(minimal._last_snapshot_state)

        stopped = self._make_helper()
        stopped._stop_requested = True
        stopped._write_snapshot = MagicMock()
        with patch("venus_evcharger.inputs.helper.snapshot.time.time", return_value=3.0):
            self.assertFalse(stopped._heartbeat_snapshot())

    def test_stamp_snapshot_metadata_contracts(self):
        helper = self._make_helper()
        helper.helper_generation = 7
        helper.runtime_instance_id = "runtime-1"
        snapshot = {}

        with patch("venus_evcharger.inputs.helper.snapshot.os.getpid", return_value=1234):
            helper._stamp_snapshot_metadata(snapshot)

        self.assertEqual(snapshot, {"writer_pid": 1234, "helper_generation": 7, "runtime_instance_id": "runtime-1"})

        defaulted = AutoInputHelper.__new__(AutoInputHelper)
        defaulted_snapshot = {}
        with patch("venus_evcharger.inputs.helper.snapshot.os.getpid", return_value=4321):
            defaulted._stamp_snapshot_metadata(defaulted_snapshot)
        self.assertEqual(defaulted_snapshot, {"writer_pid": 4321, "helper_generation": 0, "runtime_instance_id": ""})

        falsey = AutoInputHelper.__new__(AutoInputHelper)
        falsey.helper_generation = None
        falsey.runtime_instance_id = None
        falsey_snapshot = {}
        with patch("venus_evcharger.inputs.helper.snapshot.os.getpid", return_value=9876):
            falsey._stamp_snapshot_metadata(falsey_snapshot)
        self.assertEqual(falsey_snapshot, {"writer_pid": 9876, "helper_generation": 0, "runtime_instance_id": ""})

    def test_snapshot_guard_uses_configured_lock_or_null_context(self):
        helper = self._make_helper()
        lock = _RecordingLock()
        helper._snapshot_lock = lock
        self.assertIs(helper._snapshot_guard(), lock)
        with helper._snapshot_guard():
            pass
        self.assertEqual(lock.events, ["enter", "exit"])

        no_lock = AutoInputHelper.__new__(AutoInputHelper)
        guard = no_lock._snapshot_guard()
        self.assertIsNotNone(guard)
        with guard as entered:
            self.assertIs(entered, guard)

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
        self.assertEqual(helper._refresh_source.call_args_list, [call("pv", 123.0), call("battery", 123.0), call("grid", 123.0)])

        helper._refresh_source.reset_mock()
        helper._refresh_all_sources(456.0)
        self.assertEqual(helper._refresh_source.call_args_list, [call("pv", 456.0), call("battery", 456.0), call("grid", 456.0)])

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

# SPDX-License-Identifier: GPL-3.0-or-later
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests

sys.modules.setdefault("vedbus", MagicMock())

from venus_evcharger.bootstrap.wizard_render import (
    _actuator_backend_lines,
    _adapter_type_from_file,
    _charger_backend_lines,
    _measurement_backend_lines,
)
from venus_evcharger.backend.shelly_io_worker import ShellyIoWorker
from venus_evcharger.backend.shelly_support import ShellyBackendBase
from venus_evcharger.controllers.write_snapshot import (
    _restore_dbus_paths_direct,
    _snapshot_dbus_paths,
    _snapshot_direct_dbus_paths,
)
from venus_evcharger.controllers.state_restore_support import _StateRuntimeRestoreVictronEss
from venus_evcharger.controllers import state_runtime_snapshot as runtime_snapshot_mod
from venus_evcharger.core import common as common_mod
from venus_evcharger.energy import aggregate as aggregate_mod
from venus_evcharger.energy.aggregate import _effective_soc
from venus_evcharger.energy.probe import _optional_detected_int
from venus_evcharger.energy import EnergySourceSnapshot
from venus_evcharger.inputs.helper.subscriptions import _AutoInputHelperSubscription
from venus_evcharger.inputs.supervisor_process import _AutoInputSupervisorProcess
from venus_evcharger.dbus_gateway import EVCS_FIELD_TO_PATH
from venus_evcharger.publish.dbus_core import _DbusPublishCore
from venus_evcharger.publish.dbus_config import _DbusPublishConfig
from venus_evcharger.publish.dbus_diagnostics import _DbusPublishDiagnostics
from venus_evcharger.runtime.audit_fields import _RuntimeAuditFields
from venus_evcharger.runtime.health import _RuntimeHealth
from venus_evcharger.topology.schema import (
    ActuatorConfig,
    ChargerConfig,
    EvChargerTopologyConfig,
    MeasurementConfig,
    PolicyConfig,
    TopologyConfig,
)
from venus_evcharger.update.pm_snapshot import _UpdateCyclePmSnapshot
from venus_evcharger.update.runtime_cycle import _UpdateCycleRuntime
from venus_evcharger_auto_input_helper import AutoInputHelper
from venus_evcharger_service import ShellyWallboxService


class _DbusCoreHarness(_DbusPublishCore):
    PHASE_NAMES = ("L1", "L2", "L3")

    def __init__(self, service: object) -> None:
        self.service = service


class _DiagnosticsHarness(_DbusPublishDiagnostics):
    def __init__(self, service: object) -> None:
        self.service = service


class _SupervisorHarness(_AutoInputSupervisorProcess):
    SNAPSHOT_SOURCE_KEYS = ()

    def __init__(self, service: object) -> None:
        self.service = service


class _ShellyWorkerHarness(ShellyIoWorker):
    def __init__(self, service: object) -> None:
        self.service = service


class _RuntimeHealthHarness(_RuntimeHealth):
    def __init__(self, service: object) -> None:
        self.service = service


class _PmSnapshotHarness(_UpdateCyclePmSnapshot):
    pass


class _RuntimeCycleHarness(_UpdateCycleRuntime):
    def __init__(self, service: object) -> None:
        self.service = service


class _SubscriptionHarness(_AutoInputHelperSubscription):
    def __init__(self) -> None:
        self._refresh_scheduled = False
        self._stop_requested = False
        self.auto_dbus_backoff_base_seconds = 1.0
        self._dbus_generation = 1
        self._dbus_subscription_backoff_until = 0.0
        self._system_bus = None
        self._signal_matches = {}
        self._monitored_specs = {}
        self._name_owner_match = None

    def _ensure_poll_state(self) -> None:
        return None


class RemainingCoverageHelperTests(unittest.TestCase):
    def test_runtime_snapshot_helper_accepts_bool_as_non_negative_int(self) -> None:
        self.assertEqual(runtime_snapshot_mod._victron_ess_balance_runtime_non_negative_int(True), 1)

    def test_energy_effective_soc_returns_none_for_single_online_source_without_soc(self) -> None:
        source = EnergySourceSnapshot(
            source_id="hybrid",
            role="battery",
            service_name="com.victronenergy.battery.ttyO1",
            online=True,
            soc=None,
            ac_power_w=1000.0,
            captured_at=1.0,
        )
        self.assertIsNone(_effective_soc(None, (source,)))

    def test_optional_detected_int_returns_none_for_invalid_values(self) -> None:
        self.assertIsNone(_optional_detected_int("not-an-int"))
        self.assertIsNone(_optional_detected_int(True))

    def test_dbus_and_audit_backend_fallback_helpers_cover_unknown_attributes(self) -> None:
        service = SimpleNamespace(custom_backend="  custom  ", empty_backend=None)
        self.assertEqual(_DbusPublishConfig._backend_type_value(service, "custom_backend", "fallback"), "custom")
        self.assertEqual(_DbusPublishConfig._backend_type_value(service, "empty_backend", "fallback"), "fallback")
        self.assertEqual(_RuntimeAuditFields._backend_value(service, "custom_backend", "fallback"), "custom")
        self.assertEqual(_RuntimeAuditFields._backend_value(service, "empty_backend", "fallback"), "fallback")

    def test_auto_input_helper_parent_pid_parser_accepts_bool_like_int(self) -> None:
        self.assertEqual(AutoInputHelper._parsed_parent_pid(True), 1)
        self.assertEqual(AutoInputHelper._parsed_helper_generation("7"), 7)
        self.assertEqual(AutoInputHelper._parsed_runtime_instance_id(" instance-1 "), "instance-1")
        with patch("venus_evcharger_auto_input_helper.uuid.uuid4", return_value=SimpleNamespace(hex="generated")):
            self.assertEqual(AutoInputHelper._parsed_runtime_instance_id(None), "generated")

    def test_local_datetime_falls_back_when_zoneinfo_is_unavailable(self) -> None:
        with patch.object(common_mod, "ZoneInfo", side_effect=common_mod.ZoneInfoNotFoundError):
            self.assertEqual(common_mod.local_datetime_from_timestamp(0, "Missing/Zone").year, 1970)

    def test_wizard_render_helper_errors_and_empty_config_paths(self) -> None:
        self.assertEqual(_measurement_backend_lines(None, {}), ["MeterType=none"])
        with self.assertRaisesRegex(ValueError, "unsupported legacy meter mapping"):
            _measurement_backend_lines(MeasurementConfig(type="external_meter", config_path=None), {})

        no_switch_path = EvChargerTopologyConfig(
            topology=TopologyConfig(type="simple_relay"),
            actuator=ActuatorConfig(type="template_switch", config_path=""),
            policy=PolicyConfig(mode="manual", phase="L1"),
        )
        self.assertEqual(_actuator_backend_lines(no_switch_path), ["SwitchType=template_switch"])

        no_charger = EvChargerTopologyConfig(
            topology=TopologyConfig(type="simple_relay"),
            policy=PolicyConfig(mode="manual", phase="L1"),
        )
        self.assertEqual(_charger_backend_lines(no_charger), ["ChargerType="])

        empty_charger_path = EvChargerTopologyConfig(
            topology=TopologyConfig(type="native_device"),
            charger=ChargerConfig(type="goe_charger", config_path=""),
            policy=PolicyConfig(mode="manual", phase="L1"),
        )
        self.assertEqual(_charger_backend_lines(empty_charger_path), ["ChargerType=goe_charger"])

        with self.assertRaisesRegex(ValueError, "missing adapter file"):
            _adapter_type_from_file({}, "wizard-meter.ini")
        with self.assertRaisesRegex(ValueError, "missing required \\[Adapter\\] section"):
            _adapter_type_from_file({"wizard-meter.ini": "[DEFAULT]\nType=template_meter\n"}, "wizard-meter.ini")
        with self.assertRaisesRegex(ValueError, "missing Adapter.Type"):
            _adapter_type_from_file({"wizard-meter.ini": "[Adapter]\nType=\n"}, "wizard-meter.ini")

    def test_state_restore_support_accepts_valid_activation_mode_and_service_fallback(self) -> None:
        service = SimpleNamespace(auto_battery_discharge_balance_victron_bias_activation_mode="export_only")
        self.assertEqual(
            _StateRuntimeRestoreVictronEss._victron_ess_balance_activation_mode({}, service),
            "export_only",
        )
        self.assertEqual(
            _StateRuntimeRestoreVictronEss._victron_ess_balance_activation_mode(
                {"activation_mode": "export_and_above_reserve_band"},
                service,
            ),
            "export_and_above_reserve_band",
        )
        self.assertIsNone(
            _StateRuntimeRestoreVictronEss._victron_ess_balance_activation_mode(
                {"activation_mode": "invalid"},
                service,
            )
        )

    def test_shelly_backend_reset_transport_session_covers_same_and_missing_close(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "shelly.ini")
            with open(config_path, "w", encoding="utf-8") as handle:
                handle.write("[Adapter]\nHost=192.168.1.20\n")
            session = SimpleNamespace()
            backend = ShellyBackendBase(SimpleNamespace(session=session), config_path)

            backend.reset_transport_session(session)

            self.assertIs(backend._session, session)

    def test_supervisor_snapshot_and_process_guard_paths(self) -> None:
        service = SimpleNamespace(_update_worker_snapshot=MagicMock())
        harness = _SupervisorHarness(service)
        service._auto_input_snapshot_seen_for_current_helper = False

        harness._apply_snapshot(None, None, 10.0, {"captured_at": 9.0}, False)

        self.assertIs(service._auto_input_snapshot_last_seen, None)
        self.assertFalse(service._auto_input_snapshot_seen_for_current_helper)

        service._auto_input_snapshot_seen_for_current_helper = True
        service._auto_input_snapshot_last_seen = 8.0
        harness._apply_snapshot(None, None, 11.0, {"captured_at": 10.0}, False)
        self.assertEqual(service._auto_input_snapshot_last_seen, 8.0)
        self.assertFalse(service._auto_input_snapshot_seen_for_current_helper)

        self.assertIsNone(harness._coerce_snapshot_int("bad"))

        process_service = SimpleNamespace(
            auto_input_snapshot_path="/data/not-volatile/snapshot.json",
            _auto_input_snapshot_mtime_ns=123,
        )
        process_harness = _SupervisorHarness(process_service)
        process_harness._remove_stale_snapshot_file()
        self.assertEqual(process_service._auto_input_snapshot_mtime_ns, 123)

        process_service.auto_input_snapshot_path="/run/venus-test/snapshot.json"
        process_service._auto_input_snapshot_mtime_ns = 789
        with patch("venus_evcharger.inputs.supervisor_process.os.unlink", return_value=None):
            process_harness._remove_stale_snapshot_file()
        self.assertIsNone(process_service._auto_input_snapshot_mtime_ns)

        process_service.auto_input_snapshot_path="/run/venus-test/snapshot.json"
        process_service._auto_input_snapshot_mtime_ns = 789
        with patch("venus_evcharger.inputs.supervisor_process.os.unlink", side_effect=FileNotFoundError):
            process_harness._remove_stale_snapshot_file()
        self.assertIsNone(process_service._auto_input_snapshot_mtime_ns)

        process_service._auto_input_snapshot_mtime_ns = 456
        with patch("venus_evcharger.inputs.supervisor_process.os.unlink", side_effect=RuntimeError("blocked")):
            process_harness._remove_stale_snapshot_file()
        self.assertEqual(process_service._auto_input_snapshot_mtime_ns, 456)

        empty_id_service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            _time_now=MagicMock(return_value=1.0),
            _auto_input_runtime_instance_id="",
            _auto_input_helper_generation=0,
            _auto_input_helper_path=MagicMock(return_value="/helper.py"),
            _config_path=MagicMock(return_value="/config.ini"),
            auto_input_snapshot_path="/run/venus-test/snapshot.json",
        )
        empty_id_harness = _SupervisorHarness(empty_id_service)
        with (
            patch("venus_evcharger.inputs.supervisor_process.uuid.uuid4", return_value=SimpleNamespace(hex="instance-x")),
            patch.object(empty_id_harness, "_remove_stale_snapshot_file"),
            patch.object(empty_id_harness, "_terminate_orphaned_helpers"),
            patch("venus_evcharger.inputs.supervisor_process.subprocess.Popen", return_value=SimpleNamespace(pid=12)),
        ):
            empty_id_harness.spawn_helper(now=1.0)
        self.assertEqual(empty_id_service._auto_input_runtime_instance_id, "instance-x")

    def test_supervisor_orphan_helper_process_paths(self) -> None:
        harness = _SupervisorHarness(SimpleNamespace(auto_input_snapshot_path="/run/snapshot.json"))
        self.assertEqual(_SupervisorHarness(SimpleNamespace(auto_input_snapshot_path=""))._orphaned_helper_pids(), [])
        with patch("venus_evcharger.inputs.supervisor_process.os.listdir", side_effect=OSError):
            self.assertEqual(harness._orphaned_helper_pids(), [])
        with (
            patch("venus_evcharger.inputs.supervisor_process.os.getpid", return_value=10),
            patch("venus_evcharger.inputs.supervisor_process.os.listdir", return_value=["abc", "10", "11"]),
            patch.object(harness, "_helper_cmdline_matches", return_value=True),
        ):
            self.assertEqual(harness._orphaned_helper_pids(), [11])
        with patch("venus_evcharger.inputs.supervisor_process.open", side_effect=OSError):
            self.assertFalse(harness._helper_cmdline_matches(11, "/run/snapshot.json"))
        with patch.object(harness, "_orphaned_helper_pids", return_value=[1, 2, 3]):
            with patch("venus_evcharger.inputs.supervisor_process.os.kill", side_effect=[None, ProcessLookupError, RuntimeError("no")]) as kill:
                harness._terminate_orphaned_helpers()
        self.assertEqual(kill.call_count, 3)

    def test_subscription_and_snapshot_validation_guard_paths(self) -> None:
        subscription = _SubscriptionHarness()
        subscription.auto_pv_service = ""
        subscription.auto_pv_path = "/Ac/Power"
        subscription.auto_use_dc_pv = False
        subscription.auto_dc_pv_service = ""
        subscription.auto_dc_pv_path = ""
        subscription._resolve_auto_pv_services = MagicMock(return_value=["pv1"])
        self.assertEqual(subscription._desired_pv_subscription_specs(), [("pv", "pv1", "/Ac/Power")])
        self.assertIsNone(subscription._dc_pv_subscription_spec())

        snapshot_service = SimpleNamespace(
            auto_input_snapshot_path="/tmp/auto.json",
            auto_input_helper_restart_seconds=1.0,
            _warning_throttled=MagicMock(),
        )
        snapshot_harness = _SupervisorHarness(snapshot_service)
        self.assertIsNone(
            snapshot_harness._validate_snapshot_identity(
                "/tmp/auto.json",
                {"writer_pid": 1, "helper_generation": 1, "runtime_instance_id": 7},
                {},
            )
        )

    def test_auto_input_helper_liveness_loop_guard_paths(self) -> None:
        helper = AutoInputHelper.__new__(AutoInputHelper)
        helper.poll_interval_seconds = 1.0
        helper._heartbeat_thread_stop = MagicMock()
        helper._heartbeat_thread_stop.wait.side_effect = [False]
        helper._stop_requested = True
        helper._heartbeat_loop()

        helper._heartbeat_thread_stop.wait.side_effect = [False, True]
        helper._stop_requested = False
        helper._heartbeat_snapshot = MagicMock(side_effect=RuntimeError("write failed"))
        helper._heartbeat_loop()
        helper._heartbeat_snapshot.assert_called_once()

        helper._heartbeat_thread_stop.wait.side_effect = [False]
        helper._stop_requested = True
        helper._parent_watchdog_loop()

        helper._heartbeat_thread_stop.wait.side_effect = [False, True]
        helper._stop_requested = False
        helper._parent_alive = MagicMock(return_value=True)
        helper._parent_watchdog_loop()

        helper._heartbeat_thread_stop.wait.side_effect = [False]
        helper._parent_alive = MagicMock(return_value=False)
        helper._main_loop = SimpleNamespace(quit=MagicMock())
        with patch("venus_evcharger_auto_input_helper.GLib.idle_add") as idle_add:
            helper._parent_watchdog_loop()
        idle_add.assert_called_once_with(helper._main_loop.quit)

        helper._heartbeat_thread_stop.wait.side_effect = [False]
        helper._stop_requested = False
        helper._parent_alive = MagicMock(return_value=False)
        helper._main_loop = None
        helper._parent_watchdog_loop()

        helper._refresh_scheduled = False
        helper._stop_requested = True
        with patch("venus_evcharger_auto_input_helper.GLib.idle_add", side_effect=lambda callback: callback()):
            helper._schedule_initial_dbus_refresh()

    def test_auto_input_helper_main_accepts_runtime_instance_arg(self) -> None:
        with patch("venus_evcharger_auto_input_helper.AutoInputHelper") as helper_class:
            helper = helper_class.return_value
            from venus_evcharger_auto_input_helper import main

            self.assertEqual(main(["/config.ini", "/run/snapshot.json", "1", "2", "instance-1"]), 0)

        helper_class.assert_called_once_with("/config.ini", "/run/snapshot.json", "1", "2", "instance-1")
        helper.run.assert_called_once()

    def test_pm_runtime_and_aggregate_simple_guard_paths(self) -> None:
        service = SimpleNamespace()
        _PmSnapshotHarness._remember_pm_snapshot(service, {"output": True}, 5.0, False)
        self.assertFalse(service._last_pm_status_confirmed)
        self.assertFalse(hasattr(service, "_last_confirmed_pm_status"))

        runtime_service = SimpleNamespace(
            extract_pm_measurements=MagicMock(return_value=(False, 0.0, 0.0, 0.0, 0.0)),
            apply_startup_manual_target=lambda pm_status, _now: pm_status,
            _pm_status_confirmed=lambda _pm_status: False,
            _mode_uses_auto_logic=lambda _mode: False,
            virtual_mode=0,
        )
        runtime_harness = _RuntimeCycleHarness(runtime_service)
        runtime_harness.extract_pm_measurements = runtime_service.extract_pm_measurements
        runtime_harness.apply_startup_manual_target = runtime_service.apply_startup_manual_target
        runtime_harness._pm_status_confirmed = runtime_service._pm_status_confirmed

        result = runtime_harness._prepared_online_update_state({"voltage": 0.0}, 10.0)

        self.assertEqual(result[3], 0.0)
        self.assertFalse(hasattr(runtime_service, "_last_voltage"))

    def test_diagnostics_and_service_publish_enqueue_guard_paths(self) -> None:
        diagnostics = _DiagnosticsHarness(SimpleNamespace(_shelly_retry_after="bad"))
        self.assertEqual(diagnostics._shelly_retry_remaining_value(diagnostics.service, 10.0), 0)

        auto_service = SimpleNamespace(
            _control_command_from_write=MagicMock(return_value="command"),
            _enqueue_control_command=MagicMock(return_value=True),
            _control_command_async_enabled=True,
        )
        from venus_evcharger.service.auto import DbusAutoLogic

        self.assertTrue(DbusAutoLogic._handle_write(auto_service, "/StartStop", 1))
        auto_service._enqueue_control_command.assert_called_once_with("command")

        from venus_evcharger.service.state_publish import StatePublish

        state_service = SimpleNamespace(
            _ensure_companion_dbus_bridge=MagicMock(),
            _dbus_publish_direct_allowed=MagicMock(return_value=False),
            _enqueue_companion_dbus_publish=MagicMock(return_value=True),
            _companion_dbus_bridge=SimpleNamespace(publish=MagicMock(return_value=False)),
        )
        self.assertTrue(StatePublish._publish_companion_dbus_bridge(state_service, 20.0))
        state_service._enqueue_companion_dbus_publish.assert_called_once_with(20.0)
        state_service._companion_dbus_bridge.publish.assert_not_called()

    def test_write_snapshot_and_dbus_core_queue_guard_paths(self) -> None:
        service = SimpleNamespace(
            _dbus_publish_state={"/A": {"value": 1}},
            _dbusservice=None,
            _dbus_publish_direct_allowed=MagicMock(return_value=False),
        )
        self.assertEqual(_snapshot_dbus_paths(service, ("/A", "/B")), {"/A": 1})

        self.assertEqual(_snapshot_direct_dbus_paths(SimpleNamespace(_dbusservice=None), ("/A",), {}), {})
        _restore_dbus_paths_direct(SimpleNamespace(_dbusservice=None), {"/A": 1})

        enqueue_service = SimpleNamespace(
            _dbus_publish_state={},
            _dbusservice={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
            _dbus_publish_direct_allowed=MagicMock(return_value=False),
            _enqueue_dbus_publish_values=MagicMock(return_value=True),
        )
        harness = _DbusCoreHarness(enqueue_service)
        self.assertFalse(_DbusCoreHarness(SimpleNamespace())._enqueue_publish_values([("/A", 1)], 10.0))
        self.assertFalse(harness._enqueue_publish_values([("/A", 1)], 10.0) is False)
        self.assertTrue(harness._enqueue_publish_fields([("ac_power_w", 55.0)], 10.0))
        enqueue_service._enqueue_dbus_publish_values.assert_any_call([("/Ac/Power", 55.0)], 10.0)
        self.assertEqual(harness._field_items_to_path_items([("ac_power_w", 55.0), ("unknown", 1)]), [("/Ac/Power", 55.0)])
        self.assertTrue(harness.publish_path("/A", 1, now=10.0, force=True))
        enqueue_service._dbus_publish_state["/A"] = {"value": 1, "updated_at": 10.0}
        self.assertEqual(harness._staged_values_for_enqueue({"/A": 1}, 10.0, None, False), [])
        enqueue_service._dbus_publish_state["/Ac/Power"] = {"value": 55.0, "updated_at": 10.0}
        self.assertEqual(
            harness._staged_fields_for_enqueue(
                {"unknown": 1, "ac_power_w": 55.0},
                {"/Ac/Power": 55.0},
                10.0,
                None,
                False,
            ),
            [],
        )
        self.assertFalse(harness._enqueue_transactional_publish({}, 10.0, None, False))

        enqueue_service._enqueue_dbus_update_index_bump = None
        enqueue_service._dbusservice["/UpdateIndex"] = 0
        harness.bump_update_index(10.0)
        self.assertEqual(enqueue_service._dbusservice["/UpdateIndex"], 1)

    def test_dbus_core_live_and_energy_publish_contract_fields(self) -> None:
        service = SimpleNamespace(
            _dbus_publish_state={},
            _dbusservice={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
        )
        harness = _DbusCoreHarness(service)
        phase_data = {
            "L1": {"power": 111.0, "current": 1.1, "voltage": 229.1},
            "L2": {"power": 222.0, "current": 2.2, "voltage": 229.2},
            "L3": {"power": 333.0, "current": 3.3, "voltage": 229.3},
        }

        self.assertTrue(harness.publish_live_measurements(666.0, 230.0, 6.6, phase_data, now=10.0))

        expected_live_fields = {
            "ac_power_w": 666.0,
            "ac_voltage_v": 230.0,
            "ac_current_a": 6.6,
            "charge_current_a": 6.6,
            "l1_power_w": 111.0,
            "l1_current_a": 1.1,
            "l1_voltage_v": 229.1,
            "l2_power_w": 222.0,
            "l2_current_a": 2.2,
            "l2_voltage_v": 229.2,
            "l3_power_w": 333.0,
            "l3_current_a": 3.3,
            "l3_voltage_v": 229.3,
        }
        for field, value in expected_live_fields.items():
            path = EVCS_FIELD_TO_PATH[field]
            self.assertEqual(service._dbusservice[path], value)
            self.assertEqual(service._dbus_publish_state[path], {"value": value, "updated_at": 10.0})

        self.assertFalse(harness.publish_live_measurements(666.0, 230.0, 6.6, phase_data, now=10.5))
        self.assertTrue(
            harness._publish_values_transactional(
                "forced-live",
                {EVCS_FIELD_TO_PATH["ac_power_w"]: 666.0},
                now=10.6,
                interval_seconds=1.0,
                force=True,
            )
        )
        self.assertEqual(service._dbus_publish_state[EVCS_FIELD_TO_PATH["ac_power_w"]]["updated_at"], 10.6)

        self.assertTrue(
            harness.publish_energy_time_measurements(
                12.5,
                {"L1": 1.1, "L2": 2.2, "L3": 3.3},
                45,
                4.5,
                now=20.0,
            )
        )
        expected_energy_fields = {
            "energy_forward_kwh": 12.5,
            "l1_energy_forward_kwh": 1.1,
            "l2_energy_forward_kwh": 2.2,
            "l3_energy_forward_kwh": 3.3,
            "charging_time_s": 45,
            "session_energy_kwh": 4.5,
            "session_time_s": 45,
        }
        for field, value in expected_energy_fields.items():
            self.assertEqual(service._dbusservice[EVCS_FIELD_TO_PATH[field]], value)

    def test_shelly_worker_and_runtime_health_guard_paths(self) -> None:
        service = SimpleNamespace(
            _charger_retry_active=MagicMock(return_value=True),
            _shelly_retry_active=MagicMock(return_value=True),
            _source_retry_remaining=MagicMock(return_value=12.0),
            _shelly_consecutive_errors=3,
        )
        worker = _ShellyWorkerHarness(service)
        worker._charger_retry_active = service._charger_retry_active
        worker._shelly_retry_active = service._shelly_retry_active
        self.assertTrue(worker._source_retry_blocks_pending_relay("charger", 10.0))
        self.assertEqual(worker._remember_pending_relay_command_error("other", 10.0, RuntimeError("x")), "error")
        self.assertEqual(worker._pending_relay_shelly_retry_remaining(SimpleNamespace(), "shelly", 10.0), 0.0)
        self.assertIsNone(worker._pending_relay_error_exc_info("shelly", requests.exceptions.ConnectTimeout()))

        health_service = SimpleNamespace(
            auto_watchdog_restart_attempts="bad",
            topology_configured=True,
            _recovery_attempts=5,
            _state_summary=MagicMock(return_value={}),
        )
        health = _RuntimeHealthHarness(health_service)
        health._age_seconds = lambda *_args: 1.0
        self.assertEqual(health._watchdog_restart_attempts(health_service), 0)

        with patch("venus_evcharger.runtime.health.os._exit", side_effect=SystemExit) as exit_mock:
            with self.assertRaises(SystemExit):
                health._exit_for_watchdog_restart()
        exit_mock.assert_called_once_with(75)

        with patch("venus_evcharger.runtime.health.faulthandler.dump_traceback", side_effect=RuntimeError("blocked")):
            with patch.object(health, "_exit_for_watchdog_restart", side_effect=SystemExit):
                with self.assertRaises(SystemExit):
                    health._restart_process_after_stale_watchdog(health_service, 20.0)

        self.assertFalse(worker._source_retry_blocks_pending_relay("unknown", 10.0))
        self.assertEqual(worker._pending_relay_shelly_retry_remaining(service, "shelly", 10.0), 12.0)

    def test_auto_input_subscription_guard_paths(self) -> None:
        harness = _SubscriptionHarness()
        harness._subscription_refresh_backoff_active = MagicMock(return_value=True)
        harness._schedule_refresh_subscriptions = MagicMock()
        self.assertFalse(harness._refresh_subscriptions())
        harness._schedule_refresh_subscriptions.assert_called_once_with()

        failing_refresh = _SubscriptionHarness()
        failing_refresh._subscription_refresh_backoff_active = MagicMock(return_value=False)
        failing_refresh._register_name_owner_subscription = MagicMock()
        failing_refresh._desired_subscription_specs = MagicMock(return_value=[("pv", "svc", "/P")])
        failing_refresh._subscribe_busitem_path = MagicMock(side_effect=RuntimeError("dbus down"))
        failing_refresh._handle_dbus_callback_error = MagicMock()
        self.assertFalse(failing_refresh._refresh_subscriptions())
        failing_refresh._handle_dbus_callback_error.assert_called_once()

        scheduled = _SubscriptionHarness()
        scheduled._stop_requested = True
        with patch("venus_evcharger.inputs.helper.subscriptions.GLib.idle_add", side_effect=lambda callback: callback()):
            scheduled._schedule_refresh_subscriptions()

        owner = _SubscriptionHarness()
        owner._is_relevant_name_owner_change = MagicMock(side_effect=RuntimeError("bad owner"))
        owner._handle_dbus_callback_error = MagicMock()
        owner._on_name_owner_changed(1, "svc", "", "owner")
        owner._handle_dbus_callback_error.assert_called_once()

        self.assertEqual(owner._parse_name_owner_changed_args(()), (None, ""))

        close_raises = SimpleNamespace(close=MagicMock(side_effect=RuntimeError("close failed")))
        owner._close_system_bus(close_raises)

if __name__ == "__main__":
    unittest.main()

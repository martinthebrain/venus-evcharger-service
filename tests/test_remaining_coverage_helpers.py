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
from venus_evcharger.backend.shelly_io_worker import ShellyIoWorkerMixin
from venus_evcharger.backend.shelly_support import ShellyBackendBase
from venus_evcharger.controllers.write_snapshot import (
    _restore_dbus_paths_direct,
    _snapshot_dbus_paths,
    _snapshot_direct_dbus_paths,
)
from venus_evcharger.controllers.state_restore_support import _StateRuntimeRestoreVictronEssMixin
from venus_evcharger.controllers import state_runtime_snapshot as runtime_snapshot_mod
from venus_evcharger.energy import aggregate as aggregate_mod
from venus_evcharger.energy.aggregate import _effective_soc
from venus_evcharger.energy.probe import _optional_detected_int
from venus_evcharger.energy import EnergySourceSnapshot
from venus_evcharger.inputs.helper.subscriptions import _AutoInputHelperSubscriptionMixin
from venus_evcharger.inputs.supervisor_process import _AutoInputSupervisorProcessMixin
from venus_evcharger.inputs.supervisor_snapshot_runtime import _AutoInputSupervisorSnapshotRuntimeMixin
from venus_evcharger.inputs.supervisor_snapshot_validation import _AutoInputSupervisorSnapshotValidationMixin
from venus_evcharger.publish.dbus_core import _DbusPublishCoreMixin
from venus_evcharger.publish.dbus_config import _DbusPublishConfigMixin
from venus_evcharger.publish.dbus_diagnostics import _DbusPublishDiagnosticsMixin
from venus_evcharger.runtime.audit_fields import _RuntimeSupportAuditFieldsMixin
from venus_evcharger.runtime.health import _RuntimeSupportHealthMixin
from venus_evcharger.topology.schema import (
    ActuatorConfig,
    ChargerConfig,
    EvChargerTopologyConfig,
    MeasurementConfig,
    PolicyConfig,
    TopologyConfig,
)
from venus_evcharger.update.pm_snapshot import _UpdateCyclePmSnapshotMixin
from venus_evcharger.update.runtime_cycle import _UpdateCycleRuntimeMixin
from venus_evcharger_auto_input_helper import AutoInputHelper


class _DbusCoreHarness(_DbusPublishCoreMixin):
    def __init__(self, service: object) -> None:
        self.service = service


class _DiagnosticsHarness(_DbusPublishDiagnosticsMixin):
    def __init__(self, service: object) -> None:
        self.service = service


class _SupervisorHarness(
    _AutoInputSupervisorSnapshotRuntimeMixin,
    _AutoInputSupervisorSnapshotValidationMixin,
    _AutoInputSupervisorProcessMixin,
):
    SNAPSHOT_SOURCE_KEYS = ()

    def __init__(self, service: object) -> None:
        self.service = service


class _ShellyWorkerHarness(ShellyIoWorkerMixin):
    def __init__(self, service: object) -> None:
        self.service = service


class _RuntimeHealthHarness(_RuntimeSupportHealthMixin):
    def __init__(self, service: object) -> None:
        self.service = service


class _PmSnapshotHarness(_UpdateCyclePmSnapshotMixin):
    pass


class _RuntimeCycleHarness(_UpdateCycleRuntimeMixin):
    def __init__(self, service: object) -> None:
        self.service = service


class _SubscriptionHarness(_AutoInputHelperSubscriptionMixin):
    def __init__(self) -> None:
        self._refresh_scheduled = False
        self._stop_requested = False
        self.auto_dbus_backoff_base_seconds = 1.0
        self._dbus_generation = 1
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
        self.assertEqual(_DbusPublishConfigMixin._backend_type_value(service, "custom_backend", "fallback"), "custom")
        self.assertEqual(_DbusPublishConfigMixin._backend_type_value(service, "empty_backend", "fallback"), "fallback")
        self.assertEqual(_RuntimeSupportAuditFieldsMixin._backend_value(service, "custom_backend", "fallback"), "custom")
        self.assertEqual(_RuntimeSupportAuditFieldsMixin._backend_value(service, "empty_backend", "fallback"), "fallback")

    def test_auto_input_helper_parent_pid_parser_accepts_bool_like_int(self) -> None:
        self.assertEqual(AutoInputHelper._parsed_parent_pid(True), 1)
        self.assertEqual(AutoInputHelper._parsed_helper_generation("7"), 7)

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
            _StateRuntimeRestoreVictronEssMixin._victron_ess_balance_activation_mode({}, service),
            "export_only",
        )
        self.assertEqual(
            _StateRuntimeRestoreVictronEssMixin._victron_ess_balance_activation_mode(
                {"activation_mode": "export_and_above_reserve_band"},
                service,
            ),
            "export_and_above_reserve_band",
        )
        self.assertIsNone(
            _StateRuntimeRestoreVictronEssMixin._victron_ess_balance_activation_mode(
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
        self.assertEqual(aggregate_mod._optional_float(3.5), 3.5)

    def test_diagnostics_and_service_publish_enqueue_guard_paths(self) -> None:
        diagnostics = _DiagnosticsHarness(SimpleNamespace(_shelly_retry_after="bad"))
        self.assertEqual(diagnostics._shelly_retry_remaining_value(diagnostics.service, 10.0), 0)

        auto_service = SimpleNamespace(
            _control_command_from_write=MagicMock(return_value="command"),
            _enqueue_control_command=MagicMock(return_value=True),
            _control_command_async_enabled=True,
        )
        from venus_evcharger.service.auto import DbusAutoLogicMixin

        self.assertTrue(DbusAutoLogicMixin._handle_write(auto_service, "/StartStop", 1))
        auto_service._enqueue_control_command.assert_called_once_with("command")

        from venus_evcharger.service.state_publish import StatePublishMixin

        state_service = SimpleNamespace(
            _ensure_companion_dbus_bridge=MagicMock(),
            _dbus_publish_direct_allowed=MagicMock(return_value=False),
            _enqueue_companion_dbus_publish=MagicMock(return_value=True),
            _companion_dbus_bridge=SimpleNamespace(publish=MagicMock(return_value=False)),
        )
        self.assertTrue(StatePublishMixin._publish_companion_dbus_bridge(state_service, 20.0))
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
        self.assertTrue(harness.publish_path("/A", 1, now=10.0, force=True))
        enqueue_service._dbus_publish_state["/A"] = {"value": 1, "updated_at": 10.0}
        self.assertEqual(harness._staged_values_for_enqueue({"/A": 1}, 10.0, None, False), [])
        self.assertFalse(harness._enqueue_transactional_publish({}, 10.0, None, False))

        enqueue_service._enqueue_dbus_update_index_bump = None
        enqueue_service._dbusservice["/UpdateIndex"] = 0
        harness.bump_update_index(10.0)
        self.assertEqual(enqueue_service._dbusservice["/UpdateIndex"], 1)

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

# SPDX-License-Identifier: GPL-3.0-or-later
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests

sys.modules.setdefault("vedbus", MagicMock())

from tests.support.auto_input_supervisor import (
    AutoInputSupervisorServiceFake,
    HelperProcessFake,
    valid_snapshot,
)
from tests.support.publish_runtime import PublishServiceHarness
from tests.venus_evcharger_shelly_io_controller_support import ShellyIoController
from venus_evcharger.backend.shelly_support import ShellyBackendBase
from venus_evcharger.bootstrap.wizard_render import (
    _actuator_backend_lines,
    _adapter_type_from_file,
    _charger_backend_lines,
    _measurement_backend_lines,
)
from venus_evcharger.controllers import state_runtime_snapshot_victron as snapshot_victron
from venus_evcharger.controllers.state_restore_victron_ess import VictronEssRuntimeRestorer
from venus_evcharger.controllers.write_snapshot import (
    _restore_dbus_paths_direct,
    _snapshot_dbus_paths,
    _snapshot_direct_dbus_paths,
)
from venus_evcharger.core import common as common_mod
from venus_evcharger.dbus_gateway import EVCS_FIELD_TO_PATH
from venus_evcharger.energy import EnergySourceSnapshot
from venus_evcharger.energy.aggregate import _effective_soc
from venus_evcharger.energy.probe import _optional_detected_int
from venus_evcharger.inputs.supervisor import AutoInputSupervisor
from venus_evcharger.inputs.supervisor_snapshot_values import snapshot_int
from venus_evcharger.publish.dbus_core import DbusPublishCore
from venus_evcharger.publish.dbus_diagnostics_sources import DbusDiagnosticsSources
from venus_evcharger.publish.dbus_measurements import DbusMeasurementPublisher
from venus_evcharger.publish.dbus_runtime_view import DbusRuntimeView
from venus_evcharger.publish.dbus_shared import DbusPublishContext
from venus_evcharger.runtime.audit_fields import RuntimeAuditFields
from venus_evcharger.topology.schema import (
    ActuatorConfig,
    ChargerConfig,
    EvChargerTopologyConfig,
    MeasurementConfig,
    PolicyConfig,
    TopologyConfig,
)
from venus_evcharger.update.pm_snapshot import PmSnapshotResolver
from venus_evcharger.update.runtime_cycle import RuntimeCycleCoordinator


def _auto_input_supervisor(service: AutoInputSupervisorServiceFake) -> AutoInputSupervisor:
    return AutoInputSupervisor(
        service,
        config_path="/config.ini",
        helper_path="/helper.py",
    )


def _publish_context(service: PublishServiceHarness) -> DbusPublishContext:
    return DbusPublishContext(service=service, age_seconds=lambda *_args: 0)


def _publish_core(service: PublishServiceHarness) -> DbusPublishCore:
    return DbusPublishCore(_publish_context(service))


class RemainingCoverageHelperTests(unittest.TestCase):
    def test_runtime_snapshot_helper_accepts_bool_as_non_negative_int(self) -> None:
        self.assertEqual(snapshot_victron._victron_ess_balance_runtime_non_negative_int(True), 1)

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
        self.assertEqual(DbusRuntimeView.backend_type_value(service, "custom_backend", "fallback"), "custom")
        self.assertEqual(DbusRuntimeView.backend_type_value(service, "empty_backend", "fallback"), "fallback")
        self.assertEqual(RuntimeAuditFields.backend_value(service, "custom_backend", "fallback"), "custom")
        self.assertEqual(RuntimeAuditFields.backend_value(service, "empty_backend", "fallback"), "fallback")

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

    def test_victron_ess_restorer_accepts_valid_activation_mode_and_service_fallback(self) -> None:
        service = SimpleNamespace(auto_battery_discharge_balance_victron_bias_activation_mode="export_only")
        self.assertEqual(
            VictronEssRuntimeRestorer._victron_ess_balance_activation_mode({}, service),
            "export_only",
        )
        self.assertEqual(
            VictronEssRuntimeRestorer._victron_ess_balance_activation_mode(
                {"activation_mode": "export_and_above_reserve_band"},
                service,
            ),
            "export_and_above_reserve_band",
        )
        self.assertIsNone(
            VictronEssRuntimeRestorer._victron_ess_balance_activation_mode(
                {"activation_mode": "invalid"},
                service,
            )
        )

    def test_shelly_backend_reset_transport_session_covers_same_and_missing_close(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "shelly.ini")
            with open(config_path, "w", encoding="utf-8") as handle:
                handle.write("[Adapter]\nHost=192.168.1.20\n")
            original_session = SimpleNamespace(get=MagicMock())
            replacement_session = SimpleNamespace(get=MagicMock())
            backend = ShellyBackendBase(SimpleNamespace(session=original_session), config_path)

            backend.reset_transport_session(replacement_session)
            backend.reset_transport_session(replacement_session)

            self.assertIs(backend._session, replacement_session)

    def test_supervisor_snapshot_and_process_guard_paths(self) -> None:
        service = AutoInputSupervisorServiceFake()
        harness = _auto_input_supervisor(service)
        service._auto_input_snapshot_seen_for_current_helper = False

        harness.snapshot_runtime._apply_snapshot(None, None, 10.0, {"captured_at": 9.0}, False)

        self.assertIs(service._auto_input_snapshot_last_seen, None)
        self.assertFalse(service._auto_input_snapshot_seen_for_current_helper)

        service._auto_input_snapshot_seen_for_current_helper = True
        service._auto_input_snapshot_last_seen = 8.0
        harness.snapshot_runtime._apply_snapshot(None, None, 11.0, {"captured_at": 10.0}, False)
        self.assertEqual(service._auto_input_snapshot_last_seen, 8.0)
        self.assertFalse(service._auto_input_snapshot_seen_for_current_helper)

        self.assertIsNone(snapshot_int("bad"))

        process_service = AutoInputSupervisorServiceFake(
            auto_input_snapshot_path="/data/not-volatile/snapshot.json",
            _auto_input_snapshot_mtime_ns=123,
        )
        process_harness = _auto_input_supervisor(process_service).process_lifecycle
        process_harness._remove_stale_snapshot_file()
        self.assertEqual(process_service._auto_input_snapshot_mtime_ns, 123)

        process_service.auto_input_snapshot_path = "/run/venus-test/snapshot.json"
        process_service._auto_input_snapshot_mtime_ns = 789
        with patch("venus_evcharger.inputs.supervisor_process.os.unlink", return_value=None):
            process_harness._remove_stale_snapshot_file()
        self.assertIsNone(process_service._auto_input_snapshot_mtime_ns)

        process_service.auto_input_snapshot_path = "/run/venus-test/snapshot.json"
        process_service._auto_input_snapshot_mtime_ns = 789
        with patch("venus_evcharger.inputs.supervisor_process.os.unlink", side_effect=FileNotFoundError):
            process_harness._remove_stale_snapshot_file()
        self.assertIsNone(process_service._auto_input_snapshot_mtime_ns)

        process_service._auto_input_snapshot_mtime_ns = 456
        with patch("venus_evcharger.inputs.supervisor_process.os.unlink", side_effect=RuntimeError("blocked")):
            process_harness._remove_stale_snapshot_file()
        self.assertEqual(process_service._auto_input_snapshot_mtime_ns, 456)

        empty_id_service = AutoInputSupervisorServiceFake(
            now=1.0,
            _auto_input_runtime_instance_id="",
            _auto_input_helper_generation=0,
            auto_input_snapshot_path="/run/venus-test/snapshot.json",
        )
        empty_id_harness = _auto_input_supervisor(empty_id_service).process_lifecycle
        with (
            patch("venus_evcharger.inputs.supervisor_process.uuid.uuid4", return_value=SimpleNamespace(hex="instance-x")),
            patch.object(empty_id_harness, "_remove_stale_snapshot_file"),
            patch.object(empty_id_harness, "_terminate_orphaned_helpers"),
            patch(
                "venus_evcharger.inputs.supervisor_process.subprocess.Popen",
                return_value=HelperProcessFake(pid=12),
            ) as popen,
        ):
            empty_id_harness.spawn_helper(now=1.0)
        self.assertEqual(empty_id_service._auto_input_runtime_instance_id, "instance-x")
        command = popen.call_args.args[0]
        self.assertEqual(command[2:4], ["/helper.py", "/config.ini"])

    def test_supervisor_orphan_helper_process_paths(self) -> None:
        harness = _auto_input_supervisor(
            AutoInputSupervisorServiceFake(auto_input_snapshot_path="/run/snapshot.json")
        ).process_lifecycle
        empty_path_lifecycle = _auto_input_supervisor(
            AutoInputSupervisorServiceFake(auto_input_snapshot_path="")
        ).process_lifecycle
        self.assertEqual(empty_path_lifecycle._orphaned_helper_pids(), [])
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

    def test_snapshot_validation_guard_paths(self) -> None:
        snapshot_service = AutoInputSupervisorServiceFake(
            auto_input_snapshot_path="/tmp/auto.json",
            auto_input_helper_restart_seconds=1.0,
        )
        snapshot_validator = _auto_input_supervisor(snapshot_service).validator
        self.assertIsNone(
            snapshot_validator.validate(
                "/tmp/auto.json",
                valid_snapshot(runtime_instance_id=7),
            )
        )
        self.assertEqual(snapshot_service.runtime.warnings[-1][0], "auto-input-helper-schema-invalid")

    def test_pm_runtime_and_aggregate_simple_guard_paths(self) -> None:
        service = SimpleNamespace()
        PmSnapshotResolver._remember_pm_snapshot(service, {"output": True}, 5.0, False)
        self.assertFalse(service._last_pm_status_confirmed)
        self.assertFalse(hasattr(service, "_last_confirmed_pm_status"))

        runtime_service = SimpleNamespace(
            auto=SimpleNamespace(mode_uses_auto_logic=lambda _mode: False),
            virtual_mode=0,
        )
        inputs = MagicMock()
        inputs.extract_pm_measurements.return_value = (False, 0.0, 0.0, 0.0, 0.0)
        state = MagicMock()
        state.apply_startup_manual_target.side_effect = lambda pm_status, _now: pm_status
        relay = MagicMock()
        relay.foundation.telemetry.pm_status_confirmed.return_value = False
        runtime_harness = RuntimeCycleCoordinator(
            runtime_service,
            state,
            MagicMock(),
            inputs,
            MagicMock(),
            relay,
            MagicMock(),
            MagicMock(),
        )

        result = runtime_harness._prepared_online_update_state({"voltage": 0.0}, 10.0)

        self.assertEqual(result[3], 0.0)
        self.assertFalse(hasattr(runtime_service, "_last_voltage"))

    def test_diagnostics_retry_guard_path(self) -> None:
        service = SimpleNamespace(_shelly_retry_after="bad")
        self.assertEqual(DbusDiagnosticsSources._shelly_retry_remaining_value(service, 10.0), 0)

    def test_write_snapshot_and_dbus_core_queue_guard_paths(self) -> None:
        service = SimpleNamespace(
            _dbus_publish_state={"/A": {"value": 1}},
            _dbusservice=None,
            _dbus_publish_direct_allowed=MagicMock(return_value=False),
        )
        self.assertEqual(_snapshot_dbus_paths(service, ("/A", "/B")), {"/A": 1})

        self.assertEqual(_snapshot_direct_dbus_paths(SimpleNamespace(_dbusservice=None), ("/A",), {}), {})
        _restore_dbus_paths_direct(SimpleNamespace(_dbusservice=None), {"/A": 1})

        reject_enqueue = MagicMock(return_value=False)
        reject_service = PublishServiceHarness(
            _enqueue_dbus_publish_values=reject_enqueue,
        )
        self.assertFalse(_publish_core(reject_service)._enqueue_publish_values([("/A", 1)], 10.0))
        reject_enqueue.assert_called_once_with([("/A", 1)], 10.0)

        enqueue_values = MagicMock(return_value=True)
        enqueue_fields = MagicMock(return_value=True)
        enqueue_service = PublishServiceHarness(
            _dbus_publish_state={},
            _dbusservice={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
            _dbus_publish_direct_allowed=MagicMock(return_value=False),
            _enqueue_dbus_publish_values=enqueue_values,
            _enqueue_dbus_publish_fields=enqueue_fields,
        )
        harness = _publish_core(enqueue_service)
        self.assertTrue(harness._enqueue_publish_values([("/A", 1)], 10.0))
        self.assertTrue(harness._enqueue_publish_fields([("ac_power_w", 55.0)], 10.0))
        enqueue_values.assert_called_with([("/A", 1)], 10.0)
        enqueue_fields.assert_called_once_with([("ac_power_w", 55.0)], 10.0)
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

        direct_service = PublishServiceHarness(
            _dbus_publish_state={},
            _dbusservice={"/UpdateIndex": 0},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
        )
        _publish_core(direct_service).bump_update_index(10.0)
        self.assertEqual(direct_service._dbusservice["/UpdateIndex"], 1)

    def test_dbus_core_live_and_energy_publish_contract_fields(self) -> None:
        service = PublishServiceHarness(
            _dbus_publish_state={},
            _dbusservice={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
        )
        harness = _publish_core(service)
        measurements = DbusMeasurementPublisher(_publish_context(service), harness)
        phase_data = {
            "L1": {"power": 111.0, "current": 1.1, "voltage": 229.1},
            "L2": {"power": 222.0, "current": 2.2, "voltage": 229.2},
            "L3": {"power": 333.0, "current": 3.3, "voltage": 229.3},
        }

        self.assertTrue(measurements.publish_live_measurements(666.0, 230.0, 6.6, phase_data, now=10.0))

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

        self.assertFalse(measurements.publish_live_measurements(666.0, 230.0, 6.6, phase_data, now=10.5))
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
            measurements.publish_energy_time_measurements(
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

    def test_shelly_worker_guard_paths(self) -> None:
        service = SimpleNamespace(
            _source_retry_after={"shelly": 22.0},
            _shelly_consecutive_errors=3,
        )
        controller = ShellyIoController(service)
        worker = controller.worker
        controller.runtime.charger_retry_active = MagicMock(return_value=True)
        self.assertTrue(worker._source_retry_blocks_pending_relay("charger", 10.0))
        self.assertTrue(worker._source_retry_blocks_pending_relay("shelly", 10.0))
        self.assertEqual(worker._remember_pending_relay_command_error("other", 10.0, RuntimeError("x")), "error")
        self.assertIsNone(worker._pending_relay_error_exc_info("shelly", requests.exceptions.ConnectTimeout()))

        self.assertFalse(worker._source_retry_blocks_pending_relay("unknown", 10.0))
        self.assertEqual(worker._pending_relay_shelly_retry_remaining("shelly", 10.0), 12.0)

if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact contracts for the service runtime composition roles."""

from __future__ import annotations

import unittest
from collections.abc import Mapping
from unittest.mock import MagicMock, call, patch

from venus_evcharger.control import ControlCommand
from venus_evcharger.service import control_state_core as state_core_module
from venus_evcharger.service import factory as factory_module
from venus_evcharger.service.control_state_core import _ControlApiStateCore
from venus_evcharger.service.factory import ServiceControllerFactory
from venus_evcharger.service.runtime import RuntimeHelper
from venus_evcharger.service.state_publish import StatePublish
from venus_evcharger.service.update import UpdateCycle


def _plain(payload: Mapping[str, object]) -> dict[str, object]:
    return dict(payload)


class _ControlStateFacade(_ControlApiStateCore):
    def __init__(self) -> None:
        self.write_controller = MagicMock()
        self.publisher = MagicMock()

    def _ensure_write_controller(self) -> MagicMock:
        return self.write_controller

    def _ensure_dbus_publisher(self) -> MagicMock:
        return self.publisher

    def _state_summary(self) -> str:
        return "summary-value"

    def _current_runtime_state(self) -> dict[str, object]:
        return {"mode": 2, "relay": True}


class _RuntimeFacade(RuntimeHelper):
    def __init__(self) -> None:
        self.runtime = MagicMock(unsafe=True)
        self.supervisor = MagicMock()
        self.shelly = MagicMock()

    def _ensure_runtime_support_controller(self) -> MagicMock:
        return self.runtime

    def _ensure_auto_input_supervisor(self) -> MagicMock:
        return self.supervisor

    def _ensure_shelly_io_controller(self) -> MagicMock:
        return self.shelly


class _StatePublishFacade(StatePublish):
    def __init__(self) -> None:
        self.state = MagicMock()
        self.publisher = MagicMock()
        self.companion = MagicMock()

    def _ensure_state_controller(self) -> MagicMock:
        return self.state

    def _ensure_dbus_publisher(self) -> MagicMock:
        return self.publisher

    def _ensure_companion_dbus_bridge(self) -> MagicMock:
        return self.companion


class _UpdateFacade(UpdateCycle):
    def __init__(self) -> None:
        self.update_controller = MagicMock()

    def _ensure_update_controller(self) -> MagicMock:
        return self.update_controller


class ControlStateCoreContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = _ControlStateFacade()

    def test_command_contract_requires_real_control_command(self) -> None:
        command = ControlCommand(name="set_mode", path="/Mode", value=2)
        self.service.write_controller.build_control_command_from_payload.return_value = command
        payload = {"name": "set_mode", "value": 2}
        self.assertIs(self.service._control_command_from_payload(payload, source="test"), command)
        self.service.write_controller.build_control_command_from_payload.assert_called_once_with(payload, source="test")

        self.service.write_controller.build_control_command_from_payload.return_value = {"name": "set_mode"}
        with self.assertRaisesRegex(TypeError, "write controller returned non-ControlCommand payload"):
            self.service._control_command_from_payload(payload)

    def test_summary_and_runtime_payloads_are_exact(self) -> None:
        with (
            patch.object(state_core_module, "normalized_state_api_summary_fields", side_effect=_plain) as summary,
            patch.object(state_core_module, "normalized_state_api_runtime_fields", side_effect=_plain) as runtime,
        ):
            self.assertEqual(
                self.service._state_api_summary_payload(),
                {"ok": True, "api_version": "v1", "kind": "summary", "summary": "summary-value"},
            )
            self.assertEqual(
                self.service._state_api_runtime_payload(),
                {
                    "ok": True,
                    "api_version": "v1",
                    "kind": "runtime",
                    "state": {"mode": 2, "relay": True},
                },
            )
        self.assertEqual(summary.call_count, 1)
        self.assertEqual(runtime.call_count, 1)

    def test_dbus_diagnostics_payload_uses_numeric_clock_and_plain_mappings(self) -> None:
        self.service._time_now = MagicMock(return_value=123.5)
        self.service.publisher._diagnostic_counter_values.return_value = {"timeouts": 2}
        self.service.publisher._diagnostic_age_values.return_value = {"age": 4.5}
        with patch.object(
            state_core_module,
            "normalized_state_api_dbus_diagnostics_fields",
            side_effect=_plain,
        ):
            payload = self.service._state_api_dbus_diagnostics_payload()
        self.assertEqual(
            payload,
            {
                "ok": True,
                "api_version": "v1",
                "kind": "dbus-diagnostics",
                "state": {"timeouts": 2, "age": 4.5},
            },
        )
        self.assertEqual(
            self.service.publisher.method_calls,
            [call._diagnostic_counter_values(123.5), call._diagnostic_age_values(123.5)],
        )

    def test_dbus_diagnostics_falls_back_for_non_numeric_clock_and_non_mappings(self) -> None:
        self.service._time_now = MagicMock(return_value="bad")
        self.service.publisher._diagnostic_counter_values.return_value = None
        self.service.publisher._diagnostic_age_values.return_value = []
        with (
            patch.object(state_core_module.time, "time", return_value=88.0) as clock,
            patch.object(state_core_module, "normalized_state_api_dbus_diagnostics_fields", side_effect=_plain),
        ):
            payload = self.service._state_api_dbus_diagnostics_payload()
        self.assertEqual(payload["state"], {})
        clock.assert_called_once_with()

    def test_topology_payload_contains_every_boundary_field(self) -> None:
        self.service.supported_phase_selections = ("P1", "P3")
        self.service.active_phase_selection = "P3"
        self.service.requested_phase_selection = "P3"
        self.service.service_name = "com.example.evcharger"
        self.service.connection_name = "LAN"
        with (
            patch.object(state_core_module, "backend_mode_for_service", return_value="split") as mode,
            patch.object(
                state_core_module,
                "backend_type_for_service",
                side_effect=("meter-x", "switch-y", "charger-z"),
            ) as backend,
            patch.object(state_core_module, "normalized_state_api_topology_fields", side_effect=_plain),
        ):
            payload = self.service._state_api_topology_payload()
        self.assertEqual(
            payload,
            {
                "ok": True,
                "api_version": "v1",
                "kind": "topology",
                "state": {
                    "backend_mode": "split",
                    "meter_backend": "meter-x",
                    "switch_backend": "switch-y",
                    "charger_backend": "charger-z",
                    "active_phase_selection": "P3",
                    "requested_phase_selection": "P3",
                    "supported_phase_selections": ["P1", "P3"],
                    "available_modes": [0, 1, 2],
                    "service_name": "com.example.evcharger",
                    "connection_name": "LAN",
                },
            },
        )
        mode.assert_called_once_with(self.service, "combined")
        self.assertEqual(
            backend.call_args_list,
            [
                call(self.service, "meter", "na"),
                call(self.service, "switch", "na"),
                call(self.service, "charger", "na"),
            ],
        )

    def test_update_payload_contains_values_and_defaults(self) -> None:
        values = {
            "_software_update_current_version": "1.0",
            "_software_update_available_version": "2.0",
            "_software_update_available": True,
            "_software_update_state": "ready",
            "_software_update_detail": "new",
            "_software_update_last_check_at": 1.0,
            "_software_update_last_run_at": 2.0,
            "_software_update_last_result": "ok",
            "_software_update_run_requested_at": 3.0,
            "_software_update_next_check_at": 4.0,
            "_software_update_boot_auto_due_at": 5.0,
            "_software_update_no_update_active": True,
        }
        for name, value in values.items():
            setattr(self.service, name, value)
        with patch.object(state_core_module, "normalized_state_api_update_fields", side_effect=_plain):
            payload = self.service._state_api_update_payload()
        self.assertEqual(payload["state"], {name.removeprefix("_software_update_"): value for name, value in values.items()})


class FactoryContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = ServiceControllerFactory()
        self.factory._age_seconds_func = MagicMock()
        self.factory._health_code_func = MagicMock()
        self.factory._mode_uses_auto_logic_func = MagicMock()
        self.factory._normalize_mode_func = MagicMock()
        self.factory._normalize_phase_func = MagicMock()
        self.factory._month_window_func = MagicMock()
        self.factory._phase_values_func = MagicMock()
        self.factory._read_version_func = MagicMock()
        self.factory._gobject_module = object()
        self.factory._script_path_value = "/service.py"
        self.factory._formatter_bundle = {"x": None}

    def test_simple_controller_factories_create_once_with_exact_dependencies(self) -> None:
        cases = (
            ("DbusPublishController", "_dbus_publisher", "_ensure_dbus_publisher", (self.factory, self.factory._age_seconds_func)),
            ("ShellyIoController", "_shelly_io_controller", "_ensure_shelly_io_controller", ("host",)),
            ("ServiceStateController", "_state_controller", "_ensure_state_controller", (self.factory, self.factory._normalize_mode_func)),
            ("AutoInputSupervisor", "_auto_input_supervisor", "_ensure_auto_input_supervisor", (self.factory,)),
            ("EnergyCompanionDbusBridge", "_companion_dbus_bridge", "_ensure_companion_dbus_bridge", (self.factory, "/service.py")),
        )
        for constructor_name, attribute, ensure_name, arguments in cases:
            with self.subTest(constructor=constructor_name):
                setattr(self.factory, attribute, None)
                created = MagicMock(name=constructor_name)
                patches = [patch.object(factory_module, constructor_name, return_value=created)]
                if constructor_name == "ShellyIoController":
                    patches.append(patch.object(factory_module, "require_shelly_io_host", return_value="host"))
                with patches[0] as constructor:
                    if len(patches) == 2:
                        with patches[1] as host:
                            result = getattr(self.factory, ensure_name)()
                            host.assert_called_once_with(self.factory)
                    else:
                        result = getattr(self.factory, ensure_name)()
                    self.assertIs(result, created)
                    self.assertIs(getattr(self.factory, ensure_name)(), created)
                constructor.assert_called_once_with(*arguments)

    def test_port_controller_factories_create_once_with_exact_dependencies(self) -> None:
        cases = (
            ("AutoDecisionPort", "AutoDecisionController", "_auto_controller", "_ensure_auto_controller", (self.factory._health_code_func, self.factory._mode_uses_auto_logic_func)),
            ("WriteControllerPort", "DbusWriteController", "_write_controller", "_ensure_write_controller", ()),
            ("DbusInputPort", "DbusInputController", "_dbus_input_controller", "_ensure_dbus_input_controller", ()),
            ("UpdateCyclePort", "UpdateCycleController", "_update_controller", "_ensure_update_controller", (self.factory._phase_values_func, self.factory._health_code_func)),
        )
        for port_name, controller_name, attribute, ensure_name, trailing in cases:
            with self.subTest(controller=controller_name):
                setattr(self.factory, attribute, None)
                port = object()
                created = MagicMock(name=controller_name)
                with (
                    patch.object(factory_module, port_name, return_value=port) as port_constructor,
                    patch.object(factory_module, controller_name, return_value=created) as constructor,
                ):
                    self.assertIs(getattr(self.factory, ensure_name)(), created)
                    self.assertIs(getattr(self.factory, ensure_name)(), created)
                port_constructor.assert_called_once_with(self.factory)
                constructor.assert_called_once_with(port, *trailing)

    def test_runtime_and_bootstrap_factories_preserve_all_dependencies(self) -> None:
        runtime = MagicMock()
        with patch.object(factory_module, "RuntimeSupportController", return_value=runtime) as constructor:
            self.assertIs(self.factory._ensure_runtime_support_controller(), runtime)
            self.assertIs(self.factory._ensure_runtime_support_controller(), runtime)
        constructor.assert_called_once_with(self.factory, self.factory._age_seconds_func, self.factory._health_code_func)

        bootstrap = MagicMock()
        with patch.object(factory_module, "ServiceBootstrapController", return_value=bootstrap) as constructor:
            self.assertIs(self.factory._ensure_bootstrap_controller(), bootstrap)
            self.assertIs(self.factory._ensure_bootstrap_controller(), bootstrap)
        constructor.assert_called_once_with(
            self.factory,
            normalize_phase_func=self.factory._normalize_phase_func,
            normalize_mode_func=self.factory._normalize_mode_func,
            mode_uses_auto_logic_func=self.factory._mode_uses_auto_logic_func,
            month_window_func=self.factory._month_window_func,
            age_seconds_func=self.factory._age_seconds_func,
            health_code_func=self.factory._health_code_func,
            phase_values_func=self.factory._phase_values_func,
            read_version_func=self.factory._read_version_func,
            gobject_module=self.factory._gobject_module,
            script_path="/service.py",
            formatters={"x": None},
        )


class RuntimeRoleContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = _RuntimeFacade()

    def test_gateway_and_runtime_survivor_contracts(self) -> None:
        command = ControlCommand(name="set_mode", path="/Mode", value=2)
        self.service.runtime.dbus_publish_direct_allowed.return_value = True
        self.service.runtime.enqueue_dbus_publish_values.return_value = True
        self.service.runtime.enqueue_dbus_publish_fields.return_value = False
        self.service.runtime.enqueue_companion_dbus_publish.return_value = True
        self.service.runtime.flush_dbus_publish_queue.return_value = False
        self.service.runtime.enqueue_control_command.return_value = True
        self.service.runtime.mainloop_heartbeat_tick.return_value = False
        self.service.runtime.get_worker_snapshot.return_value = {"sequence": 7}
        self.service.runtime.is_update_stale.return_value = True
        self.service.runtime.source_retry_ready.return_value = False
        self.service.shelly.phase_selection_requires_pause.return_value = True

        self.assertIs(self.service._dbus_publish_direct_allowed(), True)
        self.service._assert_dbus_mainloop_thread("publish")
        self.assertIs(self.service._enqueue_dbus_publish_values([("/Mode", 2)], 10.0), True)
        self.assertIs(self.service._enqueue_dbus_publish_fields([("mode", 2)], 11.0), False)
        self.assertIs(self.service._enqueue_companion_dbus_publish(12.0), True)
        self.assertIs(self.service._flush_dbus_publish_queue(), False)
        self.assertIs(self.service._enqueue_control_command(command), True)
        self.assertIs(self.service._mainloop_heartbeat_tick(), False)
        self.assertEqual(self.service._get_worker_snapshot(), {"sequence": 7})
        self.assertIs(self.service._is_update_stale(13.0), True)
        self.service._warning_throttled("key", 5.0, "message %s", "arg", extra=True)
        self.service._write_auto_audit_event("reason", cached=True)
        self.assertIs(self.service._source_retry_ready("pv", 14.0), False)
        self.service._stop_auto_input_helper(force=True)
        self.assertIs(self.service._phase_selection_requires_pause(), True)

        self.assertEqual(
            self.service.runtime.method_calls,
            [
                call.dbus_publish_direct_allowed(),
                call.assert_dbus_mainloop_thread("publish"),
                call.enqueue_dbus_publish_values([("/Mode", 2)], 10.0),
                call.enqueue_dbus_publish_fields([("mode", 2)], 11.0),
                call.enqueue_companion_dbus_publish(12.0),
                call.flush_dbus_publish_queue(),
                call.enqueue_control_command(command),
                call.mainloop_heartbeat_tick(),
                call.get_worker_snapshot(),
                call.is_update_stale(13.0),
                call.warning_throttled("key", 5.0, "message %s", "arg", extra=True),
                call.write_auto_audit_event("reason", True),
                call.source_retry_ready("pv", 14.0),
            ],
        )
        self.service.supervisor.stop_helper.assert_called_once_with(True)
        self.service.shelly.phase_selection_requires_pause.assert_called_once_with()

    def test_direct_dbus_access_is_always_rejected(self) -> None:
        with self.assertRaises(RuntimeError) as raised:
            self.service._get_system_bus()
        self.assertEqual(str(raised.exception), "Direct DBus access is disabled; use the DBus gateway adapter")

    def test_runtime_return_contract_labels_are_exact(self) -> None:
        cases = (
            ("dbus_publish_direct_allowed", self.service._dbus_publish_direct_allowed, ()),
            ("enqueue_dbus_publish_values", self.service._enqueue_dbus_publish_values, ([], 1.0)),
            ("enqueue_dbus_publish_fields", self.service._enqueue_dbus_publish_fields, ([], 1.0)),
            ("enqueue_companion_dbus_publish", self.service._enqueue_companion_dbus_publish, (1.0,)),
            ("flush_dbus_publish_queue", self.service._flush_dbus_publish_queue, ()),
            ("enqueue_control_command", self.service._enqueue_control_command, (ControlCommand(name="x", path="/X", value=1),)),
            ("mainloop_heartbeat_tick", self.service._mainloop_heartbeat_tick, ()),
            ("get_worker_snapshot", self.service._get_worker_snapshot, ()),
            ("is_update_stale", self.service._is_update_stale, (1.0,)),
            ("source_retry_ready", self.service._source_retry_ready, ("pv", 1.0)),
        )
        for controller_method, facade_method, arguments in cases:
            with self.subTest(method=controller_method):
                getattr(self.service.runtime, controller_method).return_value = "invalid"
                with self.assertRaisesRegex(TypeError, controller_method):
                    facade_method(*arguments)
        self.service.shelly.phase_selection_requires_pause.return_value = "invalid"
        with self.assertRaisesRegex(TypeError, "phase_selection_requires_pause"):
            self.service._phase_selection_requires_pause()


class StatePublishContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = _StatePublishFacade()

    def test_static_coercion_helpers_preserve_defaults(self) -> None:
        with (
            patch("venus_evcharger.service.state_publish.ServiceStateController.coerce_runtime_int", return_value=7) as integer,
            patch("venus_evcharger.service.state_publish.ServiceStateController.coerce_runtime_float", return_value=2.5) as floating,
        ):
            self.assertEqual(self.service._coerce_runtime_int("7", 3), 7)
            self.assertEqual(self.service._coerce_runtime_float("2.5", 1.5), 2.5)
        integer.assert_called_once_with("7", 3)
        floating.assert_called_once_with("2.5", 1.5)

    def test_publish_delegations_preserve_arguments_and_results(self) -> None:
        self.service.publisher.publish_field.return_value = True
        self.service.publisher.publish_live_measurements.return_value = False
        self.service.publisher.publish_energy_time_measurements.return_value = True
        self.service.publisher.publish_config_paths.return_value = False
        self.service.publisher.publish_diagnostic_paths.return_value = True
        phases = {"L1": {"power": 100.0}}
        self.assertIs(self.service._publish_dbus_field("mode", 2, 1.0, force=True), True)
        self.assertIs(self.service._publish_live_measurements(100.0, 230.0, 0.5, phases, 2.0), False)
        self.assertIs(self.service._publish_energy_time_measurements(5.0, {"L1": 5.0}, 60, 0.2, 3.0), True)
        self.assertIs(self.service._publish_config_paths(1, 4.0), False)
        self.assertIs(self.service._publish_diagnostic_paths(5.0), True)
        self.assertEqual(
            self.service.publisher.method_calls,
            [
                call.publish_field("mode", 2, 1.0, force=True),
                call.publish_live_measurements(100.0, 230.0, 0.5, phases, 2.0),
                call.publish_energy_time_measurements(5.0, {"L1": 5.0}, 60, 0.2, 3.0),
                call.publish_config_paths(1, 4.0),
                call.publish_diagnostic_paths(5.0),
            ],
        )

    def test_companion_publish_uses_queue_or_direct_path(self) -> None:
        self.service._dbus_publish_direct_allowed = MagicMock(return_value=False)
        self.service._enqueue_companion_dbus_publish = MagicMock(return_value=True)
        self.assertIs(self.service._publish_companion_dbus_bridge(10.0), True)
        self.service._enqueue_companion_dbus_publish.assert_called_once_with(10.0)
        self.service.companion.publish.assert_not_called()

        self.service._dbus_publish_direct_allowed.return_value = True
        self.service.companion.publish.return_value = False
        self.assertIs(self.service._publish_companion_dbus_bridge(11.0), False)
        self.service.companion.publish.assert_called_once_with(11.0)


class UpdateCycleContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = _UpdateFacade()

    def test_surviving_delegations_preserve_arguments_and_results(self) -> None:
        controller = self.service.update_controller
        controller.phase_energies_for_total.return_value = {"L1": 1.5}
        controller.publish_virtual_state_paths.return_value = True
        controller.update_virtual_state.return_value = False
        controller.publish_offline_update.return_value = True
        controller.extract_pm_measurements.return_value = (True, 1.0, 2.0, 3.0, 4.0)
        controller.resolve_cached_input_value.return_value = (5.0, True)
        controller.resolve_auto_inputs.return_value = (6.0, 7.0, 8.0)
        controller.apply_relay_decision.return_value = (True, 9.0, 10.0, False)
        controller.derive_status_code.return_value = 11
        controller.sign_of_life.return_value = True
        snapshot = {"sequence": 1}
        pm = {"relay": True}

        self.assertEqual(self.service._phase_energies_for_total(1.5), {"L1": 1.5})
        self.assertIs(self.service._publish_virtual_state_paths(1.5, 60, 0.2, 1, 2.0), True)
        self.assertIs(self.service._update_virtual_state(2, 1.5, True), False)
        self.assertIs(self.service._publish_offline_update(3.0), True)
        self.assertEqual(self.service._extract_pm_measurements(pm), (True, 1.0, 2.0, 3.0, 4.0))
        self.assertEqual(self.service._resolve_cached_input_value(5.0, 1.0, "value", "at", 4.0, 30.0), (5.0, True))
        self.assertEqual(self.service._resolve_auto_inputs(snapshot, 5.0, True), (6.0, 7.0, 8.0))
        self.assertEqual(self.service._apply_relay_decision(True, False, pm, 9.0, 10.0, 6.0, True), (True, 9.0, 10.0, False))
        self.assertEqual(self.service._derive_status_code(True, 9.0, True), 11)
        self.assertIs(self.service._sign_of_life(), True)
        self.assertEqual(
            controller.method_calls,
            [
                call.phase_energies_for_total(self.service, 1.5),
                call.publish_virtual_state_paths(1.5, 60, 0.2, 1, 2.0),
                call.update_virtual_state(2, 1.5, True),
                call.publish_offline_update(3.0),
                call.extract_pm_measurements(self.service, pm),
                call.resolve_cached_input_value(self.service, 5.0, 1.0, "value", "at", 4.0, max_age_seconds=30.0),
                call.resolve_auto_inputs(snapshot, 5.0, True),
                call.apply_relay_decision(True, False, pm, 9.0, 10.0, 6.0, True),
                call.derive_status_code(self.service, True, 9.0, True),
                call.sign_of_life(),
            ],
        )


if __name__ == "__main__":
    unittest.main()

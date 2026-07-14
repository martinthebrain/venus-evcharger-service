# SPDX-License-Identifier: GPL-3.0-or-later
"""Failure and default contracts for service composition roles."""

from __future__ import annotations

import unittest
from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock, call, patch

from venus_evcharger.control import ControlCommand
from venus_evcharger.service import control_state_core as state_core_module
from venus_evcharger.service import factory as factory_module
from venus_evcharger.service.factory import ServiceControllerFactory

from tests.test_service_runtime_role_contracts import (
    _ControlStateFacade,
    _RuntimeFacade,
    _StatePublishFacade,
    _UpdateFacade,
    _plain,
)


class _ExactTypeErrorCase(unittest.TestCase):
    def assert_contract_error(self, expected: str, operation: Callable[[], object]) -> None:
        with self.assertRaises(TypeError) as raised:
            operation()
        self.assertEqual(str(raised.exception), expected)


class ControlStateDefaultContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = _ControlStateFacade()

    def test_command_default_source_is_http(self) -> None:
        command = ControlCommand(name="set_mode", path="/Mode", value=1)
        self.service.write_controller.build_control_command_from_payload.return_value = command
        payload = {"name": "set_mode", "value": 1}
        self.assertIs(self.service._control_command_from_payload(payload), command)
        self.service.write_controller.build_control_command_from_payload.assert_called_once_with(payload, source="http")

        self.service.write_controller.build_control_command_from_payload.return_value = object()
        with self.assertRaises(TypeError) as raised:
            self.service._control_command_from_payload(payload)
        self.assertEqual(str(raised.exception), "write controller returned non-ControlCommand payload")

    def test_topology_defaults_are_exact(self) -> None:
        with (
            patch.object(state_core_module, "backend_mode_for_service", return_value="combined"),
            patch.object(state_core_module, "backend_type_for_service", return_value="na"),
            patch.object(state_core_module, "normalized_state_api_topology_fields", side_effect=_plain),
        ):
            payload = self.service._state_api_topology_payload()
        self.assertEqual(
            payload["state"],
            {
                "backend_mode": "combined",
                "meter_backend": "na",
                "switch_backend": "na",
                "charger_backend": "na",
                "active_phase_selection": "P1",
                "requested_phase_selection": "P1",
                "supported_phase_selections": ["P1"],
                "available_modes": [0, 1, 2],
                "service_name": "",
                "connection_name": "",
            },
        )

        self.service.supported_phase_selections = ()
        with (
            patch.object(state_core_module, "backend_mode_for_service", return_value="combined"),
            patch.object(state_core_module, "backend_type_for_service", return_value="na"),
            patch.object(state_core_module, "normalized_state_api_topology_fields", side_effect=_plain),
        ):
            empty_payload = self.service._state_api_topology_payload()
        self.assertEqual(empty_payload["state"]["supported_phase_selections"], ["P1"])

    def test_update_defaults_are_exact(self) -> None:
        with patch.object(state_core_module, "normalized_state_api_update_fields", side_effect=_plain):
            payload = self.service._state_api_update_payload()
        self.assertEqual(
            payload,
            {
                "ok": True,
                "api_version": "v1",
                "kind": "update",
                "state": {
                    "current_version": "",
                    "available_version": "",
                    "available": False,
                    "state": "idle",
                    "detail": "",
                    "last_check_at": None,
                    "last_run_at": None,
                    "last_result": "",
                    "run_requested_at": None,
                    "next_check_at": None,
                    "boot_auto_due_at": None,
                    "no_update_active": False,
                },
            },
        )

    def test_diagnostics_uses_system_clock_without_callable_override(self) -> None:
        self.service._time_now = None
        self.service.publisher._diagnostic_counter_values.return_value = {}
        self.service.publisher._diagnostic_age_values.return_value = {}
        with (
            patch.object(state_core_module.time, "time", return_value=44.0) as clock,
            patch.object(state_core_module, "normalized_state_api_dbus_diagnostics_fields", side_effect=_plain),
        ):
            self.service._state_api_dbus_diagnostics_payload()
        clock.assert_called_once_with()
        self.assertEqual(
            self.service.publisher.method_calls,
            [call._diagnostic_counter_values(44.0), call._diagnostic_age_values(44.0)],
        )


class FactoryFailureContractTests(unittest.TestCase):
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
        self.factory._formatter_bundle = {}

    def test_simple_factory_none_results_name_the_failed_cache(self) -> None:
        cases = (
            ("DbusPublishController", "_dbus_publisher", "_ensure_dbus_publisher"),
            ("ShellyIoController", "_shelly_io_controller", "_ensure_shelly_io_controller"),
            ("ServiceStateController", "_state_controller", "_ensure_state_controller"),
            ("AutoInputSupervisor", "_auto_input_supervisor", "_ensure_auto_input_supervisor"),
            ("RuntimeSupportController", "_runtime_support_controller", "_ensure_runtime_support_controller"),
            ("ServiceBootstrapController", "_bootstrap_controller", "_ensure_bootstrap_controller"),
            ("EnergyCompanionDbusBridge", "_companion_dbus_bridge", "_ensure_companion_dbus_bridge"),
        )
        for constructor_name, attribute, ensure_name in cases:
            with self.subTest(controller=constructor_name):
                setattr(self.factory, attribute, None)
                with (
                    patch.object(factory_module, constructor_name, return_value=None),
                    patch.object(factory_module, "require_shelly_io_host", return_value="host"),
                ):
                    with self.assertRaises(RuntimeError) as raised:
                        getattr(self.factory, ensure_name)()
                self.assertEqual(str(raised.exception), f"{attribute} was not initialized")

    def test_port_factory_none_results_name_the_failed_cache(self) -> None:
        cases = (
            ("AutoDecisionPort", "AutoDecisionController", "_auto_controller", "_ensure_auto_controller"),
            ("WriteControllerPort", "DbusWriteController", "_write_controller", "_ensure_write_controller"),
            ("DbusInputPort", "DbusInputController", "_dbus_input_controller", "_ensure_dbus_input_controller"),
            ("UpdateCyclePort", "UpdateCycleController", "_update_controller", "_ensure_update_controller"),
        )
        for port_name, controller_name, attribute, ensure_name in cases:
            with self.subTest(controller=controller_name):
                setattr(self.factory, attribute, None)
                with (
                    patch.object(factory_module, port_name, return_value=object()),
                    patch.object(factory_module, controller_name, return_value=None),
                ):
                    with self.assertRaises(RuntimeError) as raised:
                        getattr(self.factory, ensure_name)()
                self.assertEqual(str(raised.exception), f"{attribute} was not initialized")


class RuntimeFailureContractTests(_ExactTypeErrorCase):
    def setUp(self) -> None:
        self.service = _RuntimeFacade()

    def test_return_contract_labels_are_not_renamable(self) -> None:
        cases: tuple[tuple[str, Callable[[], object], str], ...] = (
            ("dbus_publish_direct_allowed", self.service._dbus_publish_direct_allowed, "bool"),
            ("enqueue_dbus_publish_values", lambda: self.service._enqueue_dbus_publish_values([], 1.0), "bool"),
            ("enqueue_dbus_publish_fields", lambda: self.service._enqueue_dbus_publish_fields([], 1.0), "bool"),
            ("enqueue_companion_dbus_publish", lambda: self.service._enqueue_companion_dbus_publish(1.0), "bool"),
            ("flush_dbus_publish_queue", self.service._flush_dbus_publish_queue, "bool"),
            (
                "enqueue_control_command",
                lambda: self.service._enqueue_control_command(ControlCommand(name="x", path="/X", value=1)),
                "bool",
            ),
            ("mainloop_heartbeat_tick", self.service._mainloop_heartbeat_tick, "bool"),
            ("get_worker_snapshot", self.service._get_worker_snapshot, "dict"),
            ("is_update_stale", lambda: self.service._is_update_stale(1.0), "bool"),
            ("source_retry_ready", lambda: self.service._source_retry_ready("pv", 1.0), "bool"),
        )
        for name, operation, expected_type in cases:
            with self.subTest(method=name):
                getattr(self.service.runtime, name).return_value = "invalid"
                self.assert_contract_error(f"{name} must return {expected_type}, got str", operation)

        self.service.shelly.phase_selection_requires_pause.return_value = "invalid"
        self.assert_contract_error(
            "phase_selection_requires_pause must return bool, got str",
            self.service._phase_selection_requires_pause,
        )

    def test_optional_arguments_keep_their_declared_defaults(self) -> None:
        self.service._assert_dbus_mainloop_thread()
        self.service._write_auto_audit_event("reason")
        self.service._stop_auto_input_helper()
        self.assertEqual(
            self.service.runtime.method_calls,
            [call.assert_dbus_mainloop_thread("dbus access"), call.write_auto_audit_event("reason", False)],
        )
        self.service.supervisor.stop_helper.assert_called_once_with(False)


class StatePublishFailureContractTests(_ExactTypeErrorCase):
    def setUp(self) -> None:
        self.service = _StatePublishFacade()

    def test_publish_contract_labels_are_not_renamable(self) -> None:
        cases: tuple[tuple[str, Callable[[], object]], ...] = (
            ("publish_field", lambda: self.service._publish_dbus_field("mode", 2, 1.0)),
            ("publish_live_measurements", lambda: self.service._publish_live_measurements(1.0, 2.0, 3.0, {}, 4.0)),
            (
                "publish_energy_time_measurements",
                lambda: self.service._publish_energy_time_measurements(1.0, {}, 2, 3.0, 4.0),
            ),
            ("publish_config_paths", lambda: self.service._publish_config_paths(1, 2.0)),
            ("publish_diagnostic_paths", lambda: self.service._publish_diagnostic_paths(1.0)),
        )
        for name, operation in cases:
            with self.subTest(method=name):
                getattr(self.service.publisher, name).return_value = "invalid"
                self.assert_contract_error(f"{name} must return bool, got str", operation)

    def test_optional_arguments_keep_their_declared_defaults(self) -> None:
        with (
            patch("venus_evcharger.service.state_publish.ServiceStateController.coerce_runtime_int", return_value=0) as integer,
            patch("venus_evcharger.service.state_publish.ServiceStateController.coerce_runtime_float", return_value=0.0) as floating,
        ):
            self.assertEqual(self.service._coerce_runtime_int("bad"), 0)
            self.assertEqual(self.service._coerce_runtime_float("bad"), 0.0)
        integer.assert_called_once_with("bad", 0)
        floating.assert_called_once_with("bad", 0.0)

        self.service.publisher.publish_field.return_value = True
        self.assertIs(self.service._publish_dbus_field("mode", 2, None), True)
        self.service.publisher.publish_field.assert_called_once_with("mode", 2, None, force=False)

        self.service._dbus_publish_direct_allowed = MagicMock(return_value=False)
        self.service._enqueue_companion_dbus_publish = MagicMock(return_value=True)
        self.assertIs(self.service._publish_companion_dbus_bridge(), True)
        self.service._enqueue_companion_dbus_publish.assert_called_once_with(None)

    def test_companion_contract_labels_are_exact(self) -> None:
        self.service._dbus_publish_direct_allowed = MagicMock(return_value=False)
        self.service._enqueue_companion_dbus_publish = MagicMock(return_value="invalid")
        self.assert_contract_error(
            "_enqueue_companion_dbus_publish must return bool, got str",
            lambda: self.service._publish_companion_dbus_bridge(1.0),
        )

        self.service._dbus_publish_direct_allowed = MagicMock(return_value=True)
        self.service.companion.publish.return_value = "invalid"
        self.assert_contract_error(
            "companion_dbus_bridge.publish must return bool, got str",
            lambda: self.service._publish_companion_dbus_bridge(2.0),
        )


class UpdateCycleFailureContractTests(_ExactTypeErrorCase):
    def setUp(self) -> None:
        self.service = _UpdateFacade()

    def test_surviving_return_contract_labels_are_not_renamable(self) -> None:
        cases: tuple[tuple[str, Callable[[], object], str], ...] = (
            ("phase_energies_for_total", lambda: self.service._phase_energies_for_total(1.0), "dict"),
            (
                "publish_virtual_state_paths",
                lambda: self.service._publish_virtual_state_paths(1.0, 2, 3.0, 1, 4.0),
                "bool",
            ),
            ("update_virtual_state", lambda: self.service._update_virtual_state(2, 1.0, True), "bool"),
            ("publish_offline_update", lambda: self.service._publish_offline_update(1.0), "bool"),
            ("extract_pm_measurements", lambda: self.service._extract_pm_measurements({}), "tuple"),
            (
                "resolve_cached_input_value",
                lambda: self.service._resolve_cached_input_value(None, None, "value", "at", 1.0),
                "tuple",
            ),
            ("resolve_auto_inputs", lambda: self.service._resolve_auto_inputs({}, 1.0, True), "tuple"),
            (
                "apply_relay_decision",
                lambda: self.service._apply_relay_decision(True, False, {}, 1.0, 2.0, 3.0, True),
                "tuple",
            ),
            ("derive_status_code", lambda: self.service._derive_status_code(True, 1.0, True), "int"),
            ("sign_of_life", self.service._sign_of_life, "bool"),
        )
        for name, operation, expected_type in cases:
            with self.subTest(method=name):
                getattr(self.service.update_controller, name).return_value = "invalid"
                self.assert_contract_error(f"{name} must return {expected_type}, got str", operation)


if __name__ == "__main__":
    unittest.main()

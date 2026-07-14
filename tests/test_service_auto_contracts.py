# SPDX-License-Identifier: GPL-3.0-or-later
"""Direct contracts for the service Auto facade."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, call, patch

from venus_evcharger.control import ControlCommand, ControlResult
from venus_evcharger.service.auto import DbusAutoLogic


class _AutoFacade(DbusAutoLogic):
    def __init__(self) -> None:
        self.dbus_input = MagicMock()
        self.auto = MagicMock()
        self.write = MagicMock()
        self.bootstrap = MagicMock()
        self._mode_uses_auto_logic_func = MagicMock()
        self._normalize_mode_func = MagicMock()

    def _ensure_dbus_input_controller(self) -> MagicMock:
        return self.dbus_input

    def _ensure_auto_controller(self) -> MagicMock:
        return self.auto

    def _ensure_write_controller(self) -> MagicMock:
        return self.write

    def _ensure_bootstrap_controller(self) -> MagicMock:
        return self.bootstrap


def _command(*, value: object = 1) -> ControlCommand:
    return ControlCommand(name="set_mode", path="/Mode", value=value)


class ServiceAutoContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = _AutoFacade()

    def test_static_and_mode_helpers_preserve_arguments_and_results(self) -> None:
        with patch(
            "venus_evcharger.service.auto.AutoDecisionController.get_available_surplus_watts",
            return_value=725.5,
        ) as surplus:
            self.assertEqual(self.service._get_available_surplus_watts(1200.0, -725.5), 725.5)
        surplus.assert_called_once_with(1200.0, -725.5)

        self.service._mode_uses_auto_logic_func.return_value = True
        self.service._normalize_mode_func.return_value = 2
        self.assertIs(self.service._mode_uses_auto_logic(2), True)
        self.assertEqual(self.service._normalize_mode("2"), 2)
        self.service._mode_uses_auto_logic_func.assert_called_once_with(2)
        self.service._normalize_mode_func.assert_called_once_with("2")

    def test_dbus_input_facade_delegates_all_operations(self) -> None:
        marker = object()
        self.service.dbus_input.get_dbus_value.return_value = marker
        self.service.dbus_input.list_dbus_services.return_value = ["svc.one", "svc.two"]
        self.service.dbus_input.resolve_auto_pv_services.return_value = ["pv.one"]
        self.service.dbus_input.get_pv_power.return_value = 1800
        self.service.dbus_input.resolve_auto_battery_service.return_value = "battery.one"
        self.service.dbus_input.get_battery_soc.return_value = 63
        self.service.dbus_input.get_grid_power.return_value = -450

        self.assertIs(self.service._get_dbus_value("svc.one", "/Value"), marker)
        self.assertEqual(self.service._list_dbus_services(), ["svc.one", "svc.two"])
        self.service._invalidate_auto_pv_services()
        self.service._invalidate_auto_battery_service()
        self.assertEqual(self.service._resolve_auto_pv_services(), ["pv.one"])
        self.assertEqual(self.service._get_pv_power(), 1800.0)
        self.assertEqual(self.service._resolve_auto_battery_service(), "battery.one")
        self.assertEqual(self.service._get_battery_soc(), 63.0)
        self.assertEqual(self.service._get_grid_power(), -450.0)

        self.assertEqual(
            self.service.dbus_input.method_calls,
            [
                call.get_dbus_value("svc.one", "/Value"),
                call.list_dbus_services(),
                call.invalidate_auto_pv_services(),
                call.invalidate_auto_battery_service(),
                call.resolve_auto_pv_services(),
                call.get_pv_power(),
                call.resolve_auto_battery_service(),
                call.get_battery_soc(),
                call.get_grid_power(),
            ],
        )

    def test_optional_dbus_inputs_preserve_none(self) -> None:
        self.service.dbus_input.get_pv_power.return_value = None
        self.service.dbus_input.resolve_auto_battery_service.return_value = None
        self.service.dbus_input.get_battery_soc.return_value = None
        self.service.dbus_input.get_grid_power.return_value = None
        self.assertIsNone(self.service._get_pv_power())
        self.assertIsNone(self.service._resolve_auto_battery_service())
        self.assertIsNone(self.service._get_battery_soc())
        self.assertIsNone(self.service._get_grid_power())

    def test_dbus_input_return_contracts_reject_invalid_values(self) -> None:
        cases = (
            (
                "list_dbus_services",
                self.service._list_dbus_services,
                ["valid", 1],
                "list_dbus_services must return list[str]",
            ),
            (
                "resolve_auto_pv_services",
                self.service._resolve_auto_pv_services,
                "pv",
                "resolve_auto_pv_services must return list, got str",
            ),
            ("get_pv_power", self.service._get_pv_power, "1800", "get_pv_power must return float, got str"),
            (
                "resolve_auto_battery_service",
                self.service._resolve_auto_battery_service,
                61,
                "resolve_auto_battery_service must return str, got int",
            ),
            (
                "get_battery_soc",
                self.service._get_battery_soc,
                "63",
                "get_battery_soc must return float, got str",
            ),
            (
                "get_grid_power",
                self.service._get_grid_power,
                "-450",
                "get_grid_power must return float, got str",
            ),
        )
        for controller_method, facade_method, value, expected_message in cases:
            with self.subTest(controller_method=controller_method):
                getattr(self.service.dbus_input, controller_method).return_value = value
                with self.assertRaises(TypeError) as raised:
                    facade_method()
                self.assertEqual(str(raised.exception), expected_message)

    def test_auto_controller_facade_preserves_every_argument(self) -> None:
        self.service.auto.average_auto_metric.return_value = 125.25
        self.service.auto.is_within_auto_daytime_window.return_value = False
        self.service.auto.auto_decide_relay.return_value = True
        current_dt = object()

        self.service._add_auto_sample(10.0, 900.0, -100.0)
        self.service._clear_auto_samples()
        self.assertEqual(self.service._average_auto_metric(2), 125.25)
        self.service._mark_relay_changed(True, 11.0)
        self.service._mark_relay_changed(False)
        self.assertIs(self.service._is_within_auto_daytime_window(current_dt), False)
        self.service._set_health("cached-input", cached=True)
        self.assertIs(self.service._auto_decide_relay(False, 900.0, 55.0, -100.0), True)

        self.assertEqual(
            self.service.auto.method_calls,
            [
                call.add_auto_sample(10.0, 900.0, -100.0),
                call.clear_auto_samples(),
                call.average_auto_metric(2),
                call.mark_relay_changed(True, 11.0),
                call.mark_relay_changed(False, None),
                call.is_within_auto_daytime_window(current_dt),
                call.set_health("cached-input", True),
                call.auto_decide_relay(False, 900.0, 55.0, -100.0),
            ],
        )

    def test_auto_controller_return_contracts_reject_invalid_values(self) -> None:
        self.service.auto.average_auto_metric.return_value = "1"
        with self.assertRaises(TypeError) as average_error:
            self.service._average_auto_metric(0)
        self.assertEqual(
            str(average_error.exception),
            "average_auto_metric must return float, got str",
        )
        self.service.auto.is_within_auto_daytime_window.return_value = 1
        with self.assertRaises(TypeError) as daytime_error:
            self.service._is_within_auto_daytime_window()
        self.assertEqual(
            str(daytime_error.exception),
            "is_within_auto_daytime_window must return bool, got int",
        )
        self.service.auto.auto_decide_relay.return_value = 1
        with self.assertRaises(TypeError) as decision_error:
            self.service._auto_decide_relay(False, None, None, None)
        self.assertEqual(
            str(decision_error.exception),
            "auto_decide_relay must return bool, got int",
        )

    def test_health_default_is_not_cached(self) -> None:
        self.service._set_health("live-input")
        self.service.auto.set_health.assert_called_once_with("live-input", False)

    def test_control_facade_builds_handles_and_publishes_exact_models(self) -> None:
        command = _command()
        result = ControlResult.applied_result(command)
        publish = MagicMock()
        self.service._publish_control_api_command_event = publish
        self.service.write.build_control_command.return_value = command
        self.service.write.handle_control_command.return_value = result

        self.assertIs(self.service._control_command_from_write("/Mode", 1, source="mqtt"), command)
        self.assertIs(self.service._handle_control_command(command), result)
        self.service.write.build_control_command.assert_called_once_with("/Mode", 1, source="mqtt")
        self.service.write.handle_control_command.assert_called_once_with(command)
        publish.assert_called_once_with(command, result)

        self.service.write.build_control_command.reset_mock()
        self.service.write.build_control_command.return_value = command
        self.assertIs(self.service._control_command_from_write("/Mode", 1), command)
        self.service.write.build_control_command.assert_called_once_with("/Mode", 1, source="dbus")

    def test_control_facade_rejects_invalid_models(self) -> None:
        self.service.write.build_control_command.return_value = object()
        with self.assertRaisesRegex(TypeError, "build_control_command must return ControlCommand"):
            self.service._control_command_from_write("/Mode", 1)
        self.service.write.handle_control_command.return_value = object()
        with self.assertRaisesRegex(TypeError, "handle_control_command must return ControlResult"):
            self.service._handle_control_command(_command())

    def test_handle_command_allows_absent_or_non_callable_event_hook(self) -> None:
        command = _command()
        result = ControlResult.rejected_result(command)
        self.service.write.handle_control_command.return_value = result
        self.assertIs(self.service._handle_control_command(command), result)
        self.service._publish_control_api_command_event = "disabled"
        self.assertIs(self.service._handle_control_command(command), result)
        self.assertEqual(self.service.write.handle_control_command.call_count, 2)

    def test_handle_write_uses_sync_path_without_enabled_async_queue(self) -> None:
        for accepted in (False, True):
            with self.subTest(accepted=accepted):
                service = _AutoFacade()
                command = _command(value=accepted)
                result = (
                    ControlResult.applied_result(command)
                    if accepted
                    else ControlResult.rejected_result(command)
                )
                service.write.build_control_command.return_value = command
                service.write.handle_control_command.return_value = result
                service._enqueue_control_command = MagicMock(return_value=True)
                service._control_command_async_enabled = False
                self.assertIs(service._handle_write("/Mode", accepted), accepted)
                service._enqueue_control_command.assert_not_called()
                service.write.build_control_command.assert_called_once_with("/Mode", accepted, source="dbus")
                service.write.handle_control_command.assert_called_once_with(command)

    def test_handle_write_marks_dbus_source_at_the_transport_boundary(self) -> None:
        command = _command()
        result = ControlResult.applied_result(command)
        self.service._control_command_from_write = MagicMock(return_value=command)
        self.service._handle_control_command = MagicMock(return_value=result)
        self.assertIs(self.service._handle_write("/Mode", 1), True)
        self.service._control_command_from_write.assert_called_once_with("/Mode", 1, source="dbus")
        self.service._handle_control_command.assert_called_once_with(command)

    def test_handle_write_defaults_to_sync_without_an_async_queue(self) -> None:
        command = _command()
        result = ControlResult.applied_result(command)
        self.service.write.build_control_command.return_value = command
        self.service.write.handle_control_command.return_value = result
        self.assertFalse(hasattr(self.service, "_enqueue_control_command"))
        self.assertIs(self.service._control_command_async_enabled, False)
        self.assertIs(self.service._handle_write("/Mode", 1), True)
        self.service.write.handle_control_command.assert_called_once_with(command)

    def test_handle_write_does_not_infer_async_mode_from_queue_presence(self) -> None:
        command = _command()
        result = ControlResult.applied_result(command)
        self.service.write.build_control_command.return_value = command
        self.service.write.handle_control_command.return_value = result
        self.service._enqueue_control_command = MagicMock(return_value=False)
        self.assertIs(self.service._control_command_async_enabled, False)
        self.assertIs(self.service._handle_write("/Mode", 1), True)
        self.service._enqueue_control_command.assert_not_called()
        self.service.write.handle_control_command.assert_called_once_with(command)

    def test_handle_write_uses_enabled_async_queue_and_enforces_bool(self) -> None:
        command = _command()
        self.service.write.build_control_command.return_value = command
        self.service._control_command_async_enabled = True
        self.service._enqueue_control_command = MagicMock(return_value=False)
        self.assertIs(self.service._handle_write("/Mode", 1), False)
        self.service._enqueue_control_command.assert_called_once_with(command)
        self.service.write.handle_control_command.assert_not_called()

        self.service._enqueue_control_command.return_value = 1
        with self.assertRaisesRegex(TypeError, "enqueue_control_command must return bool"):
            self.service._handle_write("/Mode", 1)

    def test_handle_write_ignores_enabled_flag_without_callable_queue(self) -> None:
        command = _command()
        result = ControlResult.applied_result(command)
        self.service.write.build_control_command.return_value = command
        self.service.write.handle_control_command.return_value = result
        self.service._control_command_async_enabled = True
        self.service._enqueue_control_command = None
        self.assertIs(self.service._handle_write("/Mode", 1), True)
        self.service.write.handle_control_command.assert_called_once_with(command)

    def test_bootstrap_facade_delegates_and_enforces_mapping(self) -> None:
        payload = {"product": "EVCS", "instance": 61}
        self.service.bootstrap.fetch_device_info_with_fallback.return_value = payload
        self.service._register_paths()
        self.assertEqual(self.service._fetch_device_info_with_fallback(), payload)
        self.service.bootstrap.register_paths.assert_called_once_with()
        self.service.bootstrap.fetch_device_info_with_fallback.assert_called_once_with()

        self.service.bootstrap.fetch_device_info_with_fallback.return_value = []
        with self.assertRaisesRegex(TypeError, "fetch_device_info_with_fallback must return dict"):
            self.service._fetch_device_info_with_fallback()


if __name__ == "__main__":
    unittest.main()

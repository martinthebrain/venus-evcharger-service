# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact delegation and validation contracts for AutoDecisionPort."""

import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, call

from venus_evcharger.ports.auto import AutoDecisionPort, _require_pending_relay_command


class AutoDecisionPortContractTests(unittest.TestCase):
    def test_pending_command_errors_name_the_actual_invalid_type(self) -> None:
        with self.assertRaisesRegex(
            TypeError,
            r"^peek_pending_relay_command must return tuple, got list$",
        ):
            _require_pending_relay_command([])
        with self.assertRaisesRegex(
            TypeError,
            r"^peek_pending_relay_command state must be bool\|None, got int$",
        ):
            _require_pending_relay_command((1, 2.0))
        with self.assertRaisesRegex(
            TypeError,
            r"^peek_pending_relay_command timestamp must be int\|float\|None, got bool$",
        ):
            _require_pending_relay_command((True, False))

    def test_optional_argument_defaults_are_public_contracts(self) -> None:
        expectations = {
            AutoDecisionPort.set_health: {"cached": False, "relay_intent": None},
            AutoDecisionPort.write_auto_audit_event: {"cached": False},
            AutoDecisionPort.is_within_auto_daytime_window: {"current_dt": None},
        }
        for method, defaults in expectations.items():
            signature = inspect.signature(method)
            for name, expected in defaults.items():
                self.assertEqual(signature.parameters[name].default, expected)

    def test_service_overrides_receive_exact_arguments(self) -> None:
        service = SimpleNamespace(
            _clear_auto_samples=MagicMock(return_value="cleared"),
            _set_health=MagicMock(return_value="healthy"),
            _write_auto_audit_event=MagicMock(return_value="audited"),
            _is_within_auto_daytime_window=MagicMock(side_effect=[1, 0]),
            _get_available_surplus_watts=MagicMock(return_value="12.5"),
            _add_auto_sample=MagicMock(return_value="sampled"),
            _average_auto_metric=MagicMock(side_effect=[None, "4.5"]),
        )
        port = AutoDecisionPort(service)
        marker = object()

        self.assertEqual(port.clear_auto_samples(), "cleared")
        self.assertEqual(port.set_health("reason", True, relay_intent=False), "healthy")
        self.assertEqual(port.write_auto_audit_event("event", True), "audited")
        self.assertTrue(port.is_within_auto_daytime_window())
        self.assertFalse(port.is_within_auto_daytime_window(marker))
        self.assertEqual(port.get_available_surplus_watts(10.0, -2.5), 12.5)
        self.assertEqual(port.add_auto_sample(1.0, 2.0, 3.0), "sampled")
        self.assertIsNone(port.average_auto_metric(0))
        self.assertEqual(port.average_auto_metric(1), 4.5)

        service._clear_auto_samples.assert_called_once_with()
        service._set_health.assert_called_once_with("reason", True, relay_intent=False)
        service._write_auto_audit_event.assert_called_once_with("event", True)
        self.assertEqual(service._is_within_auto_daytime_window.call_args_list, [call(), call(marker)])
        service._get_available_surplus_watts.assert_called_once_with(10.0, -2.5)
        service._add_auto_sample.assert_called_once_with(1.0, 2.0, 3.0)
        self.assertEqual(service._average_auto_metric.call_args_list, [call(0), call(1)])

    def test_controller_fallback_uses_public_method_names_and_arguments(self) -> None:
        service = SimpleNamespace()
        controller = SimpleNamespace(
            clear_auto_samples=MagicMock(return_value="cleared"),
            set_health=MagicMock(return_value="healthy"),
            is_within_auto_daytime_window=MagicMock(return_value=True),
            get_available_surplus_watts=MagicMock(return_value=7),
            add_auto_sample=MagicMock(return_value="sampled"),
            average_auto_metric=MagicMock(return_value=8),
        )
        port = AutoDecisionPort(service)
        port.bind_controller(controller)
        marker = object()

        self.assertEqual(port.clear_auto_samples(), "cleared")
        self.assertEqual(port.set_health("reason", True, relay_intent=None), "healthy")
        self.assertTrue(port.is_within_auto_daytime_window(marker))
        self.assertEqual(port.get_available_surplus_watts(5.0, -2.0), 7.0)
        self.assertEqual(port.add_auto_sample(1.0, 2.0, 3.0), "sampled")
        self.assertEqual(port.average_auto_metric(2), 8.0)

        controller.clear_auto_samples.assert_called_once_with()
        controller.set_health.assert_called_once_with("reason", True, relay_intent=None)
        controller.is_within_auto_daytime_window.assert_called_once_with(marker)
        controller.get_available_surplus_watts.assert_called_once_with(5.0, -2.0)
        controller.add_auto_sample.assert_called_once_with(1.0, 2.0, 3.0)
        controller.average_auto_metric.assert_called_once_with(2)


if __name__ == "__main__":
    unittest.main()

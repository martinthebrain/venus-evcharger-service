# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact delegation and validation contracts for AutoDecisionPort."""

import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from venus_evcharger.auto.policy import AutoPolicy
from venus_evcharger.ports.auto import AutoDecisionPort, _require_pending_relay_command


class AutoDecisionPortContractTests(unittest.TestCase):
    def test_pending_command_errors_name_the_actual_invalid_type(self) -> None:
        invalid_values: tuple[tuple[object, str], ...] = (
            ([], r"^peek_pending_relay_command must return tuple, got list$"),
            ((1, 2.0), r"^peek_pending_relay_command state must be bool\|None, got int$"),
            ((True, False), r"^peek_pending_relay_command timestamp must be int\|float\|None, got bool$"),
        )
        for value, pattern in invalid_values:
            with self.subTest(value=value), self.assertRaisesRegex(TypeError, pattern):
                _require_pending_relay_command(value)

    def test_pending_command_requires_exact_pair(self) -> None:
        with self.assertRaisesRegex(TypeError, r"tuple length 2, got 1"):
            _require_pending_relay_command((True,))
        self.assertEqual(_require_pending_relay_command((False, 2)), (False, 2.0))
        self.assertEqual(_require_pending_relay_command((None, None)), (None, None))

    def test_audit_default_is_part_of_the_port_contract(self) -> None:
        signature = inspect.signature(AutoDecisionPort.write_auto_audit_event)
        self.assertIs(signature.parameters["cached"].default, False)

    def test_auto_policy_preserves_the_bootstrap_contract_object(self) -> None:
        policy = AutoPolicy()
        self.assertIs(
            AutoDecisionPort(SimpleNamespace(auto_policy=policy)).auto_policy(),
            policy,
        )

    def test_side_effects_delegate_once_with_exact_arguments(self) -> None:
        state = SimpleNamespace(save_runtime_state=MagicMock(return_value="saved"))
        runtime = SimpleNamespace(
            write_auto_audit_event=MagicMock(return_value="audited"),
            pending_relay_command=MagicMock(return_value=(True, 123)),
        )
        service = SimpleNamespace(
            state=state,
            runtime=runtime,
        )
        port = AutoDecisionPort(service)

        self.assertIs(port.service, service)
        self.assertEqual(port.save_runtime_state(), "saved")
        self.assertEqual(port.write_auto_audit_event("running", True), "audited")
        self.assertEqual(port.peek_pending_relay_command(), (True, 123.0))

        state.save_runtime_state.assert_called_once_with()
        runtime.write_auto_audit_event.assert_called_once_with("running", True)
        runtime.pending_relay_command.assert_called_once_with()

    def test_control_state_is_normalized_and_cutover_updates_are_atomic(self) -> None:
        service = SimpleNamespace(
            virtual_mode="invalid",
            virtual_enable="0",
            virtual_autostart="2",
            _auto_mode_cutover_pending=True,
            _ignore_min_offtime_once=True,
        )
        port = AutoDecisionPort(service)

        self.assertEqual(port.mode(), 0)
        self.assertFalse(port.controller_enabled())
        self.assertTrue(port.autostart_enabled())
        self.assertTrue(port.mode_cutover_pending())
        self.assertTrue(port.minimum_offtime_bypass_active())

        service._auto_mode_cutover_pending = 1
        service._ignore_min_offtime_once = 1
        self.assertFalse(port.mode_cutover_pending())
        self.assertFalse(port.minimum_offtime_bypass_active())

        port.reset_mode_cutover()
        self.assertIs(service._auto_mode_cutover_pending, False)
        self.assertIs(service._ignore_min_offtime_once, False)

        port.complete_mode_cutover()
        self.assertIs(service._auto_mode_cutover_pending, False)
        self.assertIs(service._ignore_min_offtime_once, True)

        port.clear_minimum_offtime_bypass()
        self.assertIs(service._ignore_min_offtime_once, False)

    def test_port_has_no_controller_binding_or_dynamic_forwarding(self) -> None:
        port = AutoDecisionPort(SimpleNamespace())

        self.assertFalse(hasattr(port, "bind_controller"))
        self.assertNotIn("__getattr__", AutoDecisionPort.__dict__)
        with self.assertRaises(AttributeError):
            getattr(port, "virtual_mode")
        with self.assertRaises(AttributeError):
            getattr(port, "clear_auto_samples")()


if __name__ == "__main__":
    unittest.main()

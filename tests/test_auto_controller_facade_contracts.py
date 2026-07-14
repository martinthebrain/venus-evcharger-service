# SPDX-License-Identifier: GPL-3.0-or-later
"""Behavioral contracts for the public Auto controller facade."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from venus_evcharger.auto.workflow import AutoDecisionWorkflow
from venus_evcharger.controllers.auto import AutoDecisionController


def _health_code(_value: str) -> int:
    return 7


def _uses_auto(_value: object) -> bool:
    return True


class TestAutoControllerFacadeContracts(unittest.TestCase):
    def test_constructor_binds_capable_ports_and_preserves_dependencies(self) -> None:
        service = SimpleNamespace(bind_controller=MagicMock())
        controller = AutoDecisionController(service, _health_code, _uses_auto)
        self.assertIs(controller.service, service)
        self.assertIs(controller._health_code, _health_code)
        self.assertIs(controller._mode_uses_auto_logic, _uses_auto)
        service.bind_controller.assert_called_once_with(controller)

        unbound = SimpleNamespace()
        controller = AutoDecisionController(unbound, _health_code, _uses_auto)
        self.assertIs(controller.service, unbound)

    def test_service_method_resolution_prefers_bound_override_then_public_then_legacy(self) -> None:
        override = MagicMock(name="override")
        public = MagicMock(name="public")
        legacy = MagicMock(name="legacy")
        target = SimpleNamespace(_legacy=override)
        port = SimpleNamespace(_service=target, public=public, _legacy=legacy)
        controller = AutoDecisionController(port, _health_code, _uses_auto)
        self.assertIs(controller._service_method("public", "_legacy"), override)

        target._legacy = "not-callable"
        self.assertIs(controller._service_method("public", "_legacy"), public)
        port.public = None
        self.assertIs(controller._service_method("public", "_legacy"), legacy)
        port._legacy = None
        self.assertIsNone(controller._service_method("public", "_legacy"))

    def test_bound_override_requires_real_wrapped_instance_callable(self) -> None:
        controller = AutoDecisionController(SimpleNamespace(), _health_code, _uses_auto)
        self.assertIsNone(controller._bound_port_override("_method"))
        controller.service = SimpleNamespace(_service=SimpleNamespace(_method=3))
        self.assertIsNone(controller._bound_port_override("_method"))
        method = MagicMock()
        controller.service._service._method = method
        self.assertIs(controller._bound_port_override("_method"), method)

    def test_external_method_rejects_facade_recursion_and_noncallables(self) -> None:
        public = MagicMock()
        port = SimpleNamespace(public=public)
        controller = AutoDecisionController(port, _health_code, _uses_auto)
        self.assertIs(controller._external_service_method("public"), public)
        port.public = 3
        self.assertIsNone(controller._external_service_method("public"))
        port._controller = controller
        port.public = public
        self.assertIsNone(controller._external_service_method("public"))
        port.XX_XXpublic = public
        self.assertIsNone(controller._external_service_method("XX_XXpublic"))
        controller.service = SimpleNamespace(_recursive=controller.available_surplus_watts)
        self.assertIsNone(controller._external_service_method("_recursive"))

    def test_surplus_delegates_to_port_or_workflow_with_exact_numeric_result(self) -> None:
        delegated = MagicMock(return_value="17.5")
        controller = AutoDecisionController(
            SimpleNamespace(get_available_surplus_watts=delegated), _health_code, _uses_auto
        )
        self.assertEqual(controller.available_surplus_watts(20, -2), 17.5)
        delegated.assert_called_once_with(20, -2)

        controller = AutoDecisionController(SimpleNamespace(), _health_code, _uses_auto)
        with patch.object(AutoDecisionWorkflow, "get_available_surplus_watts", return_value=9.25) as fallback:
            self.assertEqual(controller.available_surplus_watts(10, 1), 9.25)
        fallback.assert_called_once_with(10, 1)

    def test_sample_and_average_delegate_or_fall_back_exactly(self) -> None:
        add = MagicMock()
        average = MagicMock(side_effect=[None, "4.5"])
        controller = AutoDecisionController(
            SimpleNamespace(add_auto_sample=add, average_auto_metric=average), _health_code, _uses_auto
        )
        self.assertIsNone(controller.add_auto_sample(1.0, 2.0, 3.0))
        add.assert_called_once_with(1.0, 2.0, 3.0)
        self.assertIsNone(controller.average_auto_metric(0))
        self.assertEqual(controller.average_auto_metric(1), 4.5)
        self.assertEqual(average.call_args_list[0].args, (0,))
        self.assertEqual(average.call_args_list[1].args, (1,))

        controller = AutoDecisionController(SimpleNamespace(), _health_code, _uses_auto)
        with (
            patch.object(AutoDecisionWorkflow, "add_auto_sample") as add_fallback,
            patch.object(AutoDecisionWorkflow, "average_auto_metric", return_value=6.0) as avg_fallback,
        ):
            controller.add_auto_sample(4.0, 5.0, 6.0)
            self.assertEqual(controller.average_auto_metric(2), 6.0)
        add_fallback.assert_called_once_with(4.0, 5.0, 6.0)
        avg_fallback.assert_called_once_with(2)

    def test_legacy_auto_methods_are_resolved_by_their_exact_names(self) -> None:
        surplus = MagicMock(return_value="8.5")
        add = MagicMock()
        average = MagicMock(return_value="3.5")
        daytime = MagicMock(return_value=True)
        controller = AutoDecisionController(
            SimpleNamespace(
                _get_available_surplus_watts=surplus,
                _add_auto_sample=add,
                _average_auto_metric=average,
                _is_within_auto_daytime_window=daytime,
            ),
            _health_code,
            _uses_auto,
        )
        self.assertEqual(controller.available_surplus_watts(9, 1), 8.5)
        controller.add_auto_sample(1.0, 2.0, 3.0)
        self.assertEqual(controller.average_auto_metric(4), 3.5)
        self.assertTrue(controller.is_within_auto_daytime_window())
        surplus.assert_called_once_with(9, 1)
        add.assert_called_once_with(1.0, 2.0, 3.0)
        average.assert_called_once_with(4)
        daytime.assert_called_once_with()

    def test_daytime_uses_no_argument_port_only_for_default_datetime(self) -> None:
        delegated = MagicMock(return_value=1)
        controller = AutoDecisionController(
            SimpleNamespace(is_within_auto_daytime_window=delegated), _health_code, _uses_auto
        )
        self.assertTrue(controller.is_within_auto_daytime_window())
        delegated.assert_called_once_with()

        current = object()
        with patch.object(AutoDecisionWorkflow, "is_within_auto_daytime_window", return_value=False) as fallback:
            self.assertFalse(controller.is_within_auto_daytime_window(current))
        fallback.assert_called_once_with(current)

    def test_runtime_pending_and_audit_methods_use_public_or_legacy_port(self) -> None:
        public_save = MagicMock(return_value="saved")
        public_peek = MagicMock(return_value="pending")
        public_audit = MagicMock(return_value="written")
        controller = AutoDecisionController(
            SimpleNamespace(
                save_runtime_state=public_save,
                peek_pending_relay_command=public_peek,
                write_auto_audit_event=public_audit,
            ),
            _health_code,
            _uses_auto,
        )
        self.assertEqual(controller.save_runtime_state(), "saved")
        self.assertEqual(controller.peek_pending_relay_command(), "pending")
        self.assertEqual(controller.write_auto_audit_event("reason", cached=True), "written")
        public_save.assert_called_once_with()
        public_peek.assert_called_once_with()
        public_audit.assert_called_once_with("reason", cached=True)

        legacy_save = MagicMock(return_value=1)
        legacy_peek = MagicMock(return_value=2)
        legacy_audit = MagicMock(return_value=3)
        controller = AutoDecisionController(
            SimpleNamespace(
                save_runtime_state=None,
                peek_pending_relay_command=None,
                write_auto_audit_event=None,
                _save_runtime_state=legacy_save,
                _peek_pending_relay_command=legacy_peek,
                _write_auto_audit_event=legacy_audit,
            ),
            _health_code,
            _uses_auto,
        )
        self.assertEqual(controller.save_runtime_state(), 1)
        self.assertEqual(controller.peek_pending_relay_command(), 2)
        self.assertEqual(controller.write_auto_audit_event("legacy", value=4), 3)
        legacy_save.assert_called_once_with()
        legacy_peek.assert_called_once_with()
        legacy_audit.assert_called_once_with("legacy", value=4)

        missing_public = AutoDecisionController(
            SimpleNamespace(
                _save_runtime_state=legacy_save,
                _peek_pending_relay_command=legacy_peek,
                _write_auto_audit_event=legacy_audit,
            ),
            _health_code,
            _uses_auto,
        )
        self.assertEqual(missing_public.save_runtime_state(), 1)
        self.assertEqual(missing_public.peek_pending_relay_command(), 2)
        self.assertEqual(missing_public.write_auto_audit_event("missing"), 3)


if __name__ == "__main__":
    unittest.main()

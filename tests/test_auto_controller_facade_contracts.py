# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for the composed public Auto controller."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from venus_evcharger.controllers.auto import AutoDecisionController
from venus_evcharger.ports.auto import AutoDecisionPort
from tests.venus_evcharger_test_fixtures import make_auto_controller_service


def _health_code(_value: str) -> int:
    return 7


def _uses_auto(_value: object) -> bool:
    return True


class TestAutoControllerFacadeContracts(unittest.TestCase):
    def _controller(self) -> tuple[AutoDecisionController, object]:
        service = make_auto_controller_service()
        return AutoDecisionController(AutoDecisionPort(service), _health_code, _uses_auto), service

    def test_constructor_requires_explicit_port_and_wires_component_graph(self) -> None:
        with self.assertRaisesRegex(TypeError, "requires AutoDecisionPort"):
            AutoDecisionController(object(), _health_code, _uses_auto)

        controller, service = self._controller()
        self.assertIs(controller.context.service, service)
        self.assertIs(controller.samples.learning, controller.learning)
        self.assertIs(controller.metrics.samples, controller.samples)
        self.assertIs(controller.metrics.battery_balance, controller.battery_balance)
        self.assertIs(controller.gates.learning, controller.learning)
        self.assertIs(controller.relay.gates, controller.gates)
        self.assertIs(controller.workflow.relay, controller.relay)

    def test_controller_and_components_use_no_runtime_inheritance_or_getattr(self) -> None:
        controller, _service = self._controller()
        components = (
            controller,
            controller.learning,
            controller.samples,
            controller.battery_learning,
            controller.battery_balance_policy,
            controller.battery_balance,
            controller.metrics,
            controller.gates,
            controller.relay,
            controller.workflow,
        )
        for component in components:
            with self.subTest(component=type(component).__name__):
                self.assertEqual(len(type(component).__mro__), 2)
                self.assertNotIn("__getattr__", type(component).__dict__)

    def test_public_api_delegates_to_the_owning_components(self) -> None:
        controller, _service = self._controller()
        controller.samples.add_auto_sample = MagicMock()
        controller.samples.clear_auto_samples = MagicMock()
        controller.samples.average_auto_metric = MagicMock(return_value=4.5)
        controller.samples.mark_relay_changed = MagicMock()
        controller.samples.is_within_auto_daytime_window = MagicMock(return_value=False)
        controller.samples.set_health = MagicMock()
        controller.workflow.auto_decide_relay = MagicMock(return_value=True)

        controller.add_auto_sample(1.0, 2.0, 3.0)
        controller.clear_auto_samples()
        self.assertEqual(controller.average_auto_metric(2), 4.5)
        controller.mark_relay_changed(True, 5.0)
        self.assertFalse(controller.is_within_auto_daytime_window())
        controller.set_health("running", True, True)
        self.assertTrue(controller.auto_decide_relay(False, 1.0, 50.0, -1.0))

        controller.samples.add_auto_sample.assert_called_once_with(1.0, 2.0, 3.0)
        controller.samples.clear_auto_samples.assert_called_once_with()
        controller.samples.average_auto_metric.assert_called_once_with(2)
        controller.samples.mark_relay_changed.assert_called_once_with(True, 5.0)
        controller.samples.set_health.assert_called_once_with("running", True, True)

    def test_port_side_effect_contracts_are_exact(self) -> None:
        service = make_auto_controller_service()
        port = AutoDecisionPort(service)

        port.save_runtime_state()
        port.write_auto_audit_event("waiting", True)
        self.assertEqual(port.peek_pending_relay_command(), (None, None))

        service.state.save_runtime_state.assert_called_once_with()
        service.runtime.write_auto_audit_event.assert_called_once_with("waiting", True)
        service.runtime.pending_relay_command.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: GPL-3.0-or-later
"""Behavioral edge contracts for Auto, backend, and bootstrap boundaries."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from venus_evcharger.auto.component_context import AutoDecisionContext
from venus_evcharger.auto.logic_learning import AutoLearningPolicy
from venus_evcharger.auto.logic_samples import AutoSampleTracker
from venus_evcharger.bootstrap.config_backend import BackendConfigLoader
from venus_evcharger.bootstrap.config_identity import _integer_attribute
from venus_evcharger.bootstrap.controller import ServiceBootstrapController
from venus_evcharger.ports.auto import AutoDecisionPort


class _GobjectTimers:
    def timeout_add(self, interval: int, callback: object) -> object:
        return interval, callback


def _bootstrap_controller(service: object) -> ServiceBootstrapController:
    return ServiceBootstrapController(
        service,
        normalize_phase_func=lambda value: str(value),
        normalize_mode_func=lambda value: int(str(value)),
        mode_uses_auto_logic_func=lambda mode: int(str(mode)) in (1, 2),
        month_window_func=lambda *_args: ((8, 0), (18, 0)),
        read_version_func=lambda _path: "1.0",
        gobject_module=_GobjectTimers(),
        script_path="/tmp/venus_evcharger_service.py",
        formatters={},
    )


class AutoBackendBootstrapEdgeContractTests(unittest.TestCase):
    def test_auto_health_initializes_missing_metrics_state(self) -> None:
        service = SimpleNamespace(virtual_mode=1, time_now=lambda: 100.0)
        context = AutoDecisionContext(
            port=AutoDecisionPort(service),
            health_code=lambda _reason: 17,
            mode_uses_auto_logic=lambda _mode: True,
        )
        tracker = AutoSampleTracker(context, AutoLearningPolicy(context))

        tracker.set_health("ready", relay_intent=False)

        self.assertEqual(service._last_health_reason, "ready")
        self.assertEqual(service._last_health_code, 17)
        self.assertIsInstance(service._last_auto_metrics, dict)
        self.assertEqual(service._last_auto_metrics["relay_intent"], 0)

    def test_backend_loader_rejects_non_parser_service_config(self) -> None:
        service = SimpleNamespace(config={"Topology": {}})

        with self.assertRaisesRegex(TypeError, "service config is not a ConfigParser"):
            BackendConfigLoader(service).load()

        self.assertFalse(hasattr(service, "_backend_runtime_summary"))

    def test_identity_integer_boundary_rejects_missing_and_non_integer_values(self) -> None:
        service = SimpleNamespace(deviceinstance=60, text_instance="60")

        self.assertEqual(_integer_attribute(service, "deviceinstance"), 60)
        for attribute in ("text_instance", "missing_instance"):
            with self.subTest(attribute=attribute):
                with self.assertRaisesRegex(
                    TypeError,
                    f"bootstrap service attribute {attribute} is not an int",
                ):
                    _integer_attribute(service, attribute)

    def test_controller_runtime_operations_delegate_to_owned_component(self) -> None:
        controller = _bootstrap_controller(SimpleNamespace())
        operations = (
            (controller.initialize_controllers, "initialize_controllers"),
            (controller.initialize_virtual_state, "initialize_virtual_state"),
            (controller.restore_runtime_state, "restore_runtime_state"),
            (controller.apply_device_metadata, "apply_device_metadata"),
            (controller.start_runtime_loops, "start_runtime_loops"),
        )

        for operation, component_method in operations:
            with self.subTest(component_method=component_method):
                with patch.object(controller.components.runtime, component_method) as delegated:
                    operation()
                delegated.assert_called_once_with()

    def test_controller_validates_identity_before_constructing_gateway_proxy(self) -> None:
        service = SimpleNamespace(service_name="com.example.evcharger", deviceinstance=60)
        controller = _bootstrap_controller(service)
        proxy = object()

        with patch(
            "venus_evcharger.bootstrap.controller.GatewayDbusServiceProxy",
            return_value=proxy,
        ) as proxy_factory:
            controller.initialize_dbus_service()

        proxy_factory.assert_called_once_with("com.example.evcharger.http_60")
        self.assertIs(service._dbusservice, proxy)

        invalid_identities = (
            SimpleNamespace(service_name=None, deviceinstance=60),
            SimpleNamespace(service_name="com.example.evcharger", deviceinstance="60"),
        )
        for invalid_service in invalid_identities:
            with self.subTest(service=invalid_service):
                with self.assertRaisesRegex(
                    TypeError,
                    "service identity must be loaded before DBus initialization",
                ):
                    _bootstrap_controller(invalid_service).initialize_dbus_service()


if __name__ == "__main__":
    unittest.main()

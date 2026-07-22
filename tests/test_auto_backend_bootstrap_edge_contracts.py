# SPDX-License-Identifier: GPL-3.0-or-later
"""Behavioral edge contracts for Auto, backend, and bootstrap boundaries."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.venus_evcharger_bootstrap_controller_support import (
    ServiceBootstrapControllerTestCase,
    _RecordingGatewayPublication,
)
from venus_evcharger.auto.component_context import AutoDecisionContext
from venus_evcharger.auto.logic_learning import AutoLearningPolicy
from venus_evcharger.auto.logic_samples import AutoSampleTracker
from venus_evcharger.bootstrap.config_backend import BackendConfigLoader
from venus_evcharger.bootstrap.config_identity import _integer_attribute
from venus_evcharger.bootstrap.controller import ServiceBootstrapController
from venus_evcharger.ports.auto import AutoDecisionPort


def _bootstrap_controller(service: object) -> ServiceBootstrapController:
    return ServiceBootstrapControllerTestCase._controller(service)


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

    def test_controller_validates_identity_before_semantic_gateway_registration(self) -> None:
        publication = _RecordingGatewayPublication()
        service = SimpleNamespace(
            product_name="EVCS",
            custom_name="Garage",
            firmware_version="1.2.3",
            hardware_version="Shelly",
            serial="AA:BB:CC",
            connection_name="Local network",
            gateway_publication=publication,
        )
        controller = _bootstrap_controller(service)
        with patch.object(controller.components.publication, "initial_fields", return_value={"mode": 2}):
            controller.register_evcs_publication()

        self.assertEqual(len(publication.evcs_registrations), 1)
        identity, fields = publication.evcs_registrations[0]
        self.assertEqual(identity.product_name, "EVCS")
        self.assertEqual(identity.custom_name, "Garage")
        self.assertEqual(identity.process_name, "/tmp/venus_evcharger_service.py")
        self.assertEqual(fields, {"mode": 2})

        invalid_identities = (
            SimpleNamespace(),
            SimpleNamespace(product_name=None),
        )
        for invalid_service in invalid_identities:
            with self.subTest(service=invalid_service):
                with self.assertRaisesRegex(
                    TypeError,
                    "EVCS identity attribute product_name is missing",
                ):
                    _bootstrap_controller(invalid_service).components.publication.identity()


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for small input-controller and snapshot boundaries."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from venus_evcharger.energy import EnergyLearningProfile, EnergySourceDefinition
from venus_evcharger.inputs.dbus import DbusInputController
from venus_evcharger.inputs.dbus_errors import (
    DBUS_INPUT_READ_ERRORS,
    DBUS_INPUT_RESOLUTION_ERRORS,
    DBUS_INPUT_SNAPSHOT_ERRORS,
)
from venus_evcharger.inputs.energy_snapshot_contracts import (
    energy_source_definitions,
    is_source_definition_iterable,
    learning_profile_payloads,
    learning_profiles,
    nested_object_mappings,
    object_mapping,
)


class InputBoundaryContractTests(unittest.TestCase):
    def test_dbus_controller_binds_only_supported_ports(self) -> None:
        class BoundPort:
            def __init__(self) -> None:
                self.controllers: list[object] = []

            def bind_controller(self, controller: object) -> None:
                self.controllers.append(controller)

        bound_port = BoundPort()
        controller = DbusInputController(bound_port)
        self.assertIs(controller.port, bound_port)
        self.assertIs(controller.service, bound_port)
        self.assertEqual(bound_port.controllers, [controller])

        plain_port = object()
        plain_controller = DbusInputController(plain_port)
        self.assertIs(plain_controller.port, plain_port)
        self.assertIs(plain_controller.service, plain_port)

    def test_error_contracts_are_exact(self) -> None:
        self.assertEqual(DBUS_INPUT_READ_ERRORS, (OSError, RuntimeError, ValueError))
        self.assertEqual(DBUS_INPUT_RESOLUTION_ERRORS, (OSError, RuntimeError, ValueError))
        self.assertEqual(DBUS_INPUT_SNAPSHOT_ERRORS, (OSError, RuntimeError, ValueError, TypeError))

    def test_source_definition_and_iterable_filters_are_exact(self) -> None:
        first = EnergySourceDefinition("first")
        second = EnergySourceDefinition("second")
        self.assertEqual(energy_source_definitions(first), (first,))
        self.assertEqual(energy_source_definitions([first, "skip", second]), (first, second))
        self.assertEqual(energy_source_definitions(item for item in (first, second)), (first, second))
        for value in (None, "text", b"bytes", {"source": first}, 7):
            with self.subTest(value=value):
                self.assertFalse(is_source_definition_iterable(value))
                self.assertEqual(energy_source_definitions(value), ())
        self.assertTrue(is_source_definition_iterable(iter((first,))))

    def test_mapping_and_learning_profile_filters_are_exact(self) -> None:
        profile = EnergyLearningProfile("source", sample_count=3)
        self.assertEqual(object_mapping({1: "one", "two": 2}), {"1": "one", "two": 2})
        self.assertEqual(object_mapping([]), {})
        self.assertEqual(
            nested_object_mappings({1: {"x": 1}, "skip": [], "two": {2: "value"}}),
            {"1": {"x": 1}, "two": {"2": "value"}},
        )
        self.assertEqual(nested_object_mappings([]), {})
        self.assertEqual(
            learning_profiles({1: profile, "mapping": {"sample_count": 2}, "skip": []}),
            {"1": profile, "mapping": {"sample_count": 2}},
        )
        self.assertEqual(learning_profiles([]), {})
        payloads = learning_profile_payloads({1: profile, "skip": {"sample_count": 2}})
        self.assertEqual(tuple(payloads), ("1",))
        self.assertEqual(payloads["1"]["source_id"], "source")
        self.assertEqual(payloads["1"]["sample_count"], 3)
        self.assertEqual(learning_profile_payloads([]), {})


if __name__ == "__main__":
    unittest.main()

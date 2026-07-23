# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for the backend-independent EVCS service identity."""

from __future__ import annotations

import configparser
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from venus_evcharger.bootstrap.runtime_metadata import apply_service_identity


def _service(config_text: str = "[DEFAULT]\n") -> SimpleNamespace:
    config = configparser.ConfigParser()
    config.read_string(config_text)
    return SimpleNamespace(config=config, custom_name_override="", deviceinstance=60)


class BootstrapRuntimeMetadataContracts(unittest.TestCase):
    def test_incomplete_identity_source_is_rejected_with_exact_boundary_error(self) -> None:
        with self.assertRaisesRegex(
            TypeError,
            "^bootstrap service does not implement ServiceIdentitySource$",
        ):
            apply_service_identity(SimpleNamespace(), read_version=lambda _name: "1.0")

    def test_default_identity_is_local_stable_and_backend_independent(self) -> None:
        service = _service()
        read_version = MagicMock(return_value="1.2.3")

        apply_service_identity(service, read_version=read_version)

        self.assertEqual(service.product_name, "Venus EV Charger Service")
        self.assertEqual(service.custom_name, "Venus EV Charger Service")
        self.assertEqual(service.serial, "venus-evcharger-60")
        self.assertEqual(service.firmware_version, "1.2.3")
        self.assertEqual(service.hardware_version, "Virtual EV charger")
        read_version.assert_called_once_with("version.txt")

    def test_configured_identity_preserves_explicit_values_and_name_override(self) -> None:
        service = SimpleNamespace(
            config={
                "DEFAULT": {
                    "ProductName": "Local EVCS",
                    "ServiceSerial": "site-a-evcs",
                    "HardwareVersion": "Relay controller",
                }
            },
            custom_name_override="Garage",
            deviceinstance=60,
        )

        apply_service_identity(service, read_version=lambda _name: "2.0")

        self.assertEqual(service.product_name, "Local EVCS")
        self.assertEqual(service.custom_name, "Garage")
        self.assertEqual(service.serial, "site-a-evcs")
        self.assertEqual(service.firmware_version, "2.0")
        self.assertEqual(service.hardware_version, "Relay controller")

    def test_blank_config_values_use_deterministic_fallbacks(self) -> None:
        service = _service(
            "[DEFAULT]\nProductName=  \nServiceSerial=  \nHardwareVersion=  \n"
        )
        service.deviceinstance = 71

        apply_service_identity(service, read_version=lambda _name: "3.0")

        self.assertEqual(service.product_name, "Venus EV Charger Service")
        self.assertEqual(service.custom_name, "Venus EV Charger Service")
        self.assertEqual(service.serial, "venus-evcharger-71")
        self.assertEqual(service.hardware_version, "Virtual EV charger")

    def test_missing_default_mapping_is_rejected_at_identity_boundary(self) -> None:
        service = SimpleNamespace(config={}, custom_name_override="", deviceinstance=60)

        with self.assertRaisesRegex(
            TypeError,
            "^bootstrap config DEFAULT section is not a mapping$",
        ):
            apply_service_identity(service, read_version=lambda _name: "1.0")

        service.config = {"DEFAULT": "invalid"}
        with self.assertRaisesRegex(
            TypeError,
            "^bootstrap config DEFAULT section is not a mapping$",
        ):
            apply_service_identity(service, read_version=lambda _name: "1.0")


if __name__ == "__main__":
    unittest.main()

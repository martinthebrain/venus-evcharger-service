# SPDX-License-Identifier: GPL-3.0-or-later
"""Boundary contracts for adapter-owned semantic publication."""

from __future__ import annotations

import configparser
import unittest
from dataclasses import replace
from typing import cast
from unittest.mock import patch

from tests.dbus_adapter_venus_stubs import FakeVeDbusService, install_venus_adapter_stubs

install_venus_adapter_stubs()

from tests.support.dbus_gateway_adapter_harness import (
    GatewayAdapterContractCase,
    companion_identity,
    companion_publication,
    companion_registration,
    evcs_registration,
    publication_registry_module,
)
from venus_evcharger.dbus_adapter.publication.identity import companion_concrete_identity
from venus_evcharger.dbus_adapter.publication.registry import EVCS_SERVICE_ID, RegisteredPublicationService
from venus_evcharger.dbus_adapter.publication.schema import (
    EVCS_PUBLICATION_SPECS,
    format_amps,
    format_kwh,
    format_status,
    format_volts,
    format_watts,
    validate_fields,
)
from venus_evcharger.ipc.gateway_publication import parse_publish_companion_fields
from venus_evcharger.ports.gateway_publication import CompanionServiceKind


def _defaults(config_text: str = "[DEFAULT]\n") -> configparser.SectionProxy:
    config = configparser.ConfigParser()
    config.read_string(config_text)
    return config["DEFAULT"]


class PublicationSchemaContractTests(unittest.TestCase):
    def test_numeric_formatters_normalize_supported_and_invalid_values(self) -> None:
        self.assertEqual(format_kwh("/Energy", "1.234"), "1.23 kWh")
        self.assertEqual(format_amps("/Current", 2), "2.0 A")
        self.assertEqual(format_watts("/Power", 3.25), "3.2 W")
        self.assertEqual(format_volts("/Voltage", True), "0.0 V")
        self.assertEqual(format_watts("/Power", "invalid"), "0.0 W")
        self.assertEqual(format_watts("/Power", object()), "0.0 W")

    def test_status_formatter_accepts_known_codes_and_rejects_invalid_values(self) -> None:
        self.assertEqual(format_status("/Status", "2"), "Laden")
        self.assertEqual(format_status("/Status", 99), "Unbekannt")
        self.assertEqual(format_status("/Status", True), "Unbekannt")
        self.assertEqual(format_status("/Status", object()), "Unbekannt")
        self.assertEqual(format_status("/Status", "invalid"), "Unbekannt")

    def test_field_validation_rejects_unknown_semantics_with_stable_diagnostics(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown evcs publication fields: invalid, unknown"):
            validate_fields(
                {"mode": 1, "unknown": 2, "invalid": 3},
                EVCS_PUBLICATION_SPECS,
                surface="evcs",
            )


class PublicationIdentityContractTests(unittest.TestCase):
    def test_invalid_configured_instance_falls_back_deterministically(self) -> None:
        identity = companion_identity("source:roof/inverter")
        defaults = _defaults(
            "[DEFAULT]\n"
            "DeviceInstance = 60\n"
            "CompanionSourceGridDeviceInstanceBase = invalid\n"
            "CompanionSourceGridServicePrefix =   \n"
        )

        concrete = companion_concrete_identity(defaults, identity)

        self.assertTrue(concrete.service_name.startswith("com.victronenergy.grid.external."))
        self.assertGreaterEqual(concrete.device_instance, 400)
        self.assertLess(concrete.device_instance, 490)


class PublicationRegistryScenarioTests(GatewayAdapterContractCase):
    def test_process_publication_health_reflects_real_registry_state(self) -> None:
        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            self.assertFalse(adapter.evcs_service_registered)

            self.assertEqual(adapter.write_scheduler.publication_executor.process(evcs_registration()), "applied")

            self.assertTrue(adapter.evcs_service_registered)
            self.assertEqual(
                adapter.registered_publication_path_count,
                adapter.publication_registry.registered_path_count,
            )

    def test_repeated_companion_registration_updates_same_kind_and_rejects_kind_change(self) -> None:
        with self.adapter_scenario() as scenario:
            registry = scenario.adapter.publication_registry
            self.assertEqual(
                scenario.adapter.write_scheduler.publication_executor.process(companion_registration()),
                "applied",
            )
            self.assertEqual(
                scenario.adapter.write_scheduler.publication_executor.process(
                    companion_registration(fields={"connected": 0, "ac_power_w": 42.0})
                ),
                "applied",
            )
            changed_kind = replace(
                companion_identity(),
                kind=cast(CompanionServiceKind, "battery"),
            )
            publication = publication_registry_module.RegisterCompanionPublication(changed_kind, {"connected": 1})

            with self.assertRaisesRegex(ValueError, "changed kind"):
                registry.register_companion(publication)

    def test_missing_and_corrupt_companion_records_fail_explicitly(self) -> None:
        with self.adapter_scenario() as scenario:
            registry = scenario.adapter.publication_registry
            missing = parse_publish_companion_fields(companion_publication("missing"))
            assert missing is not None
            self.assertEqual(registry.publish_companion(missing), "deferred")

            self.assertEqual(
                scenario.adapter.write_scheduler.publication_executor.process(companion_registration()),
                "applied",
            )
            record = registry._services["aggregate-grid"]
            record.kind = "corrupt"
            publication = parse_publish_companion_fields(companion_publication())
            assert publication is not None
            with self.assertRaisesRegex(ValueError, "Unsupported companion kind: corrupt"):
                registry.publish_companion(publication)

    def test_registry_rejects_service_name_and_device_instance_collisions(self) -> None:
        with self.adapter_scenario() as scenario:
            registry = scenario.adapter.publication_registry
            registry._reserve_identity("first", "com.example.first", 70)
            registry._reserve_identity("first", "com.example.first", 70)

            with self.assertRaisesRegex(ValueError, "service-name collision"):
                registry._reserve_identity("second", "com.example.first", 71)
            with self.assertRaisesRegex(ValueError, "DeviceInstance collision"):
                registry._reserve_identity("second", "com.example.second", 70)

    def test_gui_write_is_translated_to_core_command_and_unknown_path_is_rejected(self) -> None:
        with self.adapter_scenario() as scenario:
            registry = scenario.adapter.publication_registry
            self.assertEqual(
                scenario.adapter.write_scheduler.publication_executor.process(evcs_registration()),
                "applied",
            )
            record = registry._services[EVCS_SERVICE_ID]
            service = cast(FakeVeDbusService, record.service)
            callback = service.added_paths["/Mode"]["onchangecallback"]
            self.assertTrue(callable(callback))
            assert callable(callback)

            self.assertFalse(callback("/Mode", 2))
            pending = scenario.adapter.core_command_mailbox.load_pending()
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0][1]["name"], "set_mode")
            self.assertEqual(pending[0][1]["target"], "mode")
            self.assertEqual(pending[0][1]["value"], 2)

            with self.assertLogs(level="WARNING") as logs:
                self.assertFalse(registry._handle_gui_write("/NotAControl", 1))
            self.assertIn("Rejected GUI write without semantic route", "\n".join(logs.output))

    def test_invalid_position_uses_identity_fallback(self) -> None:
        with self.adapter_scenario("[DEFAULT]\nPosition = invalid\n") as scenario:
            with patch.object(publication_registry_module.time, "time", return_value=10.0):
                self.assertEqual(
                    scenario.adapter.write_scheduler.publication_executor.process(evcs_registration()),
                    "applied",
                )
            record: RegisteredPublicationService = scenario.adapter.publication_registry._services[EVCS_SERVICE_ID]
            self.assertEqual(record.values["/Position"], 1)


if __name__ == "__main__":
    unittest.main()

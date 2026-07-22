# SPDX-License-Identifier: GPL-3.0-or-later
"""Semantic gateway publication registration scenarios."""

from __future__ import annotations

from tests.support.dbus_gateway_adapter_harness import (
    GatewayAdapterContractCase,
    Path,
    companion_registration,
    evcs_publication,
    evcs_registration,
)


class GatewayWriteRegistrationCases(GatewayAdapterContractCase):
    """Verify registration through opaque publication identities."""

    def test_evcs_registration_creates_the_gateway_owned_service(self) -> None:
        with self.adapter_scenario("[DEFAULT]\nDeviceInstance=77\n") as scenario:
            command_path = scenario.adapter.commands.enqueue(evcs_registration())

            self.assertTrue(scenario.adapter.write_scheduler.process_one())

            self.assertFalse(Path(command_path).exists())
            self.assertTrue(scenario.adapter.publication_registry.evcs_registered)
            self.assertEqual(scenario.adapter.publication_registry.service_count, 1)
            self.assertGreater(scenario.adapter.publication_registry.registered_path_count, 2)

    def test_startup_registration_precedes_live_field_publication(self) -> None:
        with self.adapter_scenario() as scenario:
            publish_path = scenario.adapter.commands.enqueue(evcs_publication({"mode": 2}))
            registration_command_path = scenario.adapter.commands.enqueue(
                evcs_registration({"mode": 0, "connected": 1})
            )

            self.assertTrue(scenario.adapter.write_scheduler.process_one())

            self.assertTrue(scenario.adapter.publication_registry.evcs_registered)
            self.assertFalse(Path(registration_command_path).exists())
            self.assertTrue(Path(publish_path).exists())

            self.assertTrue(scenario.adapter.write_scheduler.process_one())
            self.assertFalse(Path(publish_path).exists())

    def test_repeated_evcs_registration_updates_existing_semantic_fields(self) -> None:
        with self.adapter_scenario() as scenario:
            scheduler = scenario.adapter.write_scheduler
            self.assertEqual(scheduler.process_publication(evcs_registration({"mode": 0})), "applied")
            registered_paths = scenario.adapter.publication_registry.registered_path_count

            self.assertEqual(scheduler.process_publication(evcs_registration({"mode": 2})), "applied")

            self.assertEqual(scenario.adapter.publication_registry.service_count, 1)
            self.assertEqual(scenario.adapter.publication_registry.registered_path_count, registered_paths)

    def test_companion_registration_uses_an_opaque_service_identity(self) -> None:
        with self.adapter_scenario() as scenario:
            command = companion_registration("grid-primary", {"connected": 1, "ac_power_w": 125.0})

            self.assertEqual(scenario.adapter.write_scheduler.process_publication(command), "applied")

            self.assertEqual(scenario.adapter.publication_registry.service_count, 1)
            self.assertFalse(scenario.adapter.publication_registry.evcs_registered)

    def test_publication_waits_for_its_semantic_registration(self) -> None:
        with self.adapter_scenario() as scenario:
            self.assertEqual(
                scenario.adapter.write_scheduler.process_publication(evcs_publication({"connected": 1})),
                "deferred",
            )

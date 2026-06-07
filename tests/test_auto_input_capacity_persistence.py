# SPDX-License-Identifier: GPL-3.0-or-later
import os
import tempfile
import unittest
from unittest.mock import patch

from venus_evcharger.energy import EnergySourceDefinition
from venus_evcharger.inputs.helper import capacity_persistence as persistence


class TestAutoInputCapacityPersistence(unittest.TestCase):
    def test_configured_estimated_capacity_payload_handles_optional_fields(self) -> None:
        self.assertIsNone(persistence.configured_estimated_capacity_payload(EnergySourceDefinition("battery")))

        payload = persistence.configured_estimated_capacity_payload(
            EnergySourceDefinition(
                "battery",
                estimated_capacity_wh=4800.0,
                estimated_capacity_ah=100.0,
                estimated_capacity_nominal_voltage_v=48.0,
                estimated_capacity_cell_count=15,
            )
        )

        self.assertEqual(
            payload,
            {
                "usable_capacity_wh": 4800.0,
                "usable_capacity_source": "config_estimated",
                "installed_capacity_ah": 100.0,
                "capacity_nominal_voltage_v": 48.0,
                "capacity_cell_count": 15,
            },
        )

    def test_configured_estimated_capacity_payload_skips_non_positive_optional_fields(self) -> None:
        payload = persistence.configured_estimated_capacity_payload(
            EnergySourceDefinition(
                "battery",
                estimated_capacity_wh=4800.0,
                estimated_capacity_ah=0.0,
                estimated_capacity_nominal_voltage_v=-1.0,
                estimated_capacity_cell_count=0,
            )
        )

        self.assertEqual(payload, {"usable_capacity_wh": 4800.0, "usable_capacity_source": "config_estimated"})

    def test_persist_estimated_capacity_rejects_missing_same_or_unwritable_inputs(self) -> None:
        source = EnergySourceDefinition("primary_battery", estimated_capacity_ah=100.0)
        self.assertFalse(persistence.persist_estimated_capacity_if_ah_changed("", source, {}))
        self.assertFalse(
            persistence.persist_estimated_capacity_if_ah_changed(
                "",
                source,
                {"installed_capacity_ah": "bad"},
            )
        )
        self.assertFalse(
            persistence.persist_estimated_capacity_if_ah_changed(
                "",
                source,
                {"installed_capacity_ah": 100.0001},
            )
        )
        self.assertFalse(
            persistence.persist_estimated_capacity_if_ah_changed(
                "/tmp/does-not-exist/evcharger.ini",
                EnergySourceDefinition("primary_battery"),
                {"installed_capacity_ah": 200.0},
            )
        )

    def test_persist_estimated_capacity_updates_default_values_without_reformatting_comments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "config.ini")
            with open(config_path, "w", encoding="utf-8") as handle:
                handle.write(
                    "[DEFAULT]\n"
                    "# keep me\n"
                    "AutoBatteryCapacityEstimatedWh=4800\n"
                    "AutoBatteryCapacityEstimatedAh=100\n"
                    "[Other]\n"
                    "Value=1\n"
                )

            changed = persistence.persist_estimated_capacity_if_ah_changed(
                config_path,
                EnergySourceDefinition("primary_battery", estimated_capacity_ah=100.0),
                {
                    "usable_capacity_wh": 10240.0,
                    "installed_capacity_ah": 200.0,
                    "capacity_nominal_voltage_v": 51.2,
                    "capacity_cell_count": 16,
                },
            )

            with open(config_path, encoding="utf-8") as handle:
                text = handle.read()
            self.assertTrue(changed)
            self.assertIn("# keep me", text)
            self.assertIn("AutoBatteryCapacityEstimatedWh=10240", text)
            self.assertIn("AutoBatteryCapacityEstimatedAh=200", text)
            self.assertIn("AutoBatteryCapacityEstimatedNominalVoltage=51.2", text)
            self.assertIn("AutoBatteryCapacityEstimatedCellCount=16", text)
            self.assertLess(text.index("AutoBatteryCapacityEstimatedCellCount=16"), text.index("[Other]"))

    def test_persist_estimated_capacity_uses_structured_source_keys_and_handles_noop_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "config.ini")
            with open(config_path, "w", encoding="utf-8") as handle:
                handle.write("[DEFAULT]\n")

            source = EnergySourceDefinition("external", estimated_capacity_ah=None)
            payload = {
                "usable_capacity_wh": 9600.0,
                "installed_capacity_ah": 200.0,
                "capacity_nominal_voltage_v": 48.0,
                "capacity_cell_count": 15,
            }
            self.assertTrue(persistence.persist_estimated_capacity_if_ah_changed(config_path, source, payload))
            with open(config_path, encoding="utf-8") as handle:
                text = handle.read()
            self.assertIn("AutoEnergySource.external.CapacityEstimatedWh=9600", text)
            self.assertIn("AutoEnergySource.external.CapacityEstimatedAh=200", text)

            same_source = EnergySourceDefinition("external", estimated_capacity_ah=200.0)
            self.assertFalse(persistence.persist_estimated_capacity_if_ah_changed(config_path, same_source, payload))

    def test_upsert_default_values_handles_empty_values_and_empty_text(self) -> None:
        self.assertEqual(persistence._upsert_default_values("plain", {}), "plain")
        self.assertEqual(persistence._upsert_default_values("", {"A": "1"}), "A=1\n")
        self.assertEqual(persistence._upsert_default_values("[DEFAULT]\nB=2\n", {"A": "1"}), "[DEFAULT]\nB=2\nA=1\n")
        self.assertEqual(persistence._number_text(None), "")
        self.assertEqual(persistence._number_text("bad"), "")
        self.assertEqual(persistence._number_text(12.3456), "12.346")

    def test_persist_estimated_capacity_keeps_false_when_rendered_text_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "config.ini")
            with open(config_path, "w", encoding="utf-8") as handle:
                handle.write(
                    "[DEFAULT]\n"
                    "AutoBatteryCapacityEstimatedWh=10240\n"
                    "AutoBatteryCapacityEstimatedAh=200\n"
                    "AutoBatteryCapacityEstimatedNominalVoltage=51.2\n"
                    "AutoBatteryCapacityEstimatedCellCount=16\n"
                )
            source = EnergySourceDefinition("primary_battery")
            payload = {
                "usable_capacity_wh": 10240,
                "installed_capacity_ah": 200,
                "capacity_nominal_voltage_v": 51.2,
                "capacity_cell_count": 16,
            }

            self.assertFalse(persistence._persist_estimated_capacity(config_path, source, payload))

    def test_persist_estimated_capacity_surfaces_atomic_write_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "config.ini")
            with open(config_path, "w", encoding="utf-8") as handle:
                handle.write("[DEFAULT]\n")

            with patch.object(persistence, "write_text_atomically", side_effect=RuntimeError("boom")):
                with self.assertRaisesRegex(RuntimeError, "boom"):
                    persistence.persist_estimated_capacity_if_ah_changed(
                        config_path,
                        EnergySourceDefinition("primary_battery"),
                        {"usable_capacity_wh": 1, "installed_capacity_ah": 1},
                    )


if __name__ == "__main__":
    unittest.main()

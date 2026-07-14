# SPDX-License-Identifier: GPL-3.0-or-later
import os
import tempfile
import unittest
from unittest.mock import mock_open, patch

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

        for capacity_wh in (None, 0.0, -0.001):
            self.assertIsNone(
                persistence.configured_estimated_capacity_payload(
                    EnergySourceDefinition("battery", estimated_capacity_wh=capacity_wh)
                )
            )
        self.assertEqual(
            persistence.configured_estimated_capacity_payload(
                EnergySourceDefinition("battery", estimated_capacity_wh=0.5)
            ),
            {"usable_capacity_wh": 0.5, "usable_capacity_source": "config_estimated"},
        )

    def test_positive_payload_helpers_observe_strict_zero_boundaries(self) -> None:
        payload: dict[str, object] = {}
        for value in (None, 0.0, -1.0):
            persistence._add_positive_payload_value(payload, "float", value)
        for value in (None, 0, -1):
            persistence._add_positive_int_payload_value(payload, "int", value)
        self.assertEqual(payload, {})

        persistence._add_positive_payload_value(payload, "float", 0.001)
        persistence._add_positive_int_payload_value(payload, "int", 1)
        self.assertEqual(payload, {"float": 0.001, "int": 1})

    def test_persist_change_detection_uses_documented_ah_tolerance(self) -> None:
        source = EnergySourceDefinition("primary_battery", estimated_capacity_ah=100.0)
        with patch.object(persistence, "_persist_estimated_capacity", return_value=True) as persist:
            self.assertFalse(
                persistence.persist_estimated_capacity_if_ah_changed(
                    "config.ini", source, {"installed_capacity_ah": 100.000999}
                )
            )
            persist.assert_not_called()

            self.assertTrue(
                persistence.persist_estimated_capacity_if_ah_changed(
                    "config.ini", source, {"installed_capacity_ah": 100.001}
                )
            )
            persist.assert_called_once()

        exact_boundary_source = EnergySourceDefinition("primary_battery", estimated_capacity_ah=0.0)
        with patch.object(persistence, "_persist_estimated_capacity", return_value=True) as persist:
            self.assertTrue(
                persistence.persist_estimated_capacity_if_ah_changed(
                    "config.ini", exact_boundary_source, {"installed_capacity_ah": 0.001}
                )
            )
            persist.assert_called_once()

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
        self.assertEqual(persistence._default_section_bounds(["[DEFAULT]", "B=2"]), (1, 2))
        self.assertEqual(persistence._number_text(None), "")
        self.assertEqual(persistence._number_text("bad"), "")
        self.assertEqual(persistence._number_text(12.3456), "12.346")

    def test_ini_replacement_preserves_layout_and_section_boundaries(self) -> None:
        lines = [" # comment", "  A  =  old  ", "B=keep", "[Other]", "A=outside"]
        seen = persistence._replace_existing_default_values(lines, 0, 3, {"A": "new", "C": "3"})
        self.assertEqual(seen, {"A"})
        self.assertEqual(lines, [" # comment", "  A  =  new  ", "B=keep", "[Other]", "A=outside"])
        self.assertEqual(persistence._default_section_bounds(lines), (0, 3))
        self.assertEqual(persistence._default_section_bounds([" [DEFAULT] ", "A=1", "[Next] ; x"]), (1, 2))
        self.assertEqual(persistence._default_section_bounds([]), (0, 0))

        bounded_lines = ["A=before", "ignored=1", "B=keep", "A=old"]
        seen = persistence._replace_existing_default_values(bounded_lines, 2, 4, {"A": "new"})
        self.assertEqual(seen, {"A"})
        self.assertEqual(bounded_lines, ["A=before", "ignored=1", "B=keep", "A=new"])

    def test_ini_join_and_numeric_rendering_contracts_are_exact(self) -> None:
        self.assertEqual(persistence._join_ini_lines([], ""), "")
        self.assertEqual(persistence._join_ini_lines([], "old\n"), "\n")
        self.assertEqual(persistence._join_ini_lines(["A=1"], ""), "A=1\n")
        self.assertEqual(persistence._join_ini_lines(["A=1"], "old"), "A=1\n")
        self.assertEqual(persistence._join_ini_lines(["A=1"], "old\n"), "A=1\n")
        self.assertIsNone(persistence._positive_float(None))
        self.assertIsNone(persistence._positive_float(0))
        self.assertIsNone(persistence._positive_float(-0.001))
        self.assertEqual(persistence._positive_float(0.001), 0.001)
        self.assertEqual(persistence._number_text(12.0009), "12")
        self.assertEqual(persistence._number_text(0.001), "0.001")
        self.assertEqual(persistence._number_text(12.0011), "12.001")
        self.assertEqual(persistence._number_text(12.340), "12.34")

    def test_persist_rejects_blank_path_without_attempting_io(self) -> None:
        with patch.object(persistence, "_read_text") as read_text:
            self.assertFalse(
                persistence._persist_estimated_capacity(
                    "  ", EnergySourceDefinition("primary_battery"), {"installed_capacity_ah": 1}
                )
            )
        read_text.assert_not_called()

    def test_read_text_uses_path_protocol_utf8_and_propagates_read_errors(self) -> None:
        opener = mock_open(read_data="payload")
        with patch("builtins.open", opener):
            self.assertEqual(persistence._read_text(os.path.join("tmp", "config.ini")), "payload")
        opener.assert_called_once_with(os.path.join("tmp", "config.ini"), encoding="utf-8")

        with patch("builtins.open", mock_open()) as failing_open:
            failing_open.return_value.__enter__.return_value.read.side_effect = OSError("read failed")
            with self.assertRaisesRegex(OSError, "read failed"):
                persistence._read_text("config.ini")

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

# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact contracts for normalized external energy-source configuration."""

from __future__ import annotations

import unittest
from dataclasses import fields
from unittest.mock import patch

from venus_evcharger.energy.config import (
    _bool,
    _csv_items,
    _float_or_none,
    _float_value,
    _int_or_none,
    _text,
    load_energy_source_definitions,
    load_energy_source_settings,
)
from venus_evcharger.energy.models import ENERGY_SOURCE_CONNECTOR_TYPES, EnergySourceDefinition


class EnergyConfigContractTests(unittest.TestCase):
    def test_definition_contract_contains_no_raw_dbus_transport_knowledge(self) -> None:
        self.assertEqual(
            ENERGY_SOURCE_CONNECTOR_TYPES,
            frozenset({"template_http", "template_http_energy", "modbus", "command_json", "opendtu_http"}),
        )
        field_names = {field.name for field in fields(EnergySourceDefinition)}
        self.assertTrue(
            field_names.isdisjoint(
                {
                    "service_prefix",
                    "soc_path",
                    "capacity_wh_path",
                    "capacity_ah_path",
                    "voltage_path",
                    "battery_power_path",
                    "ac_power_path",
                    "pv_power_path",
                    "grid_interaction_path",
                    "operating_mode_path",
                }
            )
        )

    def test_scalar_normalizers_define_all_boundaries(self) -> None:
        self.assertEqual(_text(None), "")
        self.assertEqual(_text("  ", "fallback"), "fallback")
        self.assertEqual(_text(" value ", "fallback"), "value")
        self.assertEqual(_text(17), "17")
        self.assertEqual(_csv_items(None), ())
        self.assertEqual(_csv_items(" first, , second ,third "), ("first", "second", "third"))

        for value in (None, "", " ", "invalid", 0, "-1"):
            with self.subTest(float_or_none=value):
                self.assertIsNone(_float_or_none(value))
        self.assertEqual(_float_or_none("0.5"), 0.5)
        self.assertEqual(_float_value("2.5", 7.0), 2.5)
        self.assertEqual(_float_value("invalid", 7.0), 7.0)
        self.assertEqual(_float_value(None, 8.0), 8.0)

        for value in (None, "", "invalid", 0, "-1"):
            with self.subTest(int_or_none=value):
                self.assertIsNone(_int_or_none(value))
        self.assertEqual(_int_or_none("1.9"), 1)
        self.assertTrue(_bool(None, True))
        self.assertFalse(_bool(None, False))
        for value in ("1", "TRUE", " yes ", "On"):
            self.assertTrue(_bool(value, False))
        for value in ("0", "false", "no", "off", "unexpected", ""):
            self.assertFalse(_bool(value, True))

    def test_unconfigured_sources_do_not_create_an_implicit_dbus_source(self) -> None:
        raw_dbus_defaults = {
            "AutoBatteryService": "com.victronenergy.battery.legacy",
            "AutoBatteryServicePrefix": "com.victronenergy.battery",
            "AutoBatterySocPath": "/Soc",
            "AutoUseCombinedBatterySoc": "no",
        }

        self.assertEqual(load_energy_source_definitions(raw_dbus_defaults), ())
        self.assertEqual(load_energy_source_settings(raw_dbus_defaults), ((), False))

    def test_explicit_external_source_maps_only_transport_neutral_fields(self) -> None:
        definitions = load_energy_source_definitions(
            {
                "AutoEnergySources": "one,two",
                "AutoBatteryChemistry": "NMC",
                "AutoBatteryCapacityAutoEstimate": "0",
                "AutoEnergySource.one.Profile": "custom-profile",
                "AutoEnergySource.one.Role": "inverter",
                "AutoEnergySource.one.Type": "template_http_energy",
                "AutoEnergySource.one.ConfigPath": "/one.ini",
                "AutoEnergySource.one.Service": "one-source",
                "AutoEnergySource.one.UsableCapacityWh": "1234",
                "AutoEnergySource.one.Chemistry": "LTO",
                "AutoEnergySource.one.CapacityAutoEstimate": "1",
                "AutoEnergySource.one.CapacityEstimateMinSoc": "-1",
                "AutoEnergySource.one.CapacityStartupRecheckSeconds": "-2",
                "AutoEnergySource.one.CapacityEstimatedWh": "1200",
                "AutoEnergySource.one.CapacityEstimatedAh": "25",
                "AutoEnergySource.one.CapacityEstimatedNominalVoltage": "48",
                "AutoEnergySource.one.CapacityEstimatedCellCount": "15.9",
                "AutoEnergySource.two.Role": "invalid",
                "AutoEnergySource.two.Type": "dbus",
            }
        )

        self.assertEqual(
            definitions[0],
            EnergySourceDefinition(
                source_id="one",
                profile_name="custom-profile",
                role="inverter",
                connector_type="template_http",
                config_path="/one.ini",
                service_name="one-source",
                usable_capacity_wh=1234.0,
                battery_chemistry="lto",
                capacity_auto_estimate=True,
                capacity_estimate_min_soc=0.0,
                capacity_startup_recheck_seconds=0.0,
                estimated_capacity_wh=1200.0,
                estimated_capacity_ah=25.0,
                estimated_capacity_nominal_voltage_v=48.0,
                estimated_capacity_cell_count=15,
            ),
        )
        self.assertEqual(
            definitions[1],
            EnergySourceDefinition(
                source_id="two",
                battery_chemistry="nmc",
                capacity_auto_estimate=False,
            ),
        )

    def test_profile_overlay_contains_no_service_or_path_transport_details(self) -> None:
        profile_defaults = {
            "Profile": "canonical-profile",
            "Role": "hybrid-inverter",
            "Type": "modbus",
            "BatteryChemistry": "NMC",
            "CapacityAutoEstimate": False,
            "CapacityEstimateMinSoc": 77.0,
            "CapacityStartupRecheckSeconds": 44.0,
        }
        with patch(
            "venus_evcharger.energy.config.energy_source_profile_defaults",
            return_value=profile_defaults,
        ) as profile_lookup:
            definitions, use_combined_soc = load_energy_source_settings(
                {
                    "AutoEnergySources": "source",
                    "AutoEnergySource.source.Profile": "SYNTHETIC",
                }
            )

        profile_lookup.assert_called_once_with("synthetic")
        self.assertIs(use_combined_soc, True)
        self.assertEqual(
            definitions,
            (
                EnergySourceDefinition(
                    source_id="source",
                    profile_name="canonical-profile",
                    role="hybrid-inverter",
                    connector_type="modbus",
                    battery_chemistry="nmc",
                    capacity_auto_estimate=False,
                    capacity_estimate_min_soc=77.0,
                    capacity_startup_recheck_seconds=44.0,
                ),
            ),
        )


if __name__ == "__main__":
    unittest.main()

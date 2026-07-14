# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact contracts for normalized energy-source configuration."""

from __future__ import annotations

import unittest
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
from venus_evcharger.energy.models import EnergySourceDefinition


class EnergyConfigContractTests(unittest.TestCase):
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
            with self.subTest(true_value=value):
                self.assertTrue(_bool(value, False))
        for value in ("0", "false", "no", "off", "unexpected", ""):
            with self.subTest(false_value=value):
                self.assertFalse(_bool(value, True))

    def test_legacy_source_contract_covers_every_field(self) -> None:
        definitions, use_combined_soc = load_energy_source_settings(
            {
                "AutoBatteryService": " service ",
                "AutoBatteryServicePrefix": " prefix ",
                "AutoBatterySocPath": " /custom/soc ",
                "AutoBatteryCapacityWh": "5120",
                "AutoBatteryChemistry": " NMC ",
                "AutoBatteryCapacityAutoEstimate": "off",
                "AutoBatteryCapacityWhPath": " /capacity/wh ",
                "AutoBatteryCapacityAhPath": " /capacity/ah ",
                "AutoBatteryVoltagePath": " /voltage ",
                "AutoBatteryCapacityEstimateMinSoc": "-4",
                "AutoBatteryCapacityStartupRecheckSeconds": "0.5",
                "AutoBatteryCapacityEstimatedWh": "5000",
                "AutoBatteryCapacityEstimatedAh": "100",
                "AutoBatteryCapacityEstimatedNominalVoltage": "51.2",
                "AutoBatteryCapacityEstimatedCellCount": "16",
                "AutoBatteryPowerPath": " /battery/power ",
                "AutoBatteryAcPowerPath": " /ac/power ",
                "AutoBatteryPvPowerPath": " /pv/power ",
                "AutoBatteryGridInteractionPath": " /grid/power ",
                "AutoBatteryOperatingModePath": " /mode ",
                "AutoUseCombinedBatterySoc": "no",
            }
        )

        self.assertIs(use_combined_soc, False)
        self.assertEqual(
            definitions,
            (
                EnergySourceDefinition(
                    source_id="primary_battery",
                    profile_name="",
                    role="battery",
                    connector_type="dbus",
                    config_path="",
                    service_name="service",
                    service_prefix="prefix",
                    soc_path="/custom/soc",
                    usable_capacity_wh=5120.0,
                    battery_chemistry="nmc",
                    capacity_auto_estimate=False,
                    capacity_wh_path="/capacity/wh",
                    capacity_ah_path="/capacity/ah",
                    voltage_path="/voltage",
                    capacity_estimate_min_soc=0.0,
                    capacity_startup_recheck_seconds=0.5,
                    estimated_capacity_wh=5000.0,
                    estimated_capacity_ah=100.0,
                    estimated_capacity_nominal_voltage_v=51.2,
                    estimated_capacity_cell_count=16,
                    battery_power_path="/battery/power",
                    ac_power_path="/ac/power",
                    pv_power_path="/pv/power",
                    grid_interaction_path="/grid/power",
                    operating_mode_path="/mode",
                ),
            ),
        )

    def test_legacy_defaults_are_exact(self) -> None:
        self.assertEqual(
            load_energy_source_definitions({}),
            (
                EnergySourceDefinition(
                    source_id="primary_battery",
                    service_prefix="com.victronenergy.battery",
                ),
            ),
        )

    def test_profile_and_global_fallbacks_cover_every_field(self) -> None:
        definitions = load_energy_source_definitions(
            {
                "AutoEnergySources": " source ",
                "AutoBatteryChemistry": "NMC",
                "AutoBatteryCapacityAutoEstimate": "0",
                "AutoBatteryCapacityWhPath": "/global/wh",
                "AutoBatteryCapacityAhPath": "/global/ah",
                "AutoBatteryVoltagePath": "/global/voltage",
                "AutoBatteryCapacityEstimateMinSoc": "88",
                "AutoBatteryCapacityStartupRecheckSeconds": "42",
                "AutoEnergySource.source.Profile": " dbus-hybrid ",
                "AutoEnergySource.source.Service": "hybrid-service",
                "AutoEnergySource.source.ConfigPath": "/config.ini",
            }
        )

        self.assertEqual(
            definitions,
            (
                EnergySourceDefinition(
                    source_id="source",
                    profile_name="dbus-hybrid",
                    role="hybrid-inverter",
                    connector_type="dbus",
                    config_path="/config.ini",
                    service_name="hybrid-service",
                    service_prefix="",
                    soc_path="/Soc",
                    usable_capacity_wh=None,
                    battery_chemistry="nmc",
                    capacity_auto_estimate=False,
                    capacity_wh_path="/global/wh",
                    capacity_ah_path="/global/ah",
                    voltage_path="/global/voltage",
                    capacity_estimate_min_soc=88.0,
                    capacity_startup_recheck_seconds=42.0,
                    estimated_capacity_wh=None,
                    estimated_capacity_ah=None,
                    estimated_capacity_nominal_voltage_v=None,
                    estimated_capacity_cell_count=None,
                    battery_power_path="/Dc/0/Power",
                    ac_power_path="/Ac/Power",
                    pv_power_path="/Pv/Power",
                    grid_interaction_path="/Grid/Power",
                    operating_mode_path="/Mode",
                ),
            ),
        )

    def test_explicit_source_values_override_profile_and_global_defaults(self) -> None:
        definitions = load_energy_source_definitions(
            {
                "AutoEnergySources": "one,two",
                "AutoBatteryChemistry": "NMC",
                "AutoBatteryCapacityAutoEstimate": "0",
                "AutoEnergySource.one.Profile": "dbus-hybrid",
                "AutoEnergySource.one.Role": "inverter",
                "AutoEnergySource.one.Type": "template_http_energy",
                "AutoEnergySource.one.ConfigPath": "/one.ini",
                "AutoEnergySource.one.Service": "one-service",
                "AutoEnergySource.one.ServicePrefix": "one-prefix",
                "AutoEnergySource.one.SocPath": "/one/soc",
                "AutoEnergySource.one.UsableCapacityWh": "1234",
                "AutoEnergySource.one.Chemistry": "LTO",
                "AutoEnergySource.one.CapacityAutoEstimate": "1",
                "AutoEnergySource.one.CapacityWhPath": "/one/wh",
                "AutoEnergySource.one.CapacityAhPath": "/one/ah",
                "AutoEnergySource.one.VoltagePath": "/one/voltage",
                "AutoEnergySource.one.CapacityEstimateMinSoc": "-1",
                "AutoEnergySource.one.CapacityStartupRecheckSeconds": "-2",
                "AutoEnergySource.one.CapacityEstimatedWh": "1200",
                "AutoEnergySource.one.CapacityEstimatedAh": "25",
                "AutoEnergySource.one.CapacityEstimatedNominalVoltage": "48",
                "AutoEnergySource.one.CapacityEstimatedCellCount": "15.9",
                "AutoEnergySource.one.BatteryPowerPath": "/one/battery",
                "AutoEnergySource.one.AcPowerPath": "/one/ac",
                "AutoEnergySource.one.PvPowerPath": "/one/pv",
                "AutoEnergySource.one.GridInteractionPath": "/one/grid",
                "AutoEnergySource.one.OperatingModePath": "/one/mode",
                "AutoEnergySource.two.Role": "invalid",
                "AutoEnergySource.two.Type": "invalid",
            }
        )

        self.assertEqual(
            definitions[0],
            EnergySourceDefinition(
                source_id="one",
                profile_name="dbus-hybrid",
                role="inverter",
                connector_type="template_http",
                config_path="/one.ini",
                service_name="one-service",
                service_prefix="one-prefix",
                soc_path="/one/soc",
                usable_capacity_wh=1234.0,
                battery_chemistry="lto",
                capacity_auto_estimate=True,
                capacity_wh_path="/one/wh",
                capacity_ah_path="/one/ah",
                voltage_path="/one/voltage",
                capacity_estimate_min_soc=0.0,
                capacity_startup_recheck_seconds=0.0,
                estimated_capacity_wh=1200.0,
                estimated_capacity_ah=25.0,
                estimated_capacity_nominal_voltage_v=48.0,
                estimated_capacity_cell_count=15,
                battery_power_path="/one/battery",
                ac_power_path="/one/ac",
                pv_power_path="/one/pv",
                grid_interaction_path="/one/grid",
                operating_mode_path="/one/mode",
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

    def test_unknown_profile_name_is_canonicalized_without_profile_defaults(self) -> None:
        self.assertEqual(
            load_energy_source_definitions(
                {
                    "AutoEnergySources": "source",
                    "AutoEnergySource.source.Profile": " CUSTOM-PROFILE ",
                }
            ),
            (EnergySourceDefinition(source_id="source", profile_name="custom-profile"),),
        )

    def test_profile_overlay_contract_maps_every_supported_default(self) -> None:
        profile_defaults = {
            "Profile": "canonical-profile",
            "Role": "inverter",
            "Type": "modbus",
            "ServicePrefix": "profile-prefix",
            "SocPath": "/profile/soc",
            "BatteryPowerPath": "/profile/battery",
            "AcPowerPath": "/profile/ac",
            "PvPowerPath": "/profile/pv",
            "GridInteractionPath": "/profile/grid",
            "OperatingModePath": "/profile/mode",
            "BatteryChemistry": "NMC",
            "CapacityAutoEstimate": False,
            "CapacityWhPath": "/profile/wh",
            "CapacityAhPath": "/profile/ah",
            "VoltagePath": "/profile/voltage",
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
                    role="inverter",
                    connector_type="modbus",
                    service_prefix="profile-prefix",
                    soc_path="/profile/soc",
                    battery_chemistry="nmc",
                    capacity_auto_estimate=False,
                    capacity_wh_path="/profile/wh",
                    capacity_ah_path="/profile/ah",
                    voltage_path="/profile/voltage",
                    capacity_estimate_min_soc=77.0,
                    capacity_startup_recheck_seconds=44.0,
                    battery_power_path="/profile/battery",
                    ac_power_path="/profile/ac",
                    pv_power_path="/profile/pv",
                    grid_interaction_path="/profile/grid",
                    operating_mode_path="/profile/mode",
                ),
            ),
        )


if __name__ == "__main__":
    unittest.main()

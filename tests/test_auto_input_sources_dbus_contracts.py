# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from unittest.mock import MagicMock

from venus_evcharger.energy import EnergySourceDefinition
from venus_evcharger.inputs.helper.sources_dbus_common import _dbus_error_name, _is_expected_missing_dbus_error
from venus_evcharger.inputs.helper.sources_dbus_primary import (
    _AutoInputHelperSourceDbusPrimary,
    _energy_source_definitions,
)


class _NamedDbusError(RuntimeError):
    def __init__(self, name: object) -> None:
        super().__init__("dbus failure")
        self.name = name

    def get_dbus_name(self) -> object:
        return self.name


class _BrokenNamedDbusError(RuntimeError):
    def get_dbus_name(self) -> str:
        raise ValueError("broken metadata")


class _AttributedDbusError(RuntimeError):
    def __init__(self, name: object) -> None:
        super().__init__("failure")
        self._dbus_error_name = name


class TestAutoInputSourcesDbusContracts(unittest.TestCase):
    @staticmethod
    def _owner(**values: object) -> _AutoInputHelperSourceDbusPrimary:
        owner = object.__new__(_AutoInputHelperSourceDbusPrimary)
        for key, value in values.items():
            setattr(owner, key, value)
        return owner

    def test_dbus_error_name_supports_foreign_error_shapes(self) -> None:
        self.assertEqual(_dbus_error_name(_NamedDbusError("org.example.Error")), "org.example.Error")
        self.assertEqual(_dbus_error_name(_NamedDbusError(None)), "")
        self.assertEqual(_dbus_error_name(_BrokenNamedDbusError("broken")), "")

        attributed = _AttributedDbusError("org.example.Attribute")
        self.assertEqual(_dbus_error_name(attributed), "org.example.Attribute")
        attributed._dbus_error_name = 0
        self.assertEqual(_dbus_error_name(attributed), "0")
        self.assertEqual(_dbus_error_name(RuntimeError("failure")), "")
        self.assertTrue(_is_expected_missing_dbus_error(RuntimeError("UnknownObject at path")))
        self.assertFalse(_is_expected_missing_dbus_error(RuntimeError("transport timeout")))

    def test_energy_source_definition_filter_accepts_only_definition_iterables(self) -> None:
        first = EnergySourceDefinition("one")
        second = EnergySourceDefinition("two")
        self.assertEqual(_energy_source_definitions([first, object(), second]), (first, second))
        for value in (None, "text", b"bytes", bytearray(b"x"), {"source": first}, 1):
            self.assertEqual(_energy_source_definitions(value), ())

    def test_primary_source_defaults_match_energy_source_contract(self) -> None:
        owner = self._owner()
        self.assertEqual(owner._primary_energy_source_id(), "primary_battery")
        self.assertEqual(owner._default_primary_energy_source(), EnergySourceDefinition("primary_battery"))
        self.assertEqual(owner._configured_primary_energy_sources(), ())
        self.assertEqual(owner._primary_energy_source(), EnergySourceDefinition("primary_battery"))

    def test_primary_source_maps_all_runtime_overrides_exactly(self) -> None:
        owner = self._owner(
            auto_battery_service=" battery.service ",
            auto_battery_service_prefix=" battery.prefix ",
            auto_battery_soc_path="/CustomSoc",
            auto_battery_capacity_wh=12345,
            auto_battery_chemistry=" NMC ",
            auto_battery_capacity_auto_estimate=False,
            auto_battery_capacity_wh_path="/CapacityWh",
            auto_battery_capacity_ah_path="/CapacityAh",
            auto_battery_voltage_path="/Voltage",
            auto_battery_capacity_estimate_min_soc=88.5,
            auto_battery_capacity_startup_recheck_seconds=45.5,
            auto_battery_capacity_estimated_wh="12000.5",
            auto_battery_capacity_estimated_ah="250.5",
            auto_battery_capacity_estimated_nominal_voltage="51.2",
            auto_battery_capacity_estimated_cell_count="16",
            auto_battery_power_path="/BatteryPower",
            auto_battery_ac_power_path="/AcPower",
            auto_battery_pv_power_path="/PvPower",
            auto_battery_grid_interaction_path="/GridPower",
            auto_battery_operating_mode_path="/Mode",
        )
        self.assertEqual(
            owner._default_primary_energy_source(),
            EnergySourceDefinition(
                "primary_battery",
                role="battery",
                service_name=" battery.service ",
                service_prefix=" battery.prefix ",
                soc_path="/CustomSoc",
                usable_capacity_wh=12345.0,
                battery_chemistry="nmc",
                capacity_auto_estimate=False,
                capacity_wh_path="/CapacityWh",
                capacity_ah_path="/CapacityAh",
                voltage_path="/Voltage",
                capacity_estimate_min_soc=88.5,
                capacity_startup_recheck_seconds=45.5,
                estimated_capacity_wh=12000.5,
                estimated_capacity_ah=250.5,
                estimated_capacity_nominal_voltage_v=51.2,
                estimated_capacity_cell_count=16,
                battery_power_path="/BatteryPower",
                ac_power_path="/AcPower",
                pv_power_path="/PvPower",
                grid_interaction_path="/GridPower",
                operating_mode_path="/Mode",
            ),
        )

    def test_primary_source_normalizes_invalid_numeric_overrides(self) -> None:
        owner = self._owner(
            auto_battery_capacity_wh="invalid",
            auto_battery_capacity_estimate_min_soc=-1,
            auto_battery_capacity_startup_recheck_seconds=-1,
            auto_battery_capacity_estimated_wh=0,
            auto_battery_capacity_estimated_ah=-1,
            auto_battery_capacity_estimated_nominal_voltage="invalid",
            auto_battery_capacity_estimated_cell_count="invalid",
        )
        source = owner._default_primary_energy_source()
        self.assertIsNone(source.usable_capacity_wh)
        self.assertEqual(source.capacity_estimate_min_soc, 0.0)
        self.assertEqual(source.capacity_startup_recheck_seconds, 0.0)
        self.assertIsNone(source.estimated_capacity_wh)
        self.assertIsNone(source.estimated_capacity_ah)
        self.assertIsNone(source.estimated_capacity_nominal_voltage_v)
        self.assertIsNone(source.estimated_capacity_cell_count)
        self.assertIsNone(
            self._owner(auto_battery_capacity_estimated_cell_count=0)._primary_energy_estimated_capacity_cell_count()
        )
        self.assertEqual(
            self._owner(auto_battery_capacity_estimated_cell_count=1)._primary_energy_estimated_capacity_cell_count(),
            1,
        )
        zero_owner = self._owner(
            auto_battery_capacity_estimate_min_soc=0,
            auto_battery_capacity_startup_recheck_seconds=0,
        )
        self.assertEqual(zero_owner._primary_energy_capacity_estimate_min_soc(), 0.0)
        self.assertEqual(zero_owner._primary_energy_capacity_startup_recheck_seconds(), 0.0)
        self.assertEqual(owner._positive_float_or_none(0.001), 0.001)
        self.assertIsNone(owner._positive_float_or_none(None))

    def test_configured_primary_source_wins_over_generated_default(self) -> None:
        configured = EnergySourceDefinition("configured")
        owner = self._owner(auto_energy_sources=(configured, EnergySourceDefinition("second")))
        self.assertEqual(owner._configured_primary_energy_sources(), (configured, EnergySourceDefinition("second")))
        self.assertIs(owner._primary_energy_source(), configured)

    def test_readability_probes_handle_values_missing_paths_and_transport_errors(self) -> None:
        owner = self._owner(auto_battery_soc_path="/Soc")
        owner._get_dbus_value = MagicMock(return_value=42)
        self.assertTrue(owner._battery_service_has_soc("service"))
        owner._get_dbus_value.assert_called_once_with("service", "/Soc")
        owner._get_dbus_value.reset_mock()
        self.assertTrue(owner._energy_service_has_readable_field("service", "/Value"))
        owner._get_dbus_value.assert_called_once_with("service", "/Value")
        self.assertFalse(owner._energy_service_has_readable_field("service", ""))

        owner._get_dbus_value.return_value = None
        self.assertFalse(owner._battery_service_has_soc("service"))
        self.assertFalse(owner._energy_service_has_readable_field("service", "/Value"))

        owner._get_dbus_value.side_effect = RuntimeError("offline")
        self.assertFalse(owner._battery_service_has_soc("service"))
        self.assertFalse(owner._energy_service_has_readable_field("service", "/Value"))

    def test_source_readability_checks_every_supported_field(self) -> None:
        owner = self._owner()
        owner._energy_service_has_readable_field = MagicMock(side_effect=[False, False, False, True, False, False])
        source = EnergySourceDefinition(
            "source",
            soc_path="/Soc",
            battery_power_path="/Battery",
            ac_power_path="/Ac",
            pv_power_path="/Pv",
            grid_interaction_path="/Grid",
            operating_mode_path="/Mode",
        )
        self.assertTrue(owner._energy_source_has_readable_data(source, "service"))
        self.assertEqual(owner._energy_service_has_readable_field.call_count, 4)
        self.assertEqual(
            owner._energy_service_has_readable_field.call_args_list,
            [
                unittest.mock.call("service", "/Soc"),
                unittest.mock.call("service", "/Battery"),
                unittest.mock.call("service", "/Ac"),
                unittest.mock.call("service", "/Pv"),
            ],
        )

        owner._energy_service_has_readable_field = MagicMock(return_value=False)
        self.assertFalse(owner._energy_source_has_readable_data(source, "service"))
        self.assertEqual(owner._energy_service_has_readable_field.call_count, 6)


if __name__ == "__main__":
    unittest.main()

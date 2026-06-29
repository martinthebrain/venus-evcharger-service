# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
import unittest

from venus_evcharger.backend.modbus_profiles import (
    GenericModbusChargerProfile,
    ModbusEnableWrite,
    ModbusNumericWrite,
    ModbusPhaseWrite,
    ModbusReadField,
    _normalized_scale,
    _normalized_data_type,
    _normalized_register_type,
    _normalized_word_order,
    _optional_bool,
    _optional_float_value,
    _optional_text_value,
    _parsed_phase_selection_map,
    _required_current_write,
    _required_enable_write,
    _required_int,
    load_generic_modbus_charger_profile,
    load_modbus_charger_profile,
)
from venus_evcharger.backend.modbus_profile_models import _modbus_scalar


class _FakeModbusClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.scalar_values: dict[tuple[str, int, str, str], object] = {}

    def read_scalar(self, register_type: str, address: int, data_type: str, word_order: str = "big") -> object:
        return self.scalar_values[(register_type, address, data_type, word_order)]

    def write_single_coil(self, address: int, value: bool) -> None:
        self.calls.append(("coil", address, value))

    def write_single_register(self, address: int, value: int) -> None:
        self.calls.append(("single", address, value))

    def write_multiple_registers(self, address: int, values: tuple[int, ...]) -> None:
        self.calls.append(("multi", address, values))


class TestShellyWallboxBackendModbusProfiles(unittest.TestCase):
    @staticmethod
    def _parser(text: str) -> configparser.ConfigParser:
        parser = configparser.ConfigParser()
        parser.read_string(text)
        return parser

    def test_modbus_write_helpers_cover_validation_and_multi_register_paths(self) -> None:
        client = _FakeModbusClient()

        ModbusEnableWrite("coil", 10, 1, 0).write(client, True)
        self.assertEqual(client.calls[-1], ("coil", 10, True))
        ModbusEnableWrite("holding", 11, 1, 0).write(client, False)
        self.assertEqual(client.calls[-1], ("single", 11, 0))

        with self.assertRaisesRegex(ValueError, "require RegisterType=holding"):
            ModbusNumericWrite("coil", 20, "uint16", 1.0, "big").write(client, 13.0)

        ModbusNumericWrite("holding", 21, "uint32", 1.0, "big").write(client, 13.0)
        self.assertEqual(client.calls[-1], ("multi", 21, (0, 13)))

        with self.assertRaisesRegex(ValueError, "Unsupported phase selection"):
            ModbusPhaseWrite("holding", 30, "uint16", "big", {"P1": 1}).write(client, "P1_P2")

        ModbusPhaseWrite("coil", 31, "uint16", "big", {"P1": 1}).write(client, "P1")
        self.assertEqual(client.calls[-1], ("coil", 31, True))

        ModbusPhaseWrite("holding", 32, "uint32", "big", {"P1": 3}).write(client, "P1")
        self.assertEqual(client.calls[-1], ("multi", 32, (0, 3)))

    def test_generic_modbus_profile_helpers_cover_fallback_and_missing_phase_write(self) -> None:
        client = _FakeModbusClient()
        client.scalar_values[("holding", 1, "uint16", "big")] = 1

        profile = GenericModbusChargerProfile(
            profile_name="generic",
            supported_phase_selections=("P1",),
            state_enabled=None,
            state_current=None,
            state_phase_selection=ModbusReadField("holding", 1, "uint16", 1.0, "big", {1: "P1_P2"}),
            state_actual_current=None,
            state_power_watts=None,
            state_energy_kwh=None,
            state_status=None,
            state_fault=None,
            enable_write=ModbusEnableWrite("holding", 10, 1, 0),
            current_write=ModbusNumericWrite("holding", 20, "uint16", 1.0, "big"),
            phase_write=None,
        )

        state = profile.read_state(client, cached_enabled=True, cached_current_amps=16.0, cached_phase_selection="P1")
        self.assertEqual(state.phase_selection, "P1")

        with self.assertRaisesRegex(ValueError, "does not expose phase selection writes"):
            profile.set_phase_selection(client, "P1")

        profile = GenericModbusChargerProfile(
            profile_name="generic",
            supported_phase_selections=("P1",),
            state_enabled=None,
            state_current=ModbusReadField("holding", 11, "uint16", 10.0, "big", None),
            state_phase_selection=None,
            state_actual_current=None,
            state_power_watts=None,
            state_energy_kwh=None,
            state_status=None,
            state_fault=None,
            enable_write=None,
            current_write=ModbusNumericWrite("holding", 20, "uint16", 1.0, "big"),
            phase_write=None,
            enable_uses_current_write=True,
            enable_default_current_amps=6.0,
        )
        client.scalar_values[("holding", 11, "uint16", "big")] = 80
        state = profile.read_state(client, cached_enabled=True, cached_current_amps=16.0, cached_phase_selection="P1")
        self.assertEqual(state.phase_selection, "P1")
        self.assertTrue(state.enabled)
        self.assertEqual(state.current_amps, 8.0)
        with self.assertRaisesRegex(ValueError, "does not expose direct enable writes"):
            profile.set_enabled(client, True)

    def test_modbus_profile_config_helpers_cover_validation_edges(self) -> None:
        section = self._parser("[Field]\n")["Field"]
        with self.assertRaisesRegex(ValueError, "requires Address"):
            _required_int(section, "Address")

        section = self._parser("[Field]\nRegisterType=weird\n")["Field"]
        with self.assertRaisesRegex(ValueError, "Unsupported Modbus RegisterType"):
            _normalized_register_type(section)

        section = self._parser("[Field]\nRegisterType=input\n")["Field"]
        with self.assertRaisesRegex(ValueError, "require RegisterType=coil or holding"):
            _normalized_register_type(section, write=True)

        section = self._parser("[Field]\nDataType=odd\n")["Field"]
        with self.assertRaisesRegex(ValueError, "Unsupported Modbus DataType"):
            _normalized_data_type(section, "uint16")

        section = self._parser("[Field]\nWordOrder=middle\n")["Field"]
        with self.assertRaisesRegex(ValueError, "Unsupported Modbus WordOrder"):
            _normalized_word_order(section)
        section = self._parser("[Field]\nScale=0\n")["Field"]
        self.assertEqual(_normalized_scale(section), 1.0)

        with self.assertRaisesRegex(ValueError, "requires Map"):
            _parsed_phase_selection_map("")

        with self.assertRaisesRegex(ValueError, r"requires \[EnableWrite\]"):
            _required_enable_write(self._parser("[Adapter]\n"))
        with self.assertRaisesRegex(ValueError, r"requires \[CurrentWrite\]"):
            _required_current_write(self._parser("[Adapter]\n"))

        parser = self._parser(
            "[Adapter]\n"
            "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2\n"
            "[EnableWrite]\nRegisterType=holding\nAddress=1\n"
            "[CurrentWrite]\nRegisterType=holding\nAddress=2\n"
        )
        with self.assertRaisesRegex(ValueError, r"require \[PhaseWrite\]"):
            load_generic_modbus_charger_profile(parser)

        parser = self._parser(
            "[Adapter]\n"
            "[CurrentWrite]\nRegisterType=holding\nAddress=2\n"
        )
        with self.assertRaisesRegex(ValueError, r"requires \[EnableWrite\]"):
            load_generic_modbus_charger_profile(parser)

        parser = self._parser(
            "[Adapter]\n"
            "[Capabilities]\nEnableUsesCurrentWrite=1\nEnableDefaultCurrentAmps=8\n"
            "[CurrentWrite]\nRegisterType=holding\nAddress=2\n"
        )
        profile = load_generic_modbus_charger_profile(parser)
        self.assertIsNone(profile.enable_write)
        self.assertTrue(profile.enable_uses_current_write)
        self.assertEqual(profile.enable_default_current_amps, 8.0)

        parser = self._parser(
            "[Adapter]\n"
            "[Capabilities]\nEnableUsesCurrentWrite=1\nEnableDefaultCurrentAmps=0\n"
            "[CurrentWrite]\nRegisterType=holding\nAddress=2\n"
        )
        profile = load_generic_modbus_charger_profile(parser)
        self.assertEqual(profile.enable_default_current_amps, 6.0)

        parser = self._parser(
            "[Adapter]\nProfile=other\n"
            "[EnableWrite]\nRegisterType=holding\nAddress=1\n"
            "[CurrentWrite]\nRegisterType=holding\nAddress=2\n"
        )
        with self.assertRaisesRegex(ValueError, "Unsupported Modbus charger profile"):
            load_modbus_charger_profile(parser)

    def test_optional_value_helpers_cover_none_branches(self) -> None:
        client = _FakeModbusClient()
        self.assertEqual(_optional_float_value(None, client, 7.5), 7.5)
        self.assertIsNone(_optional_text_value(None, client))

        field = ModbusReadField("holding", 1, "uint16", 1.0, "big", None)
        client.scalar_values[("holding", 1, "uint16", "big")] = None
        self.assertIsNone(_optional_text_value(field, client))
        self.assertIsNone(_optional_bool(None))

    def test_modbus_scalar_rejects_non_primitive_values(self) -> None:
        with self.assertRaisesRegex(TypeError, "Unsupported Modbus scalar object"):
            _modbus_scalar(object())

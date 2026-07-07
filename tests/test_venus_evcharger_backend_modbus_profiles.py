# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
import unittest

import venus_evcharger.backend.modbus_profiles as modbus_profiles
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
    _optional_phase_write,
    _optional_read_field,
    _optional_text_value,
    _parsed_phase_selection_map,
    _parsed_value_map,
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

    @staticmethod
    def _case_sensitive_parser(text: str) -> configparser.ConfigParser:
        parser = configparser.ConfigParser()
        parser.optionxform = str  # type: ignore[method-assign]
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
        section = self._case_sensitive_parser("[Field]\nRegisterType=holding\n")["Field"]
        self.assertEqual(_normalized_register_type(section), "holding")
        section = self._case_sensitive_parser("[Field]\nRegisterType=input\n")["Field"]
        self.assertEqual(_normalized_register_type(section), "input")
        section = self._case_sensitive_parser("[Field]\nRegisterType=coil\n")["Field"]
        self.assertEqual(_normalized_register_type(section), "coil")
        section = self._case_sensitive_parser("[Field]\nRegisterType=discrete\n")["Field"]
        self.assertEqual(_normalized_register_type(section), "discrete")
        with self.assertRaises(ValueError) as discrete_write:
            _normalized_register_type(section, write=True)
        self.assertEqual(str(discrete_write.exception), "Modbus writes in [Field] require RegisterType=coil or holding")

        for data_type in ("bool", "uint16", "int16", "uint32", "int32", "float32"):
            section = self._case_sensitive_parser(f"[Field]\nDataType={data_type}\n")["Field"]
            self.assertEqual(_normalized_data_type(section, "uint16"), data_type)
        section = self._case_sensitive_parser("[Field]\n")["Field"]
        self.assertEqual(_normalized_data_type(section, "int32"), "int32")

        section = self._case_sensitive_parser("[Field]\nWordOrder=little\n")["Field"]
        self.assertEqual(_normalized_word_order(section), "little")
        section = self._case_sensitive_parser("[Field]\n")["Field"]
        self.assertEqual(_normalized_word_order(section), "big")

        section = self._case_sensitive_parser("[Field]\nScale=2.5\n")["Field"]
        self.assertEqual(_normalized_scale(section), 2.5)
        section = self._case_sensitive_parser("[Field]\n")["Field"]
        self.assertEqual(_normalized_scale(section), 1.0)

        self.assertEqual(_parsed_value_map("1:ready,2:paused:manual"), {1: "ready", 2: "paused:manual"})
        self.assertEqual(_parsed_phase_selection_map(":7,P1_P2:2"), {"P1": 7, "P1_P2": 2})
        with self.assertRaises(ValueError):
            _parsed_phase_selection_map("P1:1:2")
        with self.assertRaises(ValueError) as missing_phase_map:
            _parsed_phase_selection_map("")
        self.assertEqual(str(missing_phase_map.exception), "Modbus phase write requires Map")

        section = self._parser("[Field]\n")["Field"]
        with self.assertRaisesRegex(ValueError, "requires Address"):
            _required_int(section, "Address")

        section = self._case_sensitive_parser("[Field]\n")["Field"]
        with self.assertRaises(ValueError) as missing_register_type:
            _normalized_register_type(section)
        self.assertEqual(str(missing_register_type.exception), "Unsupported Modbus RegisterType '' in [Field]")

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

        with self.assertRaisesRegex(ValueError, r"requires \[EnableWrite\]"):
            _required_enable_write(self._parser("[Adapter]\n"))
        with self.assertRaises(ValueError) as missing_enable_write:
            _required_enable_write(self._case_sensitive_parser("[Adapter]\n"))
        self.assertEqual(str(missing_enable_write.exception), "Modbus charger backend requires [EnableWrite]")
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

    def test_modbus_profile_sections_are_parsed_into_exact_descriptors(self) -> None:
        parser = self._case_sensitive_parser("[StateEnabled]\nRegisterType=coil\nAddress=5\nValueMap=0:off,1:on:manual\n")
        field = _optional_read_field(parser, "StateEnabled")
        self.assertEqual(
            field,
            ModbusReadField("coil", 5, "bool", 1.0, "big", {0: "off", 1: "on:manual"}),
        )
        self.assertIsNone(_optional_read_field(parser, "Missing"))

        parser = self._case_sensitive_parser("[StatePower]\nRegisterType=holding\nAddress=6\nScale=10\nWordOrder=little\n")
        field = _optional_read_field(parser, "StatePower")
        self.assertEqual(field, ModbusReadField("holding", 6, "uint16", 10.0, "little", None))

        parser = self._case_sensitive_parser("[StateInput]\nRegisterType=discrete\nAddress=6\n")
        field = _optional_read_field(parser, "StateInput")
        self.assertEqual(field, ModbusReadField("discrete", 6, "bool", 1.0, "big", None))

        parser = self._case_sensitive_parser("[EnableWrite]\nRegisterType=holding\nAddress=7\nTrueValue=9\nFalseValue=3\n")
        self.assertEqual(_required_enable_write(parser), ModbusEnableWrite("holding", 7, 9, 3))

        parser = self._case_sensitive_parser("[EnableWrite]\nRegisterType=coil\nAddress=8\n")
        self.assertEqual(_required_enable_write(parser), ModbusEnableWrite("coil", 8, 1, 0))

        parser = self._case_sensitive_parser("[EnableWrite]\nRegisterType=input\nAddress=8\n")
        with self.assertRaises(ValueError) as input_enable_write:
            _required_enable_write(parser)
        self.assertEqual(str(input_enable_write.exception), "Modbus writes in [EnableWrite] require RegisterType=coil or holding")

        parser = self._case_sensitive_parser("[EnableWrite]\nRegisterType=holding\nAddress=8\nTrueValue=\nFalseValue=\n")
        self.assertEqual(_required_enable_write(parser), ModbusEnableWrite("holding", 8, 1, 0))

        parser = self._case_sensitive_parser("[CurrentWrite]\nRegisterType=holding\nAddress=9\nDataType=float32\nScale=2\nWordOrder=little\n")
        self.assertEqual(_required_current_write(parser), ModbusNumericWrite("holding", 9, "float32", 2.0, "little"))

        parser = self._case_sensitive_parser("[CurrentWrite]\nRegisterType=discrete\nAddress=9\n")
        with self.assertRaises(ValueError) as discrete_current_write:
            _required_current_write(parser)
        self.assertEqual(str(discrete_current_write.exception), "Modbus writes in [CurrentWrite] require RegisterType=coil or holding")

        with self.assertRaises(ValueError) as missing_current_write:
            _required_current_write(self._case_sensitive_parser("[Adapter]\n"))
        self.assertEqual(str(missing_current_write.exception), "Modbus charger backend requires [CurrentWrite]")

        parser = self._case_sensitive_parser("[PhaseWrite]\nRegisterType=holding\nAddress=10\nDataType=uint32\nWordOrder=little\nMap=P1:1,P1_P2:2\n")
        self.assertEqual(
            _optional_phase_write(parser),
            ModbusPhaseWrite("holding", 10, "uint32", "little", {"P1": 1, "P1_P2": 2}),
        )
        parser = self._case_sensitive_parser("[PhaseWrite]\nRegisterType=holding\nAddress=10\nMap=P1:1\n")
        self.assertEqual(_optional_phase_write(parser), ModbusPhaseWrite("holding", 10, "uint16", "big", {"P1": 1}))

        parser = self._case_sensitive_parser("[PhaseWrite]\nRegisterType=holding\nAddress=10\n")
        with self.assertRaises(ValueError) as missing_phase_write_map:
            _optional_phase_write(parser)
        self.assertEqual(str(missing_phase_write_map.exception), "Modbus phase write requires Map")

        parser = self._case_sensitive_parser("[PhaseWrite]\nRegisterType=input\nAddress=10\nMap=P1:1\n")
        with self.assertRaises(ValueError) as input_phase_write:
            _optional_phase_write(parser)
        self.assertEqual(str(input_phase_write.exception), "Modbus writes in [PhaseWrite] require RegisterType=coil or holding")
        self.assertIsNone(_optional_phase_write(self._case_sensitive_parser("[Adapter]\n")))

    def test_modbus_profile_capability_contracts_cover_flags_defaults_and_phase_fallbacks(self) -> None:
        self.assertEqual(modbus_profiles._configured_supported_phase_selections(None), "P1")
        self.assertFalse(modbus_profiles._enable_uses_current_write(None))
        self.assertEqual(modbus_profiles._enable_default_current_amps(None), 6.0)

        for token in ("1", "true", "yes", "on", " TRUE "):
            capabilities = self._case_sensitive_parser(f"[Capabilities]\nEnableUsesCurrentWrite={token}\n")["Capabilities"]
            self.assertTrue(modbus_profiles._enable_uses_current_write(capabilities), token)

        for token in ("0", "false", "no", "off", ""):
            capabilities = self._case_sensitive_parser(f"[Capabilities]\nEnableUsesCurrentWrite={token}\n")["Capabilities"]
            self.assertFalse(modbus_profiles._enable_uses_current_write(capabilities), token)

        capabilities = self._case_sensitive_parser("[Capabilities]\n")["Capabilities"]
        self.assertEqual(modbus_profiles._configured_supported_phase_selections(capabilities), "P1")
        self.assertFalse(modbus_profiles._enable_uses_current_write(capabilities))
        self.assertEqual(modbus_profiles._enable_default_current_amps(capabilities), 6.0)

        capabilities = self._case_sensitive_parser("[Capabilities]\nSupportedPhaseSelections=P1_P2,P1_P2_P3\nEnableDefaultCurrentAmps=11.5\n")["Capabilities"]
        self.assertEqual(modbus_profiles._configured_supported_phase_selections(capabilities), "P1_P2,P1_P2_P3")
        self.assertEqual(modbus_profiles._enable_default_current_amps(capabilities), 11.5)

        for invalid_default in ("0", "-1", "invalid"):
            capabilities = self._case_sensitive_parser(f"[Capabilities]\nEnableDefaultCurrentAmps={invalid_default}\n")["Capabilities"]
            self.assertEqual(modbus_profiles._enable_default_current_amps(capabilities), 6.0)
        capabilities = self._case_sensitive_parser("[Capabilities]\nEnableDefaultCurrentAmps=0.5\n")["Capabilities"]
        self.assertEqual(modbus_profiles._enable_default_current_amps(capabilities), 0.5)

        phase_write = ModbusPhaseWrite("holding", 10, "uint16", "big", {"P1_P2": 2, "P1_P2_P3": 3})
        capabilities = self._case_sensitive_parser("[Capabilities]\nSupportedPhaseSelections=P1,P1_P2\n")["Capabilities"]
        self.assertEqual(modbus_profiles._mapped_supported_phase_selections(("P1", "P1_P2"), phase_write), ("P1_P2",))
        self.assertEqual(modbus_profiles._supported_phase_selections(capabilities, phase_write), ("P1_P2",))

        capabilities = self._case_sensitive_parser("[Capabilities]\nSupportedPhaseSelections=P1_P2_P3\n")["Capabilities"]
        self.assertEqual(modbus_profiles._supported_phase_selections(capabilities, phase_write), ("P1_P2_P3",))
        self.assertEqual(modbus_profiles._supported_phase_selections(None, phase_write), ("P1_P2", "P1_P2_P3"))
        self.assertEqual(modbus_profiles._supported_phase_selections(None, None), ("P1",))
        empty_phase_write = ModbusPhaseWrite("holding", 10, "uint16", "big", {})
        self.assertEqual(modbus_profiles._supported_phase_selections(None, empty_phase_write), ("P1",))

        modbus_profiles._validate_supported_phase_writes(("P1",), None)
        with self.assertRaises(ValueError) as missing_phase_write:
            modbus_profiles._validate_supported_phase_writes(("P1", "P1_P2"), None)
        self.assertEqual(str(missing_phase_write.exception), "Multi-phase Modbus charger profiles require [PhaseWrite]")

        parser = self._case_sensitive_parser("[CurrentWrite]\nRegisterType=holding\nAddress=1\n")
        self.assertIsNone(modbus_profiles._validated_enable_section(parser, True))
        parser = self._case_sensitive_parser("[EnableWrite]\nRegisterType=holding\nAddress=1\n")
        self.assertIs(modbus_profiles._validated_enable_section(parser, False), parser["EnableWrite"])
        with self.assertRaises(ValueError) as missing_enable_write:
            modbus_profiles._validated_enable_section(self._case_sensitive_parser("[Adapter]\n"), False)
        self.assertEqual(str(missing_enable_write.exception), "Modbus charger backend requires [EnableWrite]")

    def test_load_generic_modbus_profile_maps_every_runtime_field(self) -> None:
        parser = self._case_sensitive_parser(
            "[Adapter]\nProfile=generic\n"
            "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2,P1_P2_P3\nEnableUsesCurrentWrite=0\nEnableDefaultCurrentAmps=9\n"
            "[StateEnabled]\nRegisterType=coil\nAddress=1\n"
            "[StateCurrent]\nRegisterType=holding\nAddress=2\nScale=10\n"
            "[StatePhase]\nRegisterType=holding\nAddress=3\nValueMap=1:P1,2:P1_P2\n"
            "[StateActualCurrent]\nRegisterType=input\nAddress=4\nScale=100\n"
            "[StatePower]\nRegisterType=input\nAddress=5\nDataType=int32\nScale=1\nWordOrder=little\n"
            "[StateEnergy]\nRegisterType=input\nAddress=6\nDataType=uint32\nScale=1000\n"
            "[StateStatus]\nRegisterType=holding\nAddress=7\nValueMap=1:ready\n"
            "[StateFault]\nRegisterType=holding\nAddress=8\nValueMap=2:fault\n"
            "[EnableWrite]\nRegisterType=holding\nAddress=9\nTrueValue=1\nFalseValue=0\n"
            "[CurrentWrite]\nRegisterType=holding\nAddress=10\nDataType=uint16\nScale=10\n"
            "[PhaseWrite]\nRegisterType=holding\nAddress=11\nDataType=uint16\nMap=P1:1,P1_P2:2\n"
        )

        profile = load_modbus_charger_profile(parser)

        self.assertEqual(profile.profile_name, "generic")
        self.assertEqual(profile.supported_phase_selections, ("P1", "P1_P2"))
        self.assertEqual(profile.state_enabled, ModbusReadField("coil", 1, "bool", 1.0, "big", None))
        self.assertEqual(profile.state_current, ModbusReadField("holding", 2, "uint16", 10.0, "big", None))
        self.assertEqual(profile.state_phase_selection, ModbusReadField("holding", 3, "uint16", 1.0, "big", {1: "P1", 2: "P1_P2"}))
        self.assertEqual(profile.state_actual_current, ModbusReadField("input", 4, "uint16", 100.0, "big", None))
        self.assertEqual(profile.state_power_watts, ModbusReadField("input", 5, "int32", 1.0, "little", None))
        self.assertEqual(profile.state_energy_kwh, ModbusReadField("input", 6, "uint32", 1000.0, "big", None))
        self.assertEqual(profile.state_status, ModbusReadField("holding", 7, "uint16", 1.0, "big", {1: "ready"}))
        self.assertEqual(profile.state_fault, ModbusReadField("holding", 8, "uint16", 1.0, "big", {2: "fault"}))
        self.assertEqual(profile.enable_write, ModbusEnableWrite("holding", 9, 1, 0))
        self.assertEqual(profile.current_write, ModbusNumericWrite("holding", 10, "uint16", 10.0, "big"))
        self.assertEqual(profile.phase_write, ModbusPhaseWrite("holding", 11, "uint16", "big", {"P1": 1, "P1_P2": 2}))
        self.assertFalse(profile.enable_uses_current_write)
        self.assertEqual(profile.enable_default_current_amps, 9.0)

    def test_load_modbus_profile_rejects_non_generic_profile_with_exact_key(self) -> None:
        parser = self._case_sensitive_parser(
            "[Adapter]\nProfile=other\n"
            "[EnableWrite]\nRegisterType=holding\nAddress=1\n"
            "[CurrentWrite]\nRegisterType=holding\nAddress=2\n"
        )
        with self.assertRaises(ValueError) as unsupported_profile:
            load_modbus_charger_profile(parser)
        self.assertEqual(str(unsupported_profile.exception), "Unsupported Modbus charger profile 'other'")

        parser = self._case_sensitive_parser(
            "[DEFAULT]\nProfile=other\n"
            "[EnableWrite]\nRegisterType=holding\nAddress=1\n"
            "[CurrentWrite]\nRegisterType=holding\nAddress=2\n"
        )
        with self.assertRaises(ValueError) as unsupported_default_profile:
            load_modbus_charger_profile(parser)
        self.assertEqual(str(unsupported_default_profile.exception), "Unsupported Modbus charger profile 'other'")

        parser = self._case_sensitive_parser(
            "[EnableWrite]\nRegisterType=holding\nAddress=1\n"
            "[CurrentWrite]\nRegisterType=holding\nAddress=2\n"
        )
        self.assertEqual(load_modbus_charger_profile(parser).profile_name, "generic")

        parser = self._case_sensitive_parser(
            "[Adapter]\nProfile= \n"
            "[EnableWrite]\nRegisterType=holding\nAddress=1\n"
            "[CurrentWrite]\nRegisterType=holding\nAddress=2\n"
        )
        self.assertEqual(load_modbus_charger_profile(parser).profile_name, "generic")

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

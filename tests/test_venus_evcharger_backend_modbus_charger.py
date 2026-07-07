# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
import configparser
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from venus_evcharger.backend.models import ChargerState
from venus_evcharger.backend.modbus_charger import ModbusChargerBackend
from venus_evcharger.backend.modbus_transport import ModbusTransportSettings
from venus_evcharger.backend.native_modbus_backend import (
    _native_modbus_transport_factory,
    create_modbus_transport,
    native_modbus_client,
)
from venus_evcharger.backend.modbus_transport import ModbusRequest


class _FakeModbusTransport:
    def __init__(self) -> None:
        self.requests: list[ModbusRequest] = []
        self.coils: dict[int, bool] = {10: True}
        self.discrete_inputs: dict[int, bool] = {}
        self.holding_registers: dict[int, int] = {
            11: 160,
            30: 160,
        }
        self.input_registers: dict[int, int] = {
            12: 3,
            13: 128,
            14: 0,
            15: 2950,
            16: 0,
            17: 712,
            18: 1,
            19: 0,
        }

    def _read_bits(self, request: ModbusRequest) -> bytes:
        function_code = request.function_code
        address = int.from_bytes(request.payload[0:2], "big")
        count = int.from_bytes(request.payload[2:4], "big")
        source = self.coils if function_code == 0x01 else self.discrete_inputs
        values = [bool(source.get(address + index, False)) for index in range(count)]
        byte_value = 0
        for index, value in enumerate(values):
            if value:
                byte_value |= 1 << index
        return bytes((function_code, 1, byte_value))

    def _read_registers(self, request: ModbusRequest) -> bytes:
        function_code = request.function_code
        address = int.from_bytes(request.payload[0:2], "big")
        count = int.from_bytes(request.payload[2:4], "big")
        source = self.holding_registers if function_code == 0x03 else self.input_registers
        registers = [int(source.get(address + index, 0)) for index in range(count)]
        payload = b"".join(register.to_bytes(2, "big") for register in registers)
        return bytes((function_code, len(payload))) + payload

    def _write_coil(self, request: ModbusRequest) -> bytes:
        address = int.from_bytes(request.payload[0:2], "big")
        encoded = int.from_bytes(request.payload[2:4], "big")
        self.coils[address] = encoded == 0xFF00
        return bytes((request.function_code,)) + request.payload

    def _write_register(self, request: ModbusRequest) -> bytes:
        address = int.from_bytes(request.payload[0:2], "big")
        value = int.from_bytes(request.payload[2:4], "big")
        self.holding_registers[address] = value
        return bytes((request.function_code,)) + request.payload

    def exchange(self, request: ModbusRequest, *, timeout_seconds: float) -> bytes:
        del timeout_seconds
        self.requests.append(request)
        function_code = request.function_code
        if function_code in {0x01, 0x02}:
            return self._read_bits(request)
        if function_code in {0x03, 0x04}:
            return self._read_registers(request)
        if function_code == 0x05:
            return self._write_coil(request)
        if function_code == 0x06:
            return self._write_register(request)
        raise AssertionError(f"Unexpected Modbus function code {function_code}")


class _FakeProfile:
    def __init__(self) -> None:
        self.profile_name = "fake-profile"
        self.supported_phase_selections = ("P1", "P1_P2")
        self.enable_write: object | None = object()
        self.enable_uses_current_write = False
        self.enable_default_current_amps = 6.0
        self.next_state = ChargerState(enabled=True, current_amps=0.5, phase_selection="P1_P2")
        self.read_calls: list[dict[str, object]] = []
        self.enabled_calls: list[object] = []
        self.current_calls: list[float] = []
        self.phase_calls: list[str] = []

    def read_state(self, client: object, **kwargs: object) -> ChargerState:
        self.read_calls.append({"client": client, **kwargs})
        return self.next_state

    def set_enabled(self, client: object, enabled: object) -> None:
        self.enabled_calls.append(enabled)

    def set_current(self, client: object, amps: float) -> None:
        self.current_calls.append(float(amps))

    def set_phase_selection(self, client: object, selection: str) -> None:
        self.phase_calls.append(selection)


class TestShellyWallboxBackendModbusCharger(unittest.TestCase):
    @staticmethod
    def _service() -> SimpleNamespace:
        return SimpleNamespace(
            shelly_request_timeout_seconds=2.0,
        )

    @staticmethod
    def _write_config(directory: str, content: str) -> str:
        path = Path(directory) / "modbus-charger.ini"
        path.write_text(content, encoding="utf-8")
        return str(path)

    @staticmethod
    def _transport_settings() -> ModbusTransportSettings:
        return ModbusTransportSettings(
            transport_kind="tcp",
            unit_id=7,
            timeout_seconds=2.5,
            host="192.168.1.40",
            port=502,
            device=None,
            baudrate=9600,
            bytesize=8,
            parity="N",
            stopbits=1,
            serial_port_owner="none",
            serial_port_owner_stop_command=None,
            serial_port_owner_start_command=None,
            serial_retry_count=1,
            serial_retry_delay_seconds=0.1,
        )

    def _backend_with_profile(self, profile: _FakeProfile) -> ModbusChargerBackend:
        backend = object.__new__(ModbusChargerBackend)
        backend.service = self._service()
        backend.config_path = "/tmp/fake.ini"
        backend.transport_settings = self._transport_settings()
        backend.profile = profile
        backend.settings = SimpleNamespace(
            transport_settings=backend.transport_settings,
            profile_name=profile.profile_name,
            supported_phase_selections=profile.supported_phase_selections,
        )
        backend._enabled_state_cache = False
        backend._current_amps_cache = 7.5
        backend._resume_current_amps_cache = None
        backend._phase_selection_cache = "P1"
        backend._transport = None
        backend._client_cache = object()
        return backend

    def test_constructor_contracts_preserve_service_settings_and_initial_caches(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string("[Adapter]\nType=modbus_charger\n")
        settings = self._transport_settings()
        profile = _FakeProfile()
        service = SimpleNamespace(requested_phase_selection="P1_P2")

        with patch("venus_evcharger.backend.modbus_charger.load_required_backend_config", return_value=parser) as load_config, patch(
            "venus_evcharger.backend.modbus_charger.load_modbus_transport_settings",
            return_value=settings,
        ) as load_transport, patch(
            "venus_evcharger.backend.modbus_charger.load_modbus_charger_profile",
            return_value=profile,
        ) as load_profile:
            backend = ModbusChargerBackend(service, config_path=" /tmp/modbus.ini ")

        load_config.assert_called_once_with("/tmp/modbus.ini", "Modbus charger")
        load_transport.assert_called_once_with(parser, service)
        load_profile.assert_called_once_with(parser)
        self.assertIs(backend.service, service)
        self.assertEqual(backend.config_path, "/tmp/modbus.ini")
        self.assertIs(backend.transport_settings, settings)
        self.assertIs(backend.settings.transport_settings, settings)
        self.assertEqual(backend.settings.profile_name, "fake-profile")
        self.assertEqual(backend.settings.supported_phase_selections, ("P1", "P1_P2"))
        self.assertIsNone(backend._enabled_state_cache)
        self.assertIsNone(backend._current_amps_cache)
        self.assertIsNone(backend._resume_current_amps_cache)
        self.assertEqual(backend._phase_selection_cache, "P1_P2")
        self.assertIsNone(backend._transport)
        self.assertIsNone(backend._client_cache)

    def test_default_constructor_config_path_error_names_empty_path_and_label(self) -> None:
        with self.assertRaises(FileNotFoundError) as missing_config:
            ModbusChargerBackend(self._service())

        self.assertEqual(str(missing_config.exception), "Modbus charger config not found: ")

    def test_client_creation_uses_transport_settings_contract(self) -> None:
        profile = _FakeProfile()
        backend = self._backend_with_profile(profile)
        backend._client_cache = None
        backend._transport = None
        created_transport = _FakeModbusTransport()

        with patch("venus_evcharger.backend.modbus_charger.create_modbus_transport", return_value=created_transport) as create:
            client = backend._client()

        create.assert_called_once_with(backend.transport_settings)
        self.assertIs(backend._transport, created_transport)
        self.assertIs(backend._client_cache, client)
        self.assertIs(backend._client(), client)

    def test_read_state_passes_and_updates_cache_contracts(self) -> None:
        profile = _FakeProfile()
        backend = self._backend_with_profile(profile)
        client = object()

        with patch.object(backend, "_client", return_value=client):
            state = backend.read_charger_state()

        self.assertIs(state, profile.next_state)
        self.assertEqual(
            profile.read_calls,
            [{
                "client": client,
                "cached_enabled": False,
                "cached_current_amps": 7.5,
                "cached_phase_selection": "P1",
            }],
        )
        self.assertIs(backend._enabled_state_cache, True)
        self.assertEqual(backend._current_amps_cache, 0.5)
        self.assertEqual(backend._resume_current_amps_cache, 0.5)
        self.assertEqual(backend._phase_selection_cache, "P1_P2")

        profile.next_state = ChargerState(enabled=False, current_amps=0.0, phase_selection="P1_P2_P3")
        with patch.object(backend, "_client", return_value=client):
            backend.read_charger_state()
        self.assertIs(backend._enabled_state_cache, False)
        self.assertEqual(backend._current_amps_cache, 0.0)
        self.assertEqual(backend._resume_current_amps_cache, 0.5)
        self.assertEqual(backend._phase_selection_cache, "P1")

    def test_set_enabled_current_and_phase_update_profile_and_caches(self) -> None:
        profile = _FakeProfile()
        backend = self._backend_with_profile(profile)
        client = object()

        with patch.object(backend, "_client", return_value=client):
            backend.set_enabled(True)
        self.assertEqual(profile.enabled_calls, [True])
        self.assertIs(backend._enabled_state_cache, True)

        profile.enable_uses_current_write = True
        with patch.object(backend, "_client", return_value=client):
            backend.set_enabled(False)
        self.assertEqual(profile.enabled_calls, [True, False])
        self.assertEqual(profile.current_calls, [])
        self.assertIs(backend._enabled_state_cache, False)

        profile.enable_write = None
        profile.enable_uses_current_write = True
        backend._resume_current_amps_cache = 0.5
        with patch.object(backend, "_client", return_value=client):
            backend.set_enabled(True)
        self.assertEqual(profile.current_calls, [0.5])
        self.assertIs(backend._enabled_state_cache, True)
        self.assertEqual(backend._current_amps_cache, 0.5)
        self.assertEqual(backend._resume_current_amps_cache, 0.5)

        backend._resume_current_amps_cache = None
        backend._current_amps_cache = 0.5
        with patch.object(backend, "_client", return_value=client):
            backend.set_enabled(True)
        self.assertEqual(profile.current_calls[-1], 0.5)
        self.assertEqual(backend._resume_current_amps_cache, 0.5)

        with patch.object(backend, "_client", return_value=client):
            backend.set_current(0.0)
        self.assertEqual(profile.current_calls[-1], 0.0)
        self.assertEqual(backend._current_amps_cache, 0.0)
        self.assertEqual(backend._resume_current_amps_cache, 0.5)

        backend._resume_current_amps_cache = None
        with patch.object(backend, "_client", return_value=client):
            backend.set_current(0.5)
        self.assertEqual(profile.current_calls[-1], 0.5)
        self.assertEqual(backend._current_amps_cache, 0.5)
        self.assertEqual(backend._resume_current_amps_cache, 0.5)

        with patch.object(backend, "_client", return_value=client):
            backend.set_phase_selection(cast(Any, None))
            backend.set_phase_selection("P1_P2")
        self.assertEqual(profile.phase_calls[-2:], ["P1", "P1_P2"])
        self.assertEqual(backend._phase_selection_cache, "P1_P2")

        p2_only_profile = _FakeProfile()
        p2_only_profile.supported_phase_selections = ("P1_P2",)
        p2_only_backend = self._backend_with_profile(p2_only_profile)
        with patch.object(p2_only_backend, "_client", return_value=client):
            p2_only_backend.set_phase_selection(cast(Any, None))
        self.assertEqual(p2_only_profile.phase_calls, ["P1_P2"])
        self.assertEqual(p2_only_backend._phase_selection_cache, "P1_P2")

        with self.assertRaises(ValueError) as phase_error:
            backend.set_phase_selection("P1_P2_P3")
        self.assertEqual(str(phase_error.exception), "Unsupported phase selection 'P1_P2_P3' for Modbus charger backend")

    def test_cached_positive_current_contract(self) -> None:
        self.assertIsNone(ModbusChargerBackend._cached_positive_current(None))
        self.assertIsNone(ModbusChargerBackend._cached_positive_current(0.0))
        self.assertEqual(ModbusChargerBackend._cached_positive_current(0.5), 0.5)

    def test_read_charger_state_maps_generic_modbus_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=modbus_charger\nProfile=generic\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.40\nPort=502\nUnitId=7\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2_P3\n"
                "[StateEnabled]\nRegisterType=coil\nAddress=10\n"
                "[StateCurrent]\nRegisterType=holding\nAddress=11\nDataType=uint16\nScale=10\n"
                "[StatePhase]\nRegisterType=input\nAddress=12\nDataType=uint16\nValueMap=1:P1,3:P1_P2_P3\n"
                "[StateActualCurrent]\nRegisterType=input\nAddress=13\nDataType=uint16\nScale=10\n"
                "[StatePower]\nRegisterType=input\nAddress=14\nDataType=uint32\n"
                "[StateEnergy]\nRegisterType=input\nAddress=16\nDataType=uint32\nScale=10\n"
                "[StateStatus]\nRegisterType=input\nAddress=18\nDataType=uint16\nValueMap=1:charging\n"
                "[StateFault]\nRegisterType=input\nAddress=19\nDataType=uint16\nValueMap=0:none\n"
                "[EnableWrite]\nRegisterType=coil\nAddress=20\nTrueValue=1\nFalseValue=0\n"
                "[CurrentWrite]\nRegisterType=holding\nAddress=30\nDataType=uint16\nScale=10\n"
                "[PhaseWrite]\nRegisterType=holding\nAddress=31\nDataType=uint16\nMap=P1:1,P1_P2_P3:3\n",
            )
            fake_transport = _FakeModbusTransport()
            with patch(
                "venus_evcharger.backend.modbus_charger.create_modbus_transport",
                return_value=fake_transport,
            ):
                backend = ModbusChargerBackend(self._service(), config_path=config_path)

                state = backend.read_charger_state()

            self.assertTrue(state.enabled)
            self.assertEqual(state.current_amps, 16.0)
            self.assertEqual(state.phase_selection, "P1_P2_P3")
            self.assertEqual(state.actual_current_amps, 12.8)
            self.assertEqual(state.power_w, 2950.0)
            self.assertEqual(state.energy_kwh, 71.2)
            self.assertEqual(state.status_text, "charging")
            self.assertEqual(state.fault_text, "none")

    def test_modbus_charger_writes_enable_current_and_phase(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=modbus_charger\nProfile=generic\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.40\nPort=502\nUnitId=7\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2_P3\n"
                "[EnableWrite]\nRegisterType=coil\nAddress=20\nTrueValue=1\nFalseValue=0\n"
                "[CurrentWrite]\nRegisterType=holding\nAddress=30\nDataType=uint16\nScale=10\n"
                "[PhaseWrite]\nRegisterType=holding\nAddress=31\nDataType=uint16\nMap=P1:1,P1_P2_P3:3\n",
            )
            fake_transport = _FakeModbusTransport()
            with patch(
                "venus_evcharger.backend.modbus_charger.create_modbus_transport",
                return_value=fake_transport,
            ):
                backend = ModbusChargerBackend(self._service(), config_path=config_path)

                backend.set_enabled(False)
                backend.set_current(13.5)
                backend.set_phase_selection("P1_P2_P3")

            self.assertEqual(fake_transport.coils[20], False)
            self.assertEqual(fake_transport.holding_registers[30], 135)
            self.assertEqual(fake_transport.holding_registers[31], 3)

    def test_multi_phase_modbus_profile_requires_phase_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=modbus_charger\nProfile=generic\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.40\nPort=502\nUnitId=7\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2_P3\n"
                "[EnableWrite]\nRegisterType=coil\nAddress=20\nTrueValue=1\nFalseValue=0\n"
                "[CurrentWrite]\nRegisterType=holding\nAddress=30\nDataType=uint16\nScale=10\n",
            )

            with self.assertRaises(ValueError):
                ModbusChargerBackend(self._service(), config_path=config_path)

    def test_modbus_charger_covers_missing_config_phase_fallback_and_invalid_set_selection(self) -> None:
        with self.assertRaises(FileNotFoundError):
            ModbusChargerBackend(self._service(), config_path="/tmp/does-not-exist.ini")

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=modbus_charger\nProfile=generic\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.40\nPort=502\nUnitId=7\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2\n"
                "[EnableWrite]\nRegisterType=coil\nAddress=20\nTrueValue=1\nFalseValue=0\n"
                "[CurrentWrite]\nRegisterType=holding\nAddress=30\nDataType=uint16\nScale=10\n"
                "[PhaseWrite]\nRegisterType=holding\nAddress=31\nDataType=uint16\nMap=P1:1,P1_P2:2\n",
            )
            fake_transport = _FakeModbusTransport()
            service = SimpleNamespace(
                shelly_request_timeout_seconds=2.0,
                requested_phase_selection="P1_P2_P3",
            )
            with patch(
                "venus_evcharger.backend.modbus_charger.create_modbus_transport",
                return_value=fake_transport,
            ):
                backend = ModbusChargerBackend(service, config_path=config_path)

                self.assertEqual(backend._phase_selection_cache, "P1")
                with self.assertRaisesRegex(ValueError, "Unsupported phase selection"):
                    backend.set_phase_selection("P1_P2_P3")

    def test_modbus_charger_reuses_preseeded_transport_when_client_is_created(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=modbus_charger\nProfile=generic\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.40\nPort=502\nUnitId=7\n"
                "[EnableWrite]\nRegisterType=coil\nAddress=20\nTrueValue=1\nFalseValue=0\n"
                "[CurrentWrite]\nRegisterType=holding\nAddress=30\nDataType=uint16\nScale=10\n",
            )
            backend = ModbusChargerBackend(self._service(), config_path=config_path)
            backend._transport = _FakeModbusTransport()

            with patch("venus_evcharger.backend.modbus_charger.create_modbus_transport") as create_transport:
                client = backend._client()

            self.assertIs(client, backend._client_cache)
            create_transport.assert_not_called()

    def test_native_modbus_backend_contracts_cover_invalid_cache_and_transport_factory(self) -> None:
        with self.assertRaisesRegex(TypeError, "Native Modbus client cache must hold ModbusClient"):
            native_modbus_client(SimpleNamespace(_client_cache=object()))

        backend = SimpleNamespace()
        with patch("types.create_modbus_transport", object(), create=True):
            self.assertIs(_native_modbus_transport_factory(backend), create_modbus_transport)

        with patch("types.create_modbus_transport", return_value=object(), create=True):
            factory = _native_modbus_transport_factory(backend)
            with self.assertRaisesRegex(TypeError, "Modbus transport factory returned object"):
                factory(SimpleNamespace())

    def test_modbus_charger_can_emulate_enable_via_current_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=modbus_charger\nProfile=generic\nTransport=tcp\n"
                "[Transport]\nHost=openwb.local\nPort=1502\nUnitId=1\n"
                "[Capabilities]\nEnableUsesCurrentWrite=1\nEnableDefaultCurrentAmps=6\n"
                "[StateCurrent]\nRegisterType=input\nAddress=10116\nDataType=int16\nScale=100\n"
                "[CurrentWrite]\nRegisterType=holding\nAddress=10171\nDataType=int16\nScale=100\n",
            )
            fake_transport = _FakeModbusTransport()
            fake_transport.input_registers[10116] = 1325
            service = SimpleNamespace(shelly_request_timeout_seconds=2.0)
            with patch(
                "venus_evcharger.backend.modbus_charger.create_modbus_transport",
                return_value=fake_transport,
            ):
                backend = ModbusChargerBackend(service, config_path=config_path)
                state = backend.read_charger_state()
                backend.set_enabled(False)
                backend.set_enabled(True)
                backend.set_current(14.0)
                backend.set_enabled(False)
                backend.set_enabled(True)

            self.assertTrue(state.enabled)
            self.assertEqual(state.current_amps, 13.25)
            self.assertEqual(fake_transport.holding_registers[10171], 1400)

    def test_modbus_charger_enable_via_current_write_uses_default_when_no_resume_target_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=modbus_charger\nProfile=generic\nTransport=tcp\n"
                "[Transport]\nHost=openwb.local\nPort=1502\nUnitId=1\n"
                "[Capabilities]\nEnableUsesCurrentWrite=1\nEnableDefaultCurrentAmps=6\n"
                "[CurrentWrite]\nRegisterType=holding\nAddress=10171\nDataType=int16\nScale=100\n",
            )
            fake_transport = _FakeModbusTransport()
            with patch(
                "venus_evcharger.backend.modbus_charger.create_modbus_transport",
                return_value=fake_transport,
            ):
                backend = ModbusChargerBackend(self._service(), config_path=config_path)
                backend.set_enabled(True)

            self.assertEqual(fake_transport.holding_registers[10171], 600)

    def test_modbus_charger_enable_via_current_write_falls_back_from_zero_cached_current(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=modbus_charger\nProfile=generic\nTransport=tcp\n"
                "[Transport]\nHost=openwb.local\nPort=1502\nUnitId=1\n"
                "[Capabilities]\nEnableUsesCurrentWrite=1\nEnableDefaultCurrentAmps=6\n"
                "[CurrentWrite]\nRegisterType=holding\nAddress=10171\nDataType=int16\nScale=100\n",
            )
            fake_transport = _FakeModbusTransport()
            with patch(
                "venus_evcharger.backend.modbus_charger.create_modbus_transport",
                return_value=fake_transport,
            ):
                backend = ModbusChargerBackend(self._service(), config_path=config_path)
                backend.set_current(0.0)
                backend.set_enabled(True)

            self.assertEqual(fake_transport.holding_registers[10171], 600)

    def test_modbus_charger_enable_via_current_write_can_resume_from_current_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=modbus_charger\nProfile=generic\nTransport=tcp\n"
                "[Transport]\nHost=openwb.local\nPort=1502\nUnitId=1\n"
                "[Capabilities]\nEnableUsesCurrentWrite=1\nEnableDefaultCurrentAmps=6\n"
                "[CurrentWrite]\nRegisterType=holding\nAddress=10171\nDataType=int16\nScale=100\n",
            )
            fake_transport = _FakeModbusTransport()
            with patch(
                "venus_evcharger.backend.modbus_charger.create_modbus_transport",
                return_value=fake_transport,
            ):
                backend = ModbusChargerBackend(self._service(), config_path=config_path)
                backend._resume_current_amps_cache = None
                backend._current_amps_cache = 11.5
                backend.set_enabled(True)

            self.assertEqual(fake_transport.holding_registers[10171], 1150)

    def test_modbus_charger_read_state_does_not_refresh_resume_cache_for_zero_current(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=modbus_charger\nProfile=generic\nTransport=tcp\n"
                "[Transport]\nHost=openwb.local\nPort=1502\nUnitId=1\n"
                "[Capabilities]\nEnableUsesCurrentWrite=1\nEnableDefaultCurrentAmps=6\n"
                "[StateCurrent]\nRegisterType=input\nAddress=10116\nDataType=int16\nScale=100\n"
                "[CurrentWrite]\nRegisterType=holding\nAddress=10171\nDataType=int16\nScale=100\n",
            )
            fake_transport = _FakeModbusTransport()
            fake_transport.input_registers[10116] = 0
            with patch(
                "venus_evcharger.backend.modbus_charger.create_modbus_transport",
                return_value=fake_transport,
            ):
                backend = ModbusChargerBackend(self._service(), config_path=config_path)
                backend.read_charger_state()

            self.assertIsNone(backend._resume_current_amps_cache)

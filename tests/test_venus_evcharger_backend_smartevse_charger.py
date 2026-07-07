# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
import configparser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from venus_evcharger.backend.modbus_transport import ModbusRequest
from venus_evcharger.backend.smartevse_charger import (
    SmartEvseChargerBackend,
    _fault_tokens,
    _fault_text,
    _enabled,
    _normalized_current_amps,
    _rounded_current_setting,
    _smartevse_waiting_for_solar,
    _status_text,
    _supported_phase_selections,
    _validate_smartevse_current_ceiling,
    _validate_smartevse_current_range,
    load_smartevse_charger_settings,
)


class _FakeSmartEvseTransport:
    def __init__(self) -> None:
        self.requests: list[ModbusRequest] = []
        self.holding_registers: dict[int, int] = {
            0x0000: 2,
            0x0001: 0,
            0x0002: 16,
            0x0003: 0,
            0x0005: 1,
            0x0007: 32,
        }

    def exchange(self, request: ModbusRequest, *, timeout_seconds: float) -> bytes:
        self.requests.append(request)
        if request.function_code == 0x03:
            address = int.from_bytes(request.payload[0:2], "big")
            count = int.from_bytes(request.payload[2:4], "big")
            payload = b"".join(
                int(self.holding_registers.get(address + index, 0)).to_bytes(2, "big")
                for index in range(count)
            )
            return bytes((0x03, len(payload))) + payload
        if request.function_code == 0x06:
            address = int.from_bytes(request.payload[0:2], "big")
            value = int.from_bytes(request.payload[2:4], "big")
            self.holding_registers[address] = value
            return bytes((0x06,)) + request.payload
        raise AssertionError(f"Unexpected Modbus function code {request.function_code}")


class TestShellyWallboxBackendSmartEvseCharger(unittest.TestCase):
    @staticmethod
    def _service() -> SimpleNamespace:
        return SimpleNamespace(
            shelly_request_timeout_seconds=2.0,
        )

    @staticmethod
    def _write_config(directory: str, content: str) -> str:
        path = Path(directory) / "smartevse-charger.ini"
        path.write_text(content, encoding="utf-8")
        return str(path)

    def test_read_charger_state_maps_smartevse_registers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=smartevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.60\nPort=502\nUnitId=1\n",
            )
            fake_transport = _FakeSmartEvseTransport()
            with patch(
                "venus_evcharger.backend.smartevse_charger.create_modbus_transport",
                return_value=fake_transport,
            ):
                backend = SmartEvseChargerBackend(self._service(), config_path=config_path)

                state = backend.read_charger_state()

            self.assertTrue(state.enabled)
            self.assertEqual(state.current_amps, 16.0)
            self.assertEqual(state.phase_selection, "P1")
            self.assertIsNone(state.actual_current_amps)
            self.assertEqual(state.status_text, "charging")
            self.assertIsNone(state.fault_text)

    def test_smartevse_charger_uses_configured_fixed_phase_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=smartevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.60\nPort=502\nUnitId=1\n"
                "[Capabilities]\nSupportedPhaseSelections=P1_P2\n",
            )
            fake_transport = _FakeSmartEvseTransport()
            with patch(
                "venus_evcharger.backend.smartevse_charger.create_modbus_transport",
                return_value=fake_transport,
            ):
                backend = SmartEvseChargerBackend(self._service(), config_path=config_path)
                state = backend.read_charger_state()
                backend.set_phase_selection("P1_P2")

            self.assertEqual(backend.settings.supported_phase_selections, ("P1_P2",))
            self.assertEqual(state.phase_selection, "P1_P2")

    def test_read_charger_state_handles_scaled_current_and_faults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=smartevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.60\nPort=502\nUnitId=1\n",
            )
            fake_transport = _FakeSmartEvseTransport()
            fake_transport.holding_registers[0x0000] = 1
            fake_transport.holding_registers[0x0001] = 0x0020
            fake_transport.holding_registers[0x0003] = 2
            fake_transport.holding_registers[0x0002] = 160
            with patch(
                "venus_evcharger.backend.smartevse_charger.create_modbus_transport",
                return_value=fake_transport,
            ):
                backend = SmartEvseChargerBackend(self._service(), config_path=config_path)

                state = backend.read_charger_state()

            self.assertEqual(state.current_amps, 16.0)
            self.assertEqual(state.status_text, "waiting-solar")
            self.assertEqual(state.fault_text, "no-sun")

    def test_read_charger_state_maps_documented_load_balance_and_activation_states(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=smartevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.60\nPort=502\nUnitId=1\n",
            )
            fake_transport = _FakeSmartEvseTransport()
            fake_transport.holding_registers[0x0000] = 4
            with patch(
                "venus_evcharger.backend.smartevse_charger.create_modbus_transport",
                return_value=fake_transport,
            ):
                backend = SmartEvseChargerBackend(self._service(), config_path=config_path)
                state = backend.read_charger_state()
                self.assertEqual(state.status_text, "connected-load-balance")

            fake_transport = _FakeSmartEvseTransport()
            fake_transport.holding_registers[0x0000] = 8
            with patch(
                "venus_evcharger.backend.smartevse_charger.create_modbus_transport",
                return_value=fake_transport,
            ):
                backend = SmartEvseChargerBackend(self._service(), config_path=config_path)
                state = backend.read_charger_state()
                self.assertEqual(state.status_text, "activation-required")

    def test_smartevse_charger_writes_enable_and_current(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=smartevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.60\nPort=502\nUnitId=1\n",
            )
            fake_transport = _FakeSmartEvseTransport()
            with patch(
                "venus_evcharger.backend.smartevse_charger.create_modbus_transport",
                return_value=fake_transport,
            ):
                backend = SmartEvseChargerBackend(self._service(), config_path=config_path)

                backend.set_enabled(False)
                backend.set_current(13.6)
                backend.set_enabled(True)

            self.assertEqual(fake_transport.holding_registers[0x0002], 14)
            self.assertEqual(fake_transport.holding_registers[0x0005], 1)

    def test_smartevse_charger_rejects_current_above_documented_max_current_register(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=smartevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.60\nPort=502\nUnitId=1\n",
            )
            fake_transport = _FakeSmartEvseTransport()
            fake_transport.holding_registers[0x0007] = 13
            with patch(
                "venus_evcharger.backend.smartevse_charger.create_modbus_transport",
                return_value=fake_transport,
            ):
                backend = SmartEvseChargerBackend(self._service(), config_path=config_path)

                with self.assertRaisesRegex(ValueError, "maximum charging current 13 A"):
                    backend.set_current(16.0)

    def test_smartevse_charger_rejects_native_phase_switching(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=smartevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.60\nPort=502\nUnitId=1\n",
            )
            backend = SmartEvseChargerBackend(self._service(), config_path=config_path)

            with self.assertRaisesRegex(ValueError, "configured fixed phase selection: P1"):
                backend.set_phase_selection("P1_P2_P3")

    def test_smartevse_charger_rejects_multiple_supported_phase_selections(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=smartevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.60\nPort=502\nUnitId=1\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2_P3\n",
            )

            with self.assertRaisesRegex(ValueError, "requires exactly one fixed"):
                SmartEvseChargerBackend(self._service(), config_path=config_path)

    def test_smartevse_helper_edges_cover_current_fault_and_status_validation(self) -> None:
        self.assertEqual(_normalized_current_amps(-5), 0.0)
        self.assertEqual(_normalized_current_amps(0), 0.0)
        self.assertEqual(_normalized_current_amps(80), 80.0)
        self.assertEqual(_normalized_current_amps(810), 81.0)
        self.assertEqual(_normalized_current_amps(160), 16.0)
        self.assertEqual(_rounded_current_setting(0.0), 0)
        self.assertEqual(_rounded_current_setting(6.0), 6)
        self.assertEqual(_rounded_current_setting(80.0), 80)
        with self.assertRaisesRegex(ValueError, "expected 0 or 6..80 A"):
            _rounded_current_setting(5.0)
        with self.assertRaisesRegex(ValueError, "Unsupported charger current '81.0'.*expected 0 or 6..80 A"):
            _rounded_current_setting(81.0)
        with self.assertRaisesRegex(ValueError, "Unsupported charger current '16.0'.*maximum charging current 15 A"):
            _rounded_current_setting(16.0, max_current_amps=15)
        with self.assertRaisesRegex(ValueError, "Unsupported charger current '7.0'.*maximum charging current 6 A"):
            _rounded_current_setting(7.0, max_current_amps=6)
        self.assertEqual(_rounded_current_setting(16.0, max_current_amps=None), 16)
        self.assertEqual(_rounded_current_setting(6.0, max_current_amps=6), 6)
        _validate_smartevse_current_range(6.0, 6)
        _validate_smartevse_current_range(80.0, 80)
        _validate_smartevse_current_ceiling(16.0, 16, None)
        _validate_smartevse_current_ceiling(16.0, 16, 5)
        _validate_smartevse_current_ceiling(6.0, 6, 6)
        self.assertEqual(
            _fault_tokens(0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020),
            ["less-than-6a", "no-comm", "temp-high", "rcd", "no-sun"],
        )
        self.assertIsNone(_fault_text(0))
        self.assertEqual(_fault_text(0x0002 | 0x0010), "no-comm,rcd")
        self.assertFalse(_enabled(0))
        self.assertTrue(_enabled(1))
        self.assertEqual(_status_text(1, 0, False, 0), "disabled")
        self.assertEqual(_status_text(1, 0x0001, True, 0), "error")
        self.assertEqual(_status_text(1, 0x0020, True, 2), "waiting-solar")
        self.assertFalse(_smartevse_waiting_for_solar(0x0020, 1))
        self.assertFalse(_smartevse_waiting_for_solar(0, 2))
        self.assertTrue(_smartevse_waiting_for_solar(0x0020, 2))
        expected_states = {
            0: "idle",
            1: "connected",
            2: "charging",
            3: "waiting-ventilation",
            4: "connected-load-balance",
            5: "connected-load-balance",
            6: "charging-load-balance",
            7: "charging-load-balance",
            8: "activation-required",
            9: "connected-authorized",
            10: "charging-authorized",
        }
        for state_value, status_text in expected_states.items():
            with self.subTest(state_value=state_value):
                self.assertEqual(_status_text(state_value, 0, True, 0), status_text)
        for active_while_disabled in (2, 6, 7, 10):
            with self.subTest(active_while_disabled=active_while_disabled):
                self.assertEqual(
                    _status_text(active_while_disabled, 0, False, 0),
                    expected_states[active_while_disabled],
                )
        for disabled_state in (0, 1, 3, 4, 5, 8, 9):
            with self.subTest(disabled_state=disabled_state):
                self.assertEqual(_status_text(disabled_state, 0, False, 0), "disabled")
        self.assertIsNone(_status_text(99, 0, True, 0))
        with self.assertRaises(FileNotFoundError):
            SmartEvseChargerBackend(self._service(), config_path="/definitely/missing.ini")

    def test_smartevse_boundary_helpers_receive_exact_context_and_service(self) -> None:
        parser = configparser.ConfigParser()
        parser["Transport"] = {"Host": "192.168.1.60", "Port": "502", "UnitId": "7"}
        service = self._service()
        with patch(
            "venus_evcharger.backend.smartevse_charger.fixed_supported_phase_selections",
            return_value=("P1",),
        ) as fixed_supported, patch(
            "venus_evcharger.backend.smartevse_charger.load_required_backend_config",
            return_value=parser,
        ) as load_config, patch(
            "venus_evcharger.backend.smartevse_charger.load_modbus_transport_settings",
            return_value=object(),
        ) as load_transport:
            self.assertEqual(_supported_phase_selections(parser), ("P1",))
            fixed_supported.assert_called_once_with(parser, ("P1",), "SmartEVSE")
            fixed_supported.reset_mock()
            settings = load_smartevse_charger_settings(service, "/tmp/smartevse.ini")

        load_config.assert_called_once_with("/tmp/smartevse.ini", "SmartEVSE charger")
        fixed_supported.assert_called_once_with(parser, ("P1",), "SmartEVSE")
        load_transport.assert_called_once_with(parser, service)
        self.assertEqual(settings.profile_name, "smartevse")
        self.assertEqual(settings.supported_phase_selections, ("P1",))
        self.assertEqual(settings.state_register, 0x0000)
        self.assertEqual(settings.error_register, 0x0001)
        self.assertEqual(settings.current_register, 0x0002)
        self.assertEqual(settings.mode_register, 0x0003)
        self.assertEqual(settings.access_register, 0x0005)
        self.assertEqual(settings.max_current_register, 0x0007)

    def test_smartevse_native_backend_initialization_uses_settings_loader(self) -> None:
        service = self._service()
        with patch("venus_evcharger.backend.smartevse_charger.initialize_native_modbus_backend") as initialize:
            backend = SmartEvseChargerBackend(service, config_path="/tmp/smartevse.ini")
            default_backend = SmartEvseChargerBackend(service)

        self.assertEqual(
            initialize.call_args_list,
            [
                unittest.mock.call(
                    backend,
                    service,
                    "/tmp/smartevse.ini",
                    load_smartevse_charger_settings,
                ),
                unittest.mock.call(
                    default_backend,
                    service,
                    "",
                    load_smartevse_charger_settings,
                ),
            ],
        )

    def test_smartevse_read_register_uses_holding_uint16_contract(self) -> None:
        backend = SmartEvseChargerBackend.__new__(SmartEvseChargerBackend)
        client = MagicMock()
        client.read_scalar.return_value = 17
        setattr(backend, "_client", MagicMock(return_value=client))

        self.assertEqual(backend._read_register(0x1234), 17)

        client.read_scalar.assert_called_once_with("holding", 0x1234, "uint16")

    def test_smartevse_phase_write_validation_receives_backend_label(self) -> None:
        backend = SmartEvseChargerBackend.__new__(SmartEvseChargerBackend)
        backend.settings = SimpleNamespace(supported_phase_selections=("P1",))
        with patch("venus_evcharger.backend.smartevse_charger.validate_fixed_phase_selection") as validate:
            backend.set_phase_selection("P1")

        validate.assert_called_once_with("P1", "P1", "SmartEVSE")

    def test_smartevse_enable_writes_exact_boolean_register_values(self) -> None:
        backend = SmartEvseChargerBackend.__new__(SmartEvseChargerBackend)
        backend.settings = SimpleNamespace(access_register=0x0005)
        setattr(backend, "_write_register", MagicMock())

        backend.set_enabled(False)
        backend.set_enabled(True)

        self.assertEqual(
            backend._write_register.call_args_list,
            [
                unittest.mock.call(0x0005, 0),
                unittest.mock.call(0x0005, 1),
            ],
        )

    def test_smartevse_reuses_preseeded_transport_when_client_is_created(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=smartevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.60\nPort=502\nUnitId=1\n",
            )
            backend = SmartEvseChargerBackend(self._service(), config_path=config_path)
            backend._transport = _FakeSmartEvseTransport()

            with patch("venus_evcharger.backend.smartevse_charger.create_modbus_transport") as create_transport:
                client = backend._client()

            self.assertIs(client, backend._client_cache)
            create_transport.assert_not_called()

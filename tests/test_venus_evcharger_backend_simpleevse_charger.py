# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from venus_evcharger.backend.modbus_transport import ModbusRequest
from venus_evcharger.backend.simpleevse_charger import (
    SimpleEvseChargerBackend,
    _enabled,
    _evse_status_text,
    _fault_text,
    _non_error_status,
    _rounded_current_setting,
    _status_text,
    _supported_phase_selections,
    _validate_simpleevse_current,
    _vehicle_status_text,
    load_simpleevse_charger_settings,
)


class _FakeSimpleEvseTransport:
    def __init__(self) -> None:
        self.requests: list[ModbusRequest] = []
        self.holding_registers: dict[int, int] = {
            1000: 16,
            1001: 13,
            1002: 3,
            1004: 0,
            1005: 18,
            1006: 2,
            1007: 1,
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
        if request.function_code == 0x10:
            address = int.from_bytes(request.payload[0:2], "big")
            count = int.from_bytes(request.payload[2:4], "big")
            byte_count = request.payload[4]
            data = request.payload[5 : 5 + byte_count]
            for index in range(count):
                start = index * 2
                self.holding_registers[address + index] = int.from_bytes(data[start : start + 2], "big")
            return bytes((0x10,)) + request.payload[:4]
        raise AssertionError(f"Unexpected Modbus function code {request.function_code}")


class TestShellyWallboxBackendSimpleEvseCharger(unittest.TestCase):
    @staticmethod
    def _service() -> SimpleNamespace:
        return SimpleNamespace(
            shelly_request_timeout_seconds=2.0,
        )

    @staticmethod
    def _write_config(directory: str, content: str) -> str:
        path = Path(directory) / "simpleevse-charger.ini"
        path.write_text(content, encoding="utf-8")
        return str(path)

    def test_simpleevse_boundary_helpers_receive_exact_context_and_service(self) -> None:
        service = self._service()
        with patch("venus_evcharger.backend.simpleevse_charger.initialize_native_modbus_backend") as initialize:
            backend = SimpleEvseChargerBackend(service)
        initialize.assert_called_once_with(backend, service, "", load_simpleevse_charger_settings)

        parser = SimpleNamespace()
        with patch(
            "venus_evcharger.backend.simpleevse_charger.fixed_supported_phase_selections",
            return_value=("P1",),
        ) as fixed_phases:
            self.assertEqual(_supported_phase_selections(parser), ("P1",))
        fixed_phases.assert_called_once_with(parser, ("P1",), "SimpleEVSE")

        transport_settings = SimpleNamespace()
        with (
            patch("venus_evcharger.backend.simpleevse_charger.load_required_backend_config", return_value=parser) as load_config,
            patch(
                "venus_evcharger.backend.simpleevse_charger.load_modbus_transport_settings",
                return_value=transport_settings,
            ) as load_transport,
            patch("venus_evcharger.backend.simpleevse_charger._supported_phase_selections", return_value=("P1",)),
        ):
            settings = load_simpleevse_charger_settings(service, "/tmp/simpleevse.ini")

        load_config.assert_called_once_with("/tmp/simpleevse.ini", "SimpleEVSE charger")
        load_transport.assert_called_once_with(parser, service)
        self.assertIs(settings.transport_settings, transport_settings)

    def test_read_register_uses_holding_uint16_contract(self) -> None:
        backend = object.__new__(SimpleEvseChargerBackend)
        client = SimpleNamespace(read_scalar=unittest.mock.MagicMock(return_value=17))
        backend._client = unittest.mock.MagicMock(return_value=client)

        self.assertEqual(backend._read_register(1234), 17)

        client.read_scalar.assert_called_once_with("holding", 1234, "uint16")

    def test_read_charger_state_maps_simpleevse_registers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=simpleevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.0.2.50\nPort=502\nUnitId=1\n",
            )
            fake_transport = _FakeSimpleEvseTransport()
            with patch(
                "venus_evcharger.backend.native_modbus_backend.create_modbus_transport",
                return_value=fake_transport,
            ):
                backend = SimpleEvseChargerBackend(self._service(), config_path=config_path)

                state = backend.read_charger_state()

            self.assertTrue(state.enabled)
            self.assertEqual(state.current_amps, 16.0)
            self.assertEqual(state.actual_current_amps, 13.0)
            self.assertEqual(state.phase_selection, "P1")
            self.assertIsNone(state.power_w)
            self.assertIsNone(state.energy_kwh)
            self.assertEqual(state.status_text, "charging")
            self.assertIsNone(state.fault_text)

    def test_simpleevse_charger_uses_configured_fixed_phase_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=simpleevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.0.2.50\nPort=502\nUnitId=1\n"
                "[Capabilities]\nSupportedPhaseSelections=P1_P2_P3\n",
            )
            fake_transport = _FakeSimpleEvseTransport()
            with patch(
                "venus_evcharger.backend.native_modbus_backend.create_modbus_transport",
                return_value=fake_transport,
            ):
                backend = SimpleEvseChargerBackend(self._service(), config_path=config_path)
                state = backend.read_charger_state()
                backend.set_phase_selection("P1_P2_P3")

            self.assertEqual(backend.settings.supported_phase_selections, ("P1_P2_P3",))
            self.assertEqual(backend.settings.profile_name, "simpleevse")
            self.assertEqual(backend.settings.current_register, 1000)
            self.assertEqual(backend.settings.actual_current_register, 1001)
            self.assertEqual(backend.settings.vehicle_state_register, 1002)
            self.assertEqual(backend.settings.control_register, 1004)
            self.assertEqual(backend.settings.firmware_register, 1005)
            self.assertEqual(backend.settings.evse_state_register, 1006)
            self.assertEqual(backend.settings.status_register, 1007)
            self.assertEqual(state.phase_selection, "P1_P2_P3")

    def test_read_charger_state_falls_back_to_evse_state_when_vehicle_state_is_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=simpleevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.0.2.50\nPort=502\nUnitId=1\n",
            )
            fake_transport = _FakeSimpleEvseTransport()
            fake_transport.holding_registers[1002] = 99
            fake_transport.holding_registers[1006] = 3
            with patch(
                "venus_evcharger.backend.native_modbus_backend.create_modbus_transport",
                return_value=fake_transport,
            ):
                backend = SimpleEvseChargerBackend(self._service(), config_path=config_path)

                state = backend.read_charger_state()

            self.assertFalse(state.enabled)
            self.assertEqual(state.status_text, "disabled")

    def test_read_charger_state_maps_simpleevse_fault_bits(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=simpleevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.0.2.50\nPort=502\nUnitId=1\n",
            )
            fake_transport = _FakeSimpleEvseTransport()
            fake_transport.holding_registers[1002] = 5
            fake_transport.holding_registers[1006] = 1
            fake_transport.holding_registers[1007] = 0x0012
            with patch(
                "venus_evcharger.backend.native_modbus_backend.create_modbus_transport",
                return_value=fake_transport,
            ):
                backend = SimpleEvseChargerBackend(self._service(), config_path=config_path)

                state = backend.read_charger_state()

            self.assertEqual(state.status_text, "error")
            self.assertEqual(state.fault_text, "diode-check-fail,rcd-check-error")

    def test_simpleevse_charger_writes_enable_and_current(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=simpleevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.0.2.50\nPort=502\nUnitId=1\n",
            )
            fake_transport = _FakeSimpleEvseTransport()
            with patch(
                "venus_evcharger.backend.native_modbus_backend.create_modbus_transport",
                return_value=fake_transport,
            ):
                backend = SimpleEvseChargerBackend(self._service(), config_path=config_path)

                backend.set_enabled(False)
                backend.set_current(13.6)
                backend.set_enabled(True)

            self.assertEqual(fake_transport.holding_registers[1000], 14)
            self.assertEqual(fake_transport.holding_registers[1004], 0)

    def test_simpleevse_charger_rejects_native_phase_switching(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=simpleevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.0.2.50\nPort=502\nUnitId=1\n",
            )
            backend = SimpleEvseChargerBackend(self._service(), config_path=config_path)

            with self.assertRaises(ValueError) as invalid_phase:
                backend.set_phase_selection("P1_P2_P3")
            self.assertIn("configured fixed phase selection: P1", str(invalid_phase.exception))

    def test_simpleevse_charger_forwards_phase_selection_validation_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=simpleevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.0.2.50\nPort=502\nUnitId=1\n",
            )
            backend = SimpleEvseChargerBackend(self._service(), config_path=config_path)

            with patch("venus_evcharger.backend.simpleevse_charger.validate_fixed_phase_selection") as validate:
                backend.set_phase_selection("P1_P2_P3")

            validate.assert_called_once_with("P1_P2_P3", "P1", "SimpleEVSE")

            with self.assertRaises(ValueError) as invalid_phase:
                backend.set_phase_selection("P1_P2_P3")
            invalid_phase_message = str(invalid_phase.exception)
            self.assertIn("simpleevse", invalid_phase_message.lower())
            self.assertIn("configured fixed phase selection: P1", invalid_phase_message)

    def test_simpleevse_charger_rejects_multiple_supported_phase_selections(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=simpleevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.0.2.50\nPort=502\nUnitId=1\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2_P3\n",
            )

            with self.assertRaises(ValueError) as invalid_supported:
                SimpleEvseChargerBackend(self._service(), config_path=config_path)
            self.assertIn("SimpleEVSE", str(invalid_supported.exception))
            self.assertIn("requires exactly one fixed", str(invalid_supported.exception))

    def test_simpleevse_helper_edges_cover_fault_status_and_current_validation(self) -> None:
        self.assertEqual(_vehicle_status_text(1), "ready")
        self.assertEqual(_vehicle_status_text(2), "vehicle-present")
        self.assertEqual(_vehicle_status_text(3), "charging")
        self.assertEqual(_vehicle_status_text(4), "charging-ventilation")
        self.assertEqual(_vehicle_status_text(5), "error")
        self.assertIsNone(_vehicle_status_text(99))
        self.assertEqual(_evse_status_text(1), "idle")
        self.assertEqual(_evse_status_text(2), "ready")
        self.assertEqual(_evse_status_text(3), "disabled")
        self.assertIsNone(_evse_status_text(99))
        self.assertFalse(_non_error_status("error"))
        self.assertTrue(_non_error_status("ERROR"))
        self.assertEqual(_fault_text(0, 0x0004), "vent-required-fail")
        self.assertEqual(_fault_text(0, 0x0008), "pilot-release-wait")
        self.assertEqual(_fault_text(5, 0), "vehicle-failure")
        self.assertTrue(_enabled(0, 2))
        self.assertFalse(_enabled(0x0001, 3))
        self.assertTrue(_enabled(0, 4))
        self.assertEqual(_status_text(99, 2, None), "ready")
        self.assertEqual(_rounded_current_setting(0.0), 0)
        self.assertEqual(_rounded_current_setting(80.0), 80)
        _validate_simpleevse_current(0.0, 0)
        _validate_simpleevse_current(80.0, 80)
        with self.assertRaisesRegex(ValueError, "Unsupported charger current '-1.0'"):
            _rounded_current_setting(-1.0)
        with self.assertRaisesRegex(ValueError, "Unsupported charger current '81.0'"):
            _rounded_current_setting(81.0)
        with self.assertRaises(FileNotFoundError) as missing_config:
            SimpleEvseChargerBackend(self._service(), config_path="/definitely/missing.ini")
        self.assertIn("SimpleEVSE charger", str(missing_config.exception))

    def test_simpleevse_reuses_preseeded_transport_when_client_is_created(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=simpleevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.0.2.50\nPort=502\nUnitId=1\n",
            )
            backend = SimpleEvseChargerBackend(self._service(), config_path=config_path)
            backend._transport = _FakeSimpleEvseTransport()

            with patch("venus_evcharger.backend.native_modbus_backend.create_modbus_transport") as create_transport:
                client = backend._client()

            self.assertIs(client, backend._client_cache)
            create_transport.assert_not_called()

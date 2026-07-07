# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from venus_evcharger.backend.registry import create_meter_backend, create_switch_backend
from venus_evcharger.backend.tasmota_meter import TasmotaMeterBackend
from venus_evcharger.backend.tasmota_switch import TasmotaContactorSwitchBackend, TasmotaSwitchBackend
from venus_evcharger.bootstrap.wizard_adapters import tasmota_meter_config, tasmota_switch_config


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


class TestTasmotaBackends(unittest.TestCase):
    @staticmethod
    def _service(session: object) -> SimpleNamespace:
        return SimpleNamespace(session=session, phase="L1", shelly_request_timeout_seconds=2.0)

    @staticmethod
    def _write_config(directory: str, name: str, content: str) -> str:
        path = Path(directory) / name
        path.write_text(content, encoding="utf-8")
        return str(path)

    def test_tasmota_meter_registry_uses_status_sns_template_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(temp_dir, "tasmota-meter.ini", tasmota_meter_config("http://tasmota.local"))
            session = MagicMock()
            session.get.return_value = _FakeResponse(
                {"StatusSNS": {"ENERGY": {"Power": 1234, "Voltage": 230, "Current": 5.3, "Total": 7.89}}}
            )

            backend = create_meter_backend("tasmota_meter", self._service(session), config_path)
            self.assertIsInstance(backend, TasmotaMeterBackend)
            reading = backend.read_meter()

            self.assertEqual(reading.power_w, 1234.0)
            self.assertEqual(reading.voltage_v, 230.0)
            self.assertEqual(reading.current_a, 5.3)
            self.assertEqual(reading.energy_kwh, 7.89)
            session.get.assert_called_with(url="http://tasmota.local/cm?cmnd=Status+8", timeout=2.0)

    def test_tasmota_switch_and_contactor_registry_use_power_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(temp_dir, "tasmota-switch.ini", tasmota_switch_config("http://tasmota.local"))
            session = MagicMock()
            session.get.return_value = _FakeResponse({"POWER": "OFF"})

            switch = create_switch_backend("tasmota_switch", self._service(session), config_path)
            contactor = create_switch_backend("tasmota_contactor_switch", self._service(session), config_path)
            self.assertIsInstance(switch, TasmotaSwitchBackend)
            self.assertIsInstance(contactor, TasmotaContactorSwitchBackend)
            self.assertEqual(contactor.capabilities().switching_mode, "contactor")

            state = switch.read_switch_state()
            switch.set_enabled(True)

            self.assertFalse(state.enabled)
            session.get.assert_any_call(url="http://tasmota.local/cm?cmnd=Power", timeout=2.0)
            session.get.assert_any_call(url="http://tasmota.local/cm?cmnd=Power+on", timeout=2.0)
            self.assertIn("Type=tasmota_contactor_switch", tasmota_switch_config("http://tasmota.local", contactor=True))

    def test_tasmota_contactor_constructor_forwards_service_and_config_path(self) -> None:
        service = self._service(MagicMock())
        with patch("venus_evcharger.backend.tasmota_switch.TemplateSwitchBackend.__init__", return_value=None) as base_init, patch(
            "venus_evcharger.backend.tasmota_switch.force_contactor_switch_settings"
        ) as force_contactor:
            explicit = TasmotaContactorSwitchBackend(service, config_path="/tmp/tasmota-switch.ini")
            defaulted = TasmotaContactorSwitchBackend(service)

        self.assertEqual(
            base_init.call_args_list,
            [
                call(service, config_path="/tmp/tasmota-switch.ini"),
                call(service, config_path=""),
            ],
        )
        self.assertEqual(
            force_contactor.call_args_list,
            [
                call(explicit),
                call(defaulted),
            ],
        )

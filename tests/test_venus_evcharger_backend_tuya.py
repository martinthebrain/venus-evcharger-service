# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from venus_evcharger.backend.registry import create_meter_backend, create_switch_backend
from venus_evcharger.backend.tuya_meter import TuyaMeterBackend
from venus_evcharger.backend.tuya_switch import TuyaContactorSwitchBackend, TuyaSwitchBackend
from venus_evcharger.bootstrap.wizard_adapters import tuya_switch_config


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


class TestTuyaBackends(unittest.TestCase):
    @staticmethod
    def _service(session: object) -> SimpleNamespace:
        return SimpleNamespace(session=session, phase="L1", shelly_request_timeout_seconds=2.0)

    @staticmethod
    def _write_config(directory: str, name: str, content: str) -> str:
        path = Path(directory) / name
        path.write_text(content, encoding="utf-8")
        return str(path)

    def test_tuya_meter_registry_reuses_template_meter_surface(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "tuya-meter.ini",
                "[Adapter]\nType=tuya_meter\nBaseUrl=http://tuya.local\n"
                "[MeterRequest]\nUrl=/status\n"
                "[MeterResponse]\nPowerPath=power_watts\nEnergyWhPath=energy_wh\nRelayEnabledPath=relay_on\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse({"power_watts": 1234.0, "energy_wh": 4567.0, "relay_on": True})

            backend = create_meter_backend("tuya_meter", self._service(session), config_path)
            self.assertIsInstance(backend, TuyaMeterBackend)
            reading = backend.read_meter()

            self.assertEqual(reading.power_w, 1234.0)
            self.assertEqual(reading.energy_kwh, 4.567)
            self.assertTrue(reading.relay_on)

    def test_tuya_switch_and_contactor_registry_reuse_template_switch_surface(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "tuya-switch.ini",
                "[Adapter]\nType=tuya_switch\nBaseUrl=http://tuya.local\n"
                "[StateRequest]\nUrl=/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nUrl=/control\nJsonTemplate={\"enabled\": $enabled_json}\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse({"enabled": False})
            session.post.return_value = _FakeResponse({})

            switch = create_switch_backend("tuya_switch", self._service(session), config_path)
            contactor = create_switch_backend("tuya_contactor_switch", self._service(session), config_path)
            self.assertIsInstance(switch, TuyaSwitchBackend)
            self.assertIsInstance(contactor, TuyaContactorSwitchBackend)
            self.assertEqual(contactor.capabilities().switching_mode, "contactor")

            switch.set_enabled(True)
            session.post.assert_called_with(url="http://tuya.local/control", timeout=2.0, json={"enabled": True})
            self.assertIn("Type=tuya_contactor_switch", tuya_switch_config("http://tuya.local", contactor=True))

    def test_tuya_contactor_constructor_forwards_service_and_config_path(self) -> None:
        service = self._service(MagicMock())
        with patch("venus_evcharger.backend.tuya_switch.TemplateSwitchBackend.__init__", return_value=None) as base_init, patch(
            "venus_evcharger.backend.tuya_switch.force_contactor_switch_settings"
        ) as force_contactor:
            explicit = TuyaContactorSwitchBackend(service, config_path="/tmp/tuya-switch.ini")
            defaulted = TuyaContactorSwitchBackend(service)

        self.assertEqual(
            base_init.call_args_list,
            [
                call(service, config_path="/tmp/tuya-switch.ini"),
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

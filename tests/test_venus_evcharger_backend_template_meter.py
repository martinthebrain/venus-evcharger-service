# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from venus_evcharger.backend.template_meter import (
    TemplateMeterBackend,
    TemplateMeterSettings,
    load_template_meter_settings,
    _default_phase_selection,
    _meter_scalar_values,
    _phase_vector,
    _resolved_phase_vector,
    _service_single_phase_line,
    _service_timeout_default,
)
from venus_evcharger.backend.template_support import TemplateAuthSettings


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


class TestShellyWallboxBackendTemplateMeter(unittest.TestCase):
    @staticmethod
    def _service(session: object) -> SimpleNamespace:
        return SimpleNamespace(
            session=session,
            phase="L2",
            shelly_request_timeout_seconds=2.0,
        )

    @staticmethod
    def _write_config(directory: str, content: str) -> str:
        path = Path(directory) / "template-meter.ini"
        path.write_text(content, encoding="utf-8")
        return str(path)

    def test_load_template_meter_settings_preserves_explicit_contract_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\n"
                "Type=template_meter\n"
                "BaseUrl=http://adapter.local/base/\n"
                "RequestTimeoutSeconds=3.75\n"
                "AuthHeaderName=X-Adapter\n"
                "AuthHeaderValue=secret-token\n"
                "[Phase]\nMeasuredPhaseSelection=3P\n"
                "[MeterRequest]\nMethod=post\nUrl=relative/state\n"
                "[MeterResponse]\n"
                "RelayEnabledPath=relay.enabled\n"
                "PowerPath=metrics.power\n"
                "VoltagePath=metrics.voltage\n"
                "CurrentPath=metrics.current\n"
                "EnergyKwhPath=energy.kwh\n"
                "EnergyWhPath=energy.wh\n"
                "PhaseSelectionPath=phase.selection\n"
                "PhasePowersPath=phase.powers\n"
                "PhaseCurrentsPath=phase.currents\n",
            )

            settings = load_template_meter_settings(self._service(MagicMock()), config_path)

            self.assertEqual(settings.base_url, "http://adapter.local/base/")
            self.assertEqual(settings.timeout_seconds, 3.75)
            self.assertEqual(settings.meter_method, "POST")
            self.assertEqual(settings.meter_url, "http://adapter.local/base/relative/state")
            self.assertEqual(settings.relay_enabled_path, "relay.enabled")
            self.assertEqual(settings.power_path, "metrics.power")
            self.assertEqual(settings.voltage_path, "metrics.voltage")
            self.assertEqual(settings.current_path, "metrics.current")
            self.assertEqual(settings.energy_kwh_path, "energy.kwh")
            self.assertEqual(settings.energy_wh_path, "energy.wh")
            self.assertEqual(settings.phase_selection, "P1_P2_P3")
            self.assertEqual(settings.phase_selection_path, "phase.selection")
            self.assertEqual(settings.phase_powers_path, "phase.powers")
            self.assertEqual(settings.phase_currents_path, "phase.currents")
            self.assertEqual(settings.auth_settings.auth_header_name, "X-Adapter")
            self.assertEqual(settings.auth_settings.auth_header_value, "secret-token")

    def test_load_template_meter_settings_defaults_are_explicit_contract_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_meter\nBaseUrl=http://adapter.local\n"
                "[MeterRequest]\nUrl=/meter/state\n"
                "[MeterResponse]\n",
            )
            service = SimpleNamespace(
                session=MagicMock(),
                phase="L3",
                shelly_request_timeout_seconds=4.5,
            )

            settings = load_template_meter_settings(service, config_path)

            self.assertEqual(settings.base_url, "http://adapter.local")
            self.assertEqual(settings.timeout_seconds, 4.5)
            self.assertEqual(settings.meter_method, "GET")
            self.assertEqual(settings.meter_url, "http://adapter.local/meter/state")
            self.assertEqual(settings.power_path, "power_w")
            self.assertEqual(settings.phase_selection, "P1")
            self.assertIsNone(settings.relay_enabled_path)
            self.assertIsNone(settings.voltage_path)
            self.assertIsNone(settings.current_path)
            self.assertIsNone(settings.energy_kwh_path)
            self.assertIsNone(settings.energy_wh_path)
            self.assertIsNone(settings.phase_selection_path)
            self.assertIsNone(settings.phase_powers_path)
            self.assertIsNone(settings.phase_currents_path)

    def test_load_template_meter_settings_uses_service_fallbacks_deliberately(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_meter\nBaseUrl=http://adapter.local\n"
                "[MeterRequest]\nUrl=/meter/state\n"
                "[MeterResponse]\nPowerPath=power_w\n",
            )
            service = SimpleNamespace(
                session=MagicMock(),
                phase="bad-phase",
                _adapter_auth_fallback_enabled=True,
                username="fallback-user",
                password="fallback-secret",
                use_digest_auth=True,
            )

            settings = load_template_meter_settings(service, config_path)

            self.assertEqual(settings.timeout_seconds, 2.0)
            self.assertEqual(settings.phase_selection, "P1")
            self.assertEqual(settings.auth_settings.username, "fallback-user")
            self.assertEqual(settings.auth_settings.password, "fallback-secret")
            self.assertTrue(settings.auth_settings.use_digest_auth)

            backend = TemplateMeterBackend(service, config_path=config_path)
            self.assertIs(backend.service, service)
            self.assertEqual(backend.settings.auth_settings.username, "fallback-user")
            self.assertTrue(backend.settings.auth_settings.use_digest_auth)

        with tempfile.TemporaryDirectory() as temp_dir:
            invalid_timeout = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_meter\nBaseUrl=http://adapter.local\nRequestTimeoutSeconds=0\n"
                "[MeterRequest]\nUrl=/meter/state\n"
                "[MeterResponse]\nPowerPath=power_w\n",
            )

            settings = load_template_meter_settings(self._service(MagicMock()), invalid_timeout)

            self.assertEqual(settings.timeout_seconds, 2.0)

        with tempfile.TemporaryDirectory() as temp_dir:
            small_timeout = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_meter\nBaseUrl=http://adapter.local\nRequestTimeoutSeconds=0.5\n"
                "[MeterRequest]\nMethod=DELETE\nUrl=/meter/state\n"
                "[Phase]\nMeasuredPhase=2P\n"
                "[MeterResponse]\nPowerPath=power_w\n",
            )

            settings = load_template_meter_settings(SimpleNamespace(session=MagicMock()), small_timeout)

            self.assertEqual(settings.timeout_seconds, 0.5)
            self.assertEqual(settings.meter_method, "GET")
            self.assertEqual(settings.phase_selection, "P1_P2")

        with tempfile.TemporaryDirectory() as temp_dir:
            service_phase_fallback = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_meter\nBaseUrl=http://adapter.local\n"
                "[MeterRequest]\nUrl=/meter/state\n"
                "[MeterResponse]\nPowerPath=power_w\n",
            )

            settings = load_template_meter_settings(
                SimpleNamespace(session=MagicMock(), phase="3P"),
                service_phase_fallback,
            )

            self.assertEqual(settings.phase_selection, "P1_P2_P3")


    def test_read_meter_uses_normalized_paths_and_derives_phase_vectors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_meter\nBaseUrl=http://adapter.local\n"
                "[Phase]\nMeasuredPhaseSelection=P1\n"
                "[MeterRequest]\nMethod=GET\nUrl=/meter/state\n"
                "[MeterResponse]\nRelayEnabledPath=data.enabled\nPowerPath=data.power_w\n"
                "VoltagePath=data.voltage_v\nCurrentPath=data.current_a\n"
                "EnergyKwhPath=data.energy_kwh\nPhaseSelectionPath=data.phase_selection\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse(
                {
                    "data": {
                        "enabled": True,
                        "power_w": 3450.0,
                        "voltage_v": 230.0,
                        "current_a": 15.0,
                        "energy_kwh": 6.789,
                        "phase_selection": "P1_P2_P3",
                    }
                }
            )
            backend = TemplateMeterBackend(self._service(session), config_path=config_path)

            reading = backend.read_meter()

            self.assertTrue(reading.relay_on)
            self.assertEqual(reading.power_w, 3450.0)
            self.assertEqual(reading.voltage_v, 230.0)
            self.assertEqual(reading.current_a, 15.0)
            self.assertEqual(reading.energy_kwh, 6.789)
            self.assertEqual(reading.phase_selection, "P1_P2_P3")
            self.assertEqual(reading.phase_powers_w, (1150.0, 1150.0, 1150.0))
            self.assertEqual(reading.phase_currents_a, (5.0, 5.0, 5.0))

    def test_read_meter_falls_back_to_config_phase_and_zero_energy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_meter\nBaseUrl=http://adapter.local\n"
                "[Phase]\nMeasuredPhaseSelection=2P\n"
                "[MeterRequest]\nUrl=/meter/state\n"
                "[MeterResponse]\nPowerPath=power\nCurrentPath=current\nPhaseSelectionPath=phase\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse({"power": 2000.0, "current": 8.0, "phase": "bad"})
            backend = TemplateMeterBackend(self._service(session), config_path=config_path)

            reading = backend.read_meter()

            self.assertEqual(reading.energy_kwh, 0.0)
            self.assertEqual(reading.phase_selection, "P1_P2")
            self.assertEqual(reading.phase_powers_w, (1000.0, 1000.0, 0.0))
            self.assertEqual(reading.phase_currents_a, (4.0, 4.0, 0.0))

    def test_read_meter_maps_single_phase_power_to_service_phase_line(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_meter\nBaseUrl=http://adapter.local\n"
                "[Phase]\nMeasuredPhaseSelection=P1\n"
                "[MeterRequest]\nUrl=/meter/state\n"
                "[MeterResponse]\nPowerPath=power\nCurrentPath=current\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse({"power": 1800.0, "current": 7.5})
            service = SimpleNamespace(
                session=session,
                phase="L3",
                shelly_request_timeout_seconds=2.0,
            )
            backend = TemplateMeterBackend(service, config_path=config_path)

            reading = backend.read_meter()

            self.assertEqual(reading.phase_powers_w, (0.0, 0.0, 1800.0))
            self.assertEqual(reading.phase_currents_a, (0.0, 0.0, 7.5))

    def test_read_meter_accepts_explicit_phase_vectors_and_wh_energy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_meter\nBaseUrl=http://adapter.local\n"
                "[Phase]\nMeasuredPhaseSelection=P1\n"
                "[MeterRequest]\nMethod=GET\nUrl=/meter/state\n"
                "[MeterResponse]\nPowerPath=power\nEnergyWhPath=energy_wh\n"
                "PhasePowersPath=phase_powers\nPhaseCurrentsPath=phase_currents\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse(
                {
                    "power": 2300.0,
                    "energy_wh": 12500.0,
                    "phase_powers": [111.0, 222.0, 333.0],
                    "phase_currents": [1.0, 2.0, 3.0],
                }
            )
            backend = TemplateMeterBackend(self._service(session), config_path=config_path)

            reading = backend.read_meter()

            self.assertIsNone(reading.relay_on)
            self.assertEqual(reading.energy_kwh, 12.5)
            self.assertEqual(reading.phase_selection, "P1")
            self.assertEqual(reading.phase_powers_w, (111.0, 222.0, 333.0))
            self.assertEqual(reading.phase_currents_a, (1.0, 2.0, 3.0))

    def test_read_meter_supports_basic_auth_from_adapter_section(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_meter\nBaseUrl=http://adapter.local\n"
                "Username=user\nPassword=secret\n"
                "[MeterRequest]\nMethod=GET\nUrl=/meter/state\n"
                "[MeterResponse]\nPowerPath=power_w\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse({"power_w": 1234.0})
            backend = TemplateMeterBackend(self._service(session), config_path=config_path)

            reading = backend.read_meter()

            self.assertEqual(reading.power_w, 1234.0)
            session.get.assert_called_once_with(
                url="http://adapter.local/meter/state",
                timeout=2.0,
                auth=("user", "secret"),
            )

    def test_template_meter_requires_request_url_and_power_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_meter\nBaseUrl=http://adapter.local\n"
                "[MeterRequest]\nMethod=GET\n"
                "[MeterResponse]\nPowerPath=\n",
            )

            with self.assertRaises(ValueError):
                TemplateMeterBackend(self._service(MagicMock()), config_path=config_path)

        with tempfile.TemporaryDirectory() as temp_dir:
            missing_url = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_meter\nBaseUrl=http://adapter.local\n"
                "[MeterResponse]\nPowerPath=power_w\n",
            )
            with self.assertRaises(ValueError) as context:
                TemplateMeterBackend(self._service(MagicMock()), config_path=missing_url)
            self.assertEqual(str(context.exception), "Template meter backend requires [MeterRequest] Url")

        with tempfile.TemporaryDirectory() as temp_dir:
            relative_without_base = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_meter\n"
                "[MeterRequest]\nUrl=/meter/state\n"
                "[MeterResponse]\nPowerPath=power_w\n",
            )
            with self.assertRaisesRegex(ValueError, "requires Adapter.BaseUrl"):
                TemplateMeterBackend(self._service(MagicMock()), config_path=relative_without_base)

        with self.assertRaises(FileNotFoundError) as missing_context:
            TemplateMeterBackend(self._service(MagicMock()))
        self.assertEqual(str(missing_context.exception), "template backend config not found: ")

    def test_template_meter_requires_complete_auth_header_pair(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_meter\nBaseUrl=http://adapter.local\n"
                "AuthHeaderName=Authorization\n"
                "[MeterRequest]\nMethod=GET\nUrl=/meter/state\n"
                "[MeterResponse]\nPowerPath=power_w\n",
            )

            with self.assertRaises(ValueError):
                TemplateMeterBackend(self._service(MagicMock()), config_path=config_path)

    def test_template_meter_helper_edges_cover_invalid_vectors_and_enabled_parsing(self) -> None:
        self.assertIsNone(_phase_vector([1.0, 2.0]))
        self.assertIsNone(_resolved_phase_vector((1.0, None, 3.0)))
        self.assertEqual(_phase_vector([1.0, 2.5, "3.75"]), (1.0, 2.5, 3.75))
        self.assertEqual(_resolved_phase_vector((1.0, 0.0, 3.0)), (1.0, 0.0, 3.0))
        self.assertEqual(_resolved_phase_vector((0.0, 2.0, 0.0)), (0.0, 2.0, 0.0))
        self.assertIsNone(TemplateMeterBackend._enabled_state(None))
        self.assertFalse(TemplateMeterBackend._enabled_state(0))
        self.assertTrue(TemplateMeterBackend._enabled_state(1))
        self.assertTrue(TemplateMeterBackend._enabled_state("enabled"))
        self.assertFalse(TemplateMeterBackend._enabled_state("disabled"))
        self.assertIsNone(TemplateMeterBackend._enabled_state("maybe"))

        settings = TemplateMeterSettings(
            base_url="http://meter.local",
            auth_settings=TemplateAuthSettings("", "", False, None, None),
            timeout_seconds=2.0,
            meter_method="GET",
            meter_url="/meter",
            relay_enabled_path=None,
            power_path="power_w",
            voltage_path=None,
            current_path=None,
            energy_kwh_path=None,
            energy_wh_path=None,
            phase_selection="P1",
            phase_selection_path=None,
            phase_powers_path=None,
            phase_currents_path=None,
        )
        with self.assertRaisesRegex(ValueError, "Invalid meter power value"):
            _meter_scalar_values({"power_w": None}, settings)

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_meter\nBaseUrl=http://adapter.local\n"
                "[MeterRequest]\nMethod=GET\nUrl=/meter/state\n"
                "[MeterResponse]\nPowerPath=\n",
            )
            with self.assertRaisesRegex(ValueError, r"requires \[MeterResponse\] PowerPath"):
                TemplateMeterBackend(self._service(MagicMock()), config_path=config_path)

            with self.assertRaises(ValueError) as context:
                TemplateMeterBackend(self._service(MagicMock()), config_path=config_path)
            self.assertEqual(str(context.exception), "Template meter backend requires [MeterResponse] PowerPath")

    def test_service_single_phase_line_normalizes_supported_lines(self) -> None:
        self.assertEqual(_service_single_phase_line(SimpleNamespace(phase="l1")), "L1")
        self.assertEqual(_service_single_phase_line(SimpleNamespace(phase=" L2 ")), "L2")
        self.assertEqual(_service_single_phase_line(SimpleNamespace(phase="L3")), "L3")
        self.assertEqual(_service_single_phase_line(SimpleNamespace(phase="bad")), "L1")
        self.assertEqual(_service_single_phase_line(SimpleNamespace()), "L1")

    def test_settings_helper_contracts_cover_timeout_and_phase_fallback_edges(self) -> None:
        self.assertEqual(_service_timeout_default(SimpleNamespace()), 2.0)
        self.assertEqual(_service_timeout_default(SimpleNamespace(shelly_request_timeout_seconds=0)), 2.0)
        self.assertEqual(_service_timeout_default(SimpleNamespace(shelly_request_timeout_seconds=0.5)), 0.5)

        parser = configparser.ConfigParser()
        parser["Phase"] = {}
        self.assertEqual(_default_phase_selection(parser["Phase"], SimpleNamespace()), "P1")
        self.assertEqual(_default_phase_selection(parser["Phase"], SimpleNamespace(phase="3P")), "P1_P2_P3")

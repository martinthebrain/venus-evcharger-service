# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
import configparser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from venus_evcharger.backend.factory import build_service_backends
from venus_evcharger.backend.goe_charger import GoEChargerBackend
from venus_evcharger.backend.modbus_charger import ModbusChargerBackend
from venus_evcharger.backend.simpleevse_charger import SimpleEvseChargerBackend
from venus_evcharger.backend.smartevse_charger import SmartEvseChargerBackend
from venus_evcharger.backend.template_charger import TemplateChargerBackend


def _service_from_backends_config(
    *,
    mode: str,
    meter_type: str,
    switch_type: str,
    charger_type: str | None,
    meter_config_path: str = "",
    switch_config_path: str = "",
    charger_config_path: str = "",
    host: str = "192.0.2.20",
    phase: str = "L1",
) -> SimpleNamespace:
    parser = configparser.ConfigParser()
    parser.read_string(
        f"""
[DEFAULT]
Host={host}

[Backends]
Mode={mode}
MeterType={meter_type}
SwitchType={switch_type}
ChargerType={charger_type or ""}
MeterConfigPath={meter_config_path}
SwitchConfigPath={switch_config_path}
ChargerConfigPath={charger_config_path}
"""
    )
    return SimpleNamespace(
        config=parser,
        phase=phase,
        host=host,
        pm_component="Switch",
        pm_id=0,
        max_current=16.0,
        session=MagicMock(),
    )


class TestShellyWallboxBackendFactoryChargers(unittest.TestCase):
    def test_build_service_backends_supports_template_and_modbus_chargers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = Path(temp_dir) / "charger.ini"
            charger_path.write_text("[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n[Capabilities]\nSupportedPhaseSelections=P1,P1_P2_P3\n[EnableRequest]\nUrl=/charger/enable\n[CurrentRequest]\nUrl=/charger/current\n[PhaseRequest]\nUrl=/charger/phase\n", encoding="utf-8")
            service = _service_from_backends_config(
                mode="split",
                meter_type="shelly_combined",
                switch_type="shelly_combined",
                charger_type="template_charger",
                charger_config_path=str(charger_path),
            )
            resolved = build_service_backends(service)
            self.assertIsInstance(resolved.charger, TemplateChargerBackend)
            charger_path.write_text("[Adapter]\nType=modbus_charger\nProfile=generic\nTransport=tcp\n[Transport]\nHost=192.0.2.40\nPort=502\nUnitId=7\n[EnableWrite]\nRegisterType=coil\nAddress=20\nTrueValue=1\nFalseValue=0\n[CurrentWrite]\nRegisterType=holding\nAddress=30\nDataType=uint16\nScale=10\n", encoding="utf-8")
            service.config["Backends"]["ChargerType"] = "modbus_charger"
            resolved = build_service_backends(service)
            self.assertIsInstance(resolved.charger, ModbusChargerBackend)

    def test_build_service_backends_supports_simpleevse_and_smartevse_chargers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = Path(temp_dir) / "charger.ini"
            charger_path.write_text("[Adapter]\nType=simpleevse_charger\nTransport=tcp\n[Capabilities]\nSupportedPhaseSelections=P1_P2_P3\n[Transport]\nHost=192.0.2.50\nPort=502\nUnitId=1\n", encoding="utf-8")
            service = _service_from_backends_config(
                mode="split",
                meter_type="none",
                switch_type="none",
                charger_type="simpleevse_charger",
                charger_config_path=str(charger_path),
            )
            resolved = build_service_backends(service)
            self.assertIsInstance(resolved.charger, SimpleEvseChargerBackend)
            charger_path.write_text("[Adapter]\nType=smartevse_charger\nTransport=tcp\n[Capabilities]\nSupportedPhaseSelections=P1_P2\n[Transport]\nHost=192.0.2.60\nPort=502\nUnitId=1\n", encoding="utf-8")
            service.config["Backends"]["ChargerType"] = "smartevse_charger"
            resolved = build_service_backends(service)
            self.assertIsInstance(resolved.charger, SmartEvseChargerBackend)

    def test_build_service_backends_supports_topology_hybrid_external_meter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            meter_path = Path(temp_dir) / "meter.ini"
            switch_path = Path(temp_dir) / "switch.ini"
            charger_path = Path(temp_dir) / "charger.ini"
            meter_path.write_text(
                "[Adapter]\nType=template_meter\nBaseUrl=http://meter.local\n[MeterRequest]\nUrl=/meter/state\n[MeterResponse]\nPowerPath=power_w\nEnergyKwhPath=energy_kwh\n",
                encoding="utf-8",
            )
            switch_path.write_text(
                "[Adapter]\nType=shelly_contactor_switch\nHost=192.0.2.21\n",
                encoding="utf-8",
            )
            charger_path.write_text("[Adapter]\nType=goe_charger\nBaseUrl=http://goe.local\n", encoding="utf-8")
            parser = configparser.ConfigParser()
            parser.read_string(
                f"""
[Topology]
Type=hybrid_topology

[Actuator]
Type=shelly_contactor_switch
ConfigPath={switch_path}

[Measurement]
Type=external_meter
ConfigPath={meter_path}

[Charger]
Type=goe_charger
ConfigPath={charger_path}
"""
            )
            service = SimpleNamespace(
                config=parser,
                phase="L1",
                host="",
                pm_component="Switch",
                pm_id=0,
                max_current=16.0,
                session=MagicMock(),
            )

            resolved = build_service_backends(service)

            self.assertEqual(resolved.runtime.backend_mode, "split")
            self.assertEqual(resolved.runtime.meter_type, "template_meter")
            self.assertEqual(resolved.runtime.switch_type, "shelly_contactor_switch")
            self.assertEqual(resolved.runtime.charger_type, "goe_charger")
            self.assertIsInstance(resolved.charger, GoEChargerBackend)

    def test_build_service_backends_split_mode_none_edges(self) -> None:
        service = _service_from_backends_config(
            mode="split",
            meter_type="none",
            switch_type="shelly_combined",
            charger_type=None,
        )
        with self.assertRaisesRegex(ValueError, "MeterType=none requires a configured charger backend"):
            build_service_backends(service)
        service = _service_from_backends_config(
            mode="split",
            meter_type="shelly_combined",
            switch_type="none",
            charger_type=None,
        )
        with self.assertRaisesRegex(ValueError, "SwitchType=none requires a configured charger backend"):
            build_service_backends(service)

    def test_build_service_backends_combined_mode_none_edges(self) -> None:
        service = _service_from_backends_config(
            mode="combined",
            meter_type="none",
            switch_type="shelly_combined",
            charger_type=None,
        )
        with self.assertRaisesRegex(ValueError, "MeterType=none is only supported in split backend mode"):
            build_service_backends(service)
        service = _service_from_backends_config(
            mode="combined",
            meter_type="shelly_combined",
            switch_type="none",
            charger_type=None,
        )
        with self.assertRaisesRegex(ValueError, "SwitchType=none is only supported in split backend mode"):
            build_service_backends(service)

# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest
from unittest.mock import patch

from venus_evcharger.bootstrap import wizard_adapters


class BootstrapWizardAdaptersContractsTests(unittest.TestCase):
    def test_transport_blocks_are_rendered_exactly(self) -> None:
        self.assertEqual(
            wizard_adapters.serial_transport_block("/dev/ttyUSB1", 7),
            "[Transport]\nDevice=/dev/ttyUSB1\nBaudrate=9600\nParity=N\nStopBits=1\nUnitId=7\n",
        )
        self.assertEqual(
            wizard_adapters.tcp_transport_block("charger.local", 1502, 3),
            "[Transport]\nHost=charger.local\nPort=1502\nUnitId=3\n",
        )
        self.assertEqual(
            wizard_adapters.transport_block(
                "tcp",
                transport_host="tcp.local",
                transport_port=502,
                transport_device="/dev/ignored",
                transport_unit_id=4,
            ),
            "[Transport]\nHost=tcp.local\nPort=502\nUnitId=4\n",
        )
        self.assertEqual(
            wizard_adapters.transport_block(
                "serial_rtu",
                transport_host="ignored.local",
                transport_port=502,
                transport_device="/dev/ttyUSB2",
                transport_unit_id=5,
            ),
            "[Transport]\nDevice=/dev/ttyUSB2\nBaudrate=9600\nParity=N\nStopBits=1\nUnitId=5\n",
        )

    def test_template_and_shelly_adapter_configs_are_rendered_exactly(self) -> None:
        self.assertEqual(
            wizard_adapters.template_meter_config("http://meter.local"),
            (
                "[Adapter]\nType=template_meter\nBaseUrl=http://meter.local\n"
                "[MeterRequest]\nUrl=/wizard/meter\n[MeterResponse]\nPowerPath=power_watts\n"
            ),
        )
        self.assertEqual(
            wizard_adapters.template_switch_config("http://switch.local", "/wizard/p1"),
            (
                "[Adapter]\nType=template_switch\nBaseUrl=http://switch.local\n"
                "[StateRequest]\nUrl=/wizard/p1/state\n[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nUrl=/wizard/p1/control\n"
            ),
        )
        self.assertEqual(
            wizard_adapters.switch_group_config("P1,P1_P2"),
            (
                "[Adapter]\nType=switch_group\n[Members]\n"
                "P1=wizard-phase1-switch.ini\nP2=wizard-phase2-switch.ini\nP3=wizard-phase3-switch.ini\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2\n"
            ),
        )
        self.assertEqual(
            wizard_adapters.template_switch_group_files("http://switch.local", "P1,P1_P2"),
            {
                "wizard-switch-group.ini": (
                    "[Adapter]\nType=switch_group\n[Members]\n"
                    "P1=wizard-phase1-switch.ini\nP2=wizard-phase2-switch.ini\nP3=wizard-phase3-switch.ini\n"
                    "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2\n"
                ),
                "wizard-phase1-switch.ini": (
                    "[Adapter]\nType=template_switch\nBaseUrl=http://switch.local\n"
                    "[StateRequest]\nUrl=/wizard/phase1/state\n[StateResponse]\nEnabledPath=enabled\n"
                    "[CommandRequest]\nUrl=/wizard/phase1/control\n"
                ),
                "wizard-phase2-switch.ini": (
                    "[Adapter]\nType=template_switch\nBaseUrl=http://switch.local\n"
                    "[StateRequest]\nUrl=/wizard/phase2/state\n[StateResponse]\nEnabledPath=enabled\n"
                    "[CommandRequest]\nUrl=/wizard/phase2/control\n"
                ),
                "wizard-phase3-switch.ini": (
                    "[Adapter]\nType=template_switch\nBaseUrl=http://switch.local\n"
                    "[StateRequest]\nUrl=/wizard/phase3/state\n[StateResponse]\nEnabledPath=enabled\n"
                    "[CommandRequest]\nUrl=/wizard/phase3/control\n"
                ),
            },
        )
        self.assertEqual(
            wizard_adapters.template_charger_config("http://charger.local"),
            (
                "[Adapter]\nType=template_charger\nBaseUrl=http://charger.local\n"
                "[EnableRequest]\nUrl=/wizard/charger/enable\n[CurrentRequest]\nUrl=/wizard/charger/current\n"
            ),
        )
        self.assertEqual(
            wizard_adapters.shelly_meter_config("192.168.1.10"),
            "[Adapter]\nType=shelly_meter\nHost=192.168.1.10\nShellyProfile=em1_meter_single_or_dual\n",
        )
        self.assertEqual(
            wizard_adapters.shelly_switch_config("192.168.1.11"),
            "[Adapter]\nType=shelly_switch\nHost=192.168.1.11\nShellyProfile=switch_1ch_with_pm\n",
        )

    def test_tuya_tasmota_and_cerbo_switch_configs_are_rendered_exactly(self) -> None:
        self.assertEqual(
            wizard_adapters.tuya_meter_config("http://tuya.local"),
            (
                "[Adapter]\nType=tuya_meter\nBaseUrl=http://tuya.local\n[MeterRequest]\nUrl=/tuya/meter\n"
                "[MeterResponse]\nRelayEnabledPath=relay_on\nPowerPath=power_watts\nVoltagePath=voltage_volts\n"
                "CurrentPath=current_amps\nEnergyWhPath=energy_wh\n"
            ),
        )
        self.assertEqual(
            wizard_adapters.tuya_switch_config("http://tuya.local"),
            (
                "[Adapter]\nType=tuya_switch\nBaseUrl=http://tuya.local\n[Capabilities]\n"
                "SwitchingMode=direct\nSupportedPhaseSelections=P1\n[StateRequest]\nUrl=/tuya/switch/state\n"
                "[StateResponse]\nEnabledPath=enabled\n[CommandRequest]\nUrl=/tuya/switch/control\n"
                'JsonTemplate={"enabled": $enabled_json}\n'
            ),
        )
        self.assertEqual(
            wizard_adapters.tuya_switch_config("http://tuya.local", contactor=True),
            (
                "[Adapter]\nType=tuya_contactor_switch\nBaseUrl=http://tuya.local\n[Capabilities]\n"
                "SwitchingMode=contactor\nSupportedPhaseSelections=P1\n[StateRequest]\nUrl=/tuya/switch/state\n"
                "[StateResponse]\nEnabledPath=enabled\n[CommandRequest]\nUrl=/tuya/switch/control\n"
                'JsonTemplate={"enabled": $enabled_json}\n'
            ),
        )
        self.assertEqual(
            wizard_adapters.tasmota_meter_config("http://tasmota.local"),
            (
                "[Adapter]\nType=tasmota_meter\nBaseUrl=http://tasmota.local\n[MeterRequest]\nUrl=/cm?cmnd=Status+8\n"
                "[MeterResponse]\nPowerPath=StatusSNS.ENERGY.Power\nVoltagePath=StatusSNS.ENERGY.Voltage\n"
                "CurrentPath=StatusSNS.ENERGY.Current\nEnergyKwhPath=StatusSNS.ENERGY.Total\n"
            ),
        )
        self.assertEqual(
            wizard_adapters.tasmota_switch_config("http://tasmota.local"),
            (
                "[Adapter]\nType=tasmota_switch\nBaseUrl=http://tasmota.local\n[Capabilities]\n"
                "SwitchingMode=direct\nSupportedPhaseSelections=P1\n[StateRequest]\nUrl=/cm?cmnd=Power\n"
                "[StateResponse]\nEnabledPath=POWER\n[CommandRequest]\nMethod=GET\nUrl=/cm?cmnd=Power+$enabled_text\n"
            ),
        )
        self.assertEqual(
            wizard_adapters.tasmota_switch_config("http://tasmota.local", contactor=True),
            (
                "[Adapter]\nType=tasmota_contactor_switch\nBaseUrl=http://tasmota.local\n[Capabilities]\n"
                "SwitchingMode=contactor\nSupportedPhaseSelections=P1\n[StateRequest]\nUrl=/cm?cmnd=Power\n"
                "[StateResponse]\nEnabledPath=POWER\n[CommandRequest]\nMethod=GET\nUrl=/cm?cmnd=Power+$enabled_text\n"
            ),
        )
        self.assertEqual(
            wizard_adapters.cerbo_gx_relay_switch_config(2, " nc "),
            (
                "[Adapter]\nType=cerbo_gx_relay_switch\nRelayIndex=2\nContactMode=NC\nEnsureManualFunction=1\n"
                "VerifySettleSeconds=0.1\nVerifyRetrySeconds=0.2\n[Capabilities]\nSwitchingMode=contactor\n"
                "SupportedPhaseSelections=P1\nRequiresChargePauseForPhaseChange=0\n"
            ),
        )

    def test_modbus_and_native_charger_configs_are_rendered_exactly(self) -> None:
        self.assertEqual(
            wizard_adapters.modbus_charger_config(
                "tcp",
                transport_host="modbus.local",
                transport_port=1502,
                transport_device="/dev/ignored",
                transport_unit_id=6,
            ),
            (
                "[Adapter]\nType=modbus_charger\nProfile=generic\nTransport=tcp\n"
                "[Transport]\nHost=modbus.local\nPort=1502\nUnitId=6\n"
                "[EnableWrite]\nRegisterType=coil\nAddress=20\nTrueValue=1\nFalseValue=0\n"
                "[CurrentWrite]\nRegisterType=holding\nAddress=30\nDataType=uint16\nScale=10\n"
            ),
        )
        self.assertEqual(
            wizard_adapters.modbus_charger_config(
                "serial_rtu",
                transport_host="ignored.local",
                transport_port=1502,
                transport_device="/dev/ttyUSB9",
                transport_unit_id=8,
            ),
            (
                "[Adapter]\nType=modbus_charger\nProfile=generic\nTransport=serial_rtu\n"
                "[Transport]\nDevice=/dev/ttyUSB9\nBaudrate=9600\nParity=N\nStopBits=1\nUnitId=8\n"
                "[EnableWrite]\nRegisterType=coil\nAddress=20\nTrueValue=1\nFalseValue=0\n"
                "[CurrentWrite]\nRegisterType=holding\nAddress=30\nDataType=uint16\nScale=10\n"
            ),
        )
        self.assertEqual(
            wizard_adapters.native_charger_config(
                "goe_charger",
                "http://goe.local",
                charger_preset=None,
                request_timeout_seconds=3.5,
                transport_kind="tcp",
                transport_host="ignored.local",
                transport_port=502,
                transport_device="/dev/ignored",
                transport_unit_id=1,
            ),
            "[Adapter]\nType=goe_charger\nBaseUrl=http://goe.local\nRequestTimeoutSeconds=3.5\n",
        )
        self.assertEqual(
            wizard_adapters.native_charger_config(
                "goe_charger",
                "http://goe.local",
                charger_preset=None,
                request_timeout_seconds=None,
                transport_kind="tcp",
                transport_host="ignored.local",
                transport_port=502,
                transport_device="/dev/ignored",
                transport_unit_id=1,
            ),
            "[Adapter]\nType=goe_charger\nBaseUrl=http://goe.local\n",
        )
        self.assertEqual(
            wizard_adapters.native_charger_config(
                "template_charger",
                "http://template.local",
                charger_preset=None,
                request_timeout_seconds=None,
                transport_kind="tcp",
                transport_host="ignored.local",
                transport_port=502,
                transport_device="/dev/ignored",
                transport_unit_id=1,
            ),
            (
                "[Adapter]\nType=template_charger\nBaseUrl=http://template.local\n"
                "[EnableRequest]\nUrl=/wizard/charger/enable\n[CurrentRequest]\nUrl=/wizard/charger/current\n"
            ),
        )
        expected_modbus_tcp = (
            "[Adapter]\nType=modbus_charger\nProfile=generic\nTransport=tcp\n"
            "[Transport]\nHost=modbus-native.local\nPort=2502\nUnitId=11\n"
            "[EnableWrite]\nRegisterType=coil\nAddress=20\nTrueValue=1\nFalseValue=0\n"
            "[CurrentWrite]\nRegisterType=holding\nAddress=30\nDataType=uint16\nScale=10\n"
        )
        self.assertEqual(
            wizard_adapters.native_charger_config(
                "modbus_charger",
                "http://ignored.local",
                charger_preset=None,
                request_timeout_seconds=None,
                transport_kind="tcp",
                transport_host="modbus-native.local",
                transport_port=2502,
                transport_device="/dev/ignored",
                transport_unit_id=11,
            ),
            expected_modbus_tcp,
        )
        self.assertEqual(
            wizard_adapters._generic_modbus_native_charger_config(
                "tcp",
                transport_host="modbus-native.local",
                transport_port=2502,
                transport_device="/dev/ignored",
                transport_unit_id=11,
            ),
            expected_modbus_tcp,
        )
        expected_modbus_serial = (
            "[Adapter]\nType=modbus_charger\nProfile=generic\nTransport=serial_rtu\n"
            "[Transport]\nDevice=/dev/ttyUSB12\nBaudrate=9600\nParity=N\nStopBits=1\nUnitId=13\n"
            "[EnableWrite]\nRegisterType=coil\nAddress=20\nTrueValue=1\nFalseValue=0\n"
            "[CurrentWrite]\nRegisterType=holding\nAddress=30\nDataType=uint16\nScale=10\n"
        )
        self.assertEqual(
            wizard_adapters.native_charger_config(
                "modbus_charger",
                "http://ignored.local",
                charger_preset=None,
                request_timeout_seconds=None,
                transport_kind="serial_rtu",
                transport_host="ignored.local",
                transport_port=2502,
                transport_device="/dev/ttyUSB12",
                transport_unit_id=13,
            ),
            expected_modbus_serial,
        )
        self.assertEqual(
            wizard_adapters._generic_modbus_native_charger_config(
                "serial_rtu",
                transport_host="ignored.local",
                transport_port=2502,
                transport_device="/dev/ttyUSB12",
                transport_unit_id=13,
            ),
            expected_modbus_serial,
        )
        self.assertEqual(
            wizard_adapters.native_charger_config(
                "simpleevse_charger",
                "http://simple.local",
                charger_preset=None,
                request_timeout_seconds=2.0,
                transport_kind="serial_rtu",
                transport_host="ignored.local",
                transport_port=502,
                transport_device="/dev/ttyUSB4",
                transport_unit_id=9,
            ),
            (
                "[Adapter]\nType=simpleevse_charger\nRequestTimeoutSeconds=2\nTransport=serial_rtu\n"
                "[Transport]\nDevice=/dev/ttyUSB4\nBaudrate=9600\nParity=N\nStopBits=1\nUnitId=9\n"
            ),
        )
        expected_custom_tcp = (
            "[Adapter]\nType=smart_custom\nRequestTimeoutSeconds=2.75\nTransport=tcp\n"
            "[Transport]\nHost=custom.local\nPort=3502\nUnitId=12\n"
        )
        self.assertEqual(
            wizard_adapters.native_charger_config(
                "smart_custom",
                "http://custom.local",
                charger_preset=None,
                request_timeout_seconds=2.75,
                transport_kind="tcp",
                transport_host="custom.local",
                transport_port=3502,
                transport_device="/dev/ignored",
                transport_unit_id=12,
            ),
            expected_custom_tcp,
        )
        self.assertEqual(
            wizard_adapters._transport_native_charger_config(
                "smart_custom",
                "RequestTimeoutSeconds=2.75\n",
                transport_kind="tcp",
                transport_host="custom.local",
                transport_port=3502,
                transport_device="/dev/ignored",
                transport_unit_id=12,
            ),
            expected_custom_tcp,
        )
        with patch("venus_evcharger.bootstrap.wizard_adapters.render_charger_preset_config", return_value="preset\n") as preset:
            self.assertEqual(
                wizard_adapters.native_charger_config(
                    "goe_charger",
                    "http://ignored.local",
                    charger_preset="vendor-preset",
                    request_timeout_seconds=2.0,
                    transport_kind="tcp",
                    transport_host="preset.local",
                    transport_port=502,
                    transport_device="/dev/ttyUSB0",
                    transport_unit_id=2,
                ),
                "preset\n",
            )
        preset.assert_called_once_with(
            "vendor-preset",
            transport_kind="tcp",
            transport_host="preset.local",
            transport_port=502,
            transport_device="/dev/ttyUSB0",
            transport_unit_id=2,
        )


if __name__ == "__main__":
    unittest.main()

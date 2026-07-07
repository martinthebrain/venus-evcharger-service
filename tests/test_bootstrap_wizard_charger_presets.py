# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest

from venus_evcharger.bootstrap import wizard_charger_presets as presets


class BootstrapWizardChargerPresetTests(unittest.TestCase):
    def test_preset_identity_and_defaults(self) -> None:
        self.assertEqual(
            presets.CHARGER_PRESET_LABELS,
            (
                ("abb-terra-ac-modbus", "ABB Terra AC over Modbus"),
                ("cfos-power-brain-modbus", "cFos Power Brain over Modbus"),
                ("openwb-modbus-secondary", "openWB secondary over Modbus"),
            ),
        )
        self.assertEqual(
            presets.CHARGER_PRESET_VALUES,
            ("abb-terra-ac-modbus", "cfos-power-brain-modbus", "openwb-modbus-secondary"),
        )
        self.assertEqual(presets.charger_preset_backend("abb-terra-ac-modbus"), "modbus_charger")
        self.assertEqual(presets.charger_preset_backend("cfos-power-brain-modbus"), "modbus_charger")
        self.assertEqual(presets.charger_preset_backend("openwb-modbus-secondary"), "modbus_charger")
        self.assertIsNone(presets.charger_preset_backend(None))
        self.assertIsNone(presets.charger_preset_backend("unsupported"))
        self.assertEqual(
            presets.apply_charger_preset_backend("cfos-power-brain-modbus", "template_charger"),
            "modbus_charger",
        )
        self.assertEqual(presets.apply_charger_preset_backend(None, "template_charger"), "template_charger")
        self.assertIsNone(presets.apply_charger_preset_backend("unknown", None))

    def test_relevant_preset_and_transport_contracts(self) -> None:
        self.assertEqual(
            presets.relevant_charger_presets("modbus_charger"),
            ("abb-terra-ac-modbus", "cfos-power-brain-modbus", "openwb-modbus-secondary"),
        )
        self.assertEqual(presets.relevant_charger_presets(None), ())
        self.assertEqual(presets.relevant_charger_presets("template_charger"), ())

        self.assertEqual(presets.preset_transport_port("abb-terra-ac-modbus", "tcp"), 502)
        self.assertEqual(presets.preset_transport_port("cfos-power-brain-modbus", "tcp"), 4701)
        self.assertEqual(presets.preset_transport_port("openwb-modbus-secondary", "tcp"), 1502)
        self.assertIsNone(presets.preset_transport_port("abb-terra-ac-modbus", "serial_rtu"))
        self.assertIsNone(presets.preset_transport_port(None, "tcp"))
        self.assertIsNone(presets.preset_transport_port("unknown", "tcp"))

        self.assertEqual(presets.preset_transport_unit_id("abb-terra-ac-modbus"), 1)
        self.assertEqual(presets.preset_transport_unit_id("cfos-power-brain-modbus"), 1)
        self.assertEqual(presets.preset_transport_unit_id("openwb-modbus-secondary"), 1)
        self.assertIsNone(presets.preset_transport_unit_id(None))
        self.assertIsNone(presets.preset_transport_unit_id("unknown"))

    def test_abb_terra_ac_tcp_config_contract(self) -> None:
        self.assertEqual(
            _render("abb-terra-ac-modbus", host="abb.local", port=502, unit_id=3),
            "[Adapter]\n"
            "Type=modbus_charger\n"
            "Profile=generic\n"
            "Preset=abb-terra-ac-modbus\n"
            "Transport=tcp\n"
            "[Transport]\n"
            "Host=abb.local\n"
            "Port=502\n"
            "UnitId=3\n"
            "[StateCurrent]\n"
            "RegisterType=holding\n"
            "Address=16398\n"
            "DataType=uint16\n"
            "Scale=1000\n"
            "[StateStatus]\n"
            "RegisterType=holding\n"
            "Address=16396\n"
            "DataType=uint16\n"
            "[StateFault]\n"
            "RegisterType=holding\n"
            "Address=16392\n"
            "DataType=uint16\n"
            "[EnableWrite]\n"
            "RegisterType=holding\n"
            "Address=16645\n"
            "TrueValue=0\n"
            "FalseValue=1\n"
            "[CurrentWrite]\n"
            "RegisterType=holding\n"
            "Address=16640\n"
            "DataType=uint16\n"
            "Scale=1000\n",
        )

    def test_cfos_power_brain_tcp_config_contract(self) -> None:
        self.assertEqual(
            _render("cfos-power-brain-modbus", host="cfos.local", port=4701, unit_id=4),
            "[Adapter]\n"
            "Type=modbus_charger\n"
            "Profile=generic\n"
            "Preset=cfos-power-brain-modbus\n"
            "Transport=tcp\n"
            "[Transport]\n"
            "Host=cfos.local\n"
            "Port=4701\n"
            "UnitId=4\n"
            "[Capabilities]\n"
            "SupportedPhaseSelections=P1,P1_P2_P3\n"
            "[StateCurrent]\n"
            "RegisterType=holding\n"
            "Address=8093\n"
            "DataType=uint16\n"
            "Scale=10\n"
            "[StateActualCurrent]\n"
            "RegisterType=holding\n"
            "Address=8095\n"
            "DataType=uint16\n"
            "Scale=10\n"
            "[StatePower]\n"
            "RegisterType=holding\n"
            "Address=8062\n"
            "DataType=int32\n"
            "[StateStatus]\n"
            "RegisterType=holding\n"
            "Address=8092\n"
            "DataType=uint16\n"
            "Map=0:waiting,1:vehicle-detected,2:charging,3:charging-ventilation,4:no-current,"
            "5:error,9:dc-sensor-error\n"
            "[EnableWrite]\n"
            "RegisterType=holding\n"
            "Address=8094\n"
            "TrueValue=1\n"
            "FalseValue=0\n"
            "[CurrentWrite]\n"
            "RegisterType=holding\n"
            "Address=8093\n"
            "DataType=uint16\n"
            "Scale=10\n"
            "[PhaseWrite]\n"
            "RegisterType=holding\n"
            "Address=8087\n"
            "DataType=uint16\n"
            "Map=P1:1,P1_P2_P3:0\n",
        )

    def test_openwb_secondary_tcp_config_contract(self) -> None:
        self.assertEqual(
            _render("openwb-modbus-secondary", host="openwb.local", port=1502, unit_id=5),
            "[Adapter]\n"
            "Type=modbus_charger\n"
            "Profile=generic\n"
            "Preset=openwb-modbus-secondary\n"
            "Transport=tcp\n"
            "[Transport]\n"
            "Host=openwb.local\n"
            "Port=1502\n"
            "UnitId=5\n"
            "[Capabilities]\n"
            "SupportedPhaseSelections=P1\n"
            "EnableUsesCurrentWrite=1\n"
            "EnableDefaultCurrentAmps=6\n"
            "[StateCurrent]\n"
            "RegisterType=input\n"
            "Address=10116\n"
            "DataType=int16\n"
            "Scale=100\n"
            "[StatePower]\n"
            "RegisterType=input\n"
            "Address=10100\n"
            "DataType=int32\n"
            "[StateEnergy]\n"
            "RegisterType=input\n"
            "Address=10102\n"
            "DataType=int32\n"
            "Scale=1000\n"
            "[StateStatus]\n"
            "RegisterType=input\n"
            "Address=10115\n"
            "DataType=int16\n"
            "Map=0:idle,1:charging\n"
            "[CurrentWrite]\n"
            "RegisterType=holding\n"
            "Address=10171\n"
            "DataType=int16\n"
            "Scale=100\n",
        )

    def test_serial_transport_and_unsupported_preset_contracts(self) -> None:
        self.assertEqual(
            _render(
                "abb-terra-ac-modbus",
                transport_kind="serial_rtu",
                device="/dev/ttyUSB7",
                unit_id=9,
            ).split("[StateCurrent]\n", 1)[0],
            "[Adapter]\n"
            "Type=modbus_charger\n"
            "Profile=generic\n"
            "Preset=abb-terra-ac-modbus\n"
            "Transport=serial_rtu\n"
            "[Transport]\n"
            "Device=/dev/ttyUSB7\n"
            "Baudrate=9600\n"
            "Parity=N\n"
            "StopBits=1\n"
            "UnitId=9\n",
        )
        self.assertEqual(
            _render(
                "cfos-power-brain-modbus",
                transport_kind="serial_rtu",
                device="/dev/ttyUSB8",
                unit_id=10,
            ).split("[Capabilities]\n", 1)[0],
            "[Adapter]\n"
            "Type=modbus_charger\n"
            "Profile=generic\n"
            "Preset=cfos-power-brain-modbus\n"
            "Transport=serial_rtu\n"
            "[Transport]\n"
            "Device=/dev/ttyUSB8\n"
            "Baudrate=9600\n"
            "Parity=N\n"
            "StopBits=1\n"
            "UnitId=10\n",
        )
        with self.assertRaises(ValueError) as unsupported:
            _render("unknown")
        self.assertEqual(str(unsupported.exception), "Unsupported charger preset 'unknown'")


def _render(
    charger_preset: str,
    *,
    transport_kind: str = "tcp",
    host: str = "charger.local",
    port: int = 1502,
    device: str = "/dev/ttyUSB0",
    unit_id: int = 1,
) -> str:
    return presets.render_charger_preset_config(
        charger_preset,
        transport_kind=transport_kind,
        transport_host=host,
        transport_port=port,
        transport_device=device,
        transport_unit_id=unit_id,
    )


if __name__ == "__main__":
    unittest.main()

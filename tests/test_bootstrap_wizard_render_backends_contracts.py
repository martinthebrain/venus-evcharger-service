# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest
from typing import cast

from venus_evcharger.bootstrap.wizard_render_backends import (
    _actuator_backend_lines,
    _adapter_type_from_file,
    _charger_backend_lines,
    _measurement_backend_lines,
    render_legacy_backends_from_topology,
)
from venus_evcharger.topology.schema import (
    ActuatorConfig,
    ChargerConfig,
    EvChargerTopologyConfig,
    MeasurementConfig,
    MeasurementType,
    PolicyConfig,
    TopologyConfig,
)


class WizardRenderBackendsContractTests(unittest.TestCase):
    def test_simple_relay_without_adapter_files_has_no_legacy_backend_section(self) -> None:
        topology = EvChargerTopologyConfig(
            topology=TopologyConfig(type="simple_relay"),
            policy=PolicyConfig(mode="manual", phase="L1"),
        )

        self.assertEqual(render_legacy_backends_from_topology(topology, {}), [])

    def test_non_simple_topology_without_adapter_files_still_renders_split_defaults(self) -> None:
        topology = EvChargerTopologyConfig(
            topology=TopologyConfig(type="native_device"),
            policy=PolicyConfig(mode="manual", phase="L1"),
        )

        self.assertEqual(
            render_legacy_backends_from_topology(topology, {}),
            ["Mode=split", "MeterType=none", "SwitchType=none", "ChargerType="],
        )

    def test_full_split_backend_lines_preserve_measurement_switch_charger_order(self) -> None:
        topology = EvChargerTopologyConfig(
            topology=TopologyConfig(type="hybrid_topology"),
            actuator=ActuatorConfig(type="shelly_switch", config_path="switch.ini"),
            measurement=MeasurementConfig(type="external_meter", config_path="meter.ini"),
            charger=ChargerConfig(type="goe_charger", config_path="charger.ini"),
            policy=PolicyConfig(mode="auto", phase="L2"),
        )
        adapter_files = {
            "meter.ini": "[Adapter]\nType=template_meter\n",
            "switch.ini": "[Adapter]\nType=shelly_switch\n",
            "charger.ini": "[Adapter]\nType=goe_charger\n",
        }

        self.assertEqual(
            render_legacy_backends_from_topology(topology, adapter_files),
            [
                "Mode=split",
                "MeterType=template_meter",
                "MeterConfigPath=meter.ini",
                "SwitchType=shelly_switch",
                "SwitchConfigPath=switch.ini",
                "ChargerType=goe_charger",
                "ChargerConfigPath=charger.ini",
            ],
        )

    def test_non_external_measurement_modes_render_as_no_meter_backend(self) -> None:
        no_meter_types: tuple[MeasurementType, ...] = (
            "none",
            "charger_native",
            "actuator_native",
            "fixed_reference",
            "learned_reference",
        )

        for measurement_type in no_meter_types:
            with self.subTest(measurement_type=measurement_type):
                self.assertEqual(
                    _measurement_backend_lines(MeasurementConfig(type=measurement_type), {}),
                    ["MeterType=none"],
                )

    def test_external_meter_requires_config_path_and_adapter_file(self) -> None:
        self.assertEqual(
            _measurement_backend_lines(
                MeasurementConfig(type="external_meter", config_path="meter.ini"),
                {"meter.ini": "[Adapter]\nType = template_meter \n"},
            ),
            ["MeterType=template_meter", "MeterConfigPath=meter.ini"],
        )
        with self.assertRaisesRegex(ValueError, "unsupported legacy meter mapping"):
            _measurement_backend_lines(MeasurementConfig(type="external_meter"), {})
        with self.assertRaisesRegex(ValueError, "unsupported legacy meter mapping"):
            _measurement_backend_lines(MeasurementConfig(type=cast(MeasurementType, "mystery"), config_path="meter.ini"), {})

    def test_actuator_and_charger_backend_lines_include_paths_only_when_present(self) -> None:
        self.assertEqual(
            _actuator_backend_lines(EvChargerTopologyConfig(topology=TopologyConfig(type="custom_topology"))),
            ["SwitchType=none"],
        )
        self.assertEqual(
            _actuator_backend_lines(
                EvChargerTopologyConfig(
                    topology=TopologyConfig(type="simple_relay"),
                    actuator=ActuatorConfig(type="template_switch", config_path=""),
                )
            ),
            ["SwitchType=template_switch"],
        )
        self.assertEqual(
            _actuator_backend_lines(
                EvChargerTopologyConfig(
                    topology=TopologyConfig(type="simple_relay"),
                    actuator=ActuatorConfig(type="template_switch", config_path="switch.ini"),
                )
            ),
            ["SwitchType=template_switch", "SwitchConfigPath=switch.ini"],
        )

        self.assertEqual(
            _charger_backend_lines(EvChargerTopologyConfig(topology=TopologyConfig(type="simple_relay"))),
            ["ChargerType="],
        )
        self.assertEqual(
            _charger_backend_lines(
                EvChargerTopologyConfig(
                    topology=TopologyConfig(type="native_device"),
                    charger=ChargerConfig(type="goe_charger", config_path=""),
                )
            ),
            ["ChargerType=goe_charger"],
        )
        self.assertEqual(
            _charger_backend_lines(
                EvChargerTopologyConfig(
                    topology=TopologyConfig(type="native_device"),
                    charger=ChargerConfig(type="goe_charger", config_path="charger.ini"),
                )
            ),
            ["ChargerType=goe_charger", "ChargerConfigPath=charger.ini"],
        )

    def test_adapter_type_contract_rejects_missing_or_empty_adapter_type(self) -> None:
        self.assertEqual(_adapter_type_from_file({"meter.ini": "[Adapter]\nType= template_meter \n"}, "meter.ini"), "template_meter")
        with self.assertRaisesRegex(ValueError, "missing adapter file"):
            _adapter_type_from_file({}, "meter.ini")
        with self.assertRaisesRegex(ValueError, "missing required \\[Adapter\\] section"):
            _adapter_type_from_file({"meter.ini": "[DEFAULT]\nType=template_meter\n"}, "meter.ini")
        with self.assertRaisesRegex(ValueError, "missing Adapter.Type"):
            _adapter_type_from_file({"meter.ini": "[Adapter]\nOther=template_meter\n"}, "meter.ini")
        with self.assertRaisesRegex(ValueError, "missing Adapter.Type"):
            _adapter_type_from_file({"meter.ini": "[Adapter]\ntype=template_meter\n"}, "meter.ini")
        with self.assertRaisesRegex(ValueError, "missing Adapter.Type"):
            _adapter_type_from_file({"meter.ini": "[Adapter]\nTYPE=template_meter\n"}, "meter.ini")
        with self.assertRaisesRegex(ValueError, "missing Adapter.Type"):
            _adapter_type_from_file({"meter.ini": "[Adapter]\nType=\n"}, "meter.ini")


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest
from typing import cast

from venus_evcharger.bootstrap.wizard_models import WizardAnswers, WizardChargerBackend, WizardProfile
from venus_evcharger.bootstrap.wizard_topology import build_wizard_topology_config
from venus_evcharger.topology import (
    ActuatorConfig,
    ActuatorType,
    ChargerConfig,
    ChargerType,
    EvChargerTopologyConfig,
    MeasurementConfig,
    PolicyConfig,
    TopologyConfig,
    TopologyType,
)


def _answers(
    *,
    profile: WizardProfile,
    topology_preset: str | None = None,
    charger_backend: WizardChargerBackend | None = None,
) -> WizardAnswers:
    return WizardAnswers(
        profile=profile,
        host_input="host.local",
        meter_host_input="meter.local",
        switch_host_input="switch.local",
        charger_host_input="charger.local",
        device_instance=71,
        phase="L3",
        policy_mode="scheduled",
        digest_auth=False,
        username="",
        password="",
        topology_preset=topology_preset,
        charger_backend=charger_backend,
    )


def _expected(
    *,
    topology_type: TopologyType,
    actuator: ActuatorConfig | None,
    measurement: MeasurementConfig | None,
    charger: ChargerConfig | None,
) -> EvChargerTopologyConfig:
    return EvChargerTopologyConfig(
        topology=TopologyConfig(type=topology_type),
        actuator=actuator,
        measurement=measurement,
        charger=charger,
        policy=PolicyConfig(mode="scheduled", phase="L3"),
    )


class WizardTopologyContractTests(unittest.TestCase):
    def test_profile_topologies_preserve_exact_policy_and_default_roles(self) -> None:
        self.assertEqual(
            build_wizard_topology_config(_answers(profile="simple_relay")),
            _expected(
                topology_type="simple_relay",
                actuator=ActuatorConfig(type="shelly_contactor_switch"),
                measurement=MeasurementConfig(type="actuator_native"),
                charger=None,
            ),
        )
        self.assertEqual(
            build_wizard_topology_config(_answers(profile="advanced_manual")),
            _expected(
                topology_type="simple_relay",
                actuator=ActuatorConfig(type="shelly_contactor_switch"),
                measurement=MeasurementConfig(type="actuator_native"),
                charger=None,
            ),
        )
        self.assertEqual(
            build_wizard_topology_config(_answers(profile="native_device")),
            _expected(
                topology_type="native_device",
                actuator=None,
                measurement=MeasurementConfig(type="charger_native"),
                charger=ChargerConfig(type="goe_charger", config_path="wizard-charger.ini"),
            ),
        )
        self.assertEqual(
            build_wizard_topology_config(_answers(profile="native_device", charger_backend="modbus_charger")),
            _expected(
                topology_type="native_device",
                actuator=None,
                measurement=MeasurementConfig(type="charger_native"),
                charger=ChargerConfig(type="modbus_charger", config_path="wizard-charger.ini"),
            ),
        )
        self.assertEqual(
            build_wizard_topology_config(_answers(profile="hybrid_topology")),
            _expected(
                topology_type="hybrid_topology",
                actuator=ActuatorConfig(type="switch_group", config_path="wizard-switch-group.ini"),
                measurement=MeasurementConfig(type="charger_native"),
                charger=ChargerConfig(type="simpleevse_charger", config_path="wizard-charger.ini"),
            ),
        )
        self.assertEqual(
            build_wizard_topology_config(_answers(profile="hybrid_topology", charger_backend="smartevse_charger")),
            _expected(
                topology_type="hybrid_topology",
                actuator=ActuatorConfig(type="switch_group", config_path="wizard-switch-group.ini"),
                measurement=MeasurementConfig(type="charger_native"),
                charger=ChargerConfig(type="smartevse_charger", config_path="wizard-charger.ini"),
            ),
        )

    def test_cerbo_relay_presets_map_to_simple_relay_with_external_meter(self) -> None:
        for preset in (
            "template-meter-cerbo-relay",
            "shelly-meter-cerbo-relay",
            "tasmota-meter-cerbo-relay",
            "tuya-meter-cerbo-relay",
        ):
            with self.subTest(preset=preset):
                self.assertEqual(
                    build_wizard_topology_config(_answers(profile="multi_adapter_topology", topology_preset=preset)),
                    _expected(
                        topology_type="simple_relay",
                        actuator=ActuatorConfig(type="cerbo_gx_relay_switch", config_path="wizard-switch.ini"),
                        measurement=MeasurementConfig(type="external_meter", config_path="wizard-meter.ini"),
                        charger=None,
                    ),
                )

    def test_hybrid_external_meter_presets_map_switch_and_charger_pairs(self) -> None:
        cases: dict[str, tuple[ActuatorType, str, ChargerType]] = {
            "template-stack": ("template_switch", "wizard-switch.ini", "template_charger"),
            "shelly-io-template-charger": ("shelly_switch", "wizard-switch.ini", "template_charger"),
            "tuya-io-template-charger": ("tuya_switch", "wizard-switch.ini", "template_charger"),
            "tasmota-io-template-charger": ("tasmota_switch", "wizard-switch.ini", "template_charger"),
            "shelly-io-modbus-charger": ("shelly_switch", "wizard-switch.ini", "modbus_charger"),
            "tuya-io-modbus-charger": ("tuya_switch", "wizard-switch.ini", "modbus_charger"),
            "tasmota-io-modbus-charger": ("tasmota_switch", "wizard-switch.ini", "modbus_charger"),
            "template-meter-goe-switch-group": ("switch_group", "wizard-switch-group.ini", "goe_charger"),
            "shelly-meter-goe-switch-group": ("switch_group", "wizard-switch-group.ini", "goe_charger"),
            "shelly-meter-modbus-switch-group": ("switch_group", "wizard-switch-group.ini", "modbus_charger"),
        }
        for preset, (actuator_type, switch_config_name, charger_type) in cases.items():
            with self.subTest(preset=preset):
                self.assertEqual(
                    build_wizard_topology_config(_answers(profile="multi_adapter_topology", topology_preset=preset)),
                    _expected(
                        topology_type="hybrid_topology",
                        actuator=ActuatorConfig(type=actuator_type, config_path=switch_config_name),
                        measurement=MeasurementConfig(type="external_meter", config_path="wizard-meter.ini"),
                        charger=ChargerConfig(type=charger_type, config_path="wizard-charger.ini"),
                    ),
                )

    def test_missing_split_preset_uses_template_stack_defaults(self) -> None:
        self.assertEqual(
            build_wizard_topology_config(_answers(profile="multi_adapter_topology", topology_preset=None)),
            _expected(
                topology_type="hybrid_topology",
                actuator=ActuatorConfig(type="template_switch", config_path="wizard-switch.ini"),
                measurement=MeasurementConfig(type="external_meter", config_path="wizard-meter.ini"),
                charger=ChargerConfig(type="template_charger", config_path="wizard-charger.ini"),
            ),
        )

    def test_native_external_meter_presets_map_charger_types(self) -> None:
        cases: dict[str, ChargerType] = {
            "shelly-meter-goe": "goe_charger",
            "tuya-meter-goe": "goe_charger",
            "tasmota-meter-goe": "goe_charger",
            "shelly-meter-modbus-charger": "modbus_charger",
            "tuya-meter-modbus-charger": "modbus_charger",
            "tasmota-meter-modbus-charger": "modbus_charger",
        }
        for preset, charger_type in cases.items():
            with self.subTest(preset=preset):
                self.assertEqual(
                    build_wizard_topology_config(_answers(profile="multi_adapter_topology", topology_preset=preset)),
                    _expected(
                        topology_type="native_device",
                        actuator=None,
                        measurement=MeasurementConfig(type="external_meter", config_path="wizard-meter.ini"),
                        charger=ChargerConfig(type=charger_type, config_path="wizard-charger.ini"),
                    ),
                )

    def test_hybrid_charger_native_presets_map_to_switch_group_and_charger_native_measurement(self) -> None:
        self.assertEqual(
            build_wizard_topology_config(
                _answers(profile="multi_adapter_topology", topology_preset="goe-external-switch-group")
            ),
            _expected(
                topology_type="hybrid_topology",
                actuator=ActuatorConfig(type="switch_group", config_path="wizard-switch-group.ini"),
                measurement=MeasurementConfig(type="charger_native"),
                charger=ChargerConfig(type="goe_charger", config_path="wizard-charger.ini"),
            ),
        )

    def test_unsupported_profile_and_preset_raise_exact_errors(self) -> None:
        with self.assertRaisesRegex(ValueError, r"^unsupported wizard profile for topology mapping: unsupported$"):
            build_wizard_topology_config(_answers(profile=cast(WizardProfile, "unsupported")))
        with self.assertRaisesRegex(ValueError, r"^unsupported topology preset for topology mapping: unsupported-preset$"):
            build_wizard_topology_config(
                _answers(profile="multi_adapter_topology", topology_preset="unsupported-preset")
            )


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for wizard generated device inventories."""

from __future__ import annotations

import json
import unittest
from dataclasses import asdict
from typing import Any

from venus_evcharger.bootstrap.wizard_inventory import (
    _adapter_type_for_topology_preset,
    _charger_capabilities,
    _endpoint,
    _group_measurement_binding,
    _measurement_adapter_type,
    _measurement_profile_label,
    _measurement_type,
    _phase_endpoint,
    _phase_scope,
    _profile_id,
    _profile_label,
    _switch_group_phase_scope,
    _switch_supports_feedback,
    _switching_mode,
    _supports_phase_selection,
    build_wizard_inventory,
    inventory_payload,
    inventory_text,
)
from venus_evcharger.bootstrap.wizard_models import WizardAnswers
from venus_evcharger.inventory import DeviceInstance
from venus_evcharger.topology import (
    ActuatorConfig,
    ChargerConfig,
    EvChargerTopologyConfig,
    MeasurementConfig,
    PolicyConfig,
    TopologyConfig,
)


def _answers(**overrides: Any) -> WizardAnswers:
    values: dict[str, Any] = {
        "profile": "multi_adapter_topology",
        "host_input": "fallback.local",
        "meter_host_input": "meter.local",
        "switch_host_input": "switch.local",
        "charger_host_input": "charger.local",
        "device_instance": 5,
        "phase": "L1",
        "policy_mode": "manual",
        "digest_auth": False,
        "username": "",
        "password": "",
        "topology_preset": "shelly-meter-goe",
        "charger_backend": "goe_charger",
        "switch_group_supported_phase_selections": "P1,P1_P2,P1_P2_P3",
        "cerbo_relay_index": 1,
    }
    values.update(overrides)
    return WizardAnswers(**values)


def _json_payload(value: object) -> dict[str, Any]:
    return json.loads(json.dumps(asdict(value)))


class WizardInventoryContractTests(unittest.TestCase):
    def test_direct_switch_with_native_measurement_contract(self) -> None:
        answers = _answers(phase="L2")
        topology = EvChargerTopologyConfig(
            topology=TopologyConfig(type="simple_relay"),
            actuator=ActuatorConfig(type="shelly_contactor_switch"),
            measurement=MeasurementConfig(type="actuator_native"),
            policy=PolicyConfig(mode="manual", phase="L2"),
        )

        inventory = build_wizard_inventory(answers, {"switch": "  http://switch.local  "}, topology)

        self.assertEqual(
            _json_payload(inventory),
            {
                "profiles": [
                    {
                        "id": "switch_shelly_contactor_switch",
                        "label": "Shelly Contactor Switch",
                        "vendor": None,
                        "model": None,
                        "description": None,
                        "capabilities": [
                            {
                                "id": "switch",
                                "kind": "switch",
                                "adapter_type": "shelly_contactor_switch",
                                "supported_phases": ["L2"],
                                "channel": None,
                                "measures_power": False,
                                "measures_energy": False,
                                "switching_mode": "contactor",
                                "supports_feedback": True,
                                "supports_phase_selection": False,
                            },
                            {
                                "id": "meter",
                                "kind": "meter",
                                "adapter_type": "shelly_contactor_switch",
                                "supported_phases": ["L2"],
                                "channel": None,
                                "measures_power": True,
                                "measures_energy": True,
                                "switching_mode": None,
                                "supports_feedback": False,
                                "supports_phase_selection": False,
                            },
                        ],
                    }
                ],
                "devices": [
                    {
                        "id": "switch_device",
                        "profile_id": "switch_shelly_contactor_switch",
                        "label": "Switch device",
                        "endpoint": "http://switch.local",
                        "enabled": True,
                        "notes": None,
                    }
                ],
                "bindings": [
                    {
                        "id": "actuation",
                        "role": "actuation",
                        "label": "Actuation",
                        "phase_scope": ["L2"],
                        "members": [{"device_id": "switch_device", "capability_id": "switch", "phases": ["L2"]}],
                    },
                    {
                        "id": "measurement",
                        "role": "measurement",
                        "label": "Measurement",
                        "phase_scope": ["L2"],
                        "members": [{"device_id": "switch_device", "capability_id": "meter", "phases": ["L2"]}],
                    },
                ],
            },
        )
        self.assertEqual([profile.id for profile in inventory.profiles], ["switch_shelly_contactor_switch"])
        self.assertEqual(inventory.profiles[0].label, "Shelly Contactor Switch")
        self.assertEqual([capability.id for capability in inventory.profiles[0].capabilities], ["switch", "meter"])
        switch_capability, meter_capability = inventory.profiles[0].capabilities
        self.assertEqual(switch_capability.kind, "switch")
        self.assertEqual(switch_capability.supported_phases, ("L2",))
        self.assertEqual(switch_capability.switching_mode, "contactor")
        self.assertTrue(switch_capability.supports_feedback)
        self.assertFalse(switch_capability.supports_phase_selection)
        self.assertEqual(meter_capability.kind, "meter")
        self.assertTrue(meter_capability.measures_power)
        self.assertTrue(meter_capability.measures_energy)
        self.assertEqual(inventory.devices[0].endpoint, "http://switch.local")
        self.assertEqual([(binding.id, binding.role) for binding in inventory.bindings], [("actuation", "actuation"), ("measurement", "measurement")])
        self.assertEqual(inventory.bindings[1].members[0].device_id, "switch_device")
        self.assertEqual(inventory.bindings[1].members[0].phases, ("L2",))

    def test_switch_group_generates_phase_devices_and_measurement_members(self) -> None:
        answers = _answers(phase="3P", switch_group_supported_phase_selections="P1,P1_P2")
        topology = EvChargerTopologyConfig(
            topology=TopologyConfig(type="simple_relay"),
            actuator=ActuatorConfig(type="switch_group"),
            measurement=MeasurementConfig(type="actuator_native"),
            policy=PolicyConfig(mode="manual", phase="3P"),
        )

        inventory = build_wizard_inventory(answers, {"switch": "switch.local/root/"}, topology)

        self.assertEqual(
            _json_payload(inventory),
            {
                "profiles": [
                    {
                        "id": "switch_switch_group_member",
                        "label": "Switch group member",
                        "vendor": None,
                        "model": None,
                        "description": None,
                        "capabilities": [
                            {
                                "id": "switch",
                                "kind": "switch",
                                "adapter_type": "template_switch",
                                "supported_phases": ["L1", "L2", "L3"],
                                "channel": None,
                                "measures_power": False,
                                "measures_energy": False,
                                "switching_mode": "contactor",
                                "supports_feedback": True,
                                "supports_phase_selection": False,
                            },
                            {
                                "id": "meter",
                                "kind": "meter",
                                "adapter_type": "template_switch",
                                "supported_phases": ["L1", "L2", "L3"],
                                "channel": None,
                                "measures_power": True,
                                "measures_energy": True,
                                "switching_mode": None,
                                "supports_feedback": False,
                                "supports_phase_selection": False,
                            },
                        ],
                    }
                ],
                "devices": [
                    {
                        "id": "switch_l1",
                        "profile_id": "switch_switch_group_member",
                        "label": "Switch L1",
                        "endpoint": "http://switch.local/root/wizard/phase1",
                        "enabled": True,
                        "notes": None,
                    },
                    {
                        "id": "switch_l2",
                        "profile_id": "switch_switch_group_member",
                        "label": "Switch L2",
                        "endpoint": "http://switch.local/root/wizard/phase2",
                        "enabled": True,
                        "notes": None,
                    },
                ],
                "bindings": [
                    {
                        "id": "actuation",
                        "role": "actuation",
                        "label": "Actuation",
                        "phase_scope": ["L1", "L2"],
                        "members": [
                            {"device_id": "switch_l1", "capability_id": "switch", "phases": ["L1"]},
                            {"device_id": "switch_l2", "capability_id": "switch", "phases": ["L2"]},
                        ],
                    },
                    {
                        "id": "measurement",
                        "role": "measurement",
                        "label": "Measurement",
                        "phase_scope": ["L1", "L2"],
                        "members": [
                            {"device_id": "switch_l1", "capability_id": "meter", "phases": ["L1"]},
                            {"device_id": "switch_l2", "capability_id": "meter", "phases": ["L2"]},
                        ],
                    },
                ],
            },
        )
        self.assertEqual([device.id for device in inventory.devices], ["switch_l1", "switch_l2"])
        self.assertEqual([device.endpoint for device in inventory.devices], ["http://switch.local/root/wizard/phase1", "http://switch.local/root/wizard/phase2"])
        self.assertEqual(inventory.profiles[0].id, "switch_switch_group_member")
        self.assertEqual([capability.id for capability in inventory.profiles[0].capabilities], ["switch", "meter"])
        self.assertEqual(inventory.profiles[0].capabilities[0].supported_phases, ("L1", "L2", "L3"))
        self.assertEqual(inventory.bindings[0].phase_scope, ("L1", "L2"))
        self.assertEqual([member.device_id for member in inventory.bindings[0].members], ["switch_l1", "switch_l2"])
        self.assertEqual([member.capability_id for member in inventory.bindings[1].members], ["meter", "meter"])
        self.assertEqual([member.phases for member in inventory.bindings[1].members], [("L1",), ("L2",)])

    def test_external_and_reference_measurement_contracts(self) -> None:
        base_answers = _answers(phase="L3")
        external_topology = EvChargerTopologyConfig(
            topology=TopologyConfig(type="hybrid_topology"),
            measurement=MeasurementConfig(type="external_meter"),
            charger=ChargerConfig(type="goe_charger"),
            policy=PolicyConfig(mode="auto", phase="L3"),
        )

        external_inventory = build_wizard_inventory(base_answers, {"meter": " meter.local ", "charger": "charger.local"}, external_topology)

        self.assertEqual(
            _json_payload(external_inventory),
            {
                "profiles": [
                    {
                        "id": "meter_external_meter",
                        "label": "Meter device",
                        "vendor": None,
                        "model": None,
                        "description": None,
                        "capabilities": [
                            {
                                "id": "meter",
                                "kind": "meter",
                                "adapter_type": "shelly_meter",
                                "supported_phases": ["L3"],
                                "channel": None,
                                "measures_power": True,
                                "measures_energy": True,
                                "switching_mode": None,
                                "supports_feedback": False,
                                "supports_phase_selection": False,
                            }
                        ],
                    },
                    {
                        "id": "charger_goe_charger",
                        "label": "Goe Charger",
                        "vendor": None,
                        "model": None,
                        "description": None,
                        "capabilities": [
                            {
                                "id": "charger",
                                "kind": "charger",
                                "adapter_type": "goe_charger",
                                "supported_phases": ["L3"],
                                "channel": None,
                                "measures_power": False,
                                "measures_energy": False,
                                "switching_mode": None,
                                "supports_feedback": False,
                                "supports_phase_selection": False,
                            }
                        ],
                    },
                ],
                "devices": [
                    {
                        "id": "meter_device",
                        "profile_id": "meter_external_meter",
                        "label": "Meter device",
                        "endpoint": "meter.local",
                        "enabled": True,
                        "notes": None,
                    },
                    {
                        "id": "charger_device",
                        "profile_id": "charger_goe_charger",
                        "label": "Charger device",
                        "endpoint": "charger.local",
                        "enabled": True,
                        "notes": None,
                    },
                ],
                "bindings": [
                    {
                        "id": "measurement",
                        "role": "measurement",
                        "label": "Measurement",
                        "phase_scope": ["L3"],
                        "members": [{"device_id": "meter_device", "capability_id": "meter", "phases": ["L3"]}],
                    },
                    {
                        "id": "charger",
                        "role": "charger",
                        "label": "Charger",
                        "phase_scope": ["L3"],
                        "members": [{"device_id": "charger_device", "capability_id": "charger", "phases": ["L3"]}],
                    },
                ],
            },
        )
        self.assertEqual(external_inventory.profiles[0].id, "meter_external_meter")
        self.assertEqual(external_inventory.profiles[0].capabilities[0].adapter_type, "shelly_meter")
        self.assertEqual(external_inventory.profiles[0].capabilities[0].supported_phases, ("L3",))
        self.assertTrue(external_inventory.profiles[0].capabilities[0].measures_energy)
        self.assertEqual(external_inventory.devices[0].endpoint, "meter.local")
        self.assertEqual(external_inventory.bindings[0].role, "measurement")
        self.assertEqual(external_inventory.bindings[1].role, "charger")

        fixed_inventory = build_wizard_inventory(
            base_answers,
            {"meter": "fixed.local"},
            EvChargerTopologyConfig(
                topology=TopologyConfig(type="simple_relay"),
                measurement=MeasurementConfig(type="fixed_reference"),
                policy=PolicyConfig(mode="manual", phase="L3"),
            ),
        )
        self.assertEqual(asdict(fixed_inventory)["profiles"][0]["capabilities"][0]["id"], "meter")
        self.assertEqual(fixed_inventory.profiles[0].label, "Fixed reference meter")
        self.assertEqual(fixed_inventory.profiles[0].capabilities[0].adapter_type, "fixed_reference")
        self.assertFalse(fixed_inventory.profiles[0].capabilities[0].measures_energy)

        learned_inventory = build_wizard_inventory(
            base_answers,
            {"meter": "learned.local"},
            EvChargerTopologyConfig(
                topology=TopologyConfig(type="simple_relay"),
                measurement=MeasurementConfig(type="learned_reference"),
                policy=PolicyConfig(mode="manual", phase="L3"),
            ),
        )
        self.assertEqual(asdict(learned_inventory)["profiles"][0]["capabilities"][0]["id"], "meter")
        self.assertEqual(learned_inventory.profiles[0].label, "Learned reference meter")
        self.assertEqual(learned_inventory.profiles[0].capabilities[0].adapter_type, "learned_reference")
        self.assertTrue(learned_inventory.profiles[0].capabilities[0].measures_energy)

    def test_charger_native_adds_meter_capability_and_renderable_payload(self) -> None:
        answers = _answers(profile="native_device", phase="3P")
        topology = EvChargerTopologyConfig(
            topology=TopologyConfig(type="native_device"),
            measurement=MeasurementConfig(type="charger_native"),
            charger=ChargerConfig(type="goe_charger"),
            policy=PolicyConfig(mode="auto", phase="3P"),
        )

        inventory = build_wizard_inventory(answers, {"charger": "goe.local"}, topology)
        payload = inventory_payload(inventory)
        rendered = inventory_text(answers, {"charger": "goe.local"}, topology)

        self.assertEqual(
            _json_payload(inventory),
            {
                "profiles": [
                    {
                        "id": "charger_goe_charger",
                        "label": "Goe Charger",
                        "vendor": None,
                        "model": None,
                        "description": None,
                        "capabilities": [
                            {
                                "id": "charger",
                                "kind": "charger",
                                "adapter_type": "goe_charger",
                                "supported_phases": ["L1", "L2", "L3"],
                                "channel": None,
                                "measures_power": False,
                                "measures_energy": False,
                                "switching_mode": None,
                                "supports_feedback": False,
                                "supports_phase_selection": True,
                            },
                            {
                                "id": "meter",
                                "kind": "meter",
                                "adapter_type": "goe_charger",
                                "supported_phases": ["L1", "L2", "L3"],
                                "channel": None,
                                "measures_power": True,
                                "measures_energy": True,
                                "switching_mode": None,
                                "supports_feedback": False,
                                "supports_phase_selection": False,
                            },
                        ],
                    }
                ],
                "devices": [
                    {
                        "id": "charger_device",
                        "profile_id": "charger_goe_charger",
                        "label": "Charger device",
                        "endpoint": "goe.local",
                        "enabled": True,
                        "notes": None,
                    }
                ],
                "bindings": [
                    {
                        "id": "measurement",
                        "role": "measurement",
                        "label": "Measurement",
                        "phase_scope": ["L1", "L2", "L3"],
                        "members": [
                            {
                                "device_id": "charger_device",
                                "capability_id": "meter",
                                "phases": ["L1", "L2", "L3"],
                            }
                        ],
                    },
                    {
                        "id": "charger",
                        "role": "charger",
                        "label": "Charger",
                        "phase_scope": ["L1", "L2", "L3"],
                        "members": [
                            {
                                "device_id": "charger_device",
                                "capability_id": "charger",
                                "phases": ["L1", "L2", "L3"],
                            }
                        ],
                    },
                ],
            },
        )
        self.assertEqual([capability.id for capability in inventory.profiles[0].capabilities], ["charger", "meter"])
        self.assertTrue(inventory.profiles[0].capabilities[0].supports_phase_selection)
        self.assertEqual(inventory.bindings[0].members[0].capability_id, "meter")
        self.assertEqual(inventory.bindings[1].members[0].capability_id, "charger")
        self.assertEqual(payload, asdict(inventory))
        self.assertIn("[Profile:charger_goe_charger]", rendered)
        self.assertIn("SupportedPhases=L1,L2,L3", rendered)

    def test_no_optional_roles_and_cerbo_endpoint_contract(self) -> None:
        answers = _answers(phase="L1", cerbo_relay_index=2)
        no_roles = build_wizard_inventory(
            answers,
            {},
            EvChargerTopologyConfig(topology=TopologyConfig(type="custom_topology"), policy=PolicyConfig(mode="manual", phase="L1")),
        )
        self.assertEqual(no_roles.profiles, ())
        self.assertEqual(no_roles.devices, ())
        self.assertEqual(no_roles.bindings, ())

        cerbo_inventory = build_wizard_inventory(
            answers,
            {},
            EvChargerTopologyConfig(
                topology=TopologyConfig(type="simple_relay"),
                actuator=ActuatorConfig(type="cerbo_gx_relay_switch"),
                policy=PolicyConfig(mode="manual", phase="L1"),
            ),
        )
        self.assertEqual(
            _json_payload(cerbo_inventory),
            {
                "profiles": [
                    {
                        "id": "switch_cerbo_gx_relay_switch",
                        "label": "Cerbo Gx Relay Switch",
                        "vendor": None,
                        "model": None,
                        "description": None,
                        "capabilities": [
                            {
                                "id": "switch",
                                "kind": "switch",
                                "adapter_type": "cerbo_gx_relay_switch",
                                "supported_phases": ["L1"],
                                "channel": None,
                                "measures_power": False,
                                "measures_energy": False,
                                "switching_mode": "contactor",
                                "supports_feedback": False,
                                "supports_phase_selection": False,
                            }
                        ],
                    }
                ],
                "devices": [
                    {
                        "id": "switch_device",
                        "profile_id": "switch_cerbo_gx_relay_switch",
                        "label": "Switch device",
                        "endpoint": "local://cerbo-gx/relay/2",
                        "enabled": True,
                        "notes": None,
                    }
                ],
                "bindings": [
                    {
                        "id": "actuation",
                        "role": "actuation",
                        "label": "Actuation",
                        "phase_scope": ["L1"],
                        "members": [{"device_id": "switch_device", "capability_id": "switch", "phases": ["L1"]}],
                    }
                ],
            },
        )
        self.assertEqual(cerbo_inventory.devices[0].endpoint, "local://cerbo-gx/relay/2")
        self.assertFalse(cerbo_inventory.profiles[0].capabilities[0].supports_feedback)
        self.assertEqual(cerbo_inventory.profiles[0].capabilities[0].switching_mode, "contactor")

    def test_none_measurement_and_role_host_fallback_contracts(self) -> None:
        answers = _answers(host_input="fallback.local/root/", phase="3P")

        none_measurement_inventory = build_wizard_inventory(
            answers,
            {},
            EvChargerTopologyConfig(
                topology=TopologyConfig(type="simple_relay"),
                actuator=ActuatorConfig(type="template_switch"),
                measurement=MeasurementConfig(type="none"),
                policy=PolicyConfig(mode="manual", phase="3P"),
            ),
        )
        self.assertEqual([binding.role for binding in none_measurement_inventory.bindings], ["actuation"])
        self.assertEqual(none_measurement_inventory.devices[0].endpoint, "fallback.local/root/")
        self.assertTrue(none_measurement_inventory.profiles[0].capabilities[0].supports_phase_selection)

        switch_group_inventory = build_wizard_inventory(
            answers,
            {},
            EvChargerTopologyConfig(
                topology=TopologyConfig(type="simple_relay"),
                actuator=ActuatorConfig(type="switch_group"),
                policy=PolicyConfig(mode="manual", phase="3P"),
            ),
        )
        self.assertEqual(
            [device.endpoint for device in switch_group_inventory.devices],
            [
                "http://fallback.local/root/wizard/phase1",
                "http://fallback.local/root/wizard/phase2",
                "http://fallback.local/root/wizard/phase3",
            ],
        )

        external_with_actuator = build_wizard_inventory(
            answers,
            {},
            EvChargerTopologyConfig(
                topology=TopologyConfig(type="hybrid_topology"),
                actuator=ActuatorConfig(type="template_switch"),
                measurement=MeasurementConfig(type="external_meter"),
                charger=ChargerConfig(type="goe_charger"),
                policy=PolicyConfig(mode="manual", phase="3P"),
            ),
        )
        self.assertEqual([device.id for device in external_with_actuator.devices], ["switch_device", "meter_device", "charger_device"])
        self.assertEqual([device.endpoint for device in external_with_actuator.devices], ["fallback.local/root/", "fallback.local/root/", "fallback.local/root/"])
        self.assertEqual([binding.role for binding in external_with_actuator.bindings], ["actuation", "measurement", "charger"])
        self.assertEqual(external_with_actuator.bindings[1].members[0].device_id, "meter_device")

    def test_internal_edge_contracts_for_sparse_group_and_two_phase_selection(self) -> None:
        sparse_binding = _group_measurement_binding(
            devices=[DeviceInstance(id="switch_l1", profile_id="switch_switch_group_member", label="Switch L1")],
            binding_id="measurement",
            label="Measurement",
            phase_scope=("L1", "L2"),
        )
        self.assertEqual([member.device_id for member in sparse_binding.members], ["switch_l1"])
        self.assertEqual([member.phases for member in sparse_binding.members], [("L1",)])

        two_phase_charger = _charger_capabilities(
            EvChargerTopologyConfig(
                topology=TopologyConfig(type="native_device"),
                charger=ChargerConfig(type="goe_charger"),
                policy=PolicyConfig(mode="manual", phase="3P"),
            ),
            ("L1", "L2"),
        )
        self.assertTrue(two_phase_charger[0].supports_phase_selection)
        self.assertEqual(_phase_endpoint("https://switch.local/baseX/", "L1"), "https://switch.local/baseX/wizard/phase1")
        self.assertEqual(_phase_endpoint("switch.local/baseX/", "L2"), "http://switch.local/baseX/wizard/phase2")

    def test_helper_contracts_are_explicit(self) -> None:
        self.assertEqual(_profile_id("switch", "shelly-contactor_switch"), "switch_shelly_contactor_switch")
        self.assertEqual(_profile_label("template_meter"), "Template Meter")
        self.assertEqual(_measurement_profile_label("fixed_reference"), "Fixed reference meter")
        self.assertEqual(_measurement_profile_label("learned_reference"), "Learned reference meter")
        self.assertEqual(_measurement_profile_label("external_meter"), "Meter device")
        self.assertEqual(_measurement_adapter_type(_answers(topology_preset="template-stack"), "external_meter"), "template_meter")
        self.assertEqual(_measurement_adapter_type(_answers(topology_preset="tasmota-three-phase"), "external_meter"), "tasmota_meter")
        self.assertEqual(_measurement_adapter_type(_answers(topology_preset="tuya-meter"), "external_meter"), "tuya_meter")
        self.assertEqual(_measurement_adapter_type(_answers(topology_preset=None), "external_meter"), "shelly_meter")
        self.assertEqual(_measurement_adapter_type(_answers(topology_preset=""), "external_meter"), "shelly_meter")
        self.assertEqual(_measurement_adapter_type(_answers(), "fixed_reference"), "fixed_reference")
        self.assertEqual(_measurement_adapter_type(_answers(), "learned_reference"), "learned_reference")
        self.assertIsNone(_measurement_type(EvChargerTopologyConfig(topology=TopologyConfig(type="custom_topology"))))
        self.assertIsNone(
            _measurement_type(
                EvChargerTopologyConfig(
                    topology=TopologyConfig(type="custom_topology"),
                    measurement=MeasurementConfig(type="none"),
                )
            )
        )
        self.assertEqual(
            _measurement_type(
                EvChargerTopologyConfig(
                    topology=TopologyConfig(type="custom_topology"),
                    measurement=MeasurementConfig(type="external_meter"),
                )
            ),
            "external_meter",
        )
        self.assertEqual(_adapter_type_for_topology_preset("template-meter-cerbo-relay"), "template_meter")
        self.assertEqual(_adapter_type_for_topology_preset("template-meter-goe-switch-group"), "template_meter")
        self.assertEqual(_adapter_type_for_topology_preset("other"), "shelly_meter")
        self.assertEqual(_phase_scope(" 3p "), ("L1", "L2", "L3"))
        self.assertEqual(_phase_scope(" l2 "), ("L2",))
        self.assertEqual(_phase_scope(" l3 "), ("L3",))
        self.assertEqual(_phase_scope(" l1 "), ("L1",))
        self.assertEqual(_switch_group_phase_scope("p1,p1_p2,p1_p2_p3"), ("L1", "L2", "L3"))
        self.assertEqual(_switch_group_phase_scope("p1,p1_p2"), ("L1", "L2"))
        self.assertEqual(_switch_group_phase_scope("p1"), ("L1",))
        self.assertEqual(_switching_mode("template_switch"), "direct")
        self.assertEqual(_switching_mode("tuya_contactor_switch"), "contactor")
        self.assertEqual(_switching_mode("tasmota_contactor_switch"), "contactor")
        self.assertEqual(_switching_mode("cerbo_gx_relay_switch"), "contactor")
        self.assertFalse(_supports_phase_selection(("L1",)))
        self.assertTrue(_supports_phase_selection(("L1", "L2")))
        self.assertTrue(_supports_phase_selection(("L1", "L2", "L3")))
        self.assertTrue(_switch_supports_feedback("shelly_switch"))
        self.assertFalse(_switch_supports_feedback("cerbo_gx_relay_switch"))
        self.assertIsNone(_endpoint(None))
        self.assertIsNone(_endpoint("  "))
        self.assertEqual(_endpoint(" device.local "), "device.local")
        self.assertEqual(_phase_endpoint("https://switch.local/base/", "L3"), "https://switch.local/base/wizard/phase3")


if __name__ == "__main__":
    unittest.main()

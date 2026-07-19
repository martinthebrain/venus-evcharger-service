# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
import unittest

from venus_evcharger.inventory import (
    DeviceCapability,
    DeviceInventory,
    DeviceInventoryConfigError,
    parse_device_inventory_config,
    render_device_inventory_config,
)
from venus_evcharger.inventory.render import _render_switch_capability_fields


class DeviceInventoryConfigPart1Tests(unittest.TestCase):
    def test_parse_three_single_phase_meters_as_one_measurement_group(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Profile:single_phase_meter]
Label=Single phase meter

[Capability:single_phase_meter:meter]
Kind=meter
AdapterType=template_meter
SupportedPhases=L1
MeasuresPower=1
MeasuresEnergy=1

[Profile:single_phase_meter_l2]
Label=Single phase meter L2

[Capability:single_phase_meter_l2:meter]
Kind=meter
AdapterType=template_meter
SupportedPhases=L2
MeasuresPower=1
MeasuresEnergy=1

[Profile:single_phase_meter_l3]
Label=Single phase meter L3

[Capability:single_phase_meter_l3:meter]
Kind=meter
AdapterType=template_meter
SupportedPhases=L3
MeasuresPower=1
MeasuresEnergy=1

[Device:meter_l1]
Profile=single_phase_meter
Label=Meter L1
Endpoint=http://meter-l1.local

[Device:meter_l2]
Profile=single_phase_meter_l2
Label=Meter L2
Endpoint=http://meter-l2.local

[Device:meter_l3]
Profile=single_phase_meter_l3
Label=Meter L3
Endpoint=http://meter-l3.local

[Binding:evse_measurement]
Role=measurement
Label=EVSE measurement group
PhaseScope=L1,L2,L3

[BindingMember:evse_measurement:1]
Device=meter_l1
Capability=meter
Phases=L1

[BindingMember:evse_measurement:2]
Device=meter_l2
Capability=meter
Phases=L2

[BindingMember:evse_measurement:3]
Device=meter_l3
Capability=meter
Phases=L3
"""
        )

        inventory = parse_device_inventory_config(parser)

        self.assertEqual(len(inventory.profiles), 3)
        self.assertEqual(len(inventory.devices), 3)
        self.assertEqual(len(inventory.bindings), 1)
        binding = inventory.bindings[0]
        self.assertEqual(binding.role, "measurement")
        self.assertEqual(binding.phase_scope, ("L1", "L2", "L3"))
        self.assertEqual(len(binding.members), 3)

    def test_parse_three_phase_device_and_bind_only_one_phase(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Profile:three_phase_meter]
Label=Three phase meter

[Capability:three_phase_meter:meter]
Kind=meter
AdapterType=template_meter
SupportedPhases=L1,L2,L3
MeasuresPower=1
MeasuresEnergy=1

[Device:grid_meter]
Profile=three_phase_meter
Label=Grid meter
Endpoint=http://grid.local

[Binding:l2_measurement]
Role=measurement
Label=Only L2
PhaseScope=L2

[BindingMember:l2_measurement:1]
Device=grid_meter
Capability=meter
Phases=L2
"""
        )

        inventory = parse_device_inventory_config(parser)

        binding = inventory.bindings[0]
        self.assertEqual(binding.phase_scope, ("L2",))
        self.assertEqual(binding.members[0].phases, ("L2",))

    def test_switch_group_can_bind_three_single_phase_relays(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Profile:relay_l1]
Label=Relay L1

[Capability:relay_l1:switch]
Kind=switch
AdapterType=template_switch
SupportedPhases=L1
SwitchingMode=contactor
SupportsFeedback=1

[Profile:relay_l2]
Label=Relay L2

[Capability:relay_l2:switch]
Kind=switch
AdapterType=template_switch
SupportedPhases=L2
SwitchingMode=contactor
SupportsFeedback=1

[Profile:relay_l3]
Label=Relay L3

[Capability:relay_l3:switch]
Kind=switch
AdapterType=template_switch
SupportedPhases=L3
SwitchingMode=contactor
SupportsFeedback=1

[Device:relay1]
Profile=relay_l1
Label=Relay 1

[Device:relay2]
Profile=relay_l2
Label=Relay 2

[Device:relay3]
Profile=relay_l3
Label=Relay 3

[Binding:evse_contactors]
Role=actuation
Label=EVSE contactors
PhaseScope=L1,L2,L3

[BindingMember:evse_contactors:1]
Device=relay1
Capability=switch
Phases=L1

[BindingMember:evse_contactors:2]
Device=relay2
Capability=switch
Phases=L2

[BindingMember:evse_contactors:3]
Device=relay3
Capability=switch
Phases=L3
"""
        )

        inventory = parse_device_inventory_config(parser)

        self.assertEqual(inventory.bindings[0].role, "actuation")
        self.assertEqual(len(inventory.bindings[0].members), 3)

    def test_render_switch_capability_fields_returns_empty_without_switching_mode(self) -> None:
        capability = DeviceCapability(
            id="switch",
            kind="switch",
            adapter_type="template_switch",
            supported_phases=("L1",),
            switching_mode=None,
            supports_feedback=True,
            supports_phase_selection=True,
        )

        self.assertEqual(_render_switch_capability_fields(capability), [])

    def test_duplicate_phase_assignment_in_binding_fails(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Profile:meter]
Label=Meter

[Capability:meter:meter]
Kind=meter
AdapterType=template_meter
SupportedPhases=L1,L2
MeasuresPower=1
MeasuresEnergy=1

[Device:m1]
Profile=meter
Label=Meter 1

[Binding:bad_measurement]
Role=measurement
Label=Bad
PhaseScope=L1,L2

[BindingMember:bad_measurement:1]
Device=m1
Capability=meter
Phases=L1

[BindingMember:bad_measurement:2]
Device=m1
Capability=meter
Phases=L1,L2
"""
        )

        with self.assertRaisesRegex(DeviceInventoryConfigError, "duplicate phases"):
            parse_device_inventory_config(parser)

    def test_binding_rejects_capability_kind_mismatch(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Profile:combo]
Label=Combo

[Capability:combo:switch]
Kind=switch
AdapterType=template_switch
SupportedPhases=L1
SwitchingMode=direct

[Device:d1]
Profile=combo
Label=Device 1

[Binding:bad_measurement]
Role=measurement
Label=Bad
PhaseScope=L1

[BindingMember:bad_measurement:1]
Device=d1
Capability=switch
Phases=L1
"""
        )

        with self.assertRaisesRegex(DeviceInventoryConfigError, "requires capability kind 'meter'"):
            parse_device_inventory_config(parser)

    def test_render_round_trip_preserves_inventory_shape(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Profile:three_phase_device]
Label=Three phase device
Vendor=Example
Model=X1

[Capability:three_phase_device:switch]
Kind=switch
AdapterType=tasmota_switch
SupportedPhases=L1,L2,L3
SwitchingMode=contactor
SupportsFeedback=1
SupportsPhaseSelection=1

[Capability:three_phase_device:meter]
Kind=meter
AdapterType=template_meter
SupportedPhases=L1,L2,L3
MeasuresPower=1
MeasuresEnergy=1

[Device:garage_device]
Profile=three_phase_device
Label=Garage device
Endpoint=http://garage-device.local

[Binding:garage_switching]
Role=actuation
Label=Garage switching
PhaseScope=L1,L2,L3

[BindingMember:garage_switching:1]
Device=garage_device
Capability=switch
Phases=L1,L2,L3
"""
        )

        inventory = parse_device_inventory_config(parser)
        rendered = render_device_inventory_config(inventory)
        round_trip = configparser.ConfigParser()
        round_trip.read_string(rendered)
        reparsed = parse_device_inventory_config(round_trip)

        self.assertEqual(inventory, reparsed)
        self.assertIsInstance(reparsed, DeviceInventory)

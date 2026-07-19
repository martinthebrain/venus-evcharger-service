# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
import unittest

from venus_evcharger.inventory import (
    DeviceCapability,
    DeviceInstance,
    DeviceInventory,
    DeviceInventoryConfigError,
    DeviceProfile,
    parse_device_inventory_config,
    RoleBinding,
    RoleBindingMember,
    render_device_inventory_config,
)
from venus_evcharger.inventory.config_parser import (
    _binding_members,
    _bindings,
    _capabilities,
    _devices,
)
from venus_evcharger.inventory.config_validation import validate_device_inventory
from venus_evcharger.inventory.config_values import _phase_labels
from tests.test_device_inventory_config_support import FakeInventoryConfig


class DeviceInventoryConfigPart3Tests(unittest.TestCase):
    def test_parse_inventory_config_rejects_invalid_sections_and_literals(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Profile:]
Label=Broken
"""
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "invalid section name"):
            parse_device_inventory_config(parser)

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Profile:p1]
Label=P1

[Capability:p1:meter]
Kind=invalid
AdapterType=template_meter
SupportedPhases=L1
MeasuresPower=1
"""
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "Capability.Kind must be one of"):
            parse_device_inventory_config(parser)

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Profile:p1]
Label=P1

[Capability:p1:switch]
Kind=switch
AdapterType=template_switch
SupportedPhases=L1
SwitchingMode=bad
"""
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "Capability.SwitchingMode must be one of"):
            parse_device_inventory_config(parser)

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Profile:p1]
Label=P1

[Capability:p1:meter]
Kind=meter
AdapterType=template_meter
SupportedPhases=L1
MeasuresPower=1

[Device:d1]
Profile=p1
Label=D1
Endpoint=http://d1.local

[Binding:b1]
Role=invalid
PhaseScope=L1

[BindingMember:b1:1]
Device=d1
Capability=meter
Phases=L1
"""
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "Binding.Role must be one of"):
            parse_device_inventory_config(parser)

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Profile:p1]
Label=P1

[Capability:p1:meter]
Kind=meter
AdapterType=template_meter
SupportedPhases=L4
MeasuresPower=1
"""
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "phase list"):
            parse_device_inventory_config(parser)

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Capability:broken]
Kind=meter
AdapterType=template_meter
SupportedPhases=L1
MeasuresPower=1
"""
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "invalid section name"):
            parse_device_inventory_config(parser)

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Profile:p1]
Vendor=Only vendor
"""
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "missing required key Profile:p1.Label"):
            parse_device_inventory_config(parser)

    def test_render_round_trip_preserves_optional_fields(self) -> None:
        inventory = DeviceInventory(
            profiles=(
                DeviceProfile(
                    id="p1",
                    label="P1",
                    vendor="Vendor",
                    model="Model",
                    description="Description",
                    capabilities=(
                        DeviceCapability(
                            id="switch",
                            kind="switch",
                            adapter_type="template_switch",
                            supported_phases=("L1",),
                            channel="relay0",
                            switching_mode="direct",
                            supports_feedback=True,
                            supports_phase_selection=True,
                        ),
                    ),
                ),
            ),
            devices=(
                DeviceInstance(
                    id="d1",
                    profile_id="p1",
                    label="Device",
                    endpoint="http://device.local",
                    notes="Notes",
                ),
            ),
        )

        rendered = render_device_inventory_config(inventory)

        self.assertIn("Vendor=Vendor", rendered)
        self.assertIn("Description=Description", rendered)
        self.assertIn("Channel=relay0", rendered)
        self.assertIn("Notes=Notes", rendered)

    def test_inventory_private_helpers_cover_duplicate_parsing_and_render_edges(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Capability:p1:meter]
Kind=meter
AdapterType=template_meter
SupportedPhases=L1
MeasuresPower=1

[Device:d1]
Profile=p1
Label=Device 1

[Binding:b1]
Role=measurement
Label=Binding
PhaseScope=L1

[BindingMember:b1:m1]
Device=d1
Capability=meter
Phases=L1
"""
        )
        capability_section = parser["Capability:p1:meter"]
        device_section = parser["Device:d1"]
        binding_section = parser["Binding:b1"]
        member_section = parser["BindingMember:b1:m1"]

        duplicate_capability = FakeInventoryConfig(
            ["Capability:p1:meter", "Capability:p1:meter"],
            {"Capability:p1:meter": capability_section},
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "duplicate capability id 'meter'"):
            _capabilities(duplicate_capability)

        duplicate_device = FakeInventoryConfig(
            ["Device:d1", "Device:d1"],
            {"Device:d1": device_section},
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "duplicate device id 'd1'"):
            _devices(duplicate_device)

        duplicate_binding = FakeInventoryConfig(
            ["Binding:b1", "Binding:b1"],
            {"Binding:b1": binding_section},
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "duplicate binding id 'b1'"):
            _bindings(duplicate_binding)

        duplicate_member = FakeInventoryConfig(
            ["BindingMember:b1:m1", "BindingMember:b1:m1"],
            {"BindingMember:b1:m1": member_section},
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "duplicate binding member id 'm1'"):
            _binding_members(duplicate_member)

        duplicate_capability_inventory = DeviceInventory(
            profiles=(
                DeviceProfile(
                    id="p1",
                    label="P1",
                    capabilities=(
                        DeviceCapability(
                            id="meter",
                            kind="meter",
                            adapter_type="template_meter",
                            supported_phases=("L1",),
                            measures_power=True,
                        ),
                        DeviceCapability(
                            id="meter",
                            kind="meter",
                            adapter_type="template_meter",
                            supported_phases=("L2",),
                            measures_power=True,
                        ),
                    ),
                ),
            ),
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "duplicate capability id 'meter'"):
            validate_device_inventory(duplicate_capability_inventory)

        unknown_device_inventory = DeviceInventory(
            profiles=(
                DeviceProfile(
                    id="p1",
                    label="P1",
                    capabilities=(
                        DeviceCapability(
                            id="meter",
                            kind="meter",
                            adapter_type="template_meter",
                            supported_phases=("L1",),
                            measures_power=True,
                        ),
                    ),
                ),
            ),
            bindings=(
                RoleBinding(
                    id="measurement",
                    role="measurement",
                    label="Measurement",
                    phase_scope=("L1",),
                    members=(
                        RoleBindingMember(device_id="missing", capability_id="meter", phases=("L1",)),
                    ),
                ),
            ),
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "references unknown device 'missing'"):
            validate_device_inventory(unknown_device_inventory)

        with self.assertRaisesRegex(DeviceInventoryConfigError, "phase list may not be empty"):
            _phase_labels(" , ")
        self.assertEqual(_phase_labels("L1,,L2"), ("L1", "L2"))
        self.assertEqual(_phase_labels("L1,L1,L2"), ("L1", "L2"))

        rendered = render_device_inventory_config(
            DeviceInventory(
                profiles=(
                    DeviceProfile(
                        id="p1",
                        label="P1",
                        capabilities=(
                            DeviceCapability(
                                id="meter",
                                kind="meter",
                                adapter_type="template_meter",
                                supported_phases=("L1",),
                                measures_power=True,
                            ),
                        ),
                    ),
                ),
                devices=(
                    DeviceInstance(
                        id="d1",
                        profile_id="p1",
                        label="Device 1",
                        endpoint=None,
                        notes="Only notes",
                    ),
                ),
            )
        )
        self.assertIn("Notes=Only notes", rendered)


if __name__ == "__main__":
    unittest.main()

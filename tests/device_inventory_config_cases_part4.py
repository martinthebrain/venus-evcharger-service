# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
import unittest
from collections.abc import Callable

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
    _profiles,
)
from venus_evcharger.inventory.config_values import (
    _as_bool,
    _binding_role,
    _capability_kind,
    _optional_switching_mode,
    _phase_label,
    _phase_labels,
    _phase_tokens,
)
from tests.test_device_inventory_config_support import FakeInventoryConfig, FakeInventorySection


class DeviceInventoryConfigPart4Tests(unittest.TestCase):
    def _assert_inventory_error(self, expected: str, callback: Callable[..., object], *args: object) -> None:
        with self.assertRaises(DeviceInventoryConfigError) as caught:
            callback(*args)
        self.assertEqual(str(caught.exception), expected)

    def test_inventory_literal_and_bool_helper_contracts_are_explicit(self) -> None:
        for value in ("1", " true ", "YES", "on"):
            with self.subTest(value=value):
                self.assertTrue(_as_bool(value))
        for false_value in ("", "0", "false", "no", "off", None, "enabled"):
            with self.subTest(value=false_value):
                self.assertFalse(_as_bool(false_value))

        self.assertEqual(_phase_tokens(" l1, L2 ,l3 "), ["L1", "L2", "L3"])
        self.assertEqual(_phase_label(" l1 "), "L1")
        self.assertEqual(_phase_label("L2"), "L2")
        self.assertEqual(_phase_label("L3"), "L3")
        self.assertEqual(_phase_labels("L1,L2,L1"), ("L1", "L2"))
        self._assert_inventory_error("phase list may not be empty", _phase_labels, ",")
        self._assert_inventory_error(
            "phase list must be one of: L1, L2, L3 (got 'L4')",
            _phase_label,
            "L4",
        )

        self.assertEqual(_capability_kind("switch"), "switch")
        self.assertEqual(_capability_kind("meter"), "meter")
        self.assertEqual(_capability_kind("charger"), "charger")
        self._assert_inventory_error(
            "Capability.Kind must be one of: charger, meter, switch (got 'bad')",
            _capability_kind,
            "bad",
        )

        self.assertIsNone(_optional_switching_mode(None))
        self.assertEqual(_optional_switching_mode(" direct "), "direct")
        self.assertEqual(_optional_switching_mode("contactor"), "contactor")
        self._assert_inventory_error(
            "Capability.SwitchingMode must be one of: contactor, direct (got 'bad')",
            _optional_switching_mode,
            "bad",
        )

        self.assertEqual(_binding_role("actuation"), "actuation")
        self.assertEqual(_binding_role("measurement"), "measurement")
        self.assertEqual(_binding_role("charger"), "charger")
        self._assert_inventory_error(
            "Binding.Role must be one of: actuation, charger, measurement (got 'bad')",
            _binding_role,
            "bad",
        )

    def test_parse_contract_preserves_optional_fields_false_flags_and_section_ids(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Profile:combo]
Label=Combo
Vendor=Vendor
Model=Model
Description=Description

[Capability:combo:meter]
Kind=meter
AdapterType=template_meter
SupportedPhases=L1,L2
Channel=meter-channel
MeasuresPower=0
MeasuresEnergy=1

[Capability:combo:switch]
Kind=switch
AdapterType=template_switch
SupportedPhases=L1
Channel=switch-channel
SwitchingMode=direct
SupportsFeedback=0
SupportsPhaseSelection=0

[Device:combo_device]
Profile=combo
Label=Combo device
Endpoint=http://combo.local
Enabled=0
Notes=Notes

[Binding:measurement]
Role=measurement
Label=Measurement
PhaseScope=L1,L2

[BindingMember:measurement:meter_l1]
Device=combo_device
Capability=meter
Phases=L1

[BindingMember:measurement:meter_l2]
Device=combo_device
Capability=meter
Phases=L2
"""
        )

        inventory = parse_device_inventory_config(parser)

        self.assertEqual(tuple(profile.id for profile in inventory.profiles), ("combo",))
        profile = inventory.profiles[0]
        self.assertEqual(profile.label, "Combo")
        self.assertEqual(profile.vendor, "Vendor")
        self.assertEqual(profile.model, "Model")
        self.assertEqual(profile.description, "Description")
        meter, switch = profile.capabilities
        self.assertEqual(meter.id, "meter")
        self.assertEqual(meter.channel, "meter-channel")
        self.assertFalse(meter.measures_power)
        self.assertTrue(meter.measures_energy)
        self.assertEqual(switch.id, "switch")
        self.assertEqual(switch.channel, "switch-channel")
        self.assertEqual(switch.switching_mode, "direct")
        self.assertFalse(switch.supports_feedback)
        self.assertFalse(switch.supports_phase_selection)
        device = inventory.devices[0]
        self.assertEqual(device.id, "combo_device")
        self.assertEqual(device.profile_id, "combo")
        self.assertEqual(device.endpoint, "http://combo.local")
        self.assertFalse(device.enabled)
        self.assertEqual(device.notes, "Notes")
        binding = inventory.bindings[0]
        self.assertEqual(binding.id, "measurement")
        self.assertEqual(binding.label, "Measurement")
        self.assertEqual(tuple(member.phases for member in binding.members), (("L1",), ("L2",)))

    def test_section_parsers_use_explicit_schema_field_names(self) -> None:
        profile_config = FakeInventoryConfig(
            ["Profile:p1"],
            {
                "Profile:p1": FakeInventorySection(
                    "Profile:p1",
                    {
                        "Label": "Profile 1",
                        "Vendor": "Vendor 1",
                        "Model": "Model 1",
                        "Description": "Description 1",
                    },
                ),
            },
        )
        profile = _profiles(profile_config)["p1"]
        self.assertEqual(profile.label, "Profile 1")
        self.assertEqual(profile.vendor, "Vendor 1")
        self.assertEqual(profile.model, "Model 1")
        self.assertEqual(profile.description, "Description 1")

        capability_config = FakeInventoryConfig(
            ["Capability:p1:meter", "Capability:p1:switch"],
            {
                "Capability:p1:meter": FakeInventorySection(
                    "Capability:p1:meter",
                    {
                        "Kind": "meter",
                        "AdapterType": "template_meter",
                        "SupportedPhases": "L1,L2,L3",
                        "MeasuresPower": "1",
                        "MeasuresEnergy": "1",
                    },
                ),
                "Capability:p1:switch": FakeInventorySection(
                    "Capability:p1:switch",
                    {
                        "Kind": "switch",
                        "AdapterType": "template_switch",
                        "SupportedPhases": "L1,L2",
                        "Channel": "relay0",
                        "MeasuresPower": "0",
                        "MeasuresEnergy": "0",
                        "SwitchingMode": "direct",
                        "SupportsFeedback": "1",
                        "SupportsPhaseSelection": "1",
                    },
                ),
            },
        )
        capabilities = _capabilities(capability_config)["p1"]
        meter_capability = capabilities["meter"]
        self.assertEqual(meter_capability.adapter_type, "template_meter")
        self.assertTrue(meter_capability.measures_power)
        self.assertTrue(meter_capability.measures_energy)
        capability = capabilities["switch"]
        self.assertEqual(capability.adapter_type, "template_switch")
        self.assertEqual(capability.supported_phases, ("L1", "L2"))
        self.assertEqual(capability.channel, "relay0")
        self.assertFalse(capability.measures_power)
        self.assertFalse(capability.measures_energy)
        self.assertEqual(capability.switching_mode, "direct")
        self.assertTrue(capability.supports_feedback)
        self.assertTrue(capability.supports_phase_selection)

        device_config = FakeInventoryConfig(
            ["Device:d1"],
            {
                "Device:d1": FakeInventorySection(
                    "Device:d1",
                    {
                        "Profile": "p1",
                        "Label": "Device 1",
                        "Endpoint": "http://device.local",
                        "Enabled": "0",
                        "Notes": "Device notes",
                    },
                ),
            },
        )
        device = _devices(device_config)["d1"]
        self.assertEqual(device.profile_id, "p1")
        self.assertEqual(device.label, "Device 1")
        self.assertEqual(device.endpoint, "http://device.local")
        self.assertFalse(device.enabled)
        self.assertEqual(device.notes, "Device notes")
        default_enabled_config = FakeInventoryConfig(
            ["Device:d2"],
            {
                "Device:d2": FakeInventorySection(
                    "Device:d2",
                    {
                        "Profile": "p1",
                        "Label": "Device 2",
                    },
                ),
            },
        )
        self.assertTrue(_devices(default_enabled_config)["d2"].enabled)

        binding_config = FakeInventoryConfig(
            ["Binding:b1"],
            {
                "Binding:b1": FakeInventorySection(
                    "Binding:b1",
                    {
                        "Role": "measurement",
                        "Label": "Binding 1",
                        "PhaseScope": "L1,L2",
                    },
                ),
            },
        )
        binding = _bindings(binding_config)["b1"]
        self.assertEqual(binding.role, "measurement")
        self.assertEqual(binding.label, "Binding 1")
        self.assertEqual(binding.phase_scope, ("L1", "L2"))

        member_config = FakeInventoryConfig(
            ["BindingMember:b1:m1"],
            {
                "BindingMember:b1:m1": FakeInventorySection(
                    "BindingMember:b1:m1",
                    {
                        "Device": "d1",
                        "Capability": "meter",
                        "Phases": "L2",
                    },
                ),
            },
        )
        member = _binding_members(member_config)["b1"]["m1"]
        self.assertEqual(member.device_id, "d1")
        self.assertEqual(member.capability_id, "meter")
        self.assertEqual(member.phases, ("L2",))

    def test_section_parsers_report_invalid_section_names_with_full_label(self) -> None:
        invalid_capability = FakeInventoryConfig(
            ["Capability:p1"],
            {"Capability:p1": FakeInventorySection("Capability:p1", {})},
        )
        self._assert_inventory_error(
            "invalid section name 'Capability:p1'",
            _capabilities,
            invalid_capability,
        )
        invalid_member = FakeInventoryConfig(
            ["BindingMember:b1"],
            {"BindingMember:b1": FakeInventorySection("BindingMember:b1", {})},
        )
        self._assert_inventory_error(
            "invalid section name 'BindingMember:b1'",
            _binding_members,
            invalid_member,
        )

    def test_section_parsers_reject_lowercase_required_schema_keys(self) -> None:
        lowercase_profile = FakeInventoryConfig(
            ["Profile:p1"],
            {"Profile:p1": FakeInventorySection("Profile:p1", {"label": "Profile 1"})},
        )
        self._assert_inventory_error("missing required key Profile:p1.Label", _profiles, lowercase_profile)

        lowercase_capability = FakeInventoryConfig(
            ["Capability:p1:meter"],
            {
                "Capability:p1:meter": FakeInventorySection(
                    "Capability:p1:meter",
                    {
                        "kind": "meter",
                        "adaptertype": "template_meter",
                        "supportedphases": "L1",
                        "measurespower": "1",
                    },
                ),
            },
        )
        self._assert_inventory_error(
            "missing required key Capability:p1:meter.Kind",
            _capabilities,
            lowercase_capability,
        )

        lowercase_device = FakeInventoryConfig(
            ["Device:d1"],
            {"Device:d1": FakeInventorySection("Device:d1", {"profile": "p1", "label": "Device 1"})},
        )
        self._assert_inventory_error("missing required key Device:d1.Profile", _devices, lowercase_device)

        lowercase_binding = FakeInventoryConfig(
            ["Binding:b1"],
            {
                "Binding:b1": FakeInventorySection(
                    "Binding:b1",
                    {"role": "measurement", "label": "Binding 1", "phasescope": "L1"},
                ),
            },
        )
        self._assert_inventory_error("missing required key Binding:b1.Role", _bindings, lowercase_binding)

        lowercase_member = FakeInventoryConfig(
            ["BindingMember:b1:m1"],
            {
                "BindingMember:b1:m1": FakeInventorySection(
                    "BindingMember:b1:m1",
                    {"device": "d1", "capability": "meter", "phases": "L1"},
                ),
            },
        )
        self._assert_inventory_error(
            "missing required key BindingMember:b1:m1.Device",
            _binding_members,
            lowercase_member,
        )

    def test_render_contract_covers_empty_inventory_false_flags_and_member_indices(self) -> None:
        self.assertEqual(render_device_inventory_config(DeviceInventory()), "")

        inventory = DeviceInventory(
            profiles=(
                DeviceProfile(
                    id="combo",
                    label="Combo",
                    capabilities=(
                        DeviceCapability(
                            id="meter",
                            kind="meter",
                            adapter_type="template_meter",
                            supported_phases=("L1", "L2"),
                            measures_power=False,
                            measures_energy=True,
                        ),
                        DeviceCapability(
                            id="meter_power_only",
                            kind="meter",
                            adapter_type="template_meter",
                            supported_phases=("L3",),
                            measures_power=True,
                            measures_energy=False,
                        ),
                        DeviceCapability(
                            id="switch",
                            kind="switch",
                            adapter_type="template_switch",
                            supported_phases=("L1",),
                            switching_mode="direct",
                            supports_feedback=False,
                            supports_phase_selection=False,
                        ),
                    ),
                ),
            ),
            devices=(
                DeviceInstance(
                    id="combo_device",
                    profile_id="combo",
                    label="Combo device",
                    enabled=False,
                ),
            ),
            bindings=(
                RoleBinding(
                    id="measurement",
                    role="measurement",
                    label="Measurement",
                    phase_scope=("L1", "L2"),
                    members=(
                        RoleBindingMember(device_id="combo_device", capability_id="meter", phases=("L1",)),
                        RoleBindingMember(device_id="combo_device", capability_id="meter", phases=("L2",)),
                    ),
                ),
            ),
        )

        rendered = render_device_inventory_config(inventory)

        self.assertTrue(rendered.endswith("\n"))
        self.assertIn("MeasuresPower=0", rendered)
        self.assertIn("MeasuresEnergy=1", rendered)
        self.assertIn("MeasuresPower=1", rendered)
        self.assertIn("MeasuresEnergy=0", rendered)
        self.assertIn("SupportsFeedback=0", rendered)
        self.assertIn("SupportsPhaseSelection=0", rendered)
        self.assertIn("Enabled=0", rendered)
        self.assertIn("[BindingMember:measurement:1]", rendered)
        self.assertIn("[BindingMember:measurement:2]", rendered)
        self.assertNotIn("[BindingMember:measurement:0]", rendered)
        self.assertNotIn("[BindingMember:measurement:None]", rendered)

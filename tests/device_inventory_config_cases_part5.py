# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from collections.abc import Callable

from venus_evcharger.inventory import (
    DeviceCapability,
    DeviceInstance,
    DeviceInventory,
    DeviceInventoryConfigError,
    DeviceProfile,
    PhaseLabel,
    RoleBinding,
    RoleBindingMember,
)
from venus_evcharger.inventory.config_validation import validate_device_inventory
from venus_evcharger.inventory.config_values import _suffix


class DeviceInventoryConfigPart5Tests(unittest.TestCase):
    def _meter_profile(
        self,
        *,
        profile_id: str = "p1",
        capability_id: str = "meter",
        phases: tuple[PhaseLabel, ...] = ("L1",),
    ) -> DeviceProfile:
        return DeviceProfile(
            id=profile_id,
            label="Profile",
            capabilities=(
                DeviceCapability(
                    id=capability_id,
                    kind="meter",
                    adapter_type="template_meter",
                    supported_phases=phases,
                    measures_power=True,
                ),
            ),
        )

    def _inventory_error(self, expected: str, callback: Callable[..., object], *args: object) -> None:
        with self.assertRaises(DeviceInventoryConfigError) as caught:
            callback(*args)
        self.assertEqual(str(caught.exception), expected)

    def test_validation_errors_report_exact_profile_and_device_contracts(self) -> None:
        self._inventory_error(
            "duplicate profile id 'p1'",
            validate_device_inventory,
            DeviceInventory(profiles=(self._meter_profile(), self._meter_profile())),
        )
        self._inventory_error(
            "device 'd1' references unknown profile 'missing'",
            validate_device_inventory,
            DeviceInventory(devices=(DeviceInstance(id="d1", profile_id="missing", label="Device"),)),
        )
        self._inventory_error(
            "duplicate device id 'd1'",
            validate_device_inventory,
            DeviceInventory(
                profiles=(self._meter_profile(),),
                devices=(
                    DeviceInstance(id="d1", profile_id="p1", label="Device 1"),
                    DeviceInstance(id="d1", profile_id="p1", label="Device 2"),
                ),
            ),
        )
        self._inventory_error(
            "duplicate capability id 'meter' for profile 'p1'",
            validate_device_inventory,
            DeviceInventory(
                profiles=(
                    DeviceProfile(
                        id="p1",
                        label="Profile",
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
            ),
        )

    def test_validation_errors_report_exact_binding_contracts(self) -> None:
        profile = self._meter_profile(phases=("L1", "L2"))
        device = DeviceInstance(id="d1", profile_id="p1", label="Device")

        self._inventory_error(
            "duplicate binding id 'b1'",
            validate_device_inventory,
            DeviceInventory(
                profiles=(profile,),
                devices=(device,),
                bindings=(
                    RoleBinding(
                        id="b1",
                        role="measurement",
                        label="B1",
                        phase_scope=("L1",),
                        members=(RoleBindingMember(device_id="d1", capability_id="meter", phases=("L1",)),),
                    ),
                    RoleBinding(
                        id="b1",
                        role="measurement",
                        label="B1 duplicate",
                        phase_scope=("L2",),
                        members=(RoleBindingMember(device_id="d1", capability_id="meter", phases=("L2",)),),
                    ),
                ),
            ),
        )
        self._inventory_error(
            "binding 'b1' requires at least one member",
            validate_device_inventory,
            DeviceInventory(
                profiles=(profile,),
                devices=(device,),
                bindings=(RoleBinding(id="b1", role="measurement", label="B1", phase_scope=("L1",)),),
            ),
        )
        self._inventory_error(
            "binding 'b1' references unknown device 'missing'",
            validate_device_inventory,
            DeviceInventory(
                profiles=(profile,),
                bindings=(
                    RoleBinding(
                        id="b1",
                        role="measurement",
                        label="B1",
                        phase_scope=("L1",),
                        members=(RoleBindingMember(device_id="missing", capability_id="meter", phases=("L1",)),),
                    ),
                ),
            ),
        )
        self._inventory_error(
            "binding 'b1' references unknown capability 'missing' in profile 'p1'",
            validate_device_inventory,
            DeviceInventory(
                profiles=(profile,),
                devices=(device,),
                bindings=(
                    RoleBinding(
                        id="b1",
                        role="measurement",
                        label="B1",
                        phase_scope=("L1",),
                        members=(RoleBindingMember(device_id="d1", capability_id="missing", phases=("L1",)),),
                    ),
                ),
            ),
        )

    def test_validation_errors_report_exact_phase_and_kind_contracts(self) -> None:
        meter_profile = self._meter_profile(phases=("L1", "L2"))
        device = DeviceInstance(id="d1", profile_id="p1", label="Device")

        cases = (
            (
                "binding 'b1' role 'actuation' requires capability kind 'switch'",
                RoleBinding(
                    id="b1",
                    role="actuation",
                    label="B1",
                    phase_scope=("L1",),
                    members=(RoleBindingMember(device_id="d1", capability_id="meter", phases=("L1",)),),
                ),
            ),
            (
                "binding 'b1' assigns phases L2 outside capability support L1",
                RoleBinding(
                    id="b1",
                    role="measurement",
                    label="B1",
                    phase_scope=("L2",),
                    members=(RoleBindingMember(device_id="d1", capability_id="meter", phases=("L2",)),),
                ),
            ),
            (
                "binding 'b1' assigns phases L2 outside binding scope L1",
                RoleBinding(
                    id="b1",
                    role="measurement",
                    label="B1",
                    phase_scope=("L1",),
                    members=(RoleBindingMember(device_id="d1", capability_id="meter", phases=("L2",)),),
                ),
            ),
        )
        single_l1_profile = self._meter_profile(phases=("L1",))
        for expected, binding in cases:
            with self.subTest(expected=expected):
                profile = single_l1_profile if "capability support" in expected else meter_profile
                self._inventory_error(
                    expected,
                    validate_device_inventory,
                    DeviceInventory(profiles=(profile,), devices=(device,), bindings=(binding,)),
                )

        duplicate_phase = RoleBinding(
            id="b1",
            role="measurement",
            label="B1",
            phase_scope=("L1", "L2"),
            members=(
                RoleBindingMember(device_id="d1", capability_id="meter", phases=("L1",)),
                RoleBindingMember(device_id="d1", capability_id="meter", phases=("L1", "L2")),
            ),
        )
        self._inventory_error(
            "binding 'b1' assigns duplicate phases L1",
            validate_device_inventory,
            DeviceInventory(profiles=(meter_profile,), devices=(device,), bindings=(duplicate_phase,)),
        )

        missing_phase = RoleBinding(
            id="b1",
            role="measurement",
            label="B1",
            phase_scope=("L1", "L2"),
            members=(RoleBindingMember(device_id="d1", capability_id="meter", phases=("L1",)),),
        )
        self._inventory_error(
            "binding 'b1' covers phases L1 but scope is L1,L2",
            validate_device_inventory,
            DeviceInventory(profiles=(meter_profile,), devices=(device,), bindings=(missing_phase,)),
        )

    def test_validation_errors_report_exact_multi_phase_contracts(self) -> None:
        two_phase_profile = self._meter_profile(phases=("L1", "L2"))
        three_phase_profile = self._meter_profile(phases=("L1", "L2", "L3"))
        device = DeviceInstance(id="d1", profile_id="p1", label="Device")

        duplicate_phases = RoleBinding(
            id="b1",
            role="measurement",
            label="B1",
            phase_scope=("L1", "L2"),
            members=(
                RoleBindingMember(device_id="d1", capability_id="meter", phases=("L1", "L2")),
                RoleBindingMember(device_id="d1", capability_id="meter", phases=("L1", "L2")),
            ),
        )
        self._inventory_error(
            "binding 'b1' assigns duplicate phases L1,L2",
            validate_device_inventory,
            DeviceInventory(profiles=(two_phase_profile,), devices=(device,), bindings=(duplicate_phases,)),
        )

        missing_phase = RoleBinding(
            id="b1",
            role="measurement",
            label="B1",
            phase_scope=("L1", "L2", "L3"),
            members=(RoleBindingMember(device_id="d1", capability_id="meter", phases=("L1", "L2")),),
        )
        self._inventory_error(
            "binding 'b1' covers phases L1,L2 but scope is L1,L2,L3",
            validate_device_inventory,
            DeviceInventory(profiles=(three_phase_profile,), devices=(device,), bindings=(missing_phase,)),
        )

        unsupported_phase = RoleBinding(
            id="b1",
            role="measurement",
            label="B1",
            phase_scope=("L2", "L3"),
            members=(RoleBindingMember(device_id="d1", capability_id="meter", phases=("L2", "L3")),),
        )
        self._inventory_error(
            "binding 'b1' assigns phases L2,L3 outside capability support L1,L2",
            validate_device_inventory,
            DeviceInventory(profiles=(two_phase_profile,), devices=(device,), bindings=(unsupported_phase,)),
        )

        outside_scope = RoleBinding(
            id="b1",
            role="measurement",
            label="B1",
            phase_scope=("L1", "L2"),
            members=(RoleBindingMember(device_id="d1", capability_id="meter", phases=("L2", "L3")),),
        )
        self._inventory_error(
            "binding 'b1' assigns phases L2,L3 outside binding scope L1,L2",
            validate_device_inventory,
            DeviceInventory(profiles=(three_phase_profile,), devices=(device,), bindings=(outside_scope,)),
        )

    def test_validation_errors_keep_profile_context_in_capability_contracts(self) -> None:
        cases = (
            (
                "meter capability 'meter' in profile 'profile_ctx' must measure power or energy",
                DeviceCapability(
                    id="meter",
                    kind="meter",
                    adapter_type="template_meter",
                    supported_phases=("L1",),
                ),
            ),
            (
                "non-meter capability 'switch' in profile 'profile_ctx' may not declare measurement flags",
                DeviceCapability(
                    id="switch",
                    kind="switch",
                    adapter_type="template_switch",
                    supported_phases=("L1",),
                    measures_power=True,
                    switching_mode="direct",
                ),
            ),
            (
                "switch capability 'switch' in profile 'profile_ctx' requires SwitchingMode",
                DeviceCapability(
                    id="switch",
                    kind="switch",
                    adapter_type="template_switch",
                    supported_phases=("L1",),
                ),
            ),
            (
                "non-switch capability 'charger' in profile 'profile_ctx' may not declare SwitchingMode",
                DeviceCapability(
                    id="charger",
                    kind="charger",
                    adapter_type="template_charger",
                    supported_phases=("L1",),
                    switching_mode="direct",
                ),
            ),
        )
        for expected, capability in cases:
            with self.subTest(expected=expected):
                self._inventory_error(
                    expected,
                    validate_device_inventory,
                    DeviceInventory(
                        profiles=(
                            DeviceProfile(
                                id="profile_ctx",
                                label="Profile context",
                                capabilities=(capability,),
                            ),
                        ),
                    ),
                )

    def test_suffix_reports_full_invalid_section_name(self) -> None:
        self._inventory_error("invalid section name 'Device:d1'", _suffix, "Device:d1", "Profile:")

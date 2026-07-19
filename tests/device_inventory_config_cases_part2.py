# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from venus_evcharger.inventory import (
    DeviceCapability,
    DeviceInstance,
    DeviceInventory,
    DeviceInventoryConfigError,
    DeviceProfile,
    RoleBinding,
    RoleBindingMember,
    render_device_inventory_config,
)


class DeviceInventoryConfigPart2Tests(unittest.TestCase):
    def test_validation_rejects_duplicate_and_unknown_references(self) -> None:
        duplicated_profiles = DeviceInventory(
            profiles=(
                DeviceProfile(id="p1", label="P1", capabilities=(
                    DeviceCapability(
                        id="meter",
                        kind="meter",
                        adapter_type="template_meter",
                        supported_phases=("L1",),
                        measures_power=True,
                    ),
                )),
                DeviceProfile(id="p1", label="P1 duplicate", capabilities=(
                    DeviceCapability(
                        id="meter",
                        kind="meter",
                        adapter_type="template_meter",
                        supported_phases=("L1",),
                        measures_power=True,
                    ),
                )),
            ),
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "duplicate profile id"):
            render_device_inventory_config(duplicated_profiles)

        missing_profile = DeviceInventory(
            devices=(DeviceInstance(id="d1", profile_id="missing", label="Device"),),
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "unknown profile"):
            render_device_inventory_config(missing_profile)

        empty_binding = DeviceInventory(
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
            devices=(DeviceInstance(id="d1", profile_id="p1", label="Device"),),
            bindings=(RoleBinding(id="b1", role="measurement", label="B1", phase_scope=("L1",), members=()),),
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "requires at least one member"):
            render_device_inventory_config(empty_binding)

        duplicate_devices = DeviceInventory(
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
                DeviceInstance(id="d1", profile_id="p1", label="Device 1"),
                DeviceInstance(id="d1", profile_id="p1", label="Device 2"),
            ),
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "duplicate device id"):
            render_device_inventory_config(duplicate_devices)

        duplicate_bindings = DeviceInventory(
            profiles=duplicate_devices.profiles,
            devices=(DeviceInstance(id="d1", profile_id="p1", label="Device 1"),),
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
                    phase_scope=("L1",),
                    members=(RoleBindingMember(device_id="d1", capability_id="meter", phases=("L1",)),),
                ),
            ),
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "duplicate binding id"):
            render_device_inventory_config(duplicate_bindings)

    def test_validation_rejects_profile_and_binding_capability_errors(self) -> None:
        with self.assertRaisesRegex(DeviceInventoryConfigError, "requires at least one capability"):
            render_device_inventory_config(DeviceInventory(profiles=(DeviceProfile(id="p1", label="P1"),)))

        with self.assertRaisesRegex(DeviceInventoryConfigError, "must measure power or energy"):
            render_device_inventory_config(
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
                                ),
                            ),
                        ),
                    ),
                )
            )

        with self.assertRaisesRegex(DeviceInventoryConfigError, "may not declare measurement flags"):
            render_device_inventory_config(
                DeviceInventory(
                    profiles=(
                        DeviceProfile(
                            id="p1",
                            label="P1",
                            capabilities=(
                                DeviceCapability(
                                    id="switch",
                                    kind="switch",
                                    adapter_type="template_switch",
                                    supported_phases=("L1",),
                                    measures_power=True,
                                    switching_mode="contactor",
                                ),
                            ),
                        ),
                    ),
                )
            )

        with self.assertRaisesRegex(DeviceInventoryConfigError, "requires SwitchingMode"):
            render_device_inventory_config(
                DeviceInventory(
                    profiles=(
                        DeviceProfile(
                            id="p1",
                            label="P1",
                            capabilities=(
                                DeviceCapability(
                                    id="switch",
                                    kind="switch",
                                    adapter_type="template_switch",
                                    supported_phases=("L1",),
                                ),
                            ),
                        ),
                    ),
                )
            )

        with self.assertRaisesRegex(DeviceInventoryConfigError, "may not declare SwitchingMode"):
            render_device_inventory_config(
                DeviceInventory(
                    profiles=(
                        DeviceProfile(
                            id="p1",
                            label="P1",
                            capabilities=(
                                DeviceCapability(
                                    id="charger",
                                    kind="charger",
                                    adapter_type="template_charger",
                                    supported_phases=("L1",),
                                    switching_mode="direct",
                                ),
                            ),
                        ),
                    ),
                )
            )

        invalid_binding_inventory = DeviceInventory(
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
            devices=(DeviceInstance(id="d1", profile_id="p1", label="Device 1"),),
            bindings=(
                RoleBinding(
                    id="b1",
                    role="measurement",
                    label="B1",
                    phase_scope=("L1", "L2"),
                    members=(RoleBindingMember(device_id="d1", capability_id="missing", phases=("L1",)),),
                ),
            ),
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "unknown capability"):
            render_device_inventory_config(invalid_binding_inventory)

        invalid_binding_inventory = DeviceInventory(
            profiles=invalid_binding_inventory.profiles,
            devices=(DeviceInstance(id="d1", profile_id="p1", label="Device 1"),),
            bindings=(
                RoleBinding(
                    id="b1",
                    role="measurement",
                    label="B1",
                    phase_scope=("L1",),
                    members=(RoleBindingMember(device_id="d1", capability_id="meter", phases=("L2",)),),
                ),
            ),
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "outside capability support"):
            render_device_inventory_config(invalid_binding_inventory)

        invalid_binding_inventory = DeviceInventory(
            profiles=(
                DeviceProfile(
                    id="p1",
                    label="P1",
                    capabilities=(
                        DeviceCapability(
                            id="meter",
                            kind="meter",
                            adapter_type="template_meter",
                            supported_phases=("L1", "L2"),
                            measures_power=True,
                        ),
                    ),
                ),
            ),
            devices=(DeviceInstance(id="d1", profile_id="p1", label="Device 1"),),
            bindings=(
                RoleBinding(
                    id="b1",
                    role="measurement",
                    label="B1",
                    phase_scope=("L1",),
                    members=(RoleBindingMember(device_id="d1", capability_id="meter", phases=("L2",)),),
                ),
            ),
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "outside binding scope"):
            render_device_inventory_config(invalid_binding_inventory)

        invalid_binding_inventory = DeviceInventory(
            profiles=(
                DeviceProfile(
                    id="p1",
                    label="P1",
                    capabilities=(
                        DeviceCapability(
                            id="meter",
                            kind="meter",
                            adapter_type="template_meter",
                            supported_phases=("L1", "L2"),
                            measures_power=True,
                        ),
                    ),
                ),
            ),
            devices=(DeviceInstance(id="d1", profile_id="p1", label="Device 1"),),
            bindings=(
                RoleBinding(
                    id="b1",
                    role="measurement",
                    label="B1",
                    phase_scope=("L1", "L2"),
                    members=(RoleBindingMember(device_id="d1", capability_id="meter", phases=("L1",)),),
                ),
            ),
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "covers phases"):
            render_device_inventory_config(invalid_binding_inventory)

# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from venus_evcharger.bootstrap.wizard_inventory_support import (
    inventory_action_path,
    inventory_default_path,
    inventory_summary_payload,
    inventory_summary_text,
    load_inventory,
    parse_inventory_binding_role,
    parse_inventory_kind,
    parse_inventory_phases,
    parse_inventory_switching_mode,
    save_inventory,
)
from venus_evcharger.inventory import (
    DeviceCapability,
    DeviceInstance,
    DeviceInventory,
    DeviceProfile,
    RoleBinding,
    RoleBindingMember,
)


def _namespace(**values: object) -> argparse.Namespace:
    return argparse.Namespace(**values)


def _inventory() -> DeviceInventory:
    return DeviceInventory(
        profiles=(
            DeviceProfile(
                id="switch_profile",
                label="Switch profile",
                vendor="Shelly",
                model="1PM",
                capabilities=(
                    DeviceCapability(
                        id="relay",
                        kind="switch",
                        adapter_type="shelly_gen2",
                        supported_phases=("L1",),
                        channel="0",
                        switching_mode="direct",
                        supports_feedback=True,
                    ),
                ),
            ),
            DeviceProfile(
                id="meter_profile",
                label="Meter profile",
                capabilities=(
                    DeviceCapability(
                        id="meter",
                        kind="meter",
                        adapter_type="template_meter",
                        supported_phases=("L1", "L2"),
                        measures_power=True,
                        measures_energy=True,
                    ),
                ),
            ),
        ),
        devices=(
            DeviceInstance(
                id="switch_device",
                profile_id="switch_profile",
                label="Switch device",
                endpoint="http://switch.local",
            ),
            DeviceInstance(
                id="meter_device",
                profile_id="meter_profile",
                label="Meter device",
            ),
        ),
        bindings=(
            RoleBinding(
                id="actuation",
                role="actuation",
                label="Actuation",
                phase_scope=("L1",),
                members=(RoleBindingMember(device_id="switch_device", capability_id="relay", phases=("L1",)),),
            ),
            RoleBinding(
                id="measurement",
                role="measurement",
                label="Measurement",
                phase_scope=("L1", "L2"),
                members=(RoleBindingMember(device_id="meter_device", capability_id="meter", phases=("L1", "L2")),),
            ),
        ),
    )


def _multi_summary_inventory() -> DeviceInventory:
    return DeviceInventory(
        profiles=(
            DeviceProfile(
                id="combo_profile",
                label="Combo profile",
                capabilities=(
                    DeviceCapability(
                        id="relay",
                        kind="switch",
                        adapter_type="shelly_gen2",
                        supported_phases=("L1",),
                    ),
                    DeviceCapability(
                        id="meter",
                        kind="meter",
                        adapter_type="template_meter",
                        supported_phases=("L1",),
                    ),
                ),
            ),
        ),
        devices=(
            DeviceInstance(id="switch_device", profile_id="combo_profile", label="Switch device"),
            DeviceInstance(id="meter_device", profile_id="combo_profile", label="Meter device"),
        ),
        bindings=(
            RoleBinding(
                id="measurement",
                role="measurement",
                label="Measurement",
                phase_scope=("L1",),
                members=(
                    RoleBindingMember(device_id="switch_device", capability_id="relay", phases=("L1",)),
                    RoleBindingMember(device_id="meter_device", capability_id="meter", phases=("L1",)),
                ),
            ),
        ),
    )


class WizardInventorySupportContractTests(unittest.TestCase):
    def test_inventory_paths_are_deterministic_and_cli_override_wins(self) -> None:
        self.assertEqual(
            inventory_default_path("/data/etc/venus-evcharger/config.ini"),
            Path("/data/etc/venus-evcharger/config.ini.wizard-inventory.ini"),
        )
        self.assertEqual(
            inventory_action_path(
                _namespace(config_path="/data/etc/venus-evcharger/config.ini", inventory_path="/tmp/custom.ini")
            ),
            Path("/tmp/custom.ini"),
        )
        self.assertEqual(
            inventory_action_path(_namespace(config_path="/data/etc/venus-evcharger/config.ini", inventory_path="")),
            Path("/data/etc/venus-evcharger/config.ini.wizard-inventory.ini"),
        )
        self.assertEqual(
            inventory_action_path(_namespace(config_path="/data/etc/venus-evcharger/config.ini")),
            Path("/data/etc/venus-evcharger/config.ini.wizard-inventory.ini"),
        )

    def test_parse_inventory_helpers_normalize_and_reject_values(self) -> None:
        self.assertEqual(parse_inventory_phases(" l2, L1, l2, L3 "), ("L2", "L1", "L3"))
        with self.assertRaises(ValueError) as empty_phase_error:
            parse_inventory_phases(" , ")
        self.assertEqual(str(empty_phase_error.exception), "Phase list must not be empty")
        with self.assertRaisesRegex(ValueError, "Unknown phase label: l4"):
            parse_inventory_phases("L1,l4")
        self.assertEqual(parse_inventory_phases("L1,,L2"), ("L1", "L2"))

        self.assertEqual(parse_inventory_kind(" METER "), "meter")
        self.assertEqual(parse_inventory_binding_role(" Charger "), "charger")
        self.assertEqual(parse_inventory_switching_mode(" CONTACTOR "), "contactor")
        self.assertIsNone(parse_inventory_switching_mode(None))
        self.assertIsNone(parse_inventory_switching_mode("  "))
        with self.assertRaises(ValueError) as kind_error:
            parse_inventory_kind("sensor")
        self.assertEqual(str(kind_error.exception), "Capability kind must be one of: charger, meter, switch")
        with self.assertRaises(ValueError) as role_error:
            parse_inventory_binding_role("sensor")
        self.assertEqual(str(role_error.exception), "Binding role must be one of: actuation, charger, measurement")
        with self.assertRaises(ValueError) as switching_mode_error:
            parse_inventory_switching_mode("relay")
        self.assertEqual(str(switching_mode_error.exception), "Switching mode must be one of: contactor, direct")

    def test_inventory_summary_payload_is_json_ready_and_complete(self) -> None:
        self.assertEqual(
            inventory_summary_payload(Path("/tmp/inventory.ini"), _inventory()),
            {
                "inventory_path": "/tmp/inventory.ini",
                "profiles": [
                    {
                        "id": "switch_profile",
                        "label": "Switch profile",
                        "vendor": "Shelly",
                        "model": "1PM",
                        "capabilities": [
                            {
                                "id": "relay",
                                "kind": "switch",
                                "adapter_type": "shelly_gen2",
                                "supported_phases": ["L1"],
                                "channel": "0",
                                "measures_power": False,
                                "measures_energy": False,
                                "switching_mode": "direct",
                                "supports_feedback": True,
                                "supports_phase_selection": False,
                            }
                        ],
                    },
                    {
                        "id": "meter_profile",
                        "label": "Meter profile",
                        "vendor": None,
                        "model": None,
                        "capabilities": [
                            {
                                "id": "meter",
                                "kind": "meter",
                                "adapter_type": "template_meter",
                                "supported_phases": ["L1", "L2"],
                                "channel": None,
                                "measures_power": True,
                                "measures_energy": True,
                                "switching_mode": None,
                                "supports_feedback": False,
                                "supports_phase_selection": False,
                            }
                        ],
                    },
                ],
                "devices": [
                    {
                        "id": "switch_device",
                        "profile_id": "switch_profile",
                        "label": "Switch device",
                        "endpoint": "http://switch.local",
                        "enabled": True,
                    },
                    {
                        "id": "meter_device",
                        "profile_id": "meter_profile",
                        "label": "Meter device",
                        "endpoint": None,
                        "enabled": True,
                    },
                ],
                "bindings": [
                    {
                        "id": "actuation",
                        "role": "actuation",
                        "label": "Actuation",
                        "phase_scope": ["L1"],
                        "members": [
                            {"device_id": "switch_device", "capability_id": "relay", "phases": ["L1"]},
                        ],
                    },
                    {
                        "id": "measurement",
                        "role": "measurement",
                        "label": "Measurement",
                        "phase_scope": ["L1", "L2"],
                        "members": [
                            {"device_id": "meter_device", "capability_id": "meter", "phases": ["L1", "L2"]},
                        ],
                    },
                ],
            },
        )

    def test_inventory_summary_text_has_stable_human_readable_layout(self) -> None:
        self.assertEqual(
            inventory_summary_text(Path("/tmp/inventory.ini"), DeviceInventory()),
            "\n".join(
                [
                    "Inventory path: /tmp/inventory.ini",
                    "Profiles: 0",
                    "Devices: 0",
                    "Bindings: 0",
                    "Profiles:",
                    "  - none",
                    "Device instances:",
                    "  - none",
                    "Bindings:",
                    "  - none",
                ]
            ),
        )
        self.assertEqual(
            inventory_summary_text(Path("/tmp/inventory.ini"), _inventory()),
            "\n".join(
                [
                    "Inventory path: /tmp/inventory.ini",
                    "Profiles: 2",
                    "Devices: 2",
                    "Bindings: 2",
                    "Profiles:",
                    "  - switch_profile: relay/switch@shelly_gen2[L1]",
                    "  - meter_profile: meter/meter@template_meter[L1,L2]",
                    "Device instances:",
                    "  - switch_device: profile=switch_profile, label=Switch device, endpoint=http://switch.local",
                    "  - meter_device: profile=meter_profile, label=Meter device, endpoint=n/a",
                    "Bindings:",
                    "  - actuation: role=actuation, phases=L1, members=switch_device:relay[L1]",
                    "  - measurement: role=measurement, phases=L1,L2, members=meter_device:meter[L1,L2]",
                ]
            ),
        )
        self.assertIn(
            "relay/switch@shelly_gen2[L1], meter/meter@template_meter[L1]",
            inventory_summary_text(Path("/tmp/inventory.ini"), _multi_summary_inventory()),
        )
        self.assertIn(
            "switch_device:relay[L1], meter_device:meter[L1]",
            inventory_summary_text(Path("/tmp/inventory.ini"), _multi_summary_inventory()),
        )

    def test_save_and_load_inventory_roundtrips_and_creates_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "nested" / "deeper" / "inventory.ini"
            save_inventory(path, _inventory())
            save_inventory(path, _inventory())

            self.assertTrue(path.exists())
            self.assertIn("[Profile:switch_profile]\n", path.read_text(encoding="utf-8"))
            loaded = load_inventory(path)
            self.assertEqual([profile.id for profile in loaded.profiles], ["meter_profile", "switch_profile"])
            self.assertEqual([device.id for device in loaded.devices], ["meter_device", "switch_device"])
            self.assertEqual([binding.id for binding in loaded.bindings], ["actuation", "measurement"])

    def test_save_inventory_uses_explicit_utf8_write_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "inventory.ini"
            with (
                patch(
                    "venus_evcharger.bootstrap.wizard_inventory_support.render_device_inventory_config",
                    return_value="[Inventory]\n",
                ),
                patch.object(Path, "write_text", return_value=12) as write_text,
            ):
                save_inventory(path, _inventory())

        write_text.assert_called_once_with("[Inventory]\n", encoding="utf-8")

    def test_load_inventory_uses_explicit_utf8_read_contract(self) -> None:
        parser = Mock()
        inventory = _inventory()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "inventory.ini"
            path.write_text("[Inventory]\n", encoding="utf-8")
            with (
                patch("venus_evcharger.bootstrap.wizard_inventory_support.configparser.ConfigParser", return_value=parser),
                patch("venus_evcharger.bootstrap.wizard_inventory_support.parse_device_inventory_config", return_value=inventory),
            ):
                self.assertEqual(load_inventory(path), inventory)

        parser.read.assert_called_once_with(path, encoding="utf-8")

    def test_load_inventory_reports_missing_path(self) -> None:
        with self.assertRaisesRegex(ValueError, r"Inventory does not exist: /tmp/missing-inventory.ini"):
            load_inventory(Path("/tmp/missing-inventory.ini"))


if __name__ == "__main__":
    unittest.main()

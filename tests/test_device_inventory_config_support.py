# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
import unittest

from venus_evcharger.inventory import (
    DeviceCapability,
    DeviceInventory,
    DeviceInventoryConfigError,
    DeviceInstance,
    DeviceProfile,
    parse_device_inventory_config,
    RoleBinding,
    RoleBindingMember,
    render_device_inventory_config,
)
from venus_evcharger.inventory.config import (
    _binding_members,
    _bindings,
    _capabilities,
    _devices,
    _phase_labels,
    _render_switch_capability_fields,
    validate_device_inventory,
)


class _DeviceInventoryConfigTestsHelperRole:
    class _FakeConfig:
        def __init__(self, sections: list[str], mapping: dict[str, configparser.SectionProxy]) -> None:
            self._sections = sections
            self._mapping = mapping

        def sections(self) -> list[str]:
            return list(self._sections)

        def __getitem__(self, key: str) -> configparser.SectionProxy:
            return self._mapping[key]


__all__ = [name for name in globals() if not name.startswith("__")]

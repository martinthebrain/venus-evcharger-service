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
    _InventorySection,
    _as_bool,
    _binding_members,
    _binding_role,
    _bindings,
    _capability_kind,
    _capabilities,
    _devices,
    _optional_switching_mode,
    _phase_label,
    _phase_labels,
    _phase_tokens,
    _profiles,
    _render_switch_capability_fields,
    _suffix,
    validate_device_inventory,
)


class _DeviceInventoryConfigTestsHelperRole:
    class _FakeSection:
        def __init__(self, name: str, values: dict[str, object]) -> None:
            self.name = name
            self._values = values

        def get(self, key: str, fallback: object | None = None) -> object | None:
            return self._values.get(key, fallback)

    class _FakeConfig:
        def __init__(self, sections: list[str], mapping: dict[str, _InventorySection]) -> None:
            self._sections = sections
            self._mapping = mapping

        def sections(self) -> list[str]:
            return list(self._sections)

        def __getitem__(self, key: str) -> _InventorySection:
            return self._mapping[key]


__all__ = [name for name in globals() if not name.startswith("__")]

# SPDX-License-Identifier: GPL-3.0-or-later
from collections.abc import Mapping

from venus_evcharger.inventory.config_contracts import InventorySectionLike

__all__ = ["FakeInventoryConfig", "FakeInventorySection"]


class FakeInventorySection:
    """In-memory section implementing the production parser boundary."""

    def __init__(self, name: str, values: Mapping[str, object]) -> None:
        self.name = name
        self._values = dict(values)

    def get(self, key: str, fallback: object | None = None) -> object | None:
        return self._values.get(key, fallback)


class FakeInventoryConfig:
    """In-memory config implementing the production section collection boundary."""

    def __init__(
        self,
        sections: list[str],
        mapping: Mapping[str, InventorySectionLike],
    ) -> None:
        self._sections = list(sections)
        self._mapping = dict(mapping)

    def sections(self) -> list[str]:
        return list(self._sections)

    def __getitem__(self, key: str) -> InventorySectionLike:
        return self._mapping[key]

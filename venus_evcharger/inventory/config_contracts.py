# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed boundaries shared by inventory configuration components."""

from __future__ import annotations

import configparser
from typing import Protocol


class DeviceInventoryConfigError(ValueError):
    """Raised when one device inventory config is invalid."""


class InventorySection(Protocol):  # pragma: no cover
    """Minimal section interface required by the inventory parser."""

    @property
    def name(self) -> str: ...

    def get(self, key: str, fallback: object | None = None) -> object | None: ...


InventorySectionLike = InventorySection | configparser.SectionProxy


class InventoryConfigSections(Protocol):  # pragma: no cover
    """Minimal config interface required to enumerate inventory sections."""

    def sections(self) -> list[str]: ...

    def __getitem__(self, key: str) -> InventorySectionLike: ...

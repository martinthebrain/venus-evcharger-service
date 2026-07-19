# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime type guards for untyped cache and snapshot payload containers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypeGuard


def is_object_mapping(value: object) -> TypeGuard[Mapping[object, object]]:
    return isinstance(value, Mapping)


def is_object_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)

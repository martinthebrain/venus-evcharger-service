# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed contracts for DBus gateway read specifications."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypeAlias, TypedDict, cast


class ReadSpec(TypedDict, total=False):
    """Configuration for one scheduled DBus read group."""

    aggregate: str
    dc_path: str
    dc_service: str
    interval: float
    optional_confidence: float
    optional_zero_on_error: bool
    path: str
    paths: list[str]
    prefix: str
    priority: str
    service: str
    use_dc_pv: bool


ReadSpecs: TypeAlias = dict[str, ReadSpec]


def read_spec_from_mapping(spec: Mapping[str, object]) -> ReadSpec:
    """Return a mutable read-spec dict from a mapping-shaped test/config input."""
    return cast(ReadSpec, dict(spec))

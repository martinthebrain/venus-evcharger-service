# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed specifications for scheduled DBus reads."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TypeAlias, TypedDict


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
    checked: ReadSpec = {}
    for key, value in spec.items():
        _copy_read_spec_field(checked, str(key), value)
    return checked


def _copy_read_spec_field(spec: ReadSpec, key: str, value: object) -> None:
    if key in _TEXT_FIELDS:
        _copy_text_field(spec, key, value)
        return
    if key in _FLOAT_FIELDS:
        _copy_float_field(spec, key, value)
        return
    if key in _BOOL_FIELDS:
        _copy_bool_field(spec, key, value)
        return
    if key == "paths":
        _copy_paths_field(spec, value)
        return
    raise KeyError(f"unknown read spec field: {key}")


_TextSetter = Callable[[ReadSpec, str], None]
_FloatSetter = Callable[[ReadSpec, float], None]
_BoolSetter = Callable[[ReadSpec, bool], None]


def _copy_text_field(spec: ReadSpec, key: str, value: object) -> None:
    if not isinstance(value, str):
        raise TypeError(f"read spec field {key} must be str, got {type(value).__name__}")
    _TEXT_SETTERS[key](spec, value)


def _copy_float_field(spec: ReadSpec, key: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"read spec field {key} must be float, got {type(value).__name__}")
    _FLOAT_SETTERS[key](spec, float(value))


def _copy_bool_field(spec: ReadSpec, key: str, value: object) -> None:
    if not isinstance(value, bool):
        raise TypeError(f"read spec field {key} must be bool, got {type(value).__name__}")
    _BOOL_SETTERS[key](spec, value)


def _copy_paths_field(spec: ReadSpec, value: object) -> None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise TypeError(f"read spec field paths must be list[str], got {type(value).__name__}")
    spec["paths"] = list(value)


def _set_aggregate(spec: ReadSpec, value: str) -> None:
    spec["aggregate"] = value


def _set_dc_path(spec: ReadSpec, value: str) -> None:
    spec["dc_path"] = value


def _set_dc_service(spec: ReadSpec, value: str) -> None:
    spec["dc_service"] = value


def _set_path(spec: ReadSpec, value: str) -> None:
    spec["path"] = value


def _set_prefix(spec: ReadSpec, value: str) -> None:
    spec["prefix"] = value


def _set_priority(spec: ReadSpec, value: str) -> None:
    spec["priority"] = value


def _set_service(spec: ReadSpec, value: str) -> None:
    spec["service"] = value


def _set_interval(spec: ReadSpec, value: float) -> None:
    spec["interval"] = value


def _set_optional_confidence(spec: ReadSpec, value: float) -> None:
    spec["optional_confidence"] = value


def _set_optional_zero_on_error(spec: ReadSpec, value: bool) -> None:
    spec["optional_zero_on_error"] = value


def _set_use_dc_pv(spec: ReadSpec, value: bool) -> None:
    spec["use_dc_pv"] = value


_TEXT_SETTERS: dict[str, _TextSetter] = {
    "aggregate": _set_aggregate,
    "dc_path": _set_dc_path,
    "dc_service": _set_dc_service,
    "path": _set_path,
    "prefix": _set_prefix,
    "priority": _set_priority,
    "service": _set_service,
}
_FLOAT_SETTERS: dict[str, _FloatSetter] = {
    "interval": _set_interval,
    "optional_confidence": _set_optional_confidence,
}
_BOOL_SETTERS: dict[str, _BoolSetter] = {
    "optional_zero_on_error": _set_optional_zero_on_error,
    "use_dc_pv": _set_use_dc_pv,
}
_TEXT_FIELDS = frozenset(_TEXT_SETTERS)
_FLOAT_FIELDS = frozenset(_FLOAT_SETTERS)
_BOOL_FIELDS = frozenset(_BOOL_SETTERS)

# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed specifications for scheduled DBus reads."""

from __future__ import annotations

from collections.abc import Mapping
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
    stale_after_seconds: float
    use_dc_pv: bool


ReadSpecs: TypeAlias = dict[str, ReadSpec]


def read_spec_text(spec: Mapping[str, object], key: str) -> str:
    """Return one text field without leaking malformed dynamic values."""
    value = spec.get(key)
    return value.strip() if isinstance(value, str) else ""


def read_spec_stale_after_seconds(spec: ReadSpec) -> float | None:
    """Resolve explicit freshness or derive it from the polling interval."""
    configured = spec.get("stale_after_seconds")
    if configured is not None:
        return max(0.0, float(configured))
    interval = spec.get("interval")
    return max(1.0, float(interval) * 3.0) if interval is not None else None


def read_spec_optional_zero_on_error(spec: Mapping[str, object]) -> bool:
    """Return whether an explicitly truthy optional fallback is configured."""
    if "optional_zero_on_error" not in spec:
        return False
    return str(spec["optional_zero_on_error"]).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def read_spec_optional_confidence(spec: Mapping[str, object]) -> float:
    """Return the confidence assigned to an optional zero fallback."""
    if "optional_confidence" not in spec:
        return 0.2
    value = spec["optional_confidence"]
    if not value:
        return 0.2
    if isinstance(value, str | bytes | int | float):
        return float(value)
    raise TypeError(
        "read spec field optional_confidence must be numeric, "
        f"got {type(value).__name__}"
    )


def read_spec_source(spec: ReadSpec, *, fallback: str = "") -> str:
    """Resolve the most specific configured source label."""
    return read_spec_text(spec, "service") or read_spec_text(spec, "prefix") or str(fallback)


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
    _copy_non_text_field(spec, key, value)


def _copy_non_text_field(spec: ReadSpec, key: str, value: object) -> None:
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


def _copy_text_field(spec: ReadSpec, key: str, value: object) -> None:
    if not isinstance(value, str):
        raise TypeError(f"read spec field {key} must be str, got {type(value).__name__}")
    if key == "aggregate":
        spec["aggregate"] = value
        return
    if key in _SOURCE_TEXT_FIELDS:
        _copy_source_text_field(spec, key, value)
        return
    _copy_path_text_field(spec, key, value)


def _copy_source_text_field(spec: ReadSpec, key: str, value: str) -> None:
    if key == "service":
        spec["service"] = value
    elif key == "prefix":
        spec["prefix"] = value
    else:
        spec["priority"] = value


def _copy_path_text_field(spec: ReadSpec, key: str, value: str) -> None:
    if key == "path":
        spec["path"] = value
    elif key == "dc_path":
        spec["dc_path"] = value
    else:
        spec["dc_service"] = value


def _copy_float_field(spec: ReadSpec, key: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"read spec field {key} must be float, got {type(value).__name__}")
    normalized = float(value)
    if key == "interval":
        spec["interval"] = normalized
    elif key == "optional_confidence":
        spec["optional_confidence"] = normalized
    else:
        spec["stale_after_seconds"] = normalized


def _copy_bool_field(spec: ReadSpec, key: str, value: object) -> None:
    if not isinstance(value, bool):
        raise TypeError(f"read spec field {key} must be bool, got {type(value).__name__}")
    if key == "optional_zero_on_error":
        spec["optional_zero_on_error"] = value
    else:
        spec["use_dc_pv"] = value


def _copy_paths_field(spec: ReadSpec, value: object) -> None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise TypeError(f"read spec field paths must be list[str], got {type(value).__name__}")
    spec["paths"] = list(value)


_SOURCE_TEXT_FIELDS = frozenset({"service", "prefix", "priority"})
_PATH_TEXT_FIELDS = frozenset({"path", "dc_path", "dc_service"})
_TEXT_FIELDS = frozenset({"aggregate", *_SOURCE_TEXT_FIELDS, *_PATH_TEXT_FIELDS})
_FLOAT_FIELDS = frozenset({"interval", "optional_confidence", "stale_after_seconds"})
_BOOL_FIELDS = frozenset({"optional_zero_on_error", "use_dc_pv"})

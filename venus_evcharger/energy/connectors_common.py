# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared helpers for external energy-source connectors."""

from __future__ import annotations

from typing import Any, Iterable, TypeVar

from venus_evcharger.backend.template_support import json_path_value
from venus_evcharger.core.contracts import finite_float_or_none, normalize_binary_flag

T = TypeVar("T")


def _runtime_owner(owner: Any) -> Any:
    return getattr(owner, "service", owner)


def _cache_map(runtime: Any, attr_name: str) -> dict[str, object]:
    cache = getattr(runtime, attr_name, None)
    if isinstance(cache, dict):
        normalized = {str(key): value for key, value in cache.items()}
    else:
        normalized = {}
    setattr(runtime, attr_name, normalized)
    return normalized


def _typed_cache_map(runtime: Any, attr_name: str, expected_type: type[T]) -> dict[str, T]:
    cache = _cache_map(runtime, attr_name)
    typed = {key: value for key, value in cache.items() if isinstance(value, expected_type)}
    setattr(runtime, attr_name, typed)
    return typed


def _normalized_connector_type(raw_value: object) -> str:
    normalized = str(raw_value).strip().lower()
    if normalized == "template_http_energy":
        return "template_http"
    return normalized


def _optional_path(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _optional_float_path(payload: dict[str, object], path: str | None) -> float | None:
    if path is None:
        return None
    return finite_float_or_none(json_path_value(payload, path))


def _optional_text_path(payload: dict[str, object], path: str | None) -> str | None:
    if path is None:
        return None
    value = json_path_value(payload, path)
    text = "" if value is None else str(value).strip()
    return text or None


def _optional_bool_path(payload: dict[str, object], path: str | None) -> bool | None:
    if path is None:
        return None
    value = json_path_value(payload, path)
    normalized = _normalized_optional_bool_value(value)
    if normalized is not None:
        return normalized
    return bool(normalize_binary_flag(value))


def _normalized_optional_bool_value(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(int(value))
    return _normalized_optional_bool_text(value) if isinstance(value, str) else None


def _normalized_optional_bool_text(value: str) -> bool | None:
    return {
        "true": True,
        "yes": True,
        "on": True,
        "enabled": True,
        "false": False,
        "no": False,
        "off": False,
        "disabled": False,
    }.get(value.strip().lower())


def _optional_confidence_path(payload: dict[str, object], path: str | None) -> float | None:
    value = _optional_float_path(payload, path)
    if value is None:
        return None
    return min(1.0, max(0.0, float(value)))


def _sum_optional(values: Iterable[float | None]) -> float | None:
    numeric = [float(value) for value in values if value is not None]
    if not numeric:
        return None
    return float(sum(numeric))


def _csv_filter(raw_value: object) -> tuple[str, ...]:
    if raw_value is None:
        return ()
    raw = str(raw_value).strip()
    if not raw:
        return ()
    return tuple(item.strip() for item in raw.split(",") if item.strip())

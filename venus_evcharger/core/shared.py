# SPDX-License-Identifier: GPL-3.0-or-later
"""Small shared helpers reused across Venus EV charger modules."""

from collections.abc import Callable, Iterable, Sequence
import json
import os
from os import PathLike
from typing import Any, TypeAlias, cast


AUTO_INPUT_SNAPSHOT_SCHEMA_VERSION = 1
NumericScalar: TypeAlias = float | int
DbusNumeric: TypeAlias = NumericScalar | Sequence[object] | object
ServicePredicate: TypeAlias = Callable[[str], bool]
_UNCOERCED = object()


def _iter_numeric_container_items(value: Any) -> list[object] | None:
    """Return list-like DBus container items, or None for scalars and mappings."""
    if isinstance(value, (str, bytes, bytearray, dict)):
        return None
    try:
        return list(cast(Iterable[object], value)) if isinstance(value, Iterable) else None
    except TypeError:
        return None


def _coerce_scalar_numeric(value: Any) -> NumericScalar | None:
    """Convert one scalar DBus value to a Python number where possible."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    try:
        return int(value)
    except (TypeError, ValueError):
        pass
    return None


def _coerce_numeric_items(items: Iterable[object]) -> list[NumericScalar] | None:
    """Return coerced numeric items or None when any sequence entry is unusable."""
    numeric_items: list[NumericScalar] = []
    for item in items:
        numeric_item = _coerce_scalar_numeric(item)
        if numeric_item is None:
            return None
        numeric_items.append(numeric_item)
    return numeric_items


def _coerce_numeric_container_value(value: Any) -> Any:
    """Convert list-like DBus values when they contain exactly one usable number."""
    items = _iter_numeric_container_items(value)
    if items is None:
        return _UNCOERCED
    numeric_items = _coerce_numeric_items(items)
    if numeric_items is None:
        return value
    if len(numeric_items) == 1:
        return numeric_items[0]
    return value


def coerce_dbus_numeric(value: Any) -> Any:
    """Convert a raw DBus value to float or int where possible."""
    if isinstance(value, bool):
        return None
    scalar = _coerce_scalar_numeric(value)
    if scalar is not None:
        return scalar
    container_value = _coerce_numeric_container_value(value)
    return value if container_value is _UNCOERCED else container_value


def _sum_numeric_items(items: Iterable[object]) -> float | None:
    """Return the recursive sum of usable numeric items."""
    total = 0.0
    seen_numeric = False
    for item in items:
        numeric_item = sum_dbus_numeric(item)
        if numeric_item is None:
            continue
        total += float(numeric_item)
        seen_numeric = True
    return total if seen_numeric else None


def sum_dbus_numeric(value: Any) -> float | None:
    """Return a numeric sum for scalar or sequence DBus values, or None if unusable."""
    scalar = _coerce_scalar_numeric(value)
    if scalar is not None:
        return float(scalar)
    items = _iter_numeric_container_items(value)
    if items is None:
        return None
    return _sum_numeric_items(items)


def configured_grid_paths(*paths: str | None) -> list[str]:
    """Return only configured non-empty per-phase grid paths."""
    return [path for path in paths if path]


def discovery_cache_valid(cached_value: object, last_scan: float | int, scan_interval: float | int, now: float | int) -> bool:
    """Return whether a cached discovery result may still be reused."""
    return bool(cached_value) and (now - float(last_scan)) < float(scan_interval)


def prefixed_service_names(
    service_names: Iterable[object],
    prefix: str,
    max_services: int | None = None,
    sort_names: bool = False,
) -> list[str]:
    """Return services with the desired prefix, optionally sorted and limited."""
    names = [str(name) for name in service_names if str(name).startswith(prefix)]
    if sort_names:
        names.sort()
    if max_services is not None:
        names = names[: int(max_services)]
    return names


def parse_config_bool(value: object, default: bool = False) -> bool:
    """Parse a config-style truthy value."""
    if value is None:
        return bool(default)
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def first_matching_prefixed_service(
    service_names: Iterable[object],
    prefix: str,
    predicate: ServicePredicate,
) -> str | None:
    """Return the first prefixed service accepted by the supplied predicate."""
    for service_name in service_names:
        service_name_str = str(service_name)
        if not service_name_str.startswith(prefix):
            continue
        if predicate(service_name_str):
            return service_name_str
    return None


def grid_values_complete_enough(
    seen_value: object,
    missing_paths: Sequence[object],
    require_all_phases: bool,
) -> bool:
    """Return whether available grid readings are sufficient for control logic."""
    return bool(seen_value) and not (bool(require_all_phases) and bool(missing_paths))


def should_assume_zero_pv(
    explicit_service: str | None,
    service_names: Sequence[object],
    no_auto_ac_services_found: bool,
    auto_use_dc_pv: bool,
    dc_value: object,
) -> bool:
    """Return whether missing PV inputs should conservatively map to 0 W."""
    return (
        not explicit_service
        and (bool(no_auto_ac_services_found) or bool(service_names))
        and (not bool(auto_use_dc_pv) or dc_value is None)
    )


def compact_json(data: Any) -> str:
    """Serialize JSON with stable compact formatting."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def write_text_atomically(path: str | PathLike[str], payload: str, encoding: str = "utf-8") -> None:
    """Atomically replace a text file, cleaning up the temp file on failure."""
    path_str = os.fspath(path)
    tmp_path = f"{path_str}.tmp"
    target_dir = os.path.dirname(path_str)
    if target_dir:
        os.makedirs(target_dir, exist_ok=True)
    try:
        with open(tmp_path, "w", encoding=encoding) as handle:
            handle.write(payload)
        os.replace(tmp_path, path_str)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass
        raise

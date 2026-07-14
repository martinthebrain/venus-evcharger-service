# SPDX-License-Identifier: GPL-3.0-or-later
"""Persist auto-estimated battery capacity with minimal INI churn."""

from __future__ import annotations

import os
import re
from typing import Any

from venus_evcharger.core.shared import write_text_atomically
from venus_evcharger.energy import EnergySourceDefinition


_SECTION_RE = re.compile(r"^\s*\[[^]]+\]\s*(?:[#;].*)?$")
_KEY_VALUE_RE = re.compile(r"^(\s*)([^#;=\s][^=]*?)(\s*=\s*)(.*?)(\s*)$")


def configured_estimated_capacity_payload(source: EnergySourceDefinition) -> dict[str, object] | None:
    """Return a persisted estimated-capacity payload from config, if complete enough."""
    if source.estimated_capacity_wh is None or source.estimated_capacity_wh <= 0.0:
        return None
    payload: dict[str, object] = {
        "usable_capacity_wh": float(source.estimated_capacity_wh),
        "usable_capacity_source": "config_estimated",
    }
    _add_positive_payload_value(payload, "installed_capacity_ah", source.estimated_capacity_ah)
    _add_positive_payload_value(
        payload,
        "capacity_nominal_voltage_v",
        source.estimated_capacity_nominal_voltage_v,
    )
    _add_positive_int_payload_value(payload, "capacity_cell_count", source.estimated_capacity_cell_count)
    return payload


def _add_positive_payload_value(payload: dict[str, object], key: str, value: float | None) -> None:
    if value is not None and value > 0.0:
        payload[key] = float(value)


def _add_positive_int_payload_value(payload: dict[str, object], key: str, value: int | None) -> None:
    if value is not None and value > 0:
        payload[key] = int(value)


def persist_estimated_capacity_if_ah_changed(
    config_path: str,
    source: EnergySourceDefinition,
    payload: dict[str, object],
) -> bool:
    """Write the estimated capacity fields only when the installed Ah value changed."""
    new_ah = _positive_float(payload.get("installed_capacity_ah"))
    if new_ah is None:
        return False
    current_ah = source.estimated_capacity_ah
    if current_ah is not None and abs(float(current_ah) - new_ah) < 0.001:
        return False
    return _persist_estimated_capacity(config_path, source, payload)


def _persist_estimated_capacity(
    config_path: str,
    source: EnergySourceDefinition,
    payload: dict[str, object],
) -> bool:
    if not str(config_path).strip():
        return False
    try:
        current_text = _read_text(config_path)
    except OSError:
        return False
    new_text = _upsert_default_values(current_text, _estimated_capacity_config_values(source, payload))
    if new_text == current_text:
        return False
    write_text_atomically(config_path, new_text)
    return True


def _estimated_capacity_config_values(
    source: EnergySourceDefinition,
    payload: dict[str, object],
) -> dict[str, str]:
    prefix = "" if source.source_id == "primary_battery" else f"AutoEnergySource.{source.source_id}."
    key_prefix = "AutoBatteryCapacityEstimated" if not prefix else f"{prefix}CapacityEstimated"
    values = {
        f"{key_prefix}Wh": _number_text(payload.get("usable_capacity_wh")),
        f"{key_prefix}Ah": _number_text(payload.get("installed_capacity_ah")),
        f"{key_prefix}NominalVoltage": _number_text(payload.get("capacity_nominal_voltage_v")),
        f"{key_prefix}CellCount": _number_text(payload.get("capacity_cell_count")),
    }
    return {key: value for key, value in values.items() if value}


def _upsert_default_values(text: str, values: dict[str, str]) -> str:
    if not values:
        return text
    lines = text.splitlines()
    start, end = _default_section_bounds(lines)
    updated = list(lines)
    seen = _replace_existing_default_values(updated, start, end, values)
    _insert_missing_default_values(updated, end, values, seen)
    return _join_ini_lines(updated, text)


def _replace_existing_default_values(
    lines: list[str],
    start: int,
    end: int,
    values: dict[str, str],
) -> set[str]:
    seen: set[str] = set()
    for index in range(start, end):
        match = _KEY_VALUE_RE.match(lines[index])
        if match is None:
            continue
        key = match.group(2).strip()
        if key not in values:
            continue
        lines[index] = f"{match.group(1)}{key}{match.group(3)}{values[key]}{match.group(5)}"
        seen.add(key)
    return seen


def _insert_missing_default_values(
    lines: list[str],
    insertion: int,
    values: dict[str, str],
    seen: set[str],
) -> None:
    missing = _missing_default_value_lines(values, seen)
    if missing:
        lines[insertion:insertion] = missing


def _missing_default_value_lines(values: dict[str, str], seen: set[str]) -> list[str]:
    return [f"{key}={value}" for key, value in values.items() if key not in seen]


def _join_ini_lines(lines: list[str], original_text: str) -> str:
    return "\n".join(lines) + ("\n" if original_text.endswith("\n") or lines else "")


def _default_section_bounds(lines: list[str]) -> tuple[int, int]:
    start = 0
    if lines and lines[0].strip().lower() == "[default]":
        start = 1
    end = len(lines)
    for index in range(start, len(lines)):  # pragma: no branch
        if _SECTION_RE.match(lines[index]):
            end = index
            break
    return start, end


def _read_text(path: str) -> str:
    with open(os.fspath(path), encoding="utf-8") as handle:
        return handle.read()


def _positive_float(value: object) -> float | None:
    try:
        numeric = float(str(value))
    except (TypeError, ValueError):
        return None
    return numeric if numeric > 0.0 else None


def _number_text(value: Any) -> str:
    numeric = _positive_float(value)
    if numeric is None:
        return ""
    if abs(numeric - round(numeric)) < 0.001:
        return str(int(round(numeric)))
    rendered = f"{numeric:.3f}"
    while rendered.endswith("0"):
        rendered = rendered[:-1]
    return rendered

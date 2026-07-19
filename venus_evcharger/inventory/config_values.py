# SPDX-License-Identifier: GPL-3.0-or-later
"""Normalize primitive values from inventory configuration sections."""

from __future__ import annotations

from typing import NoReturn

from venus_evcharger.core.contracts_basic import optional_text

from .config_contracts import DeviceInventoryConfigError, InventorySectionLike
from .schema import BindingRole, CapabilityKind, PhaseLabel, SwitchingMode

_PHASE_LABELS = frozenset({"L1", "L2", "L3"})
_CAPABILITY_KINDS = frozenset({"switch", "meter", "charger"})
_SWITCHING_MODES = frozenset({"direct", "contactor"})
_BINDING_ROLES = frozenset({"actuation", "measurement", "charger"})


def _phase_labels(value: str) -> tuple[PhaseLabel, ...]:
    normalized: list[PhaseLabel] = []
    for raw in _phase_tokens(value):
        if not raw:
            continue
        phase = _phase_label(raw)
        if phase not in normalized:
            normalized.append(phase)
    if not normalized:
        raise DeviceInventoryConfigError("phase list may not be empty")
    return tuple(normalized)


def _phase_tokens(value: str) -> list[str]:
    """Return normalized uppercase phase tokens from one CSV payload."""
    return [part.strip().upper() for part in value.split(",")]


def _phase_label(value: str) -> PhaseLabel:
    normalized = value.strip().upper()
    if normalized == "L1":
        return "L1"
    if normalized == "L2":
        return "L2"
    if normalized == "L3":
        return "L3"
    _raise_literal_choice_error(value, _PHASE_LABELS, "phase list")


def _capability_kind(value: str) -> CapabilityKind:
    normalized = value.strip()
    if normalized == "switch":
        return "switch"
    if normalized == "meter":
        return "meter"
    if normalized == "charger":
        return "charger"
    _raise_literal_choice_error(value, _CAPABILITY_KINDS, "Capability.Kind")


def _optional_switching_mode(value: object) -> SwitchingMode | None:
    text = _optional_text(value)
    if text is None:
        return None
    normalized = text.strip()
    if normalized == "direct":
        return "direct"
    if normalized == "contactor":
        return "contactor"
    _raise_literal_choice_error(text, _SWITCHING_MODES, "Capability.SwitchingMode")


def _binding_role(value: str) -> BindingRole:
    normalized = value.strip()
    if normalized == "actuation":
        return "actuation"
    if normalized == "measurement":
        return "measurement"
    if normalized == "charger":
        return "charger"
    _raise_literal_choice_error(value, _BINDING_ROLES, "Binding.Role")


def _raise_literal_choice_error(
    value: str,
    allowed: frozenset[str],
    label: str,
) -> NoReturn:
    allowed_values = ", ".join(sorted(allowed))
    raise DeviceInventoryConfigError(
        f"{label} must be one of: {allowed_values} (got '{value}')"
    )


def _required_text(section: InventorySectionLike, key: str) -> str:
    value = _optional_text(section.get(key))
    if value is None:
        raise DeviceInventoryConfigError(f"missing required key {section.name}.{key}")
    return value


def _optional_text(value: object) -> str | None:
    return optional_text(value)


def _section_bool(section: InventorySectionLike, key: str, *, default: bool = False) -> bool:
    value = section.get(key)
    return default if value is None else _as_bool(value)


def _as_bool(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _suffix(value: str, prefix: str) -> str:
    if not value.startswith(prefix):
        raise DeviceInventoryConfigError(f"invalid section name '{value}'")
    remainder = value[len(prefix) :].strip()
    if not remainder:
        raise DeviceInventoryConfigError(f"invalid section name '{value}'")
    return remainder


def _split_section_id(value: str, *, expected_parts: int, label: str) -> tuple[str, ...]:
    parts = tuple(part.strip() for part in value.split(":"))
    if len(parts) != expected_parts or any(not part for part in parts):
        raise DeviceInventoryConfigError(f"invalid section name '{label}'")
    return parts

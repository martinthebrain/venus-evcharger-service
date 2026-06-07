# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase-selection helpers for Shelly-backed backends."""

from __future__ import annotations

import configparser

from .config_file import section_is_effectively_empty
from .models import PhaseSelection, normalize_phase_selection_tuple, normalize_switching_mode


def parse_phase_selection_list(value: object, default: tuple[PhaseSelection, ...] = ("P1",)) -> tuple[PhaseSelection, ...]:
    """Return one normalized supported-phase tuple."""
    return normalize_phase_selection_tuple(value, default)


_PHASE_MAP_KEYS: dict[str, PhaseSelection] = {
    "p1": "P1",
    "l1": "P1",
    "l2": "P1",
    "l3": "P1",
    "1p": "P1",
    "p1_p2": "P1_P2",
    "p1+p2": "P1_P2",
    "2p": "P1_P2",
    "p1_p2_p3": "P1_P2_P3",
    "p1+p2+p3": "P1_P2_P3",
    "3p": "P1_P2_P3",
}


def _parse_switch_channel_ids(value: object, default: tuple[int, ...]) -> tuple[int, ...]:
    """Return one normalized tuple of Shelly switch channel IDs."""
    tokens = _channel_id_tokens(value)
    if not tokens:
        return default
    normalized = _unique_channel_ids(tokens)
    return normalized or default


def _channel_id_tokens(value: object) -> tuple[str, ...]:
    """Return trimmed switch-channel tokens from one raw config value."""
    text = str(value).strip() if value is not None else ""
    if not text:
        return ()
    return tuple(part.strip() for part in text.split(","))


def _unique_channel_ids(tokens: tuple[str, ...]) -> tuple[int, ...]:
    """Return de-duplicated normalized channel IDs preserving config order."""
    normalized: list[int] = []
    for token in tokens:
        channel_id = _switch_channel_id(token)
        if channel_id is None or channel_id in normalized:
            continue
        normalized.append(channel_id)
    return tuple(normalized)


def _switch_channel_id(value: object) -> int | None:
    """Return one normalized Shelly switch channel ID token."""
    token = str(value).strip()
    if not token:
        return None
    try:
        channel_id = int(token)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid Shelly switch channel id '{token}'") from exc
    if channel_id < 0:
        raise ValueError(f"Invalid Shelly switch channel id '{token}'")
    return channel_id


def _default_phase_switch_targets(
    device_id: int,
    supported_phase_selections: tuple[PhaseSelection, ...],
) -> dict[PhaseSelection, tuple[int, ...]]:
    """Return backward-compatible one-channel targets for every supported selection."""
    default_target = (int(device_id),)
    return {
        selection: default_target
        for selection in supported_phase_selections
    }


def _phase_switch_targets(
    phase_map: configparser.SectionProxy,
    device_id: int,
    supported_phase_selections: tuple[PhaseSelection, ...],
) -> dict[PhaseSelection, tuple[int, ...]]:
    """Return configured phase-selection to relay-channel mappings."""
    targets = _default_phase_switch_targets(device_id, supported_phase_selections)
    if _empty_phase_map(phase_map):
        return targets
    for raw_key, raw_value in phase_map.items():
        selection = _phase_map_selection(raw_key)
        if selection is None:
            raise ValueError(f"Unsupported PhaseMap key '{raw_key}'")
        if selection not in supported_phase_selections:
            continue
        targets[selection] = _parse_switch_channel_ids(raw_value, targets[selection])
    return targets


def _empty_phase_map(phase_map: configparser.SectionProxy) -> bool:
    """Return whether a PhaseMap section carries no effective custom mappings."""
    return section_is_effectively_empty(phase_map)


def _phase_map_selection(raw_key: object) -> PhaseSelection | None:
    """Return the normalized phase selection associated with one PhaseMap key."""
    return _PHASE_MAP_KEYS.get(str(raw_key).strip().lower())


def phase_powers_for_selection(
    power_w: float,
    selection: PhaseSelection,
    single_phase_line: object = "L1",
) -> tuple[float, float, float]:
    """Split total power across the selected active phases for display."""
    total = float(power_w)
    if selection == "P1_P2_P3":
        return _distributed_phase_vector(total, 3.0)
    if selection == "P1_P2":
        distributed = _distributed_phase_vector(total, 2.0)
        return distributed[0], distributed[1], 0.0
    return _single_phase_vector(total, single_phase_line)


def phase_currents_for_selection(
    current_a: float | None,
    selection: PhaseSelection,
    single_phase_line: object = "L1",
) -> tuple[float, float, float] | None:
    """Split total current across the selected active phases for display."""
    if current_a is None:
        return None
    total = float(current_a)
    if selection == "P1_P2_P3":
        return _distributed_phase_vector(total, 3.0)
    if selection == "P1_P2":
        distributed = _distributed_phase_vector(total, 2.0)
        return distributed[0], distributed[1], 0.0
    return _single_phase_vector(total, single_phase_line)


def _distributed_phase_vector(total: float, divisor: float) -> tuple[float, float, float]:
    """Return evenly distributed per-phase values for two- or three-phase totals."""
    per_phase = float(total) / float(divisor)
    return per_phase, per_phase, per_phase


def _single_phase_vector(total: float, single_phase_line: object) -> tuple[float, float, float]:
    """Return one single-phase vector mapped to the configured measured line."""
    line = str(single_phase_line).strip().upper() if single_phase_line is not None else "L1"
    if line == "L2":
        return 0.0, total, 0.0
    if line == "L3":
        return 0.0, 0.0, total
    return total, 0.0, 0.0


__all__ = [
    "_parse_switch_channel_ids",
    "_phase_switch_targets",
    "_switch_channel_id",
    "normalize_switching_mode",
    "parse_phase_selection_list",
    "phase_currents_for_selection",
    "phase_powers_for_selection",
]

# SPDX-License-Identifier: GPL-3.0-or-later
"""Basic normalization contracts for outward-facing wallbox state."""

from __future__ import annotations

import math
from typing import Any

from venus_evcharger.core.common_types import AUTO_STATE_CODES

LEARNED_CHARGE_POWER_STATES = frozenset({"unknown", "learning", "stable", "stale"})
LEARNED_CHARGE_POWER_PHASES = frozenset({"L1", "L2", "L3", "3P"})


def finite_float_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(normalized):
        return None
    return normalized


def mutable_dict_attr(target: Any, attr_name: str) -> dict[str, Any]:
    """Return a mutable dict attribute, initializing it when absent or invalid."""
    current = getattr(target, attr_name, None)
    if isinstance(current, dict):
        return current
    current = {}
    setattr(target, attr_name, current)
    return current


def exception_detail(error: BaseException) -> str:
    """Return one stable human-readable exception detail."""
    detail = str(error).strip()
    return detail or error.__class__.__name__


def optional_text(value: object) -> str | None:
    """Return trimmed text or ``None`` when empty."""
    text = str(value).strip() if value is not None else ""
    return text or None


def non_negative_float_or_none(value: Any) -> float | None:
    normalized = finite_float_or_none(value)
    if normalized is None or normalized < 0.0:
        return None
    return normalized


def non_negative_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(default)
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return int(default)
    return max(0, normalized)


def normalize_binary_flag(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return 1 if bool(default) else 0
    return 0 if normalized <= 0 else 1


def normalize_optional_binary_state(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(normalize_binary_flag(value))


def normalize_learning_state(value: Any) -> str:
    state = str(value).strip().lower() if value is not None else "unknown"
    return state if state in LEARNED_CHARGE_POWER_STATES else "unknown"


def normalize_learning_phase(value: Any) -> str | None:
    phase = str(value).strip().upper() if value is not None else ""
    return phase if phase in LEARNED_CHARGE_POWER_PHASES else None


def paired_optional_values(left: Any, right: Any) -> bool:
    return (left is None) == (right is None)


def valid_battery_soc(value: Any) -> bool:
    normalized = finite_float_or_none(value)
    return normalized is None or 0.0 <= normalized <= 100.0


def timestamp_not_future(timestamp: Any, now: float, tolerance_seconds: float = 1.0) -> bool:
    normalized = finite_float_or_none(timestamp)
    return normalized is not None and normalized <= (float(now) + float(tolerance_seconds))


def timestamp_age_within(
    timestamp: Any,
    now: float,
    max_age_seconds: float,
    *,
    future_tolerance_seconds: float = 1.0,
) -> bool:
    normalized = finite_float_or_none(timestamp)
    if normalized is None:
        return False
    age_seconds = float(now) - normalized
    return -float(future_tolerance_seconds) <= age_seconds <= float(max_age_seconds)


def thresholds_ordered(start_watts: Any, stop_watts: Any) -> bool:
    start_value = non_negative_float_or_none(start_watts)
    stop_value = non_negative_float_or_none(stop_watts)
    return start_value is not None and stop_value is not None and stop_value <= start_value


def normalize_auto_state(value: Any) -> str:
    state = str(value).strip().lower() if value is not None else "idle"
    return state if state in AUTO_STATE_CODES else "idle"


def normalized_auto_state_pair(state: Any, _code: Any) -> tuple[str, int]:
    normalized_state = normalize_auto_state(state)
    return normalized_state, AUTO_STATE_CODES[normalized_state]


def normalized_status_source(value: Any) -> str:
    source = str(value).strip() if value is not None else ""
    return source or "unknown"


def normalized_fault_state(reason: Any) -> tuple[str, int]:
    normalized_reason = "" if reason is None else str(reason).strip()
    return normalized_reason, int(bool(normalized_reason))

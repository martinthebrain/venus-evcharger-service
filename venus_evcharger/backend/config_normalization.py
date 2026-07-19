# SPDX-License-Identifier: GPL-3.0-or-later
"""Backend config normalization and legacy role compatibility helpers."""

from __future__ import annotations

from pathlib import Path

from .config_file import normalized_optional_lower_text
from .models import BackendMode

DEFAULT_COMBINED_METER_TYPE = "shelly_meter"
DEFAULT_COMBINED_SWITCH_TYPE = "shelly_contactor_switch"


def _normalized_text_or_default(value: object, default: str = "") -> str:
    """Return trimmed text or the provided default."""
    normalized = str(value).strip() if value is not None else ""
    return normalized or default


def normalize_backend_mode(value: object) -> BackendMode:
    """Return one supported backend mode string."""
    if value is None:
        return "combined"
    mode = str(value).strip().lower()
    return "split" if mode == "split" else "combined"


def normalize_backend_type(value: object, fallback: str) -> str:
    """Return one normalized backend type name."""
    if value is None:
        return fallback
    normalized = str(value).strip().lower()
    return normalized or fallback


def normalize_optional_backend_type(value: object) -> str | None:
    """Return one optional backend type name."""
    normalized: str | None = normalized_optional_lower_text(value)
    return normalized


def normalize_config_path(value: object) -> Path | None:
    """Return one normalized optional config path."""
    normalized = str(value).strip() if value is not None else ""
    if not normalized:
        return None
    return Path(normalized)


def _configured_text(value: object) -> str:
    """Return one normalized non-empty text payload or an empty string."""
    if value is None:
        return ""
    return str(value).strip()


def _split_none_role(mode: BackendMode, normalized: str) -> bool:
    """Return whether one split runtime role explicitly disables a backend."""
    return mode == "split" and normalized == "none"


def _runtime_role_alias(combined_fallback: str, normalized: str) -> str | None:
    """Return one normalized runtime backend role after alias expansion."""
    if normalized == "shelly_combined":
        return combined_fallback
    return normalized or None


def _legacy_none_backend_errors(mode: BackendMode, charger_type: str | None, field_name: str) -> list[str]:
    """Return legacy validation errors for one backend field set to ``none``."""
    errors: list[str] = []
    if mode != "split":
        errors.append(f"{field_name}=none is only supported in split backend mode")
    if charger_type is None:
        errors.append(f"{field_name}=none requires a configured charger backend")
    return errors


def _validate_legacy_backend_values(
    mode: BackendMode,
    meter_type: str,
    switch_type: str,
    charger_type: str | None,
) -> None:
    """Raise when legacy backend settings express one unsupported topology."""
    errors: list[str] = []
    if meter_type == "none":
        errors.extend(_legacy_none_backend_errors(mode, charger_type, "MeterType"))
    if switch_type == "none":
        errors.extend(_legacy_none_backend_errors(mode, charger_type, "SwitchType"))
    for message in errors:
        raise ValueError(message)

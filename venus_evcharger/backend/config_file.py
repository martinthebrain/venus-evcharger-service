# SPDX-License-Identifier: GPL-3.0-or-later
"""Small shared config-file loaders for backend adapters."""

from __future__ import annotations

import configparser
from collections.abc import Mapping
from pathlib import Path

from venus_evcharger.core.contracts import finite_float_or_none

from .models import PhaseSelection, normalize_phase_selection, normalize_phase_selection_tuple


def normalized_optional_path(value: str | None) -> Path | None:
    """Return one normalized optional backend config path."""
    if value is None:
        return None
    text = value.strip()
    return Path(text) if text else None


def normalized_optional_lower_text(value: object) -> str | None:
    """Return trimmed lowercase text or ``None``."""
    normalized = str(value).strip().lower() if value is not None else ""
    return normalized or None


def load_required_backend_config(config_path: str, label: str) -> configparser.ConfigParser:
    """Load one required backend config file."""
    normalized_path = str(config_path).strip()
    parser = configparser.ConfigParser()
    read_files = parser.read(normalized_path)
    if not read_files:
        raise FileNotFoundError(f"{label} config not found: {config_path}")
    return parser


def config_section(parser: configparser.ConfigParser, name: str) -> configparser.SectionProxy:
    """Return one named section or DEFAULT when absent."""
    return parser[name] if parser.has_section(name) else parser["DEFAULT"]


def section_is_effectively_empty(section: configparser.SectionProxy) -> bool:
    """Return whether one optional config section has no effective values."""
    return not tuple(section.items())


def backend_request_timeout_seconds(adapter: Mapping[str, object], service: object, default: float = 2.0) -> float:
    """Return one normalized backend HTTP request timeout in seconds."""
    raw_default = getattr(service, "shelly_request_timeout_seconds", None)
    timeout_seconds = finite_float_or_none(adapter.get("RequestTimeoutSeconds", raw_default))
    if timeout_seconds is None or timeout_seconds <= 0.0:
        return float(default)
    return float(timeout_seconds)


def fixed_supported_phase_selections(
    parser: configparser.ConfigParser,
    default: tuple[PhaseSelection, ...],
    backend_label: str,
) -> tuple[PhaseSelection, ...]:
    """Return exactly one fixed supported phase selection from backend capabilities."""
    raw_value: object = (
        parser["Capabilities"].get("SupportedPhaseSelections")
        if parser.has_section("Capabilities")
        else default
    )
    normalized = normalize_phase_selection_tuple(raw_value, default)
    if len(normalized) != 1:
        raise ValueError(f"{backend_label} charger backend requires exactly one fixed [Capabilities] SupportedPhaseSelections value")
    return normalized


def validate_fixed_phase_selection(
    selection: object,
    fixed_phase_selection: PhaseSelection,
    backend_label: str,
) -> None:
    """Reject native phase writes that differ from a backend's fixed phase layout."""
    normalized = normalize_phase_selection(selection, fixed_phase_selection)
    if normalized != fixed_phase_selection:
        raise ValueError(
            f"{backend_label} charger backend does not support native phase switching "
            f"(configured fixed phase selection: {fixed_phase_selection})"
        )


__all__ = [
    "backend_request_timeout_seconds",
    "config_section",
    "fixed_supported_phase_selections",
    "load_required_backend_config",
    "normalized_optional_lower_text",
    "normalized_optional_path",
    "section_is_effectively_empty",
    "validate_fixed_phase_selection",
]

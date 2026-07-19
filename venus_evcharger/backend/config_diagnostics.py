# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed diagnostic projection of canonical backend configuration."""

from __future__ import annotations

import configparser
from pathlib import Path
from typing import TypedDict

from .config_loader import load_runtime_backend_summary
from .config_normalization import DEFAULT_COMBINED_METER_TYPE, DEFAULT_COMBINED_SWITCH_TYPE
from .models import BackendMode, BackendRuntimeSummary


class BackendSelectionView(TypedDict):
    """Stable JSON-ready backend selection exposed by probe diagnostics."""

    mode: BackendMode
    meter_type: str
    switch_type: str
    charger_type: str | None
    meter_config_path: Path | None
    switch_config_path: Path | None
    charger_config_path: Path | None


def backend_selection_view(runtime: BackendRuntimeSummary) -> BackendSelectionView:
    """Project one canonical runtime summary into the diagnostic schema."""
    if not isinstance(runtime, BackendRuntimeSummary):
        raise TypeError("runtime must be BackendRuntimeSummary")
    return {
        "mode": runtime.backend_mode,
        "meter_type": _diagnostic_role(
            runtime.backend_mode,
            DEFAULT_COMBINED_METER_TYPE,
            runtime.meter_type,
        ),
        "switch_type": _diagnostic_role(
            runtime.backend_mode,
            DEFAULT_COMBINED_SWITCH_TYPE,
            runtime.switch_type,
        ),
        "charger_type": runtime.charger_type,
        "meter_config_path": runtime.meter_config_path,
        "switch_config_path": runtime.switch_config_path,
        "charger_config_path": runtime.charger_config_path,
    }


def backend_selection_view_from_config(config: configparser.ConfigParser) -> BackendSelectionView:
    """Normalize a config and project its backend selection for diagnostics."""
    return backend_selection_view(load_runtime_backend_summary(config))


def _diagnostic_role(mode: BackendMode, combined_fallback: str, value: str | None) -> str:
    """Return the outward role label used by the diagnostic schema."""
    if value is None:
        return "none" if mode == "split" else combined_fallback
    normalized = value.strip().lower()
    return normalized or ("none" if mode == "split" else combined_fallback)

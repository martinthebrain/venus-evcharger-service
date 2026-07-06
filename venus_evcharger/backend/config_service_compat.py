# SPDX-License-Identifier: GPL-3.0-or-later
"""Legacy backend-view reconstruction from normalized runtime summaries."""

from __future__ import annotations

import configparser
from typing import Any

from .config_loader import load_runtime_backend_summary
from .config_normalization import (
    _legacy_meter_view_role_from_runtime,
    _legacy_switch_view_role_from_runtime,
    normalize_backend_mode,
    normalize_config_path,
    normalize_optional_backend_type,
)
from .models import BackendRuntimeSummary


def compat_legacy_backend_view_from_runtime(runtime: BackendRuntimeSummary | Any) -> dict[str, object] | None:
    """Return one legacy-shaped backend view reconstructed from runtime data."""
    if not _has_runtime_backend_mode(runtime):
        return None
    mode = normalize_backend_mode(_runtime_attr(runtime, "backend_mode"))
    return {
        "mode": mode,
        "meter_type": _legacy_meter_view_role_from_runtime(mode, _runtime_attr(runtime, "meter_type")),
        "switch_type": _legacy_switch_view_role_from_runtime(mode, _runtime_attr(runtime, "switch_type")),
        "charger_type": normalize_optional_backend_type(_runtime_attr(runtime, "charger_type")),
        "meter_config_path": normalize_config_path(_runtime_attr(runtime, "meter_config_path")),
        "switch_config_path": normalize_config_path(_runtime_attr(runtime, "switch_config_path")),
        "charger_config_path": normalize_config_path(_runtime_attr(runtime, "charger_config_path")),
    }


def compat_legacy_backend_view_from_config(config: configparser.ConfigParser) -> dict[str, object]:
    """Return one legacy-shaped backend view reconstructed from config."""
    return compat_legacy_backend_view_from_runtime(load_runtime_backend_summary(config)) or {}


def _has_runtime_backend_mode(runtime: object) -> bool:
    """Return whether one object has the minimal runtime-summary shape."""
    return runtime is not None and hasattr(runtime, "backend_mode")


def _runtime_attr(runtime: object, attribute_name: str) -> object:
    """Return one optional attribute from a runtime-summary-like object."""
    return getattr(runtime, attribute_name, None)

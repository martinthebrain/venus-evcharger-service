# SPDX-License-Identifier: GPL-3.0-or-later
"""Service-facing backend summary facade."""

from __future__ import annotations

from .config_diagnostics import (
    backend_selection_view,
    backend_selection_view_from_config,
)
from .config_service_labels import backend_mode_for_service, backend_type_for_service
from .config_service_runtime import runtime_summary_from_service


__all__ = [
    "backend_mode_for_service",
    "backend_type_for_service",
    "backend_selection_view",
    "backend_selection_view_from_config",
    "runtime_summary_from_service",
]

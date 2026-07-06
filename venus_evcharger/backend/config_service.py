# SPDX-License-Identifier: GPL-3.0-or-later
"""Service-facing backend summary facade."""

from __future__ import annotations

from .config_service_compat import (
    compat_legacy_backend_view_from_config,
    compat_legacy_backend_view_from_runtime,
)
from .config_service_labels import backend_mode_for_service, backend_type_for_service
from .config_service_runtime import runtime_summary_from_service


__all__ = [
    "backend_mode_for_service",
    "backend_type_for_service",
    "compat_legacy_backend_view_from_config",
    "compat_legacy_backend_view_from_runtime",
    "runtime_summary_from_service",
]

# SPDX-License-Identifier: GPL-3.0-or-later
"""Public backend-configuration facade.

The implementation lives in role-focused modules. This facade keeps the stable
public import path for callers that need normalized backend summaries or
service-facing backend labels.
"""

from __future__ import annotations

from .config_loader import load_runtime_backend_summary
from .config_normalization import (
    DEFAULT_COMBINED_METER_TYPE,
    DEFAULT_COMBINED_SWITCH_TYPE,
    normalize_backend_mode,
    normalize_backend_type,
    normalize_config_path,
    normalize_optional_backend_type,
)
from .config_service import (
    backend_mode_for_service,
    backend_type_for_service,
    compat_legacy_backend_view_from_config,
    compat_legacy_backend_view_from_runtime,
    runtime_summary_from_service,
)
from .config_summary import (
    runtime_summary_is_configured,
    runtime_summary_uses_legacy_primary_rpc,
)


__all__ = [
    "DEFAULT_COMBINED_METER_TYPE",
    "DEFAULT_COMBINED_SWITCH_TYPE",
    "backend_mode_for_service",
    "backend_type_for_service",
    "compat_legacy_backend_view_from_config",
    "compat_legacy_backend_view_from_runtime",
    "load_runtime_backend_summary",
    "normalize_backend_mode",
    "normalize_backend_type",
    "normalize_config_path",
    "normalize_optional_backend_type",
    "runtime_summary_from_service",
    "runtime_summary_is_configured",
    "runtime_summary_uses_legacy_primary_rpc",
]

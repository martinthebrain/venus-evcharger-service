# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime-state and runtime-override helpers for the state controller."""

from __future__ import annotations

from venus_evcharger.core.shared import write_text_atomically
from venus_evcharger.controllers.state_runtime_overrides import _StateRuntimeOverrides
from venus_evcharger.controllers.state_specs import (
    RUNTIME_OVERRIDE_BY_CONFIG_KEY,
    RUNTIME_OVERRIDE_SPECS,
    RUNTIME_OVERRIDE_SECTION,
    RuntimeOverrideSpec,
    _CasePreservingConfigParser,
)


class _StateRuntime(_StateRuntimeOverrides):
    """Compose runtime-state persistence and runtime-override handling."""


__all__ = [
    "_StateRuntime",
    "RuntimeOverrideSpec",
    "RUNTIME_OVERRIDE_SPECS",
    "RUNTIME_OVERRIDE_BY_CONFIG_KEY",
    "RUNTIME_OVERRIDE_SECTION",
    "_CasePreservingConfigParser",
    "write_text_atomically",
]

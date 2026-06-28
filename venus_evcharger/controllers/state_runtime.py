# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime-state and runtime-override helpers for the state controller."""

from __future__ import annotations

import logging

from venus_evcharger.core.shared import write_text_atomically
from venus_evcharger.controllers.state_runtime_normalize import _StateRuntimeNormalizeMixin
from venus_evcharger.controllers.state_runtime_overrides import _StateRuntimeOverridesMixin
from venus_evcharger.controllers.state_runtime_snapshot import _StateRuntimeSnapshotMixin
from venus_evcharger.controllers.state_specs import (
    RUNTIME_OVERRIDE_BY_CONFIG_KEY,
    RUNTIME_OVERRIDE_SPECS,
    RUNTIME_OVERRIDE_SECTION,
    RuntimeOverrideSpec,
    _CasePreservingConfigParser,
)


class _StateRuntimeMixin(
    _StateRuntimeOverridesMixin,
    _StateRuntimeSnapshotMixin,
    _StateRuntimeNormalizeMixin,
):
    @staticmethod
    def _write_text_atomically(path: str, payload: str) -> None:
        write_text_atomically(path, payload)


__all__ = [
    "_StateRuntimeMixin",
    "RuntimeOverrideSpec",
    "RUNTIME_OVERRIDE_SPECS",
    "RUNTIME_OVERRIDE_BY_CONFIG_KEY",
    "RUNTIME_OVERRIDE_SECTION",
    "_CasePreservingConfigParser",
    "write_text_atomically",
    "logging",
]

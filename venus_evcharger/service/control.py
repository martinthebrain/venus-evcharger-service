# SPDX-License-Identifier: GPL-3.0-or-later
"""Control API role for the Venus EV charger service."""

from __future__ import annotations

from venus_evcharger.control import LocalControlApiHttpServer

from .control_runtime import _ControlApiRuntime

__all__ = ["ControlApi", "LocalControlApiHttpServer"]


class ControlApi(_ControlApiRuntime):
    """Expose canonical command building and optional local HTTP control transport."""

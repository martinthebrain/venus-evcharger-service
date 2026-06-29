# SPDX-License-Identifier: GPL-3.0-or-later
"""Type contracts shared by diagnostic publishing roles."""

from __future__ import annotations

from typing import Any, Callable

from venus_evcharger.publish.dbus_learned import _DbusPublishLearned


DiagnosticValue = str | int | float


class _DbusDiagnosticsContracts(_DbusPublishLearned):
    """Declare sibling-role helpers used by diagnostic publishers."""

    service: Any
    _age_seconds: Callable[[Any, float], float]

# SPDX-License-Identifier: GPL-3.0-or-later
"""Internal Auto-mode decision workflow helpers for the Venus EV charger service.

The public import path stays stable here while the actual decision helpers live
in smaller implementation modules.
"""

from __future__ import annotations

import logging

from .logic_gates_runtime import _AutoDecisionRuntimeGates


class _AutoDecisionGates(_AutoDecisionRuntimeGates):
    """Composed Auto decision helpers kept under the legacy module path."""


__all__ = ["_AutoDecisionGates", "logging"]

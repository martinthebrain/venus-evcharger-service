# SPDX-License-Identifier: GPL-3.0-or-later
"""Internal Auto-mode decision workflow helpers for the Venus EV charger service.

The Auto controller keeps the policy readable by splitting the decision tree
into many small helper methods. The high-level behavior is:
- gather fresh PV, grid, and battery inputs
- smooth the relevant values
- check hard safety gates first
- evaluate start/stop conditions
- return the desired relay state plus a diagnostic health reason
"""

from __future__ import annotations

import time

from venus_evcharger.auto.logic_decisions import _AutoDecisionDecision
from venus_evcharger.auto.logic_types import NO_RELAY_DECISION, RelayDecisionState

__all__ = ("AutoDecisionWorkflow", "NO_RELAY_DECISION", "RelayDecisionState")


class AutoDecisionWorkflow(_AutoDecisionDecision):
    """Provide the detailed Auto-mode decision flow used by AutoDecisionController."""

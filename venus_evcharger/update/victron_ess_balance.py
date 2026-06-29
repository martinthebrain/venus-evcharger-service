# SPDX-License-Identifier: GPL-3.0-or-later
"""Experimental Victron ESS balance-bias helpers for the update cycle."""

from __future__ import annotations

from .victron_ess_balance_adaptive import _UpdateCycleVictronEssBalanceAdaptive


class _UpdateCycleVictronEssBalance(_UpdateCycleVictronEssBalanceAdaptive):
    """Composed Victron ESS balance-bias helpers for the update cycle."""

# SPDX-License-Identifier: GPL-3.0-or-later
"""Victron ESS balance-bias learning helpers."""

from __future__ import annotations

from .victron_ess_balance_learning_profiles import _UpdateCycleVictronEssBalanceLearningProfiles


class _UpdateCycleVictronEssBalanceLearning(_UpdateCycleVictronEssBalanceLearningProfiles):
    """Composed Victron ESS balance-bias learning helpers."""

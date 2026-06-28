# SPDX-License-Identifier: GPL-3.0-or-later
"""Experimental Victron ESS balance-bias helpers for the update cycle."""

from __future__ import annotations

from .victron_ess_balance_adaptive import _UpdateCycleVictronEssBalanceAdaptiveMixin
from .victron_ess_balance_apply import _UpdateCycleVictronEssBalanceApplyMixin
from .victron_ess_balance_learning import _UpdateCycleVictronEssBalanceLearningMixin
from .victron_ess_balance_recommendation import _UpdateCycleVictronEssBalanceRecommendationMixin
from .victron_ess_balance_safety import _UpdateCycleVictronEssBalanceSafetyMixin


class _UpdateCycleVictronEssBalanceMixin(
    _UpdateCycleVictronEssBalanceApplyMixin,
    _UpdateCycleVictronEssBalanceRecommendationMixin,
    _UpdateCycleVictronEssBalanceLearningMixin,
    _UpdateCycleVictronEssBalanceAdaptiveMixin,
    _UpdateCycleVictronEssBalanceSafetyMixin,
):
    """Composed Victron ESS balance-bias helpers for the update cycle."""

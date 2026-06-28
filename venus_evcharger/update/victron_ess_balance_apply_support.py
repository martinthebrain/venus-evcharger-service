# SPDX-License-Identifier: GPL-3.0-or-later
"""Composed support mixin for Victron ESS balance-bias application."""

from __future__ import annotations

from .victron_ess_balance_apply_pid import _UpdateCycleVictronEssBalanceApplyPidMixin
from .victron_ess_balance_apply_sources import _UpdateCycleVictronEssBalanceApplySourcesMixin
from .victron_ess_balance_apply_write import _UpdateCycleVictronEssBalanceApplyWriteMixin


class _UpdateCycleVictronEssBalanceApplySupportMixin(
    _UpdateCycleVictronEssBalanceApplySourcesMixin,
    _UpdateCycleVictronEssBalanceApplyPidMixin,
    _UpdateCycleVictronEssBalanceApplyWriteMixin,
):
    """Bundle source selection, PID computation, and gateway writes for the apply mixin."""

# SPDX-License-Identifier: GPL-3.0-or-later
"""Type-only contracts for Victron ESS balance learning-profile helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


class _VictronEssBalanceLearningProfilesContractsMixin:
    """Declare sibling-mixin helpers used by learning-profile logic."""

    if TYPE_CHECKING:  # pragma: no cover

        @staticmethod
        def _optional_float(value: Any) -> float | None: ...

        @staticmethod
        def _victron_ess_balance_ev_active(svc: Any) -> bool: ...

        @staticmethod
        def _ewma_learned_value(current: float | None, sample: float, samples: int) -> float: ...

        @staticmethod
        def _victron_ess_balance_stability_score_values(
            settled_count: int,
            overshoot_count: int,
            estimated_gain: float | None,
            response_delay_seconds: float | None,
        ) -> float: ...

        @staticmethod
        def _victron_ess_balance_variance_score(
            delay_mean: float | None,
            delay_mad: float | None,
            gain_mean: float | None,
            gain_mad: float | None,
        ) -> float: ...

        @classmethod
        def _victron_ess_balance_regime_consistency_score(cls, profile: dict[str, Any]) -> float: ...

        @classmethod
        def _victron_ess_balance_reproducibility_score(cls, profile: dict[str, Any]) -> float: ...

        def _victron_ess_balance_activation_mode(self, svc: Any) -> str: ...

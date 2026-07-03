# SPDX-License-Identifier: GPL-3.0-or-later
"""Learned charge-power state and adaptive threshold helpers."""

from __future__ import annotations

import logging
import time
from typing import Any

from venus_evcharger.core.contracts import thresholds_ordered
from venus_evcharger.core.controller_contracts import ControllerAssemblyContract

from .policy import AutoPolicy, validate_auto_policy

LEARNED_CHARGE_POWER_STATES = {"unknown", "learning", "stable", "stale"}


class _AutoDecisionLearning(ControllerAssemblyContract):
    """Expose learned charge-power state and Auto threshold scaling."""

    def _learning_policy_now(self) -> float:
        """Return the current timestamp for learned-power freshness checks."""
        time_now = getattr(self.service, "_time_now", None)
        if callable(time_now):
            current_time = time_now()
            if isinstance(current_time, (int, float)):
                return float(current_time)
        return time.time()

    @staticmethod
    def _normalize_learned_charge_power_state(value: Any) -> str:
        """Return one supported learned-power state string."""
        if value is None:
            return "unknown"
        state = str(value).strip().lower()
        return state if state in LEARNED_CHARGE_POWER_STATES else "unknown"

    def _current_learned_charge_power_state(self, now: float | None = None) -> str:
        """Return the effective learned-power state, including age-based staleness."""
        state = self._stored_learned_charge_power_state()
        if not self._has_positive_learned_charge_power():
            return "unknown"
        stale_state = self._stale_learned_charge_power_state(state, now)
        return state if stale_state is None else stale_state

    def _active_learned_charge_power(self, now: float | None = None) -> float | None:
        """Return the learned charging power when it is present and still fresh."""
        learned_value = self._positive_learned_charge_power()
        if self._learned_charge_power_inactive_for_auto(learned_value, now):
            return None
        return learned_value

    def _stored_learned_charge_power_state(self) -> str:
        """Return the normalized learned-power state stored on the service."""
        if not hasattr(self.service, "learned_charge_power_state"):
            return "unknown"
        return self._normalize_learned_charge_power_state(self.service.learned_charge_power_state)

    def _positive_learned_charge_power(self) -> float | None:
        """Return the learned charging power when it is positive."""
        learned_power = getattr(self.service, "learned_charge_power_watts", None)
        if learned_power is None:
            return None
        learned_value = float(learned_power)
        if learned_value <= 0:
            return None
        return learned_value

    def _has_positive_learned_charge_power(self) -> bool:
        """Return True when a usable learned charging power is present."""
        return self._positive_learned_charge_power() is not None

    def _learned_charge_power_can_expire(self) -> bool:
        """Return True when learned charging power has an age limit."""
        return float(self._auto_policy().learn_charge_power.max_age_seconds) > 0

    def _learned_charge_power_missing_update_time(self) -> bool:
        """Return True when learned charging power has no update timestamp."""
        return (
            not hasattr(self.service, "learned_charge_power_updated_at")
            or self.service.learned_charge_power_updated_at is None
        )

    @staticmethod
    def _unknown_or_stale_learning_state(state: str) -> str:
        """Return the fallback state used when learning data has no timestamp."""
        return "unknown" if state == "unknown" else "stale"

    def _learned_charge_power_age_seconds(self, now: float | None = None) -> float | None:
        """Return the age of the learned charging power, if it is timestamped."""
        updated_at = getattr(self.service, "learned_charge_power_updated_at", None)
        if updated_at is None:
            return None
        current_time = self._learning_policy_now() if now is None else float(now)
        return current_time - float(updated_at)

    def _learned_charge_power_expired(self, now: float | None = None) -> bool:
        """Return True when the learned charging power is older than its max age."""
        age_seconds = self._learned_charge_power_age_seconds(now)
        if age_seconds is None:
            return True
        max_age_seconds = float(self._auto_policy().learn_charge_power.max_age_seconds)
        return age_seconds > max_age_seconds

    def _stale_learned_charge_power_state(self, state: str, now: float | None = None) -> str | None:
        """Return an overridden state when learned power is missing freshness."""
        if not self._learned_charge_power_can_expire():
            return None
        if self._learned_charge_power_missing_update_time():
            return self._unknown_or_stale_learning_state(state)
        if self._learned_charge_power_expired(now):
            return "stale"
        return None

    def _learned_charge_power_policy_enabled(self) -> bool:
        """Return True when adaptive learned-power scaling is enabled."""
        return bool(self._auto_policy().learn_charge_power.enabled)

    def _learned_charge_power_age_invalid_for_auto(self, now: float | None = None) -> bool:
        """Return True when learned power cannot be trusted because of age metadata."""
        return self._learned_charge_power_can_expire() and (
            self._learned_charge_power_missing_update_time() or self._learned_charge_power_expired(now)
        )

    def _learned_charge_power_inactive_for_auto(
        self,
        learned_value: float | None,
        now: float | None = None,
    ) -> bool:
        """Return True when learned power must not influence Auto thresholds."""
        return any(
            (
                not self._learned_charge_power_policy_enabled(),
                self._current_learned_charge_power_state(now) != "stable",
                learned_value is None,
                self._learned_charge_power_age_invalid_for_auto(now),
            )
        )

    def _learned_charge_power_scale(self, now: float | None = None) -> float:
        """Return a linear threshold scale derived from the learned charging power."""
        policy = self._auto_policy().learn_charge_power
        learned_value = self._active_learned_charge_power(now)
        if not policy.enabled or learned_value is None:
            return 1.0
        return learned_value / float(policy.reference_power_watts)

    def _scale_surplus_thresholds(self, start_watts: float, stop_watts: float) -> tuple[float, float]:
        """Scale the configured surplus thresholds around the reference charging load."""
        scale = self._learned_charge_power_scale()
        return round(float(start_watts) * scale, 1), round(float(stop_watts) * scale, 1)

    def _surplus_thresholds_for_soc(self, battery_soc: float) -> tuple[float, float, str]:
        """Return the active start/stop surplus thresholds for the current battery SOC."""
        svc = self.service
        policy = self._auto_policy()
        profile, active, profile_name = policy.resolve_threshold_profile(
            battery_soc,
            getattr(svc, "_auto_high_soc_profile_active", None),
        )
        svc._auto_high_soc_profile_active = bool(active)
        start_watts, stop_watts = self._scale_surplus_thresholds(
            float(profile.start_surplus_watts),
            float(profile.stop_surplus_watts),
        )
        if not thresholds_ordered(start_watts, stop_watts):
            logging.warning(
                "Adaptive surplus thresholds became invalid for profile %s: start=%s stop=%s; falling back to static profile values",
                profile_name,
                start_watts,
                stop_watts,
            )
            return float(profile.start_surplus_watts), float(profile.stop_surplus_watts), profile_name
        return start_watts, stop_watts, profile_name

    def _auto_policy(self) -> AutoPolicy:
        """Return the structured AutoPolicy, synthesizing it from legacy attrs when needed."""
        svc = self.service
        policy = getattr(svc, "auto_policy", None)
        if policy is None:
            policy = validate_auto_policy(AutoPolicy.from_service(svc))
            try:
                svc.auto_policy = policy
            except AttributeError:
                pass
        return policy

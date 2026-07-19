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

from typing import Any

from venus_evcharger.auto.tracking import clear_auto_decision_tracking
from venus_evcharger.auto.logic_types import NO_RELAY_DECISION, require_relay_bool

from .component_context import AutoDecisionContext
from .logic_gates_runtime import AutoRuntimeGates
from .logic_learning import AutoLearningPolicy
from .logic_samples import AutoSampleTracker
from .policy import AutoPolicy


class AutoRelayDecision:
    """Resolve start, stop, and scheduled-night relay decisions."""

    def __init__(
        self,
        context: AutoDecisionContext,
        learning: AutoLearningPolicy,
        samples: AutoSampleTracker,
        gates: AutoRuntimeGates,
    ) -> None:
        self._context = context
        self.service = context.service
        self.learning = learning
        self.samples = samples
        self.gates = gates

    def handle_relay_on(
        self,
        avg_surplus_power: float,
        avg_grid_power: float,
        battery_soc: float,
        daytime_window_open: bool,
        now: float,
        cached_inputs: bool,
    ) -> bool:
        """Evaluate Auto stop conditions while the relay is already on."""
        svc = self.service
        svc.auto_start_condition_since = None
        minimum_runtime_elapsed = self.gates._minimum_runtime_elapsed(now)

        stop_reason = self._relay_on_stop_reason(
            avg_surplus_power,
            avg_grid_power,
            battery_soc,
            daytime_window_open,
            minimum_runtime_elapsed,
        )

        if stop_reason is None:
            return require_relay_bool(self.samples._running_result_with_health("running", cached_inputs))
        stop_delay_seconds = None
        reported_reason = stop_reason
        if stop_reason == "auto-stop-surplus":
            stop_delay_seconds = float(self.learning._auto_policy().stop_surplus_delay_seconds)
            reported_reason = "auto-stop"
        elif stop_reason in ("auto-stop-grid", "auto-stop-soc"):
            reported_reason = "auto-stop"
        return require_relay_bool(
            self.gates._pending_stop_or_running(
                now,
                reported_reason,
                cached_inputs,
                "running",
                delay_seconds=stop_delay_seconds,
                stop_key=stop_reason,
            ),
        )

    def _relay_on_stop_reason(
        self,
        avg_surplus_power: float,
        avg_grid_power: float,
        battery_soc: float,
        daytime_window_open: bool,
        minimum_runtime_elapsed: bool,
    ) -> str | None:
        """Return the concrete stop reason while Auto is already running."""
        if not minimum_runtime_elapsed:
            return None
        if self._night_lock_stop_requested(daytime_window_open):
            return "night-lock"
        return self._policy_relay_on_stop_reason(avg_surplus_power, avg_grid_power, battery_soc)

    def _night_lock_stop_requested(self, daytime_window_open: bool) -> bool:
        """Return whether the configured night lock should stop the running relay."""
        if not hasattr(self.service, "auto_night_lock_stop"):
            return False
        return getattr(self.service, "auto_night_lock_stop") is True and not daytime_window_open

    def _policy_relay_on_stop_reason(
        self,
        avg_surplus_power: float,
        avg_grid_power: float,
        battery_soc: float,
    ) -> str | None:
        """Return the relay-on stop reason derived from policy thresholds."""
        _, stop_surplus_watts, _ = self.learning._surplus_thresholds_for_soc(battery_soc)
        policy = self.learning._auto_policy()
        if battery_soc < policy.min_soc:
            return "auto-stop-soc"
        if avg_grid_power >= policy.stop_grid_import_watts:
            return "auto-stop-grid"
        if avg_surplus_power <= stop_surplus_watts:
            return "auto-stop-surplus"
        return None

    @staticmethod
    def _relay_off_start_conditions_met(
        minimum_offtime_elapsed: bool,
        daytime_window_open: bool,
        avg_surplus_power: float,
        avg_grid_power: float,
        battery_soc: float,
        start_surplus_watts: float,
        policy: AutoPolicy,
    ) -> bool:
        """Return whether all Auto start gates are satisfied."""
        return (
            minimum_offtime_elapsed
            and daytime_window_open
            and avg_surplus_power >= start_surplus_watts
            and avg_grid_power <= policy.start_max_grid_import_watts
            and battery_soc >= policy.resume_soc
        )

    def _arm_or_fire_start(self, now: float, cached_inputs: bool) -> bool:
        """Track delayed start conditions and start once the configured delay elapsed."""
        svc = self.service
        if svc.auto_start_condition_since is None:
            svc.auto_start_condition_since = now
            return False
        if (now - svc.auto_start_condition_since) >= svc.auto_start_delay_seconds:
            self._context.port.clear_minimum_offtime_bypass()
            self._context.port.save_runtime_state()
            self.samples.set_health("auto-start", cached_inputs, relay_intent=True)
            return True
        return False

    def _set_waiting_health(
        self,
        minimum_offtime_elapsed: bool,
        daytime_window_open: bool,
        avg_surplus_power: float,
        avg_grid_power: float,
        battery_soc: float,
        cached_inputs: bool,
    ) -> None:
        """Set the most useful waiting reason while Auto is idle."""
        self.samples.set_health(
            self._waiting_health_reason(
                minimum_offtime_elapsed,
                daytime_window_open,
                avg_surplus_power,
                avg_grid_power,
                battery_soc,
            ),
            cached_inputs,
            relay_intent=False,
        )

    def _waiting_health_reason(
        self,
        minimum_offtime_elapsed: bool,
        daytime_window_open: bool,
        avg_surplus_power: float,
        avg_grid_power: float,
        battery_soc: float,
    ) -> str:
        """Return the most useful waiting reason while Auto is idle."""
        if not minimum_offtime_elapsed:
            return "waiting-offtime"
        if not daytime_window_open:
            return "waiting-daytime"
        return self._threshold_waiting_health_reason(avg_surplus_power, avg_grid_power, battery_soc)

    def _threshold_waiting_health_reason(
        self,
        avg_surplus_power: float,
        avg_grid_power: float,
        battery_soc: float,
    ) -> str:
        """Return the waiting reason derived from start thresholds and SOC."""
        policy = self.learning._auto_policy()
        start_surplus_watts, _, _ = self.learning._surplus_thresholds_for_soc(battery_soc)
        if avg_surplus_power < start_surplus_watts:
            return "waiting-surplus"
        if avg_grid_power > policy.start_max_grid_import_watts:
            return "waiting-grid"
        if battery_soc < policy.resume_soc:
            return "waiting-soc"
        return "waiting"

    def handle_relay_off(
        self,
        avg_surplus_power: float,
        avg_grid_power: float,
        battery_soc: float,
        daytime_window_open: bool,
        now: float,
        cached_inputs: bool,
    ) -> bool:
        """Evaluate Auto start conditions while the relay is off."""
        svc = self.service
        start_surplus_watts, _, _ = self.learning._surplus_thresholds_for_soc(battery_soc)
        svc.auto_stop_condition_since = None
        if not self._context.port.autostart_enabled():
            return require_relay_bool(self.samples._idle_result_with_health("autostart-disabled", cached_inputs))

        minimum_offtime_elapsed = self.gates._minimum_offtime_elapsed(now)
        if self._relay_off_start_conditions_met(
            minimum_offtime_elapsed,
            daytime_window_open,
            avg_surplus_power,
            avg_grid_power,
            battery_soc,
            start_surplus_watts,
            self.learning._auto_policy(),
        ):
            return self._arm_or_fire_start(now, cached_inputs)

        self.samples._clear_auto_start_tracking()
        self._set_waiting_health(
            minimum_offtime_elapsed,
            daytime_window_open,
            avg_surplus_power,
            avg_grid_power,
            battery_soc,
            cached_inputs,
        )
        return False

    def scheduled_night_decision(
        self,
        relay_on: bool,
        now: float,
        cached_inputs: bool,
    ) -> bool:
        """Return the relay decision for scheduled/plan mode nighttime fallback."""
        svc = self.service
        decision = self.gates.handle_common_runtime_gates(relay_on, now, cached_inputs)
        if decision is not NO_RELAY_DECISION:
            assert isinstance(decision, bool)
            return decision
        if relay_on:
            self._clear_scheduled_night_stop_tracking(svc)
            return require_relay_bool(self.samples._running_result_with_health("scheduled-night-charge", cached_inputs))
        return self._scheduled_night_start_result(svc, now, cached_inputs)

    def _scheduled_night_start_result(self, svc: Any, now: float, cached_inputs: bool) -> bool:
        """Return the off-to-on decision while scheduled night charging is active."""
        blocked_health = self._scheduled_night_blocked_health(now)
        if blocked_health is not None:
            return require_relay_bool(self.samples._idle_result_with_health(blocked_health, cached_inputs))
        self._clear_scheduled_night_stop_tracking(svc)
        return require_relay_bool(self.samples._running_result_with_health("scheduled-night-charge", cached_inputs))

    def _scheduled_night_blocked_health(self, now: float) -> str | None:
        """Return the blocking health reason before scheduled night charging may start."""
        if not self._context.port.autostart_enabled():
            return "autostart-disabled"
        if not self.gates._minimum_offtime_elapsed(now):
            return "waiting-offtime"
        return None

    @staticmethod
    def _clear_scheduled_night_stop_tracking(svc: Any) -> None:
        """Clear Auto start/stop tracking when scheduled night charging takes over."""
        clear_auto_decision_tracking(svc)

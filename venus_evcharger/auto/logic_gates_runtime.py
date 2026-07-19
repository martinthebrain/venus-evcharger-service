# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime gates for Auto-mode decisions.

The methods here combine cached inputs, relay state, timing constraints, and
charger health into small decision helpers used by the Auto workflow.
"""

from __future__ import annotations

import logging
from typing import Any

from venus_evcharger.auto.logic_types import NO_RELAY_DECISION, RelayDecision, require_relay_bool, require_relay_decision
from venus_evcharger.core.common_auto import (
    _confirmed_relay_state_max_age_seconds,
)
from venus_evcharger.core.contracts_snapshot import cutover_confirmed_off
from .component_context import AutoDecisionContext
from .logic_learning import AutoLearningPolicy
from .logic_samples import AutoSampleTracker

AutoDecision = RelayDecision


def _bool_attr_is_true(owner: Any, name: str) -> bool:
    """Return True only when an attribute exists and is exactly True."""
    try:
        return getattr(owner, name) is True
    except AttributeError:
        return False


def _battery_scan_warning_interval_seconds(svc: Any) -> float:
    """Return the warning throttle interval for invalid battery SOC samples."""
    try:
        configured = svc.auto_battery_scan_interval_seconds
    except AttributeError:
        return 60.0
    if configured is None or configured == 0:
        return 60.0
    return max(1.0, float(configured))


class AutoRuntimeGates:
    """Apply runtime, freshness, and transition gates to Auto decisions."""

    def __init__(
        self,
        context: AutoDecisionContext,
        learning: AutoLearningPolicy,
        samples: AutoSampleTracker,
    ) -> None:
        self._context = context
        self.service = context.service
        self.learning = learning
        self.samples = samples

    def _pending_stop_or_running(
        self,
        now: float,
        stop_reason: str,
        cached_inputs: bool,
        running_reason: str,
        delay_seconds: float | None = None,
        stop_key: str | None = None,
    ) -> bool:
        """Arm a delayed stop or keep running with the supplied health reason."""
        decision = self._arm_or_fire_stop(
            now,
            stop_reason,
            cached_inputs,
            delay_seconds=delay_seconds,
            stop_key=stop_key,
        )
        if decision is NO_RELAY_DECISION:
            return require_relay_bool(self.samples._set_health_result(running_reason, cached_inputs, True))
        return False

    def _minimum_runtime_elapsed(self, now: float) -> bool:
        """Return True when the current relay-on period may be stopped."""
        svc = self.service
        return svc.relay_last_changed_at is None or (now - svc.relay_last_changed_at) >= svc.auto_min_runtime_seconds

    def _minimum_offtime_elapsed(self, now: float) -> bool:
        """Return True when the relay may be started again."""
        svc = self.service
        if self._context.port.minimum_offtime_bypass_active():
            return True
        relay_last_off_at = svc.relay_last_off_at
        if relay_last_off_at is None:
            return True
        return (now - float(relay_last_off_at)) >= float(svc.auto_min_offtime_seconds)

    def grid_recently_read(self, grid_power: float | None, now: float) -> bool:
        """Return True when the grid reading is still fresh enough for Auto decisions."""
        svc = self.service
        grid_last_read_at = getattr(svc, "_last_grid_at", None)
        grid_missing_stop_seconds = float(getattr(svc, "auto_grid_missing_stop_seconds", 60.0))
        if grid_last_read_at is None:
            return grid_power is not None
        return (now - float(grid_last_read_at)) <= grid_missing_stop_seconds

    def handle_non_auto_mode(self, relay_on: bool) -> bool:
        """Leave relay state untouched outside of Auto-like modes."""
        self.samples._reset_auto_state()
        self._context.port.reset_mode_cutover()
        self.samples._set_auto_state("idle")
        self._context.port.save_runtime_state()
        return relay_on

    def handle_disabled_mode(self, cached_inputs: bool) -> bool:
        """Force relay off when Auto has been disabled by the GUI."""
        self.samples._reset_auto_state()
        self._context.port.reset_mode_cutover()
        self.samples.set_health("disabled", cached_inputs, relay_intent=False)
        self._context.port.save_runtime_state()
        return False

    def _normalized_battery_soc(self, battery_soc: float | int | None) -> float | None:
        """Return a validated battery SOC reading or None when unavailable/invalid."""
        svc = self.service
        if battery_soc is None:
            return None
        normalized_battery_soc = float(battery_soc)
        if 0.0 <= normalized_battery_soc <= 100.0:
            svc._last_battery_allow_warning = None
            return normalized_battery_soc
        svc.runtime.warning_throttled(
            "battery-soc-invalid",
            _battery_scan_warning_interval_seconds(svc),
            "Auto mode ignored out-of-range battery SOC %s",
            normalized_battery_soc,
        )
        return None

    def _allowed_missing_battery_soc(
        self,
        relay_on: bool,
        now: float,
        cached_inputs: bool,
    ) -> tuple[float, AutoDecision]:
        """Return the fallback decision when missing battery SOC is explicitly allowed."""
        svc = self.service
        if (
            svc._last_battery_allow_warning is None
            or (now - svc._last_battery_allow_warning) > svc.auto_battery_scan_interval_seconds
        ):
            logging.warning("Auto mode: battery SOC missing, allowing Auto based on resume SOC.")
            svc._last_battery_allow_warning = now
        self.samples.set_health("battery-soc-missing-allowed", cached_inputs, relay_intent=relay_on)
        return float(self.learning._auto_policy().resume_soc), NO_RELAY_DECISION

    def _blocked_missing_battery_soc(
        self,
        relay_on: bool,
        cached_inputs: bool,
    ) -> tuple[None, AutoDecision]:
        """Return the terminal decision when missing battery SOC must block Auto mode."""
        self.samples._reset_auto_state()
        self.samples.set_health("battery-soc-missing", cached_inputs, relay_intent=relay_on)
        return None, relay_on

    def resolve_battery_soc(
        self,
        battery_soc: float | int | None,
        relay_on: bool,
        now: float,
        cached_inputs: bool,
    ) -> tuple[float | None, AutoDecision]:
        """Normalize battery SOC or return a terminal decision when it is unavailable."""
        svc = self.service
        normalized_battery_soc = self._normalized_battery_soc(battery_soc)
        if normalized_battery_soc is not None:
            return normalized_battery_soc, NO_RELAY_DECISION
        if _bool_attr_is_true(svc, "auto_allow_without_battery_soc"):
            return self._allowed_missing_battery_soc(relay_on, now, cached_inputs)
        return self._blocked_missing_battery_soc(relay_on, cached_inputs)

    def handle_cutover_pending(self, relay_on: bool, cached_inputs: bool) -> AutoDecision:
        """Honor the Manual -> Auto clean-cutover until the relay is confirmed off."""
        svc = self.service
        if not self._context.port.mode_cutover_pending():
            return NO_RELAY_DECISION

        now = self.learning.learning_policy_now()
        self.samples._reset_auto_state()
        if not self._cutover_relay_off_confirmed(relay_on, now):
            self.samples.set_health("mode-transition", cached_inputs, relay_intent=False)
            return False

        self._complete_cutover_pending()
        return NO_RELAY_DECISION

    def _confirmed_cutover_pm_status(self) -> tuple[dict[str, Any] | None, float | None]:
        """Return only the canonical confirmed Shelly PM status and timestamp."""
        confirmed_pm_status = getattr(self.service, "_last_confirmed_pm_status", None)
        if not isinstance(confirmed_pm_status, dict):
            return None, None
        confirmed_at = getattr(self.service, "_last_confirmed_pm_status_at", None)
        if isinstance(confirmed_at, bool) or not isinstance(confirmed_at, int | float):
            return confirmed_pm_status, None
        return confirmed_pm_status, float(confirmed_at)

    def _cutover_confirmed_sample_fresh(self, confirmed_pm_status_at: float | None, now: float) -> bool:
        """Return True when the confirmed relay sample is fresh enough for cutover release."""
        if confirmed_pm_status_at is None:
            return False
        return (float(now) - float(confirmed_pm_status_at)) <= float(
            _confirmed_relay_state_max_age_seconds(self.service)
        )

    def _cutover_confirmed_after_request(self, confirmed_pm_status_at: float | None) -> bool:
        """Return True when the confirmed relay sample happened after the cutover request."""
        relay_sync_requested_at = getattr(self.service, "_relay_sync_requested_at", None)
        if relay_sync_requested_at is None:
            return True
        if confirmed_pm_status_at is None:
            return False
        return float(confirmed_pm_status_at) >= float(relay_sync_requested_at)

    def _cutover_relay_off_confirmed(self, relay_on: bool, now: float) -> bool:
        """Return True when the relay-off cutover has been confirmed by Shelly."""
        svc = self.service
        pending_state, _ = self._context.port.peek_pending_relay_command()
        confirmed_pm_status, confirmed_pm_status_at = self._confirmed_cutover_pm_status()
        if not isinstance(confirmed_pm_status, dict):
            return False
        return cutover_confirmed_off(
            relay_on=relay_on,
            pending_state=pending_state,
            confirmed_output=confirmed_pm_status.get("output", True),
            confirmed_at=confirmed_pm_status_at,
            requested_at=getattr(svc, "_relay_sync_requested_at", None),
            now=now,
            max_age_seconds=_confirmed_relay_state_max_age_seconds(svc),
        )

    def _complete_cutover_pending(self) -> None:
        """Finish one confirmed Manual -> Auto cutover."""
        self._context.port.complete_mode_cutover()
        self._context.port.save_runtime_state()

    @staticmethod
    def _stop_tracking_needs_reset(
        stop_since: float | None,
        current_stop_key: str | None,
        active_stop_key: str,
    ) -> bool:
        """Return whether the delayed-stop timer must restart for a new reason."""
        return stop_since is None or (current_stop_key is not None and current_stop_key != active_stop_key)

    @staticmethod
    def _stop_delay_elapsed(stop_since: float, now: float, delay_seconds: float) -> bool:
        """Return whether the delayed-stop timer already elapsed."""
        return (now - stop_since) >= delay_seconds

    @staticmethod
    def _active_stop_key(reason: str, stop_key: str | None) -> str:
        """Return the effective key used to track one delayed stop condition."""
        return reason if stop_key is None else stop_key

    @staticmethod
    def _effective_stop_delay(default_delay: float, delay_seconds: float | None) -> float:
        """Return the effective delayed-stop timeout for one stop reason."""
        return default_delay if delay_seconds is None else float(delay_seconds)

    def _reset_stop_tracking(self, now: float, active_stop_key: str) -> bool:
        """Start or restart delayed-stop tracking for the supplied stop key."""
        svc = self.service
        current_stop_key = getattr(svc, "auto_stop_condition_reason", None)
        if not self._stop_tracking_needs_reset(svc.auto_stop_condition_since, current_stop_key, active_stop_key):
            return False
        svc.auto_stop_condition_since = now
        svc.auto_stop_condition_reason = active_stop_key
        return True

    def _ensure_stop_tracking_reason(self, active_stop_key: str) -> None:
        """Ensure delayed-stop tracking keeps its stop reason once a timer is active."""
        svc = self.service
        if getattr(svc, "auto_stop_condition_reason", None) is None:
            svc.auto_stop_condition_reason = active_stop_key

    def _arm_or_fire_stop(
        self,
        now: float,
        reason: str,
        cached_inputs: bool,
        delay_seconds: float | None = None,
        stop_key: str | None = None,
    ) -> AutoDecision:
        """Track delayed stop conditions and stop once the configured delay elapsed."""
        svc = self.service
        active_stop_key = self._active_stop_key(reason, stop_key)
        if self._reset_stop_tracking(now, active_stop_key):
            return NO_RELAY_DECISION
        self._ensure_stop_tracking_reason(active_stop_key)
        effective_delay = self._effective_stop_delay(svc.auto_stop_delay_seconds, delay_seconds)
        assert svc.auto_stop_condition_since is not None
        if not self._stop_delay_elapsed(svc.auto_stop_condition_since, now, effective_delay):
            return NO_RELAY_DECISION
        self.samples.set_health(reason, cached_inputs, relay_intent=False)
        return False

    def handle_grid_missing(self, relay_on: bool, now: float, cached_inputs: bool) -> bool:
        """Fail safe when no fresh grid reading is available."""
        svc = self.service
        self.samples._clear_auto_start_tracking(clear_samples=True)
        svc._grid_recovery_required = True
        svc._grid_recovery_since = None
        if not relay_on:
            return require_relay_bool(self.samples._idle_result_with_health("grid-missing", cached_inputs))

        if not self._minimum_runtime_elapsed(now):
            return require_relay_bool(self.samples._running_result_with_health("grid-missing", cached_inputs))
        return self._pending_stop_or_running(now, "grid-missing", cached_inputs, "grid-missing")

    def handle_grid_recovery_start_gate(self, relay_on: bool, now: float, cached_inputs: bool) -> AutoDecision:
        """Require a short fresh-grid window before Auto may start after grid loss."""
        svc = self.service
        if not self._grid_recovery_gate_active(svc):
            return NO_RELAY_DECISION
        recovery_seconds = float(self.learning._auto_policy().grid_recovery_start_seconds)
        if self._grid_recovery_completes_immediately(now, recovery_seconds):
            return NO_RELAY_DECISION
        if self._grid_recovery_waiting(now, recovery_seconds):
            return self._grid_recovery_wait_decision(relay_on, cached_inputs)
        svc._grid_recovery_required = False
        return NO_RELAY_DECISION

    @staticmethod
    def _grid_recovery_gate_active(svc: Any) -> bool:
        """Return whether the fresh-grid recovery gate is configured and active."""
        return (
            hasattr(svc, "_grid_recovery_since")
            and hasattr(svc, "_grid_recovery_required")
            and _bool_attr_is_true(svc, "_grid_recovery_required")
        )

    def _grid_recovery_completes_immediately(self, now: float, recovery_seconds: float) -> bool:
        """Return True when the recovery gate may be cleared immediately."""
        if recovery_seconds > 0:
            return False
        svc = self.service
        svc._grid_recovery_since = now
        svc._grid_recovery_required = False
        return True

    def _grid_recovery_waiting(
        self,
        now: float,
        recovery_seconds: float,
    ) -> bool:
        """Return whether Auto must keep waiting for the fresh-grid recovery window."""
        svc = self.service
        grid_recovery_since = getattr(svc, "_grid_recovery_since", None)
        if grid_recovery_since is None:
            svc._grid_recovery_since = now
            return True
        return (now - float(grid_recovery_since)) < recovery_seconds

    def _grid_recovery_wait_decision(self, relay_on: bool, cached_inputs: bool) -> AutoDecision:
        """Return the relay decision while the fresh-grid recovery window is still open."""
        if relay_on:
            return NO_RELAY_DECISION
        self.samples._clear_auto_start_tracking()
        return require_relay_decision(self.samples._set_health_result("waiting-grid-recovery", cached_inputs, False))

    def _policy_stop_reason(self, battery_soc: float, grid_power: float | None) -> str | None:
        """Return a stop reason caused by SOC or grid import thresholds."""
        policy = self.learning._auto_policy()
        if battery_soc < policy.min_soc:
            return "auto-stop"
        if grid_power is not None and float(grid_power) >= policy.stop_grid_import_watts:
            return "auto-stop"
        return None

    def _known_missing_input_stop_reason(
        self,
        battery_soc: float,
        grid_power: float | None,
        daytime_window_open: bool,
    ) -> str | None:
        """Return a concrete stop reason when inputs are missing but stopping is still warranted."""
        svc = self.service
        if _bool_attr_is_true(svc, "auto_night_lock_stop") or not daytime_window_open:
            return "night-lock"
        return self._policy_stop_reason(battery_soc, grid_power)

    def handle_missing_inputs(
        self,
        relay_on: bool,
        battery_soc: float,
        grid_power: float | None,
        now: float,
        cached_inputs: bool,
    ) -> bool:
        """Preserve safe behavior when PV or grid inputs are incomplete."""
        self.samples._clear_auto_start_tracking(clear_samples=True)

        if not relay_on:
            return require_relay_bool(self.samples._idle_result_with_health("inputs-missing", cached_inputs))

        daytime_window_open = self.samples.is_within_auto_daytime_window()
        stop_reason = self._known_missing_input_stop_reason(battery_soc, grid_power, daytime_window_open)
        if not self._minimum_runtime_elapsed(now) or stop_reason is None:
            return require_relay_bool(self.samples._running_result_with_health("inputs-missing", cached_inputs))
        return self._pending_stop_or_running(now, stop_reason, cached_inputs, "inputs-missing")

    def handle_common_runtime_gates(self, relay_on: bool, now: float, cached_inputs: bool) -> AutoDecision:
        """Honor startup warmup and manual override holdoff."""
        svc = self.service
        if (now - svc.started_at) < svc.auto_startup_warmup_seconds:
            self.samples._reset_auto_state()
            return require_relay_decision(self.samples._set_health_result("warmup", cached_inputs, relay_on))

        if now < svc.manual_override_until:
            self.samples._reset_auto_state()
            return require_relay_decision(self.samples._set_health_result("manual-override", cached_inputs, relay_on))

        return NO_RELAY_DECISION

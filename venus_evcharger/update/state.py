# SPDX-License-Identifier: GPL-3.0-or-later
"""Virtual-state publishing and update-cycle helpers for the Venus EV charger service.

The update cycle is the heartbeat of the wallbox integration. Every pass reads
the latest Shelly snapshot, lets Auto mode decide whether the relay should be
on, applies corrections if needed, and then publishes the resulting charger
state back to Venus OS.
"""

from __future__ import annotations

import time
from typing import Any

from venus_evcharger.auto.tracking import clear_auto_decision_tracking
from venus_evcharger.core.contracts import finite_float_or_none
from venus_evcharger.core.split_mixins import ComposableControllerMixin as _ComposableControllerMixin



class _UpdateCycleStateMixin(_ComposableControllerMixin):
    @staticmethod
    def _charger_state_max_age_seconds(svc: Any) -> float:
        """Return how fresh charger readback must be before it drives session state."""
        candidates = [2.0]
        worker_poll_interval = finite_float_or_none(getattr(svc, "_worker_poll_interval_seconds", None))
        if worker_poll_interval is not None and worker_poll_interval > 0.0:
            candidates.append(float(worker_poll_interval) * 2.0)
        soft_fail_seconds = finite_float_or_none(getattr(svc, "auto_shelly_soft_fail_seconds", 10.0))
        if soft_fail_seconds is not None and soft_fail_seconds > 0.0:
            candidates.append(float(soft_fail_seconds))
        return max(1.0, min(candidates))

    @classmethod
    def _fresh_charger_enabled_readback(cls, svc: Any, now: float | None = None) -> bool | None:
        """Return fresh native charger enabled-state readback when available."""
        if getattr(svc, "_charger_backend", None) is None:
            return None
        raw_enabled = getattr(svc, "_last_charger_state_enabled", None)
        if raw_enabled is None:
            return None
        if cls._stale_charger_enabled_readback(svc, now):
            return None
        return bool(raw_enabled)

    @classmethod
    def _stale_charger_enabled_readback(cls, svc: Any, now: float | None = None) -> bool:
        """Return whether cached charger enabled readback is too old to trust."""
        state_at = finite_float_or_none(getattr(svc, "_last_charger_state_at", None))
        if state_at is None:
            return True
        current = float(now) if now is not None else time.time()
        return abs(current - state_at) > cls._charger_state_max_age_seconds(svc)

    @staticmethod
    def _fallback_local_pm_status(pm_status: dict[str, Any], relay_on: bool) -> dict[str, Any]:
        """Return one synthesized local PM payload when no helper publish is available."""
        local_status = dict(pm_status)
        local_status["output"] = bool(relay_on)
        local_status["apower"] = 0.0
        local_status["current"] = 0.0
        return local_status

    def _publish_startup_local_pm_status(
        self,
        pm_status: dict[str, Any],
        relay_on: bool,
        now: float,
    ) -> dict[str, Any]:
        """Publish or synthesize a startup placeholder relay state without losing the target."""
        svc = self.service
        publish_local_pm_status = getattr(svc, "_publish_local_pm_status", None)
        if callable(publish_local_pm_status):
            try:
                published = publish_local_pm_status(relay_on, now)
                if isinstance(published, dict):
                    return published
            except Exception as error:  # pylint: disable=broad-except
                svc._warning_throttled(
                    "startup-manual-target-placeholder-failed",
                    svc.auto_shelly_soft_fail_seconds,
                    "Failed to publish startup manual placeholder state %s: %s",
                    relay_on,
                    error,
                    exc_info=error,
                )
        return self._fallback_local_pm_status(pm_status, relay_on)

    def apply_startup_manual_target(self, pm_status: dict[str, Any], now: float) -> dict[str, Any]:
        """Synchronize the configured manual on/off state once after startup."""
        svc = self.service
        target_on = self._startup_manual_target(svc)
        if target_on is None or svc._mode_uses_auto_logic(svc.virtual_mode):
            return pm_status
        relay_on = bool(pm_status.get("output", False))
        if relay_on == target_on:
            svc._startup_manual_target = None
            return pm_status

        return self._apply_startup_manual_target(pm_status, now, target_on)

    @staticmethod
    def _startup_manual_target(svc: Any) -> bool | None:
        """Return the pending startup manual target, initializing the field when needed."""
        if not hasattr(svc, "_startup_manual_target"):
            svc._startup_manual_target = None
        value = getattr(svc, "_startup_manual_target", None)
        return None if value is None else bool(value)

    def _apply_startup_manual_target(
        self,
        pm_status: dict[str, Any],
        now: float,
        target_on: bool,
    ) -> dict[str, Any]:
        """Apply the pending startup manual target or keep live PM status on failure."""
        svc = self.service

        try:
            # Startup manual state is best-effort. If Shelly access is currently
            # unavailable, we keep the live status and let the normal update loop
            # retry on the next cycle instead of failing startup.
            applied = self._apply_enabled_target(svc, target_on, now)
        except Exception as error:  # pylint: disable=broad-except
            source_key = self._enable_control_source_key(svc)
            source_label = self._enable_control_label(svc)
            svc._mark_failure(source_key)
            svc._warning_throttled(
                "startup-manual-target-failed",
                svc.auto_shelly_soft_fail_seconds,
                "Failed to apply startup manual %s state %s: %s",
                source_label,
                target_on,
                error,
                exc_info=error,
            )
            return pm_status
        if not applied:
            return pm_status
        svc._startup_manual_target = None
        return self._publish_startup_local_pm_status(pm_status, target_on, now)

    def ensure_virtual_state_defaults(self) -> None:
        """Populate defaults used by virtual session and health publishing."""
        svc = self.service
        svc._ensure_observability_state()
        if not hasattr(svc, "_last_health_reason"):
            svc._last_health_reason = "init"
        if not hasattr(svc, "_last_health_code"):
            svc._last_health_code = self._health_code(svc._last_health_reason)

    @classmethod
    def session_state_from_status(
        cls,
        svc: Any,
        status: int,
        current_total_energy: float,
        relay_on: bool,
        now: float,
    ) -> tuple[int, float]:
        """Compute current session timing and energy values."""
        if cls._session_active(status):
            return cls._active_session_state(svc, current_total_energy, now)
        if relay_on and cls._session_was_active(svc):
            cls._clear_auto_tracking_after_physical_session_end(svc)
        return cls._reset_session_state(svc, current_total_energy)

    @staticmethod
    def _session_active(status: int) -> bool:
        """Return whether the current status should keep the session active."""
        return status == 2

    @staticmethod
    def _session_was_active(svc: Any) -> bool:
        """Return whether the previous update still considered a car session active."""
        return getattr(svc, "charging_started_at", None) is not None

    @staticmethod
    def _clear_auto_tracking_after_physical_session_end(svc: Any) -> None:
        """Treat a load drop with relay still on as the end of one plug session."""
        clear_auto_decision_tracking(svc)

    def save_runtime_state_best_effort(self, reason: str) -> None:
        """Persist runtime state without letting persistence break the live loop."""
        svc = self.service
        save_runtime_state = getattr(svc, "_save_runtime_state", None)
        if not callable(save_runtime_state):
            return
        try:
            save_runtime_state()
        except Exception as error:  # pylint: disable=broad-except
            warning_throttled = getattr(svc, "_warning_throttled", None)
            if callable(warning_throttled):
                warning_throttled(
                    f"runtime-state-save-failed-{reason}",
                    getattr(svc, "auto_shelly_soft_fail_seconds", 10.0),
                    "Unable to save runtime state during %s update: %s",
                    reason,
                    error,
                    exc_info=error,
                )

    @classmethod
    def _active_session_state(cls, svc: Any, current_total_energy: float, now: float) -> tuple[int, float]:
        """Return timing and energy values for an active charging session."""
        if svc.charging_started_at is None:
            svc.charging_started_at = now
            svc.energy_at_start = current_total_energy
        charging_time = int(now - svc.charging_started_at)
        return charging_time, cls._session_energy(current_total_energy, svc.energy_at_start)

    @classmethod
    def _reset_session_state(cls, svc: Any, current_total_energy: float) -> tuple[int, float]:
        """Reset session timing and energy when charging is no longer enabled."""
        svc.charging_started_at = None
        svc.energy_at_start = current_total_energy
        return 0, 0.0

    @staticmethod
    def _session_energy(current_total_energy: float, energy_at_start: float) -> float:
        """Return normalized session energy delta."""
        return round(max(0.0, current_total_energy - energy_at_start), 3)

    @classmethod
    def startstop_display_for_state(cls, svc: Any, relay_on: bool, now: float) -> int:
        """Return the GUI start/stop indicator for the current mode."""
        charger_enabled = cls._fresh_charger_enabled_readback(svc, now)
        if charger_enabled is not None:
            return int(charger_enabled)
        svc.virtual_startstop = 1 if relay_on else 0
        if svc._mode_uses_auto_logic(svc.virtual_mode):
            return int(relay_on or svc.virtual_enable)
        return int(svc.virtual_startstop)

    @staticmethod
    def phase_energies_for_total(svc: Any, current_total_energy: float) -> dict[str, float]:
        """Split total energy across phases according to configured wiring."""
        phase = getattr(svc, "phase", "L1")
        if phase == "3P":
            per_phase = current_total_energy / 3.0
            return {"L1": per_phase, "L2": per_phase, "L3": per_phase}
        return {
            "L1": current_total_energy if phase == "L1" else 0.0,
            "L2": current_total_energy if phase == "L2" else 0.0,
            "L3": current_total_energy if phase == "L3" else 0.0,
        }

    def publish_virtual_state_paths(
        self,
        current_total_energy: float,
        charging_time: int,
        session_energy: float,
        startstop_display: int,
        now: float,
    ) -> bool:
        """Publish session, config, and diagnostic values derived from the live state."""
        svc = self.service
        # Shelly ``aenergy.total`` is a lifetime counter. Venus' EV-charger UI
        # expects the charger-facing energy paths to describe the active charge.
        phase_energies = self.phase_energies_for_total(svc, session_energy)
        changed = svc._publish_energy_time_measurements(
            session_energy,
            phase_energies,
            charging_time,
            session_energy,
            now,
        )
        changed |= svc._publish_config_paths(startstop_display, now)
        changed |= svc._publish_diagnostic_paths(now)
        return bool(changed)

    @staticmethod
    def _total_phase_current(phase_data: dict[str, dict[str, float]]) -> float:
        """Return the summed AC current across all published phases."""
        return sum(phase_data[phase_name]["current"] for phase_name in ("L1", "L2", "L3"))

    def update_virtual_state(self, status: int, current_total_energy: float, relay_on: bool) -> bool:
        """Update DBus state that is derived from relay state and energy."""
        svc = self.service
        status = int(status)
        current_total_energy = float(current_total_energy)
        relay_on = bool(relay_on)
        self.ensure_virtual_state_defaults()
        now = svc._time_now()
        charging_time, session_energy = self.session_state_from_status(
            svc,
            status,
            current_total_energy,
            relay_on,
            now,
        )
        startstop_display = self.startstop_display_for_state(svc, relay_on, now)
        svc.last_status = status
        changed = self.publish_virtual_state_paths(
            current_total_energy,
            charging_time,
            session_energy,
            startstop_display,
            now,
        )
        self.save_runtime_state_best_effort("virtual-state")
        return bool(changed)

    @staticmethod
    def prepare_update_cycle(svc: Any, now: float) -> Any:
        """Run pre-update recovery/supervision hooks and return the latest worker snapshot."""
        start_io_worker = getattr(svc, "_start_io_worker", None)
        topology_configured = bool(getattr(svc, "topology_configured", getattr(svc, "host_configured", False)))
        if topology_configured and callable(start_io_worker):
            start_io_worker()
        svc._watchdog_recover(now)
        svc._ensure_auto_input_helper_process(now)
        svc._refresh_auto_input_snapshot(now)
        return svc._get_worker_snapshot()

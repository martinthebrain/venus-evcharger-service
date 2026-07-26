# SPDX-License-Identifier: GPL-3.0-or-later
"""Virtual-state publishing and update-cycle helpers for the Venus EV charger service.

The update cycle is the heartbeat of the wallbox integration. Every pass reads
the latest Shelly snapshot, lets Auto mode decide whether the relay should be
on, applies corrections if needed, and then publishes the resulting charger
state back to Venus OS.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, TypeGuard

from venus_evcharger.auto.tracking import clear_auto_decision_tracking
from venus_evcharger.update.readback_resolver import FreshReadbacks
from venus_evcharger.update.relay_charger_current import ChargerTargetController
from venus_evcharger.update.relay_charger_health import ChargerHealthMonitor

STARTUP_MANUAL_TARGET_ERRORS = (KeyError, OSError, RuntimeError, TypeError, ValueError)
RUNTIME_STATE_SAVE_ERRORS = (OSError, RuntimeError, TypeError, ValueError)


def _is_object_dict(value: object) -> TypeGuard[dict[object, object]]:
    return isinstance(value, dict)


def _is_string_keyed_dict(value: object) -> TypeGuard[dict[str, object]]:
    return _is_object_dict(value) and all(isinstance(key, str) for key in value)


class StateAutoPort(Protocol):
    def mode_uses_auto_logic(self, mode: object) -> bool: ...


class StateReadbackPort(Protocol):
    def resolve(self, now: float | None = None) -> FreshReadbacks: ...


class StateRuntimePort(Protocol):
    def ensure_observability_state(self) -> None: ...
    def ensure_auto_input_helper(self, now: float) -> None: ...
    def recover_watchdog(self, now: float) -> None: ...
    def refresh_auto_input_snapshot(self, now: float) -> None: ...
    def worker_snapshot(self) -> dict[str, object]: ...
    def mark_failure(self, source_key: str) -> None: ...
    def mark_recovery(self, source_key: str, message: str, *args: object) -> None: ...
    def queue_relay_command(self, relay_on: bool, current_time: float) -> object: ...
    def publish_local_pm_status(self, relay_on: bool, now: float) -> object: ...
    def start_io_worker(self) -> None: ...
    def warning_throttled(
        self,
        warning_key: str,
        interval_seconds: float,
        warning_message: str,
        *args: object,
        **kwargs: object,
    ) -> None: ...


class StatePublishPort(Protocol):
    def save_runtime_state(self) -> object: ...
    def maintain_evcs_registration(self, now: float) -> bool: ...
    def publish_energy_time_measurements(
        self,
        session_energy: float,
        phase_energies: dict[str, float],
        charging_time: int,
        energy_forward: float,
        now: float,
    ) -> bool: ...
    def publish_config_paths(self, startstop_display: int, now: float) -> bool: ...
    def publish_diagnostic_paths(self, now: float) -> bool: ...


class SessionStatePort(Protocol):
    charging_started_at: float | None
    energy_at_start: float


class UpdateStateService(Protocol):
    @property
    def auto(self) -> StateAutoPort: ...

    @property
    def runtime(self) -> StateRuntimePort: ...

    @property
    def state(self) -> StatePublishPort: ...

    @property
    def _readback_resolver(self) -> StateReadbackPort: ...

    auto_shelly_soft_fail_seconds: float
    virtual_mode: int
    virtual_enable: int
    virtual_startstop: int
    charging_started_at: float | None
    energy_at_start: float
    phase: str
    last_status: int
    _startup_manual_target: bool | None
    _last_health_reason: str
    _last_health_code: int
    _charger_target_current_amps: float | None
    _charger_target_current_applied_at: float | None
    topology_configured: bool

    def time_now(self) -> float: ...


class UpdateStateController:
    """Own virtual-session state transitions and their outward publication."""

    def __init__(
        self,
        service: UpdateStateService,
        targets: ChargerTargetController,
        health: ChargerHealthMonitor,
        health_code: Callable[[str], int],
    ) -> None:
        self.service = service
        self._targets = targets
        self._health = health
        self._health_code = health_code

    @staticmethod
    def _fallback_local_pm_status(pm_status: dict[str, object], relay_on: bool) -> dict[str, object]:
        """Return one synthesized local PM payload when no helper publish is available."""
        local_status = dict(pm_status)
        local_status["output"] = bool(relay_on)
        local_status["apower"] = 0.0
        local_status["current"] = 0.0
        return local_status

    def _publish_startup_local_pm_status(
        self,
        pm_status: dict[str, object],
        relay_on: bool,
        now: float,
    ) -> dict[str, object]:
        """Publish or synthesize a startup placeholder relay state without losing the target."""
        svc = self.service
        try:
            published = self._string_keyed_pm_status(svc.runtime.publish_local_pm_status(relay_on, now))
            if published is not None:
                return published
        except STARTUP_MANUAL_TARGET_ERRORS as error:
            svc.runtime.warning_throttled(
                "startup-manual-target-placeholder-failed",
                svc.auto_shelly_soft_fail_seconds,
                "Failed to publish startup manual placeholder state %s: %s",
                relay_on,
                error,
                exc_info=error,
            )
        return self._fallback_local_pm_status(pm_status, relay_on)

    @staticmethod
    def _string_keyed_pm_status(value: object) -> dict[str, object] | None:
        return value if _is_string_keyed_dict(value) else None

    def apply_startup_manual_target(self, pm_status: dict[str, object], now: float) -> dict[str, object]:
        """Synchronize the configured manual on/off state once after startup."""
        svc = self.service
        target_on = self._startup_manual_target(svc)
        if target_on is None or svc.auto.mode_uses_auto_logic(svc.virtual_mode):
            return pm_status
        relay_on = bool(pm_status.get("output"))
        if relay_on == target_on:
            svc._startup_manual_target = None
            return pm_status

        return self._apply_startup_manual_target(pm_status, now, target_on)

    @staticmethod
    def _startup_manual_target(svc: UpdateStateService) -> bool | None:
        """Return the pending startup manual target, initializing the field when needed."""
        if not hasattr(svc, "_startup_manual_target"):
            svc._startup_manual_target = None
        value = getattr(svc, "_startup_manual_target")
        return None if value is None else bool(value)

    def _apply_startup_manual_target(
        self,
        pm_status: dict[str, object],
        now: float,
        target_on: bool,
    ) -> dict[str, object]:
        """Apply the pending startup manual target or keep live PM status on failure."""
        svc = self.service

        try:
            # Startup manual state is best-effort. If Shelly access is currently
            # unavailable, we keep the live status and let the normal update loop
            # retry on the next cycle instead of failing startup.
            applied = self._targets.apply_enabled_target(svc, target_on, now)
        except STARTUP_MANUAL_TARGET_ERRORS as error:
            source_key = self._health._enable_control_source_key(svc)
            source_label = self._health._enable_control_label(svc)
            svc.runtime.mark_failure(source_key)
            svc.runtime.warning_throttled(
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
        svc.runtime.ensure_observability_state()
        if not hasattr(svc, "_last_health_reason"):
            svc._last_health_reason = "init"
        if not hasattr(svc, "_last_health_code"):
            svc._last_health_code = self._health_code(svc._last_health_reason)

    @classmethod
    def session_state_from_status(
        cls,
        svc: UpdateStateService,
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
    def _session_was_active(svc: SessionStatePort) -> bool:
        """Return whether the previous update still considered a car session active."""
        return getattr(svc, "charging_started_at", None) is not None

    @staticmethod
    def _clear_auto_tracking_after_physical_session_end(svc: UpdateStateService) -> None:
        """Treat a load drop with relay still on as the end of one plug session."""
        clear_auto_decision_tracking(svc)

    def save_runtime_state_best_effort(self, reason: str) -> None:
        """Persist runtime state without letting persistence break the live loop."""
        svc = self.service
        try:
            svc.state.save_runtime_state()
        except RUNTIME_STATE_SAVE_ERRORS as error:
            svc.runtime.warning_throttled(
                f"runtime-state-save-failed-{reason}",
                svc.auto_shelly_soft_fail_seconds,
                "Unable to save runtime state during %s update: %s",
                reason,
                error,
                exc_info=error,
            )

    @classmethod
    def _active_session_state(cls, svc: SessionStatePort, current_total_energy: float, now: float) -> tuple[int, float]:
        """Return timing and energy values for an active charging session."""
        if svc.charging_started_at is None:
            svc.charging_started_at = now
            svc.energy_at_start = current_total_energy
        charging_time = max(0, int(now - svc.charging_started_at))
        return charging_time, cls._session_energy(current_total_energy, svc.energy_at_start)

    @classmethod
    def _reset_session_state(cls, svc: SessionStatePort, current_total_energy: float) -> tuple[int, float]:
        """Reset session timing and energy when charging is no longer enabled."""
        svc.charging_started_at = None
        svc.energy_at_start = current_total_energy
        return 0, 0.0

    @staticmethod
    def _session_energy(current_total_energy: float, energy_at_start: float) -> float:
        """Return normalized session energy delta."""
        return round(max(0.0, current_total_energy - energy_at_start), 3)

    @classmethod
    def startstop_display_for_state(cls, svc: UpdateStateService, relay_on: bool, now: float) -> int:
        """Return the GUI start/stop indicator for the current mode."""
        charger_enabled = cls._charger_enabled_for_display(svc, now)
        if charger_enabled is not None:
            return int(charger_enabled)
        return cls._fallback_startstop_display(svc, relay_on)

    @staticmethod
    def _charger_enabled_for_display(svc: UpdateStateService, now: float) -> bool | None:
        charger = svc._readback_resolver.resolve(now).charger
        return None if charger is None else charger.state.enabled

    @staticmethod
    def _fallback_startstop_display(svc: UpdateStateService, relay_on: bool) -> int:
        svc.virtual_startstop = 1 if relay_on else 0
        if svc.auto.mode_uses_auto_logic(svc.virtual_mode):
            return int(relay_on or svc.virtual_enable)
        return int(svc.virtual_startstop)

    @staticmethod
    def phase_energies_for_total(svc: UpdateStateService, current_total_energy: float) -> dict[str, float]:
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
        changed = svc.state.maintain_evcs_registration(now)
        changed |= svc.state.publish_energy_time_measurements(
            session_energy,
            phase_energies,
            charging_time,
            session_energy,
            now,
        )
        changed |= svc.state.publish_config_paths(startstop_display, now)
        changed |= svc.state.publish_diagnostic_paths(now)
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
        now = svc.time_now()
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
    def prepare_update_cycle(svc: UpdateStateService, now: float) -> dict[str, object]:
        """Run pre-update recovery/supervision hooks and return the latest worker snapshot."""
        if svc.topology_configured:
            svc.runtime.start_io_worker()
        svc.runtime.recover_watchdog(now)
        svc.runtime.ensure_auto_input_helper(now)
        svc.runtime.refresh_auto_input_snapshot(now)
        return svc.runtime.worker_snapshot()


__all__ = ["UpdateStateController", "UpdateStateService"]

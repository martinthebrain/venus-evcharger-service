# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline publishing helpers for the update cycle."""

from __future__ import annotations

from typing import Any, ClassVar

from venus_evcharger.core.contracts import timestamp_age_within
from venus_evcharger.update.input_cache import _UpdateCycleInputCache


class _UpdateCycleOffline(_UpdateCycleInputCache):
    FUTURE_INPUT_TIMESTAMP_TOLERANCE_SECONDS: ClassVar[float]
    service: Any

    @staticmethod
    def _offline_health_reason(svc: Any) -> str:
        """Return the health reason used for one offline publish."""
        configured = getattr(svc, "topology_configured", getattr(svc, "host_configured", True))
        return "shelly-offline" if bool(configured) else "not-configured"

    @staticmethod
    def _offline_confirmed_relay_max_age_seconds(svc: Any) -> float:
        """Return how old a confirmed relay sample may be for offline publishing."""
        candidates = [2.0]
        worker_poll_seconds = getattr(svc, "_worker_poll_interval_seconds", None)
        if worker_poll_seconds is not None and float(worker_poll_seconds) > 0:
            candidates.append(float(worker_poll_seconds) * 2.0)
        relay_sync_timeout_seconds = getattr(svc, "relay_sync_timeout_seconds", None)
        if relay_sync_timeout_seconds is not None and float(relay_sync_timeout_seconds) > 0:
            candidates.append(float(relay_sync_timeout_seconds))
        return max(1.0, min(candidates))

    @classmethod
    def _offline_confirmed_relay_state(cls, svc: Any, now: float) -> bool:
        """Return the last fresh confirmed relay state, defaulting to OFF when unknown."""
        raw_pm_status = getattr(svc, "_last_confirmed_pm_status", None)
        raw_captured_at = getattr(svc, "_last_confirmed_pm_status_at", None)
        if not cls._offline_confirmed_relay_sample_present(raw_pm_status, raw_captured_at):
            return False
        pm_status = raw_pm_status
        captured_at = raw_captured_at
        assert isinstance(pm_status, dict)
        assert captured_at is not None
        if not cls._offline_confirmed_relay_sample_fresh(svc, now, float(captured_at)):
            return False
        return bool(pm_status.get("output"))

    @staticmethod
    def _offline_confirmed_relay_sample_present(
        pm_status: Any,
        captured_at: float | None,
    ) -> bool:
        """Return True when offline publishing has one confirmed relay sample to inspect."""
        return isinstance(pm_status, dict) and "output" in pm_status and captured_at is not None

    @classmethod
    def _offline_confirmed_relay_sample_fresh(
        cls,
        svc: Any,
        now: float,
        captured_at: float,
    ) -> bool:
        """Return True when one confirmed relay sample is fresh enough for offline status."""
        max_age_seconds = cls._offline_confirmed_relay_max_age_seconds(svc)
        return bool(
            timestamp_age_within(
                captured_at,
                now,
                max_age_seconds,
                future_tolerance_seconds=cls.FUTURE_INPUT_TIMESTAMP_TOLERANCE_SECONDS,
            )
        )

    def publish_offline_update(self, now: float) -> bool:
        """Publish a disconnected Shelly state when no recent status is available."""
        svc = self.service
        voltage = self._offline_voltage(svc)
        offline_pm_status = self._fresh_offline_pm_status(svc, now)
        relay_on = self._offline_confirmed_relay_state(svc, now)
        power, energy_forward, status = self._offline_power_state()
        self._mark_offline_status_state(svc)
        phase_data = self._phase_data_for_pm_status(offline_pm_status, power, voltage)
        svc._set_health(self._offline_health_reason(svc), cached=False)
        total_current = self._total_phase_current(phase_data)
        changed = self._publish_offline_live_state(
            svc,
            power=power,
            voltage=voltage,
            total_current=total_current,
            phase_data=phase_data,
            now=now,
            status=status,
            energy_forward=energy_forward,
            relay_on=relay_on,
        )
        if changed:
            svc._bump_update_index(now)
        completed_at = self._mark_offline_update_completed(svc)
        publish_companion = getattr(svc, "_publish_companion_dbus_bridge", None)
        if callable(publish_companion):
            publish_companion(completed_at)
        return True

    @staticmethod
    def _mark_offline_update_completed(svc: Any) -> float:
        """Mark one offline publish as a completed, watchdog-fresh update cycle."""
        completed_at = float(svc._time_now())
        svc._last_successful_update_at = completed_at
        svc._last_recovery_attempt_at = None
        svc.last_update = completed_at
        return completed_at

    @staticmethod
    def _offline_voltage(svc: Any) -> float:
        """Return the fallback voltage used for one offline publish."""
        return float(svc._last_voltage) if getattr(svc, "_last_voltage", None) else 230.0

    @staticmethod
    def _offline_confirmed_pm_status_timestamp(raw_timestamp: Any) -> float | None:
        """Return the numeric timestamp of the last confirmed PM status when valid."""
        if not isinstance(raw_timestamp, (int, float)) or isinstance(raw_timestamp, bool):
            return None
        return float(raw_timestamp)

    @classmethod
    def _fresh_offline_pm_status(cls, svc: Any, now: float) -> dict[str, Any] | None:
        """Return the last confirmed PM status only while it stays fresh for offline use."""
        offline_pm_status = getattr(svc, "_last_confirmed_pm_status", None)
        offline_pm_status_at = cls._offline_confirmed_pm_status_timestamp(
            getattr(svc, "_last_confirmed_pm_status_at", None)
        )
        if offline_pm_status_at is None:
            return None
        if not isinstance(offline_pm_status, dict) or "output" not in offline_pm_status:
            return None
        if not cls._offline_confirmed_relay_sample_fresh(svc, now, offline_pm_status_at):
            return None
        return offline_pm_status

    @staticmethod
    def _offline_power_state() -> tuple[float, float, int]:
        """Return the fixed power/energy/status tuple used for offline publishing."""
        return 0.0, 0.0, 0

    @staticmethod
    def _mark_offline_status_state(svc: Any) -> None:
        """Mark service observability fields for one offline Shelly publish."""
        svc._last_status_source = _UpdateCycleOffline._offline_health_reason(svc)
        svc._last_charger_fault_active = 0

    def _publish_offline_live_state(
        self,
        svc: Any,
        *,
        power: float,
        voltage: float,
        total_current: float,
        phase_data: dict[str, dict[str, float]],
        now: float,
        status: int,
        energy_forward: float,
        relay_on: bool,
    ) -> bool:
        """Publish one offline measurement set and matching virtual charger state."""
        measurements_changed = bool(svc._publish_live_measurements(power, voltage, total_current, phase_data, now))
        virtual_state_changed = bool(self.update_virtual_state(status, energy_forward, relay_on))
        return measurements_changed or virtual_state_changed

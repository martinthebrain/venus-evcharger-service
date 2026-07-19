# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline publishing helpers for the update cycle."""

from __future__ import annotations

from typing import ClassVar, Protocol, TypeGuard, TypedDict

from venus_evcharger.core.contracts import timestamp_age_within
from venus_evcharger.update.relay_phase_publish import RelayTelemetry, RelayTelemetryService
from venus_evcharger.update.relay_status_publish import VirtualStatePublisher


class OfflineAutoPort(Protocol):
    def set_health(self, reason: str, *, cached: bool) -> None: ...


class OfflineStatePort(Protocol):
    def bump_update_index(self, now: float) -> None: ...
    def publish_live_measurements(
        self,
        power: float,
        voltage: float,
        total_current: float,
        phase_data: dict[str, dict[str, float]],
        now: float,
    ) -> bool: ...
    def publish_companion_bridge(self, now: float | None = None) -> bool: ...


class OfflineService(RelayTelemetryService, Protocol):
    @property
    def auto(self) -> OfflineAutoPort: ...

    @property
    def state(self) -> OfflineStatePort: ...

    topology_configured: bool
    host_configured: bool
    _worker_poll_interval_seconds: float
    _last_confirmed_pm_status: dict[str, object] | None
    _last_confirmed_pm_status_at: float | None
    _last_voltage: float | None
    _last_status_source: str
    _last_charger_fault_active: int
    _last_successful_update_at: float | None
    _last_recovery_attempt_at: float | None
    last_update: float

    def time_now(self) -> float: ...


class _ConfirmedPmStatus(TypedDict):
    output: bool


def _is_object_dict(value: object) -> TypeGuard[dict[object, object]]:
    return isinstance(value, dict)


def _is_string_object_dict(value: object) -> TypeGuard[dict[str, object]]:
    return _is_object_dict(value) and all(isinstance(key, str) for key in value)


def _is_confirmed_pm_status(value: object) -> TypeGuard[_ConfirmedPmStatus]:
    return _is_string_object_dict(value) and isinstance(value.get("output"), bool)


class OfflinePublisher:
    FUTURE_INPUT_TIMESTAMP_TOLERANCE_SECONDS: ClassVar[float] = 1.0

    def __init__(
        self,
        service: OfflineService,
        telemetry: RelayTelemetry,
        virtual_state: VirtualStatePublisher,
    ) -> None:
        self.service = service
        self._telemetry = telemetry
        self._virtual_state = virtual_state

    @staticmethod
    def _offline_health_reason(svc: OfflineService) -> str:
        """Return the health reason used for one offline publish."""
        configured = getattr(svc, "topology_configured", getattr(svc, "host_configured", True))
        return "shelly-offline" if bool(configured) else "not-configured"

    @staticmethod
    def _offline_confirmed_relay_max_age_seconds(svc: OfflineService) -> float:
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
    def _offline_confirmed_relay_state(cls, svc: OfflineService, now: float) -> bool:
        """Return the last fresh confirmed relay state, defaulting to OFF when unknown."""
        sample = cls._offline_confirmed_pm_sample(svc)
        if sample is None:
            return False
        pm_status, captured_at = sample
        if not cls._offline_confirmed_relay_sample_fresh(svc, now, captured_at):
            return False
        return pm_status["output"]

    @classmethod
    def _offline_confirmed_pm_sample(
        cls,
        svc: OfflineService,
    ) -> tuple[_ConfirmedPmStatus, float] | None:
        """Return one structurally valid confirmed PM sample."""
        pm_status = getattr(svc, "_last_confirmed_pm_status", None)
        captured_at = cls._offline_confirmed_pm_status_timestamp(
            getattr(svc, "_last_confirmed_pm_status_at", None)
        )
        if (
            not _is_confirmed_pm_status(pm_status)
            or captured_at is None
        ):
            return None
        return pm_status, captured_at

    @classmethod
    def _offline_confirmed_relay_sample_fresh(
        cls,
        svc: OfflineService,
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
        phase_data = self._telemetry._phase_data_for_pm_status(svc, offline_pm_status, power, voltage)
        svc.auto.set_health(self._offline_health_reason(svc), cached=False)
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
            svc.state.bump_update_index(now)
        completed_at = self._mark_offline_update_completed(svc)
        svc.state.publish_companion_bridge(completed_at)
        return True

    @staticmethod
    def _mark_offline_update_completed(svc: OfflineService) -> float:
        """Mark one offline publish as a completed, watchdog-fresh update cycle."""
        completed_at = float(svc.time_now())
        svc._last_successful_update_at = completed_at
        svc._last_recovery_attempt_at = None
        svc.last_update = completed_at
        return completed_at

    @staticmethod
    def _offline_voltage(svc: OfflineService) -> float:
        """Return the fallback voltage used for one offline publish."""
        last_voltage = svc._last_voltage
        return 230.0 if last_voltage is None or last_voltage == 0.0 else float(last_voltage)

    @staticmethod
    def _offline_confirmed_pm_status_timestamp(raw_timestamp: object) -> float | None:
        """Return the numeric timestamp of the last confirmed PM status when valid."""
        if not isinstance(raw_timestamp, (int, float)) or isinstance(raw_timestamp, bool):
            return None
        return float(raw_timestamp)

    @classmethod
    def _fresh_offline_pm_status(cls, svc: OfflineService, now: float) -> dict[str, object] | None:
        """Return the last confirmed PM status only while it stays fresh for offline use."""
        sample = cls._offline_confirmed_pm_sample(svc)
        if sample is None:
            return None
        offline_pm_status, offline_pm_status_at = sample
        if not cls._offline_confirmed_relay_sample_fresh(svc, now, offline_pm_status_at):
            return None
        confirmed_status: dict[str, object] = dict(offline_pm_status)
        return confirmed_status

    @staticmethod
    def _offline_power_state() -> tuple[float, float, int]:
        """Return the fixed power/energy/status tuple used for offline publishing."""
        return 0.0, 0.0, 0

    @staticmethod
    def _mark_offline_status_state(svc: OfflineService) -> None:
        """Mark service observability fields for one offline Shelly publish."""
        svc._last_status_source = OfflinePublisher._offline_health_reason(svc)
        svc._last_charger_fault_active = 0

    def _publish_offline_live_state(
        self,
        svc: OfflineService,
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
        measurements_changed = bool(
            svc.state.publish_live_measurements(power, voltage, total_current, phase_data, now)
        )
        virtual_state_changed = bool(self._virtual_state.update_virtual_state(status, energy_forward, relay_on))
        return measurements_changed or virtual_state_changed

    @staticmethod
    def _total_phase_current(phase_data: dict[str, dict[str, float]]) -> float:
        return sum(phase_data[phase_name]["current"] for phase_name in ("L1", "L2", "L3"))


__all__ = ["OfflinePublisher", "OfflineService"]

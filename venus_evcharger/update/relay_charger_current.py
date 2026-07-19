# SPDX-License-Identifier: GPL-3.0-or-later
"""Apply charger current and enable targets through explicit collaborators."""

from __future__ import annotations

from typing import Protocol

from venus_evcharger.backend.modbus_transport import modbus_transport_issue_reason
from venus_evcharger.core.contracts import finite_float_or_none
from venus_evcharger.update.relay_charger_current_targets import ChargerCurrentTargetPolicy
from venus_evcharger.update.relay_charger_readback import ChargerBackendAccess, ChargerCurrentBackend
from venus_evcharger.update.relay_charger_transport import ChargerTransportTracker


CHARGER_CURRENT_APPLY_ERRORS = (OSError, RuntimeError, TypeError, ValueError)


class ChargerRuntimePort(Protocol):
    def mark_failure(self, source_key: str) -> None: ...
    def mark_recovery(self, source_key: str, message: str, *args: object) -> None: ...
    def queue_relay_command(self, relay_on: bool, current_time: float) -> object: ...
    def warning_throttled(
        self,
        warning_key: str,
        interval_seconds: float,
        warning_message: str,
        *args: object,
        **kwargs: object,
    ) -> None: ...


class ChargerControlService(Protocol):
    @property
    def runtime(self) -> ChargerRuntimePort: ...

    auto_shelly_soft_fail_seconds: float
    _charger_target_current_amps: float | None
    _charger_target_current_applied_at: float | None


class ChargerTargetController:
    """Derive and apply charger current targets from learned and scheduled policy."""

    def __init__(
        self,
        backends: ChargerBackendAccess,
        targets: ChargerCurrentTargetPolicy,
        transport: ChargerTransportTracker,
    ) -> None:
        self._backends = backends
        self._targets = targets
        self._transport = transport

    def apply_current_target(
        self,
        svc: ChargerControlService,
        desired_relay: bool,
        now: float,
        auto_mode_active: bool,
    ) -> float | None:
        backend = self._backends.current_backend(svc)
        if backend is None:
            return None
        if self.current_reset_needed(desired_relay, auto_mode_active):
            self.reset_current_target(svc)
            return None

        target_amps = self._targets.current_target(svc, desired_relay, now, auto_mode_active)
        if target_amps is None:
            return None

        last_target = finite_float_or_none(getattr(svc, "_charger_target_current_amps", None))
        if self.target_unchanged(last_target, target_amps):
            return self.known_current_target(last_target)
        return self._apply_new_current_target(svc, backend, target_amps, now, last_target)

    def _apply_new_current_target(
        self,
        svc: ChargerControlService,
        backend: ChargerCurrentBackend,
        target_amps: float,
        now: float,
        last_target: float | None,
    ) -> float | None:
        if self._transport.retry_active(svc, now):
            return last_target
        try:
            backend.set_current(float(target_amps))
        except CHARGER_CURRENT_APPLY_ERRORS as error:
            self._handle_current_target_failure(svc, error, now)
            return last_target
        self._transport.clear_issue(svc)
        self._transport.clear_retry(svc)
        return self.remember_current_target(svc, target_amps, now)

    @staticmethod
    def current_reset_needed(desired_relay: bool, auto_mode_active: bool) -> bool:
        return not auto_mode_active or not bool(desired_relay)

    @staticmethod
    def reset_current_target(svc: ChargerControlService) -> None:
        svc._charger_target_current_amps = None
        svc._charger_target_current_applied_at = None

    @staticmethod
    def target_unchanged(last_target: float | None, target_amps: float) -> bool:
        return last_target is not None and abs(last_target - target_amps) < 0.01

    def _handle_current_target_failure(
        self,
        svc: ChargerControlService,
        error: Exception,
        now: float | None = None,
    ) -> None:
        transport_reason = modbus_transport_issue_reason(error)
        if transport_reason is not None:
            self._transport.remember_issue(svc, transport_reason, "current", error, now)
            self._transport.remember_retry(svc, transport_reason, "current", now)
        svc.runtime.mark_failure("charger")
        svc.runtime.warning_throttled(
            "charger-current-failed",
            svc.auto_shelly_soft_fail_seconds,
            "Charger current request failed: %s",
            error,
            exc_info=error,
        )

    @staticmethod
    def remember_current_target(svc: ChargerControlService, target_amps: float, now: float) -> float:
        svc._charger_target_current_amps = float(target_amps)
        svc._charger_target_current_applied_at = float(now)
        svc.runtime.mark_recovery("charger", "Charger current writes recovered")
        return float(target_amps)

    def apply_enabled_target(self, svc: ChargerControlService, enabled: bool, now: float) -> bool:
        backend = self._backends.enable_backend(svc)
        if backend is not None:
            if self._transport.retry_active(svc, now):
                return False
            backend.set_enabled(bool(enabled))
            self._transport.clear_issue(svc)
            self._transport.clear_retry(svc)
            svc.runtime.mark_recovery("charger", "Charger enable writes recovered")
            return True
        svc.runtime.queue_relay_command(bool(enabled), now)
        return True

    @staticmethod
    def known_current_target(last_target: float | None) -> float:
        if last_target is None:
            raise TypeError("last charger current target must be available when unchanged")
        return float(last_target)


__all__ = ["ChargerControlService", "ChargerTargetController"]

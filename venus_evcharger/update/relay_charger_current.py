# SPDX-License-Identifier: GPL-3.0-or-later
"""Native charger-current and enable-target helpers for the update cycle."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from venus_evcharger.backend.modbus_transport import modbus_transport_issue_reason
from venus_evcharger.core.contracts import finite_float_or_none
from venus_evcharger.update.relay_charger_current_targets import _RelayChargerCurrentTargets

if TYPE_CHECKING:
    from venus_evcharger.update.relay_charger_readback import ChargerCurrentBackend


CHARGER_CURRENT_APPLY_ERRORS = (OSError, RuntimeError, TypeError, ValueError)


class _RelayChargerCurrent(_RelayChargerCurrentTargets):
    """Derive and apply charger current targets from learned and scheduled policy."""

    if TYPE_CHECKING:  # pragma: no cover

        @staticmethod
        def _charger_current_backend(svc: Any) -> ChargerCurrentBackend | None: ...

        @staticmethod
        def _charger_enable_backend(svc: Any) -> Any | None: ...

        @classmethod
        def _charger_retry_active(cls, svc: Any, now: float | None = None) -> bool: ...

        @classmethod
        def _clear_charger_transport_issue(cls, svc: Any) -> None: ...

        @classmethod
        def _clear_charger_retry(cls, svc: Any) -> None: ...

        @classmethod
        def _remember_charger_transport_issue(
            cls,
            svc: Any,
            reason: str,
            source: str,
            error: BaseException,
            now: float | None = None,
        ) -> None: ...

        @classmethod
        def _remember_charger_retry(
            cls,
            svc: Any,
            reason: str,
            source: str,
            now: float | None = None,
        ) -> None: ...

    @classmethod
    def _contactor_heuristic_delay_seconds(cls, svc: Any) -> float:
        return max(0.0, float(getattr(svc, "auto_shelly_soft_fail_seconds", 0.0)))

    @classmethod
    def _contactor_lockout_threshold(cls, svc: Any) -> int:
        return max(0, int(getattr(svc, "auto_contactor_fault_latch_count", 3)))

    @classmethod
    def _contactor_lockout_persistence_seconds(cls, svc: Any) -> float:
        return max(0.0, float(getattr(svc, "auto_contactor_fault_latch_seconds", 60.0)))

    @classmethod
    def _contactor_power_threshold_w(cls, svc: Any) -> float:
        configured = finite_float_or_none(getattr(svc, "charging_threshold_watts", None))
        if configured is None:
            return 100.0
        return max(100.0, float(configured))

    @classmethod
    def _contactor_current_threshold_a(cls, svc: Any) -> float:
        configured = finite_float_or_none(getattr(svc, "min_current", None))
        if configured is None:
            return 1.0
        return max(1.0, float(configured) / 4.0)

    @classmethod
    def apply_charger_current_target(
        cls,
        svc: Any,
        desired_relay: bool,
        now: float,
        auto_mode_active: bool,
    ) -> float | None:
        backend = cls._charger_current_backend(svc)
        if backend is None:
            return None
        if cls._charger_current_reset_needed(desired_relay, auto_mode_active):
            cls._reset_charger_current_target(svc)
            return None

        target_amps = cls._charger_current_target_amps(svc, desired_relay, now, auto_mode_active)
        if target_amps is None:
            return None

        last_target = finite_float_or_none(getattr(svc, "_charger_target_current_amps", None))
        if cls._charger_target_unchanged(last_target, target_amps):
            return cls._known_charger_current_target(last_target)
        return cls._apply_new_charger_current_target(svc, backend, target_amps, now, last_target)

    @classmethod
    def _apply_new_charger_current_target(
        cls,
        svc: Any,
        backend: ChargerCurrentBackend,
        target_amps: float,
        now: float,
        last_target: float | None,
    ) -> float | None:
        if cls._charger_retry_active(svc, now):
            return last_target
        try:
            backend.set_current(float(target_amps))
        except CHARGER_CURRENT_APPLY_ERRORS as error:
            cls._handle_charger_current_target_failure(svc, error, now)
            return last_target
        cls._clear_charger_transport_issue(svc)
        cls._clear_charger_retry(svc)
        return cls._remember_charger_current_target(svc, target_amps, now)

    @staticmethod
    def _charger_current_reset_needed(desired_relay: bool, auto_mode_active: bool) -> bool:
        return not auto_mode_active or not bool(desired_relay)

    @staticmethod
    def _reset_charger_current_target(svc: Any) -> None:
        svc._charger_target_current_amps = None
        svc._charger_target_current_applied_at = None

    @staticmethod
    def _charger_target_unchanged(last_target: float | None, target_amps: float) -> bool:
        return last_target is not None and abs(last_target - target_amps) < 0.01

    @classmethod
    def _handle_charger_current_target_failure(cls, svc: Any, error: Exception, now: float | None = None) -> None:
        transport_reason = modbus_transport_issue_reason(error)
        if transport_reason is not None:
            cls._remember_charger_transport_issue(svc, transport_reason, "current", error, now)
            cls._remember_charger_retry(svc, transport_reason, "current", now)
        svc._mark_failure("charger")
        svc._warning_throttled(
            "charger-current-failed",
            svc.auto_shelly_soft_fail_seconds,
            "Charger current request failed: %s",
            error,
            exc_info=error,
        )

    @staticmethod
    def _remember_charger_current_target(svc: Any, target_amps: float, now: float) -> float:
        svc._charger_target_current_amps = float(target_amps)
        svc._charger_target_current_applied_at = float(now)
        svc._mark_recovery("charger", "Charger current writes recovered")
        return float(target_amps)

    @classmethod
    def _apply_enabled_target(cls, svc: Any, enabled: bool, now: float) -> bool:
        backend = cls._charger_enable_backend(svc)
        if backend is not None:
            if cls._charger_retry_active(svc, now):
                return False
            backend.set_enabled(bool(enabled))
            cls._clear_charger_transport_issue(svc)
            cls._clear_charger_retry(svc)
            svc._mark_recovery("charger", "Charger enable writes recovered")
            return True
        svc._queue_relay_command(bool(enabled), now)
        return True

    @staticmethod
    def _known_charger_current_target(last_target: float | None) -> float:
        if last_target is None:
            raise TypeError("last charger current target must be available when unchanged")
        return float(last_target)

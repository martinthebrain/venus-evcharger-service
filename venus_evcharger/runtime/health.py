# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime, worker-state, and watchdog helpers for the Venus EV charger service.

This controller owns the "glue" state that keeps the service robust in the
field: cached worker snapshots, throttled warnings, watchdog recovery,
auto-audit logging, and safe persistence of runtime-only state.
"""

from __future__ import annotations

import faulthandler
import logging
import os
import time
from collections.abc import Callable
from typing import Any, TYPE_CHECKING

from venus_evcharger.runtime.audit import _RuntimeAudit

WATCHDOG_TRACEBACK_DUMP_ERRORS = (OSError, RuntimeError, TypeError, ValueError)

class _RuntimeHealth(_RuntimeAudit):
    if TYPE_CHECKING:  # pragma: no cover
        _age_seconds: Callable[[float | int | None, float | int | None], int]

    @staticmethod
    def _float_attr(value: Any, default: float = 0.0) -> float:
        """Return one runtime attribute as float with a safe fallback."""
        return float(value) if isinstance(value, (int, float)) else float(default)

    def is_update_stale(self, now: float | None = None) -> bool:
        """Return True when no successful full update was completed recently."""
        svc = self.service
        svc._ensure_observability_state()
        current = time.time() if now is None else float(now)
        stale_seconds = self._float_attr(getattr(svc, "auto_watchdog_stale_seconds", 0.0))
        if stale_seconds <= 0:
            return False
        last_successful_update_at = getattr(svc, "_last_successful_update_at", None)
        if not isinstance(last_successful_update_at, (int, float)):
            return (current - self._float_attr(getattr(svc, "started_at", 0.0))) > stale_seconds
        return (current - float(last_successful_update_at)) > stale_seconds

    @staticmethod
    def _watchdog_base_timestamp(svc: Any) -> float:
        """Return the timestamp used as the stale-age origin."""
        last_successful_update_at = getattr(svc, "_last_successful_update_at", None)
        if isinstance(last_successful_update_at, (int, float)):
            return float(last_successful_update_at)
        return _RuntimeHealth._float_attr(getattr(svc, "started_at", 0.0))

    @staticmethod
    def _watchdog_recovery_suppressed(svc: Any, now: float) -> bool:
        """Return whether watchdog recovery is currently rate-limited."""
        recovery_seconds = _RuntimeHealth._float_attr(
            getattr(svc, "auto_watchdog_recovery_seconds", 0.0)
        )
        last_recovery_attempt_at = getattr(svc, "_last_recovery_attempt_at", None)
        if recovery_seconds <= 0 and isinstance(last_recovery_attempt_at, (int, float)):
            return True
        return isinstance(last_recovery_attempt_at, (int, float)) and (
            now - float(last_recovery_attempt_at)
        ) < recovery_seconds

    @staticmethod
    def _perform_watchdog_reset(svc: Any) -> None:
        """Reset lightweight in-memory discovery state during watchdog recovery."""
        svc._reset_system_bus()
        svc._invalidate_auto_pv_services()
        svc._invalidate_auto_battery_service()
        svc._dbus_list_backoff_until = 0.0

    @staticmethod
    def _watchdog_restart_attempts(svc: Any) -> int:
        """Return how many recovery attempts are allowed before process restart."""
        value = getattr(svc, "auto_watchdog_restart_attempts", 0)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return 0
        return max(0, int(value))

    @staticmethod
    def _watchdog_restart_enabled_for_topology(svc: Any) -> bool:
        """Return whether a configured load topology may use runit restart escalation."""
        return bool(getattr(svc, "topology_configured", getattr(svc, "host_configured", False)))

    @classmethod
    def _watchdog_restart_due(cls, svc: Any) -> bool:
        """Return whether repeated stale recoveries should restart the service process."""
        restart_attempts = cls._watchdog_restart_attempts(svc)
        if restart_attempts <= 0:
            return False
        if not cls._watchdog_restart_enabled_for_topology(svc):
            return False
        return int(getattr(svc, "_recovery_attempts", 0)) >= restart_attempts

    @staticmethod
    def _exit_for_watchdog_restart() -> None:
        """Exit immediately so the runit supervisor starts a clean service process."""
        os._exit(75)

    def _restart_process_after_stale_watchdog(self, svc: Any, now: float) -> None:
        """Dump stacks and let the process supervisor recover a persistently stale service."""
        logging.critical(
            "Watchdog restart after %s stale recovery attempts over %ss (%s)",
            svc._recovery_attempts,
            self._age_seconds(self._watchdog_base_timestamp(svc), now),
            svc._state_summary(),
        )
        try:
            faulthandler.dump_traceback(all_threads=True)
        except WATCHDOG_TRACEBACK_DUMP_ERRORS as error:
            logging.debug("Unable to dump watchdog traceback before restart: %s", error)
        self._exit_for_watchdog_restart()

    def watchdog_recover(self, now: float) -> None:
        """Perform low-risk in-memory recovery steps after prolonged stale periods."""
        svc = self.service
        if not svc._is_update_stale(now):
            return
        if self._watchdog_recovery_suppressed(svc, now):
            return

        svc._last_recovery_attempt_at = now
        svc._recovery_attempts += 1
        self._perform_watchdog_reset(svc)
        logging.warning(
            "Watchdog recovery attempt %s after stale update period of %ss (%s)",
            svc._recovery_attempts,
            self._age_seconds(self._watchdog_base_timestamp(svc), now),
            svc._state_summary(),
        )
        if self._watchdog_restart_due(svc):
            self._restart_process_after_stale_watchdog(svc, now)

    def warning_throttled(
        self,
        key: str,
        interval_seconds: float,
        message: str,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Log a warning at most once per interval for the given key."""
        svc = self.service
        svc._ensure_observability_state()
        now = time.time()
        last_logged = svc._warning_state.get(key)
        if last_logged is None or (now - last_logged) > interval_seconds:
            logging.warning(message, *args, **kwargs)
            svc._warning_state[key] = now

    def mark_failure(self, key: str) -> None:
        """Track a failure streak for counters and later recovery logs."""
        svc = self.service
        svc._ensure_observability_state()
        if key in svc._error_state:
            svc._error_state[key] += 1
        if key in svc._failure_active:
            svc._failure_active[key] = True

    def mark_recovery(self, key: str, message: str, *args: Any) -> None:
        """Emit a one-time recovery log when a failing source becomes healthy again."""
        svc = self.service
        svc._ensure_observability_state()
        if svc._failure_active.get(key):
            logging.info(message, *args)
            svc._failure_active[key] = False
        svc._source_retry_after[key] = 0.0

    def source_retry_ready(self, key: str, now: float | None = None) -> bool:
        """Return True when a data source may be queried again."""
        svc = self.service
        svc._ensure_observability_state()
        current = time.time() if now is None else float(now)
        return current >= float(svc._source_retry_after.get(key, 0.0))

    def source_retry_remaining(self, key: str, now: float | None = None) -> int:
        """Return how many whole seconds remain before a source may be retried."""
        svc = self.service
        svc._ensure_observability_state()
        current = time.time() if now is None else float(now)
        retry_after = float(svc._source_retry_after.get(key, 0.0))
        return max(0, int(retry_after - current)) if retry_after > current else 0

    def delay_source_retry(self, key: str, now: float | None = None, delay_seconds: float | None = None) -> None:
        """Delay repeated retries for a failing data source to keep the main loop responsive."""
        svc = self.service
        svc._ensure_observability_state()
        current = time.time() if now is None else float(now)
        default_delay = max(1.0, float(getattr(svc, "auto_dbus_backoff_base_seconds", 5.0)))
        delay = default_delay if delay_seconds is None else max(0.0, float(delay_seconds))
        svc._source_retry_after[key] = current + delay


__all__ = ["_RuntimeHealth"]

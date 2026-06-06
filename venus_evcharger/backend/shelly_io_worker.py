# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code="attr-defined"
# pyright: reportAttributeAccessIssue=false
"""Relay queue and worker-loop helpers for Shelly I/O support."""

from __future__ import annotations

import json
import threading
from typing import Any, cast

import requests
from requests import exceptions as requests_exceptions

from venus_evcharger.backend.modbus_transport import modbus_transport_issue_reason
from venus_evcharger.backend.shelly_io_types import PendingRelayCommand, ShellyEnergyData, ShellyPmStatus

_SHELLY_TRANSPORT_ERROR_REASONS = frozenset(
    {
        "connect-timeout",
        "read-timeout",
        "timeout",
        "no-route",
        "connection-refused",
        "connection-error",
    }
)
_SHELLY_JSON_ERROR_TYPES = tuple(
    error_type
    for error_type in (
        getattr(requests_exceptions, "JSONDecodeError", None),
        json.JSONDecodeError,
    )
    if isinstance(error_type, type)
)


class ShellyIoWorkerMixin:
    """Handle optimistic PM publishing, queued relay writes, and the worker loop."""

    @staticmethod
    def _normalized_energy_payload(value: object) -> ShellyEnergyData:
        payload: ShellyEnergyData = {}
        if isinstance(value, dict):
            total = value.get("total")
            if isinstance(total, (int, float)) and not isinstance(total, bool):
                payload["total"] = float(total)
        payload.setdefault("total", 0.0)
        return payload

    def build_local_pm_status(self, relay_on: bool) -> ShellyPmStatus:
        svc = self.service
        source = getattr(svc, "_last_pm_status", None)
        raw_status = dict(source) if isinstance(source, dict) else {}
        pm_status = cast(ShellyPmStatus, raw_status)
        last_voltage = getattr(svc, "_last_voltage", None)
        voltage = (
            float(last_voltage)
            if isinstance(last_voltage, (int, float)) and not isinstance(last_voltage, bool)
            else 230.0
        )
        pm_status["output"] = bool(relay_on)
        pm_status["voltage"] = float(pm_status.get("voltage", voltage) or voltage)
        pm_status["aenergy"] = self._normalized_energy_payload(pm_status.get("aenergy"))
        pm_status["apower"] = 0.0
        pm_status["current"] = 0.0
        return pm_status

    def publish_local_pm_status(self, relay_on: bool, now: float | None = None) -> ShellyPmStatus:
        svc = self.service
        current = svc._time_now() if now is None else float(now)
        pm_status = svc._build_local_pm_status(relay_on)
        pm_status["_pm_confirmed"] = False
        svc._last_pm_status = dict(pm_status)
        svc._last_pm_status_at = current
        svc._last_pm_status_confirmed = False
        svc._update_worker_snapshot(
            captured_at=current,
            pm_captured_at=current,
            pm_status=pm_status,
            pm_confirmed=False,
        )
        return cast(ShellyPmStatus, pm_status)

    def queue_relay_command(self, relay_on: bool, now: float | None = None) -> None:
        svc = self.service
        svc._ensure_worker_state()
        current = svc._time_now() if now is None else float(now)
        self._warn_if_direct_switching_under_load(bool(relay_on))
        with svc._relay_command_lock:
            svc._pending_relay_state = bool(relay_on)
            svc._pending_relay_requested_at = current
            svc._relay_sync_expected_state = bool(relay_on)
            svc._relay_sync_requested_at = current
            svc._relay_sync_deadline_at = current + float(getattr(svc, "relay_sync_timeout_seconds", 2.0))
            svc._relay_sync_failure_reported = False

    def peek_pending_relay_command(self) -> PendingRelayCommand:
        svc = self.service
        svc._ensure_worker_state()
        with svc._relay_command_lock:
            return svc._pending_relay_state, svc._pending_relay_requested_at

    def clear_pending_relay_command(self, relay_on: bool) -> None:
        svc = self.service
        svc._ensure_worker_state()
        with svc._relay_command_lock:
            if svc._pending_relay_state == bool(relay_on):
                svc._pending_relay_state = None
                svc._pending_relay_requested_at = None

    def worker_apply_pending_relay_command(self) -> None:
        command_context = self._pending_relay_command_context()
        if command_context is None:
            return
        svc, target_on, source_key, source_label, current = command_context
        try:
            self._apply_pending_relay_target(svc, bool(target_on))
        except Exception as error:
            self._handle_pending_relay_command_error(svc, source_key, source_label, current, error)
            return
        self._finalize_pending_relay_command(svc, bool(target_on), source_key, source_label)

    def _pending_relay_command_context(self) -> tuple[Any, bool, str, str, float] | None:
        svc = self.service
        target_on, _requested_at = svc._peek_pending_relay_command()
        if target_on is None:
            return None
        source_key = self._split_enable_source_key()
        current = self._runtime_now()
        if source_key == "charger" and self._charger_retry_active(current):
            return None
        if source_key == "shelly" and self._shelly_retry_active(current):
            return None
        return svc, bool(target_on), source_key, self._split_enable_source_label(), current

    def _apply_pending_relay_target(self, svc: Any, target_on: bool) -> None:
        backend = self._split_enable_backend()
        if backend is not None:
            cast(Any, backend).set_enabled(bool(target_on))
            return
        svc._rpc_call_with_session(
            svc._worker_session,
            "Switch.Set",
            id=svc.pm_id,
            on=bool(target_on),
        )

    def _handle_pending_relay_command_error(
        self,
        svc: Any,
        source_key: str,
        source_label: str,
        current: float,
        error: BaseException,
    ) -> None:
        if source_key == "charger":
            transport_reason = modbus_transport_issue_reason(error)
            if transport_reason is not None:
                self._remember_charger_transport_issue(transport_reason, "enable", error, current)
                self._remember_charger_retry(transport_reason, "enable", current)
        elif source_key == "shelly":
            reason = self._classify_shelly_error(error)
            self._remember_shelly_failure(reason, "relay", error, current)
        svc._mark_failure(source_key)
        exc_info = error if source_key != "shelly" or not self._is_shelly_common_network_error(error) else None
        svc._warning_throttled(
            f"worker-{source_key}-switch-failed-{self._classify_shelly_error(error) if source_key == 'shelly' else 'error'}",
            svc.auto_shelly_soft_fail_seconds,
            "%s switch failed (%s, consecutive=%s, retry=%ss): %s",
            source_label,
            self._classify_shelly_error(error) if source_key == "shelly" else "error",
            int(getattr(svc, "_shelly_consecutive_errors", 0)) if source_key == "shelly" else 0,
            svc._source_retry_remaining("shelly", current) if source_key == "shelly" and hasattr(svc, "_source_retry_remaining") else 0,
            error,
            exc_info=exc_info,
        )

    def _finalize_pending_relay_command(
        self,
        svc: Any,
        target_on: bool,
        source_key: str,
        source_label: str,
    ) -> None:
        completed_at = svc._time_now()
        svc._clear_pending_relay_command(bool(target_on))
        svc._mark_relay_changed(bool(target_on), completed_at)
        if source_key == "shelly":
            self._remember_shelly_success(svc._time_now(), "Shelly relay writes recovered")
        else:
            self._clear_charger_transport_issue()
            self._clear_charger_retry()
            svc._mark_recovery(source_key, "%s writes recovered", source_label)
        svc._publish_local_pm_status(bool(target_on), completed_at)

    def io_worker_once(self) -> None:
        svc = self.service
        svc._ensure_worker_state()
        now = svc._time_now()
        svc._update_worker_snapshot(
            captured_at=now,
            auto_mode_active=svc._mode_uses_auto_logic(getattr(svc, "virtual_mode", 0)),
        )
        svc._worker_apply_pending_relay_command()

        if self._shelly_retry_active(now):
            svc._update_worker_snapshot(
                captured_at=now,
                auto_mode_active=svc._mode_uses_auto_logic(getattr(svc, "virtual_mode", 0)),
                pm_status=None,
                pm_captured_at=None,
                pm_confirmed=False,
            )
            return

        try:
            pm_status = svc._worker_fetch_pm_status()
            read_at = svc._time_now()
            self._remember_shelly_success(read_at, "Shelly status reads recovered")
            svc._update_worker_snapshot(
                captured_at=read_at,
                pm_captured_at=read_at,
                auto_mode_active=svc._mode_uses_auto_logic(getattr(svc, "virtual_mode", 0)),
                pm_status=pm_status,
                pm_confirmed=True,
            )
        except Exception as error:
            reason = self._classify_shelly_error(error)
            self._remember_shelly_failure(reason, "read", error, now)
            svc._mark_failure("shelly")
            exc_info = None if self._is_shelly_common_network_error(error) else error
            svc._warning_throttled(
                f"worker-shelly-read-failed-{reason}",
                svc.auto_shelly_soft_fail_seconds,
                "Shelly status read failed (%s, consecutive=%s, retry=%ss): %s",
                reason,
                int(getattr(svc, "_shelly_consecutive_errors", 0)),
                svc._source_retry_remaining("shelly", now) if hasattr(svc, "_source_retry_remaining") else 0,
                error,
                exc_info=exc_info,
            )
            svc._update_worker_snapshot(
                captured_at=now,
                auto_mode_active=svc._mode_uses_auto_logic(getattr(svc, "virtual_mode", 0)),
                pm_status=None,
                pm_captured_at=None,
                pm_confirmed=False,
            )

    def _shelly_retry_active(self, now: float) -> bool:
        svc = self.service
        source_retry_ready = getattr(svc, "_source_retry_ready", None)
        if callable(source_retry_ready):
            return not bool(source_retry_ready("shelly", now))
        retry_after = self._shelly_retry_after_value(svc)
        return retry_after > float(now)

    @staticmethod
    def _shelly_retry_after_value(svc: Any) -> float:
        retry_after = getattr(svc, "_shelly_retry_after", 0.0)
        if isinstance(retry_after, (int, float)) and not isinstance(retry_after, bool):
            return float(retry_after)
        source_retry_after = getattr(svc, "_source_retry_after", None)
        if isinstance(source_retry_after, dict):
            candidate = source_retry_after.get("shelly", 0.0)
            if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
                return float(candidate)
        return 0.0

    @staticmethod
    def _classify_shelly_error(error: BaseException) -> str:
        if isinstance(error, requests_exceptions.ConnectTimeout):
            return "connect-timeout"
        if isinstance(error, requests_exceptions.ReadTimeout):
            return "read-timeout"
        if isinstance(error, requests_exceptions.Timeout):
            return "timeout"
        if isinstance(error, requests_exceptions.HTTPError):
            response = getattr(error, "response", None)
            status_code = getattr(response, "status_code", None)
            if status_code in (401, 403):
                return "auth-error"
            return "http-error"
        if isinstance(error, requests_exceptions.ConnectionError):
            text = str(error).lower()
            if "no route to host" in text:
                return "no-route"
            if "connection refused" in text:
                return "connection-refused"
            return "connection-error"
        if isinstance(error, _SHELLY_JSON_ERROR_TYPES):
            return "bad-json"
        return "error"

    @classmethod
    def _is_shelly_transport_error_reason(cls, reason: str) -> bool:
        return reason in _SHELLY_TRANSPORT_ERROR_REASONS

    @classmethod
    def _is_shelly_common_network_error(cls, error: BaseException) -> bool:
        return cls._is_shelly_transport_error_reason(cls._classify_shelly_error(error))

    @staticmethod
    def _shelly_retry_delay_seconds(reason: str, consecutive_errors: int) -> float:
        if reason == "auth-error":
            return 60.0
        if reason == "bad-json":
            return min(15.0, max(1.0, float(consecutive_errors)))
        base = min(30.0, float(2 ** max(0, min(4, int(consecutive_errors) - 1))))
        if reason == "no-route":
            return max(30.0, base)
        if reason == "connection-refused":
            return max(10.0, base)
        if reason in ("connect-timeout", "read-timeout", "timeout", "connection-error"):
            return max(2.0, base)
        if reason == "http-error":
            return max(5.0, base)
        return max(1.0, base)

    def _remember_shelly_failure(
        self,
        reason: str,
        source: str,
        error: BaseException,
        now: float,
    ) -> None:
        svc = self.service
        previous_errors = getattr(svc, "_shelly_consecutive_errors", 0)
        try:
            consecutive_errors = int(previous_errors) + 1
        except (TypeError, ValueError):
            consecutive_errors = 1
        delay_seconds = self._shelly_retry_delay_seconds(reason, consecutive_errors)
        retry_after = float(now) + delay_seconds
        svc._shelly_state = "offline" if delay_seconds >= float(getattr(svc, "auto_shelly_soft_fail_seconds", 10.0)) else "degraded"
        svc._shelly_last_error_reason = str(reason)
        svc._shelly_last_error_detail = f"{source}: {error}"
        svc._shelly_last_error_at = float(now)
        svc._shelly_consecutive_errors = consecutive_errors
        svc._shelly_retry_after = retry_after
        if svc._shelly_state == "offline" and not isinstance(getattr(svc, "_shelly_offline_since", None), (int, float)):
            svc._shelly_offline_since = float(now)
        delay_source_retry = getattr(svc, "_delay_source_retry", None)
        if callable(delay_source_retry):
            delay_source_retry("shelly", now, delay_seconds)
        else:
            source_retry_after = getattr(svc, "_source_retry_after", None)
            if isinstance(source_retry_after, dict):
                source_retry_after["shelly"] = retry_after
        if self._is_shelly_transport_error_reason(reason):
            self._reset_shelly_worker_session()

    def _remember_shelly_success(self, now: float, recovery_message: str) -> None:
        svc = self.service
        svc._shelly_state = "online"
        svc._shelly_consecutive_errors = 0
        svc._shelly_last_ok_at = float(now)
        svc._shelly_retry_after = 0.0
        svc._shelly_offline_since = None
        source_retry_after = getattr(svc, "_source_retry_after", None)
        if isinstance(source_retry_after, dict):
            source_retry_after["shelly"] = 0.0
        svc._mark_recovery("shelly", recovery_message)

    def _reset_shelly_worker_session(self) -> None:
        svc = self.service
        self._close_object(getattr(svc, "_worker_session", None))
        svc._worker_session = requests.Session()
        self._reset_shelly_shared_session(svc)
        self._reset_shelly_backend_sessions(svc)
        try:
            svc._shelly_session_reset_count = int(getattr(svc, "_shelly_session_reset_count", 0)) + 1
        except (TypeError, ValueError):
            svc._shelly_session_reset_count = 1

    def _reset_shelly_shared_session(self, svc: Any) -> None:
        if not hasattr(svc, "session"):
            return
        self._close_object(getattr(svc, "session", None))
        svc.session = requests.Session()

    def _reset_shelly_backend_sessions(self, svc: Any) -> None:
        shared_session = getattr(svc, "session", None)
        for backend in self._shelly_transport_backends(svc):
            reset_transport_session = getattr(backend, "reset_transport_session", None)
            if callable(reset_transport_session):
                reset_transport_session(shared_session)

    @staticmethod
    def _shelly_transport_backends(svc: Any) -> tuple[Any, ...]:
        backends: list[Any] = []
        seen: set[int] = set()
        for attr_name in ("_meter_backend", "_switch_backend", "_charger_backend"):
            backend = getattr(svc, attr_name, None)
            if backend is None or id(backend) in seen:
                continue
            seen.add(id(backend))
            backends.append(backend)
        return tuple(backends)

    def io_worker_loop(self) -> None:
        svc = self.service
        svc._ensure_worker_state()
        stop_event = svc._worker_stop_event
        while not stop_event.is_set():
            cycle_started = svc._time_now()
            try:
                self.io_worker_once()
            except Exception as error:
                svc._warning_throttled(
                    "io-worker-cycle-failed",
                    max(1.0, svc._worker_poll_interval_seconds),
                    "Background I/O worker cycle failed: %s",
                    error,
                    exc_info=error,
                )

            wait_seconds = max(0.05, svc._worker_poll_interval_seconds - (svc._time_now() - cycle_started))
            if stop_event.wait(wait_seconds):
                break

    @staticmethod
    def _worker_stale_restart_seconds(svc: Any) -> float:
        poll_seconds = max(0.2, float(getattr(svc, "_worker_poll_interval_seconds", 1.0) or 1.0))
        request_timeout = max(0.0, float(getattr(svc, "shelly_request_timeout_seconds", 2.0) or 2.0))
        relay_sync_timeout = max(0.0, float(getattr(svc, "relay_sync_timeout_seconds", 2.0) or 2.0))
        return max(5.0, poll_seconds * 5.0, request_timeout * 3.0, relay_sync_timeout * 2.0)

    @staticmethod
    def _worker_snapshot_captured_at(svc: Any) -> float | None:
        snapshot = ShellyIoWorkerMixin._worker_snapshot_payload(svc)
        if snapshot is None:
            return None
        return ShellyIoWorkerMixin._worker_snapshot_number(snapshot, "captured_at")

    @staticmethod
    def _worker_snapshot_payload(svc: Any) -> dict[str, Any] | None:
        get_snapshot = getattr(svc, "_get_worker_snapshot", None)
        if not callable(get_snapshot):
            return None
        try:
            snapshot = get_snapshot()
        except Exception:
            return None
        if not isinstance(snapshot, dict):
            return None
        return cast(dict[str, Any], snapshot)

    @staticmethod
    def _worker_snapshot_number(snapshot: dict[str, Any], key: str) -> float | None:
        value = snapshot.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return None
        return float(value)

    @classmethod
    def _worker_snapshot_age(cls, svc: Any, now: float) -> float | None:
        captured_at = cls._worker_snapshot_captured_at(svc)
        if captured_at is None:
            return None
        return max(0.0, float(now) - captured_at)

    @classmethod
    def _worker_thread_stale(cls, svc: Any, now: float) -> bool:
        thread = getattr(svc, "_worker_thread", None)
        if thread is None or not thread.is_alive():
            return False
        snapshot_age = cls._worker_snapshot_age(svc, now)
        return snapshot_age is not None and snapshot_age > cls._worker_stale_restart_seconds(svc)

    def _restart_stale_io_worker(self, now: float) -> None:
        svc = self.service
        stale_after = self._worker_stale_restart_seconds(svc)
        snapshot_age = self._worker_snapshot_age(svc, now)
        svc._warning_throttled(
            "io-worker-stale-restart",
            stale_after,
            "Background I/O worker stale for %.1fs, restarting worker session",
            self._display_snapshot_age(snapshot_age),
        )
        self._set_object_event(getattr(svc, "_worker_stop_event", None))
        self._close_object(getattr(svc, "_worker_session", None))
        svc._worker_stop_event = threading.Event()
        svc._worker_session = requests.Session()
        svc._worker_thread = None

    @staticmethod
    def _display_snapshot_age(snapshot_age: float | None) -> float:
        return -1.0 if snapshot_age is None else float(snapshot_age)

    @staticmethod
    def _set_object_event(candidate: Any) -> None:
        if candidate is not None and hasattr(candidate, "set"):
            candidate.set()

    @staticmethod
    def _close_object(candidate: Any) -> None:
        if candidate is not None and hasattr(candidate, "close"):
            candidate.close()

    def start_io_worker(self) -> None:
        svc = self.service
        svc._ensure_worker_state()
        current = self._runtime_now()
        if self._worker_thread_stale(svc, current):
            self._restart_stale_io_worker(current)
        if svc._worker_thread is None or not svc._worker_thread.is_alive():
            svc._worker_thread = threading.Thread(
                target=self.io_worker_loop,
                name="shelly-wallbox-shelly-io",
                daemon=True,
            )
            svc._worker_thread.start()
        svc._ensure_auto_input_helper_process()

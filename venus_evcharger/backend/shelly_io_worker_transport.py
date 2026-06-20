# SPDX-License-Identifier: GPL-3.0-or-later
"""Shelly worker transport error classification, retry, and session reset helpers."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

import requests
from requests import exceptions as requests_exceptions

from venus_evcharger.backend.shelly_io_types import ShellyIoHost, _TransportSessionResetBackendLike

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
_SHELLY_TIMEOUT_REASONS: tuple[tuple[type[BaseException], str], ...] = (
    (requests_exceptions.ConnectTimeout, "connect-timeout"),
    (requests_exceptions.ReadTimeout, "read-timeout"),
    (requests_exceptions.Timeout, "timeout"),
)
_SHELLY_RETRY_MINIMUMS = {
    "no-route": 30.0,
    "connection-refused": 10.0,
    "connect-timeout": 2.0,
    "read-timeout": 2.0,
    "timeout": 2.0,
    "connection-error": 2.0,
    "http-error": 5.0,
}


class ShellyIoWorkerTransportMixin:
    """Classify Shelly transport failures and maintain RAM-only retry state."""

    if TYPE_CHECKING:
        service: ShellyIoHost

        @staticmethod
        def _close_object(candidate: object) -> None: ...

    def _shelly_retry_active(self, now: float) -> bool:
        svc = self.service
        source_retry_ready = getattr(svc, "_source_retry_ready", None)
        if callable(source_retry_ready):
            return not bool(source_retry_ready("shelly", now))
        retry_after = self._shelly_retry_after_value(svc)
        return retry_after > float(now)

    @staticmethod
    def _shelly_retry_after_value(svc: ShellyIoHost) -> float:
        retry_after = getattr(svc, "_shelly_retry_after", 0.0)
        if _shelly_numeric(retry_after):
            return float(retry_after)
        source_retry_after = getattr(svc, "_source_retry_after", None)
        return _shelly_source_retry_after_value(source_retry_after)

    @staticmethod
    def _classify_shelly_error(error: BaseException) -> str:
        for classifier in _SHELLY_ERROR_CLASSIFIERS:
            reason = classifier(error)
            if reason is not None:
                return reason
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
        return max(_SHELLY_RETRY_MINIMUMS.get(reason, 1.0), base)

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
        self._record_shelly_failure_state(svc, reason, source, error, now, consecutive_errors, delay_seconds, retry_after)
        self._record_shelly_retry_after(svc, now, delay_seconds, retry_after)
        if self._is_shelly_transport_error_reason(reason):
            self._reset_shelly_worker_session()

    def _record_shelly_failure_state(
        self,
        svc: ShellyIoHost,
        reason: str,
        source: str,
        error: BaseException,
        now: float,
        consecutive_errors: int,
        delay_seconds: float,
        retry_after: float,
    ) -> None:
        """Update in-memory Shelly failure diagnostics."""
        soft_fail_seconds = float(getattr(svc, "auto_shelly_soft_fail_seconds", 10.0))
        svc._shelly_state = "offline" if delay_seconds >= soft_fail_seconds else "degraded"
        svc._shelly_last_error_reason = str(reason)
        svc._shelly_last_error_detail = f"{source}: {error}"
        svc._shelly_last_error_at = float(now)
        svc._shelly_consecutive_errors = consecutive_errors
        svc._shelly_retry_after = retry_after
        self._record_shelly_offline_since(svc, now)

    @staticmethod
    def _record_shelly_offline_since(svc: ShellyIoHost, now: float) -> None:
        """Remember when Shelly became offline for the current incident."""
        if svc._shelly_state != "offline":
            return
        if isinstance(getattr(svc, "_shelly_offline_since", None), (int, float)):
            return
        svc._shelly_offline_since = float(now)

    @staticmethod
    def _record_shelly_retry_after(
        svc: ShellyIoHost,
        now: float,
        delay_seconds: float,
        retry_after: float,
    ) -> None:
        """Update source-level Shelly retry state."""
        delay_source_retry = getattr(svc, "_delay_source_retry", None)
        if callable(delay_source_retry):
            delay_source_retry("shelly", now, delay_seconds)
            return
        source_retry_after = getattr(svc, "_source_retry_after", None)
        if isinstance(source_retry_after, dict):
            source_retry_after["shelly"] = retry_after

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

    def _reset_shelly_shared_session(self, svc: ShellyIoHost) -> None:
        if not hasattr(svc, "session"):
            return
        self._close_object(getattr(svc, "session", None))
        svc.session = requests.Session()

    def _reset_shelly_backend_sessions(self, svc: ShellyIoHost) -> None:
        shared_session = getattr(svc, "session", None)
        for backend in self._shelly_transport_backends(svc):
            reset_transport_session = getattr(backend, "reset_transport_session", None)
            if callable(reset_transport_session):
                cast(_TransportSessionResetBackendLike, backend).reset_transport_session(shared_session)

    @staticmethod
    def _shelly_transport_backends(svc: ShellyIoHost) -> tuple[object, ...]:
        backends: list[object] = []
        seen: set[int] = set()
        for attr_name in ("_meter_backend", "_switch_backend", "_charger_backend"):
            backend = getattr(svc, attr_name, None)
            if backend is None or id(backend) in seen:
                continue
            seen.add(id(backend))
            backends.append(backend)
        return tuple(backends)


__all__ = ["ShellyIoWorkerTransportMixin"]


def _shelly_numeric(value: object) -> bool:
    """Return whether a value is numeric but not bool-like."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _shelly_source_retry_after_value(source_retry_after: object) -> float:
    """Return retry-after value from the shared source-retry mapping."""
    if not isinstance(source_retry_after, dict):
        return 0.0
    candidate = source_retry_after.get("shelly", 0.0)
    return float(candidate) if _shelly_numeric(candidate) else 0.0


def _classify_shelly_http_error(error: BaseException) -> str:
    """Return the Shelly reason for one HTTP error."""
    response = getattr(error, "response", None)
    status_code = getattr(response, "status_code", None)
    return "auth-error" if status_code in (401, 403) else "http-error"


def _classify_shelly_connection_error(error: BaseException) -> str:
    """Return the Shelly reason for one connection error."""
    text = str(error).lower()
    if "no route to host" in text:
        return "no-route"
    if "connection refused" in text:
        return "connection-refused"
    return "connection-error"


def _classify_shelly_timeout_error(error: BaseException) -> str | None:
    """Return a timeout reason when the error is a requests timeout."""
    for error_type, reason in _SHELLY_TIMEOUT_REASONS:
        if isinstance(error, error_type):
            return reason
    return None


def _classify_shelly_requests_error(error: BaseException) -> str | None:
    """Return a requests-layer Shelly error reason when applicable."""
    if isinstance(error, requests_exceptions.HTTPError):
        return _classify_shelly_http_error(error)
    if isinstance(error, requests_exceptions.ConnectionError):
        return _classify_shelly_connection_error(error)
    return None


def _classify_shelly_json_error(error: BaseException) -> str | None:
    """Return the JSON error reason when applicable."""
    return "bad-json" if isinstance(error, _SHELLY_JSON_ERROR_TYPES) else None


_SHELLY_ERROR_CLASSIFIERS = (
    _classify_shelly_timeout_error,
    _classify_shelly_requests_error,
    _classify_shelly_json_error,
)

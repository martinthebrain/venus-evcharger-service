# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded asynchronous DBus operation execution for the gateway process."""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass

from venus_evcharger.dbus_adapter.async_protocols import (
    AsyncCircuitBreaker,
    AsyncDbusConnection,
    AsyncRateLimiter,
)
from venus_evcharger.dbus_adapter.async_request import DbusWireRequest
from venus_evcharger.dbus_adapter.rate import DbusOperationDeferred
from venus_evcharger.ipc.command_types import CommandPayload

DbusReplyHandler = Callable[[object], None]
DbusErrorHandler = Callable[[BaseException], None]
DbusOperationStarter = Callable[[DbusReplyHandler, DbusErrorHandler], object | None]


@dataclass(frozen=True, slots=True)
class DbusAsyncOperation:
    """One bounded DBus method call and its completion continuations."""

    rate_kind: str
    metric_kind: str
    source: str
    priority: str
    timeout_seconds: float
    starter: DbusOperationStarter
    on_success: DbusReplyHandler
    on_error: DbusErrorHandler
    on_callback_failure: DbusErrorHandler | None = None
    optional_failure: bool = False
    owner_path: str = ""

    def __post_init__(self) -> None:
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0.0:
            raise ValueError("DBus async operation timeout must be finite and positive")


@dataclass(frozen=True, slots=True)
class DbusMethodCall:
    """Describe one low-level DBus method independently of its callbacks."""

    service: str
    path: str
    interface: str
    method_name: str
    signature: str
    rate_kind: str
    metric_kind: str
    source: str
    priority: str
    timeout_seconds: float
    args: tuple[object, ...] = ()
    optional_failure: bool = False
    owner_path: str = ""

    def __post_init__(self) -> None:
        self.wire_request()

    def wire_request(self) -> DbusWireRequest:
        """Return the adapter-neutral request consumed by the connection port."""
        return DbusWireRequest(
            service=self.service,
            path=self.path,
            interface=self.interface,
            method_name=self.method_name,
            signature=self.signature,
            timeout_seconds=self.timeout_seconds,
            args=self.args,
        )


@dataclass(slots=True)
class _InFlightOperation:
    operation_id: int
    operation: DbusAsyncOperation
    started_at: float
    deadline: float
    pending_call: object | None = None


class DbusAsyncTimeoutError(TimeoutError):
    """Report a broker-enforced hard deadline expiration."""


class DbusAsyncOperationBroker:
    """Run at most one external DBus method without blocking the GLib loop."""

    def __init__(
        self,
        rate_limiter: AsyncRateLimiter,
        circuit: AsyncCircuitBreaker,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._rate_limiter = rate_limiter
        self._circuit = circuit
        self._monotonic = monotonic
        self._next_operation_id = 1
        self._in_flight: _InFlightOperation | None = None
        self._submitted = 0
        self._completed = 0
        self._failed = 0
        self._timed_out = 0
        self._cancelled = 0
        self._busy_rejections = 0
        self._late_replies = 0
        self._callback_errors = 0
        self._last_completed_at = 0.0
        self._last_error = ""

    @property
    def busy(self) -> bool:
        """Return whether an operation still owns the single in-flight slot."""
        return self._in_flight is not None

    def owns_path(self, path: str) -> bool:
        """Return whether the current transport owns one durable command file."""
        current = self._in_flight
        return current is not None and bool(path) and current.operation.owner_path == path

    def submit(self, operation: DbusAsyncOperation) -> int:
        """Start one operation or defer it without altering durable work."""
        if self._in_flight is not None:
            self._busy_rejections += 1
            raise DbusOperationDeferred("async-broker-busy")
        self._rate_limiter.require_due(operation.rate_kind)
        started_at = self._monotonic()
        operation_id = self._next_operation_id
        self._next_operation_id += 1
        current = _InFlightOperation(
            operation_id=operation_id,
            operation=operation,
            started_at=started_at,
            deadline=started_at + operation.timeout_seconds,
        )
        self._in_flight = current
        self._submitted += 1
        try:
            pending_call = operation.starter(
                lambda value: self._finish_success(operation_id, value),
                lambda error: self._finish_error(operation_id, _as_exception(error)),
            )
            if self._is_current(operation_id):
                current.pending_call = pending_call
        except Exception as error:  # DBus proxy setup and test-double failures
            self._finish_error(operation_id, error)
        return operation_id

    def expire_due(self, *, now: float | None = None) -> bool:
        """Expire one overdue operation and make late DBus replies harmless."""
        current = self._in_flight
        monotonic_at = self._monotonic() if now is None else float(now)
        if current is None or monotonic_at < current.deadline:
            return False
        self._in_flight = None
        self._record_timeout(current, monotonic_at)
        return True

    def cancel_current(self, reason: str) -> bool:
        """Cancel only transport bookkeeping while retaining durable work."""
        current = self._in_flight
        if current is None:
            return False
        self._in_flight = None
        self._cancel_pending_call(current.pending_call)
        self._cancelled += 1
        self._last_completed_at = self._monotonic()
        self._last_error = str(reason)
        return True

    def health(self, *, now: float | None = None) -> CommandPayload:
        """Return bounded broker lifecycle and current-operation diagnostics."""
        monotonic_at = self._monotonic() if now is None else float(now)
        return {
            **self._operation_health(monotonic_at),
            "submitted": self._submitted,
            "completed": self._completed,
            "failed": self._failed,
            "timed_out": self._timed_out,
            "cancelled": self._cancelled,
            "busy_rejections": self._busy_rejections,
            "late_replies": self._late_replies,
            "callback_errors": self._callback_errors,
            "last_completed_monotonic": self._last_completed_at,
            "last_error": self._last_error,
        }

    def _operation_health(self, monotonic_at: float) -> CommandPayload:
        current = self._in_flight
        if current is None:
            return {
                "state": "idle",
                "in_flight": False,
                "operation_id": 0,
                "kind": "",
                "source": "",
                "priority": "",
                "owner_path": "",
                "age_ms": 0.0,
                "deadline_in_ms": 0.0,
            }
        return {
            "state": "busy",
            "in_flight": True,
            "operation_id": current.operation_id,
            "kind": current.operation.metric_kind,
            "source": current.operation.source,
            "priority": current.operation.priority,
            "owner_path": current.operation.owner_path,
            "age_ms": max(0.0, monotonic_at - current.started_at) * 1000.0,
            "deadline_in_ms": max(0.0, current.deadline - monotonic_at) * 1000.0,
        }

    def _finish_success(self, operation_id: int, value: object) -> None:
        current = self._take_current(operation_id)
        if current is None:
            return
        finished_at = self._monotonic()
        if finished_at >= current.deadline:
            self._record_timeout(current, finished_at)
            return
        latency_ms = max(0.0, finished_at - current.started_at) * 1000.0
        self._circuit.record_success(
            latency_ms,
            kind=current.operation.metric_kind,
            source=current.operation.source,
        )
        self._completed += 1
        self._last_completed_at = finished_at
        self._last_error = ""
        self._invoke_success(current.operation, value)

    def _finish_error(
        self,
        operation_id: int,
        error: BaseException,
    ) -> None:
        current = self._take_current(operation_id)
        if current is None:
            return
        completed_at = self._monotonic()
        if completed_at >= current.deadline:
            self._record_timeout(current, completed_at)
            return
        self._record_error(current, error, completed_at)

    def _record_timeout(
        self,
        current: _InFlightOperation,
        completed_at: float,
    ) -> None:
        self._timed_out += 1
        error = DbusAsyncTimeoutError(
            f"DBus {current.operation.metric_kind} operation timed out after {current.operation.timeout_seconds:.3f}s"
        )
        self._cancel_pending_call(current.pending_call)
        self._record_error(current, error, completed_at)

    def _record_error(
        self,
        current: _InFlightOperation,
        error: BaseException,
        completed_at: float,
    ) -> None:
        latency_ms = max(0.0, completed_at - current.started_at) * 1000.0
        if current.operation.optional_failure:
            self._circuit.record_optional_source_failure(
                error,
                source=current.operation.source,
                latency_ms=latency_ms,
            )
        else:
            self._circuit.record_error(
                error,
                kind=current.operation.metric_kind,
                source=current.operation.source,
                latency_ms=latency_ms,
            )
        self._failed += 1
        self._last_completed_at = completed_at
        self._last_error = str(error)
        self._invoke_error(current.operation, error)

    def _take_current(self, operation_id: int) -> _InFlightOperation | None:
        current = self._in_flight
        if current is None or current.operation_id != operation_id:
            self._late_replies += 1
            return None
        self._in_flight = None
        return current

    def _is_current(self, operation_id: int) -> bool:
        return self._in_flight is not None and self._in_flight.operation_id == operation_id

    def _invoke_success(self, operation: DbusAsyncOperation, value: object) -> None:
        try:
            operation.on_success(value)
        except Exception as error:
            self._callback_errors += 1
            logging.exception("Asynchronous DBus success callback failed")
            self._invoke_callback_failure(operation, error)

    def _invoke_error(self, operation: DbusAsyncOperation, error: BaseException) -> None:
        try:
            operation.on_error(error)
        except Exception:
            self._callback_errors += 1
            logging.exception("Asynchronous DBus error callback failed")

    def _invoke_callback_failure(
        self,
        operation: DbusAsyncOperation,
        error: BaseException,
    ) -> None:
        handler = operation.on_callback_failure
        if handler is None:
            return
        try:
            handler(error)
        except Exception:
            self._callback_errors += 1
            logging.exception("Asynchronous DBus callback-failure handler failed")

    @staticmethod
    def _cancel_pending_call(pending_call: object | None) -> None:
        cancel = getattr(pending_call, "cancel", None)
        if callable(cancel):
            try:
                cancel()
            except Exception:
                logging.debug("Unable to cancel pending DBus call", exc_info=True)


def dbus_call_starter(
    connection: AsyncDbusConnection,
    call: DbusMethodCall,
) -> DbusOperationStarter:
    """Adapt the connection port to the broker callback contract."""

    def _start(reply: DbusReplyHandler, error: DbusErrorHandler) -> object | None:
        def _reply_handler(*values: object) -> None:
            reply(_reply_value(values))

        return connection.send_async(
            call.wire_request(),
            _reply_handler,
            lambda value: error(_as_exception(value)),
        )

    return _start


def dbus_call_operation(
    connection: AsyncDbusConnection,
    call: DbusMethodCall,
    *,
    on_success: DbusReplyHandler,
    on_error: DbusErrorHandler,
) -> DbusAsyncOperation:
    """Build one broker operation around an explicit low-level DBus call."""

    return DbusAsyncOperation(
        rate_kind=call.rate_kind,
        metric_kind=call.metric_kind,
        source=call.source,
        priority=call.priority,
        timeout_seconds=call.timeout_seconds,
        starter=dbus_call_starter(connection, call),
        on_success=on_success,
        on_error=on_error,
        on_callback_failure=on_error,
        optional_failure=call.optional_failure,
        owner_path=call.owner_path,
    )


def _reply_value(values: tuple[object, ...]) -> object:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return values


def _as_exception(error: object) -> BaseException:
    return error if isinstance(error, BaseException) else RuntimeError(str(error))


__all__ = [
    "DbusAsyncOperation",
    "DbusAsyncOperationBroker",
    "DbusAsyncTimeoutError",
    "DbusErrorHandler",
    "DbusMethodCall",
    "DbusOperationStarter",
    "DbusReplyHandler",
    "dbus_call_operation",
    "dbus_call_starter",
]

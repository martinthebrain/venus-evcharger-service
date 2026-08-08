#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for bounded asynchronous DBus operation execution."""

from __future__ import annotations

import unittest
from collections.abc import Callable
from typing import cast
from unittest.mock import call, patch

from tests.dbus_adapter_venus_stubs import install_venus_adapter_stubs

install_venus_adapter_stubs()

from venus_evcharger.dbus_adapter.async_broker import (
    DbusAsyncOperation,
    DbusAsyncOperationBroker,
    DbusAsyncTimeoutError,
    DbusErrorHandler,
    DbusReplyHandler,
)
from venus_evcharger.dbus_adapter.rate import DbusOperationDeferred


class _Clock:
    def __init__(self, value: float = 10.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class _RateLimiter:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.error: BaseException | None = None

    def require_due(self, kind: str) -> None:
        self.calls.append(kind)
        if self.error is not None:
            raise self.error


class _Circuit:
    def __init__(self) -> None:
        self.successes: list[tuple[float, str, str]] = []
        self.errors: list[tuple[BaseException, str, str, float | None]] = []
        self.optional_errors: list[tuple[BaseException, str, float]] = []

    def record_success(
        self,
        latency_ms: float,
        *,
        kind: str = "dbus",
        source: str = "",
    ) -> None:
        self.successes.append((latency_ms, kind, source))

    def record_error(
        self,
        error: BaseException,
        *,
        kind: str = "dbus",
        source: str = "",
        latency_ms: float | None = None,
    ) -> None:
        self.errors.append((error, kind, source, latency_ms))

    def record_optional_source_failure(
        self,
        error: BaseException,
        *,
        source: str,
        latency_ms: float,
    ) -> None:
        self.optional_errors.append((error, source, latency_ms))


class _PendingCall:
    def __init__(self, *, fail_cancel: bool = False) -> None:
        self.cancel_count = 0
        self.fail_cancel = fail_cancel

    def cancel(self) -> None:
        self.cancel_count += 1
        if self.fail_cancel:
            raise RuntimeError("cancel failed")


class _Starter:
    def __init__(self, pending: object | None = None) -> None:
        self.pending = pending
        self.reply: DbusReplyHandler | None = None
        self.error: DbusErrorHandler | None = None

    def __call__(
        self,
        reply: DbusReplyHandler,
        error: DbusErrorHandler,
    ) -> object | None:
        self.reply = reply
        self.error = error
        return self.pending

    def succeed(self, value: object) -> None:
        assert self.reply is not None
        self.reply(value)

    def fail(self, error: object) -> None:
        assert self.error is not None
        cast(Callable[[object], None], self.error)(error)


def _operation(
    starter: Callable[[DbusReplyHandler, DbusErrorHandler], object | None],
    successes: list[object],
    errors: list[BaseException],
    *,
    timeout_seconds: float = 1.0,
    optional_failure: bool = False,
    on_success: DbusReplyHandler | None = None,
    on_error: DbusErrorHandler | None = None,
    on_callback_failure: DbusErrorHandler | None = None,
    owner_path: str = "",
) -> DbusAsyncOperation:
    return DbusAsyncOperation(
        rate_kind="read",
        metric_kind="optional_read" if optional_failure else "read",
        source="service/path",
        priority="diagnostic",
        timeout_seconds=timeout_seconds,
        starter=starter,
        on_success=successes.append if on_success is None else on_success,
        on_error=errors.append if on_error is None else on_error,
        on_callback_failure=on_callback_failure,
        optional_failure=optional_failure,
        owner_path=owner_path,
    )


class DbusAsyncOperationBrokerContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = _Clock()
        self.rate = _RateLimiter()
        self.circuit = _Circuit()
        self.broker = DbusAsyncOperationBroker(
            self.rate,
            self.circuit,
            monotonic=self.clock,
        )
        self.successes: list[object] = []
        self.errors: list[BaseException] = []

    def test_operation_requires_positive_timeout(self) -> None:
        starter = _Starter()
        with self.assertRaisesRegex(ValueError, "timeout must be finite and positive"):
            _operation(starter, self.successes, self.errors, timeout_seconds=0.0)
        with self.assertRaisesRegex(ValueError, "timeout must be finite and positive"):
            _operation(starter, self.successes, self.errors, timeout_seconds=-0.1)
        for invalid in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaisesRegex(ValueError, "timeout must be finite and positive"):
                _operation(starter, self.successes, self.errors, timeout_seconds=invalid)

    def test_initial_health_is_an_exact_idle_lifecycle_snapshot(self) -> None:
        self.assertEqual(
            self.broker.health(now=10.0),
            {
                "state": "idle",
                "in_flight": False,
                "operation_id": 0,
                "kind": "",
                "source": "",
                "priority": "",
                "owner_path": "",
                "age_ms": 0.0,
                "deadline_in_ms": 0.0,
                "submitted": 0,
                "completed": 0,
                "failed": 0,
                "timed_out": 0,
                "cancelled": 0,
                "busy_rejections": 0,
                "late_replies": 0,
                "callback_errors": 0,
                "last_completed_monotonic": 0.0,
                "last_error": "",
            },
        )

    def test_operation_ids_and_success_counters_progress(self) -> None:
        for expected_id in (1, 2):
            starter = _Starter()
            self.assertEqual(
                self.broker.submit(_operation(starter, self.successes, self.errors)),
                expected_id,
            )
            starter.succeed(expected_id)

        health = self.broker.health()
        self.assertEqual(health["submitted"], 2)
        self.assertEqual(health["completed"], 2)
        self.assertEqual(self.successes, [1, 2])

    def test_synchronous_reply_releases_slot_before_starter_returns(self) -> None:
        pending = _PendingCall()

        def immediate_reply(
            reply: DbusReplyHandler,
            _error: DbusErrorHandler,
        ) -> object:
            reply("immediate")
            return pending

        operation_id = self.broker.submit(
            _operation(immediate_reply, self.successes, self.errors)
        )

        self.assertEqual(operation_id, 1)
        self.assertFalse(self.broker.busy)
        self.assertEqual(self.successes, ["immediate"])
        self.assertEqual(pending.cancel_count, 0)
        self.assertFalse(self.broker.expire_due(now=20.0))

    def test_success_owns_one_slot_and_records_latency(self) -> None:
        pending = _PendingCall()
        starter = _Starter(pending)

        operation_id = self.broker.submit(_operation(starter, self.successes, self.errors))
        health = self.broker.health(now=10.25)

        self.assertEqual(operation_id, 1)
        self.assertTrue(self.broker.busy)
        self.assertEqual(self.rate.calls, ["read"])
        self.assertEqual(health["state"], "busy")
        self.assertIs(health["in_flight"], True)
        self.assertEqual(health["operation_id"], 1)
        self.assertEqual(health["kind"], "read")
        self.assertEqual(health["source"], "service/path")
        self.assertEqual(health["priority"], "diagnostic")
        self.assertEqual(health["owner_path"], "")
        self.assertEqual(health["age_ms"], 250.0)
        self.assertEqual(health["deadline_in_ms"], 750.0)

        self.clock.value = 10.4
        starter.succeed(42)

        self.assertFalse(self.broker.busy)
        self.assertEqual(self.successes, [42])
        self.assertEqual(self.errors, [])
        self.assertEqual(self.circuit.successes[0][1:], ("read", "service/path"))
        self.assertAlmostEqual(self.circuit.successes[0][0], 400.0)
        self.assertEqual(pending.cancel_count, 0)
        idle = self.broker.health(now=10.5)
        self.assertEqual(idle["state"], "idle")
        self.assertEqual(idle["completed"], 1)
        self.assertEqual(idle["last_completed_monotonic"], 10.4)
        self.assertEqual(idle["last_error"], "")

    def test_busy_and_rate_deferrals_do_not_replace_current_work(self) -> None:
        first = _Starter()
        self.broker.submit(_operation(first, self.successes, self.errors))

        for expected_rejections in (1, 2):
            with self.assertRaises(DbusOperationDeferred) as raised:
                self.broker.submit(_operation(_Starter(), [], []))
            self.assertEqual(str(raised.exception), "async-broker-busy")
            self.assertEqual(
                self.broker.health()["busy_rejections"],
                expected_rejections,
            )

        first.succeed("done")
        self.rate.error = DbusOperationDeferred("rate-limited")
        with self.assertRaisesRegex(DbusOperationDeferred, "rate-limited"):
            self.broker.submit(_operation(_Starter(), [], []))

        self.assertFalse(self.broker.busy)
        self.assertEqual(self.broker.health()["submitted"], 1)

    def test_starter_and_regular_async_errors_are_circuit_failures(self) -> None:
        def broken_starter(
            _reply: DbusReplyHandler,
            _error: DbusErrorHandler,
        ) -> object | None:
            raise RuntimeError("setup failed")

        operation_id = self.broker.submit(_operation(broken_starter, self.successes, self.errors))
        self.assertEqual(operation_id, 1)
        self.assertIsInstance(self.errors[-1], RuntimeError)
        self.assertEqual(str(self.circuit.errors[-1][0]), "setup failed")

        self.clock.value = 11.0
        starter = _Starter()
        self.broker.submit(_operation(starter, self.successes, self.errors))
        self.clock.value = 11.2
        starter.fail("wire failure")

        error, kind, source, latency_ms = self.circuit.errors[-1]
        self.assertIsInstance(error, RuntimeError)
        self.assertEqual(str(error), "wire failure")
        self.assertEqual((kind, source), ("read", "service/path"))
        assert latency_ms is not None
        self.assertAlmostEqual(latency_ms, 200.0)
        health = self.broker.health()
        self.assertEqual(health["failed"], 2)
        self.assertEqual(health["last_completed_monotonic"], 11.2)
        self.assertEqual(health["last_error"], "wire failure")

    def test_optional_error_is_isolated_from_main_circuit(self) -> None:
        starter = _Starter()
        self.broker.submit(
            _operation(
                starter,
                self.successes,
                self.errors,
                optional_failure=True,
            )
        )
        self.clock.value = 10.125
        starter.fail(ValueError("sleeping source"))

        self.assertEqual(self.circuit.errors, [])
        self.assertEqual(len(self.circuit.optional_errors), 1)
        error, source, latency_ms = self.circuit.optional_errors[0]
        self.assertEqual(str(error), "sleeping source")
        self.assertEqual((source, latency_ms), ("service/path", 125.0))

    def test_hard_timeout_cancels_pending_and_ignores_late_replies(self) -> None:
        pending = _PendingCall()
        starter = _Starter(pending)
        self.broker.submit(_operation(starter, self.successes, self.errors, timeout_seconds=0.5))

        self.assertFalse(self.broker.expire_due(now=10.499))
        self.assertTrue(self.broker.expire_due(now=10.5))
        self.assertFalse(self.broker.busy)
        self.assertEqual(pending.cancel_count, 1)
        self.assertIsInstance(self.errors[-1], DbusAsyncTimeoutError)
        self.assertEqual(
            str(self.errors[-1]),
            "DBus read operation timed out after 0.500s",
        )
        self.assertIsInstance(self.circuit.errors[-1][0], DbusAsyncTimeoutError)
        self.assertEqual(self.circuit.errors[-1][3], 500.0)
        health = self.broker.health(now=10.5)
        self.assertEqual(health["timed_out"], 1)
        self.assertEqual(health["failed"], 1)
        self.assertEqual(health["last_completed_monotonic"], 10.5)

        starter.succeed("late")
        starter.fail(RuntimeError("also late"))
        self.assertEqual(self.successes, [])
        self.assertEqual(len(self.errors), 1)
        self.assertEqual(self.broker.health()["late_replies"], 2)

        no_cancel_method = _Starter(object())
        self.broker.submit(
            _operation(
                no_cancel_method,
                self.successes,
                self.errors,
                timeout_seconds=0.5,
            )
        )
        self.assertTrue(self.broker.expire_due(now=10.5))
        self.assertEqual(self.broker.health()["timed_out"], 2)

    def test_success_callback_after_deadline_is_a_timeout_without_polling(self) -> None:
        pending = _PendingCall()
        starter = _Starter(pending)
        self.broker.submit(_operation(starter, self.successes, self.errors, timeout_seconds=0.5))

        self.clock.value = 10.5
        starter.succeed("too late")

        self.assertFalse(self.broker.busy)
        self.assertEqual(self.successes, [])
        self.assertIsInstance(self.errors[-1], DbusAsyncTimeoutError)
        self.assertIsInstance(self.circuit.errors[-1][0], DbusAsyncTimeoutError)
        self.assertEqual(pending.cancel_count, 1)
        self.assertEqual(self.broker.health()["timed_out"], 1)

    def test_error_callback_after_deadline_is_classified_as_timeout(self) -> None:
        pending = _PendingCall()
        starter = _Starter(pending)
        self.broker.submit(_operation(starter, self.successes, self.errors, timeout_seconds=0.5))

        self.clock.value = 10.5
        starter.fail(RuntimeError("late wire error"))

        self.assertEqual(len(self.errors), 1)
        self.assertIsInstance(self.errors[0], DbusAsyncTimeoutError)
        self.assertNotIn("late wire error", str(self.circuit.errors[-1][0]))
        self.assertEqual(self.circuit.errors[-1][3], 500.0)
        self.assertEqual(pending.cancel_count, 1)
        self.assertEqual(self.broker.health()["timed_out"], 1)

    def test_expire_without_current_and_negative_health_intervals(self) -> None:
        self.assertFalse(self.broker.expire_due(now=10.0))
        starter = _Starter()
        self.broker.submit(_operation(starter, self.successes, self.errors))
        health = self.broker.health(now=9.0)
        self.assertEqual(health["age_ms"], 0.0)
        self.assertEqual(health["deadline_in_ms"], 2000.0)

    def test_cancel_is_local_and_tolerates_pending_cancel_failure(self) -> None:
        self.assertFalse(self.broker.cancel_current("nothing"))
        pending = _PendingCall(fail_cancel=True)
        starter = _Starter(pending)
        self.broker.submit(_operation(starter, self.successes, self.errors))
        self.clock.value = 10.2

        with patch(
            "venus_evcharger.dbus_adapter.async_broker.logging.debug"
        ) as logged:
            self.assertTrue(self.broker.cancel_current("shutdown"))
        self.assertFalse(self.broker.busy)
        self.assertEqual(pending.cancel_count, 1)
        self.assertEqual(self.errors, [])
        self.assertEqual(self.circuit.errors, [])
        health = self.broker.health()
        self.assertEqual(health["cancelled"], 1)
        self.assertEqual(health["last_completed_monotonic"], 10.2)
        self.assertEqual(health["last_error"], "shutdown")
        logged.assert_called_once_with(
            "Unable to cancel pending DBus call",
            exc_info=True,
        )

        no_cancel_method = _Starter(object())
        self.broker.submit(_operation(no_cancel_method, self.successes, self.errors))
        self.clock.value = 10.4
        self.assertTrue(self.broker.cancel_current("stop"))
        health = self.broker.health()
        self.assertEqual(health["cancelled"], 2)
        self.assertEqual(health["last_completed_monotonic"], 10.4)
        self.assertEqual(self.errors, [])

    def test_owner_path_is_exposed_only_while_transport_is_in_flight(self) -> None:
        starter = _Starter()
        self.broker.submit(
            _operation(
                starter,
                self.successes,
                self.errors,
                owner_path="/run/commands/user.json",
            )
        )

        self.assertTrue(self.broker.owns_path("/run/commands/user.json"))
        self.assertFalse(self.broker.owns_path(""))
        self.assertEqual(
            self.broker.health()["owner_path"],
            "/run/commands/user.json",
        )
        starter.succeed("done")
        self.assertFalse(self.broker.owns_path("/run/commands/user.json"))

    def test_current_generation_check_handles_idle_and_wrong_ids(self) -> None:
        self.assertFalse(self.broker._is_current(1))
        starter = _Starter()
        self.broker.submit(_operation(starter, self.successes, self.errors))
        self.assertTrue(self.broker._is_current(1))
        self.assertFalse(self.broker._is_current(2))
        starter.succeed("done")
        self.assertFalse(self.broker._is_current(1))

    def test_callback_failures_are_contained_and_counted(self) -> None:
        def broken_success(_value: object) -> None:
            raise RuntimeError("success callback")

        def broken_error(_error: BaseException) -> None:
            raise RuntimeError("error callback")

        with patch(
            "venus_evcharger.dbus_adapter.async_broker.logging.exception"
        ) as logged:
            for _index in range(2):
                success_starter = _Starter()
                self.broker.submit(
                    _operation(
                        success_starter,
                        self.successes,
                        self.errors,
                        on_success=broken_success,
                    )
                )
                success_starter.succeed("value")

            for _index in range(2):
                error_starter = _Starter()
                self.broker.submit(
                    _operation(
                        error_starter,
                        self.successes,
                        self.errors,
                        on_error=broken_error,
                    )
                )
                error_starter.fail(RuntimeError("wire"))

        self.assertEqual(
            logged.call_args_list,
            [
                call("Asynchronous DBus success callback failed"),
                call("Asynchronous DBus success callback failed"),
                call("Asynchronous DBus error callback failed"),
                call("Asynchronous DBus error callback failed"),
            ],
        )
        self.assertEqual(self.broker.health()["callback_errors"], 4)
        self.assertFalse(self.broker.busy)

    def test_success_callback_failure_uses_explicit_failure_route(self) -> None:
        fallback_errors: list[BaseException] = []

        def broken_success(_value: object) -> None:
            raise RuntimeError("cannot finalize")

        starter = _Starter()
        self.broker.submit(
            _operation(
                starter,
                self.successes,
                self.errors,
                on_success=broken_success,
                on_callback_failure=fallback_errors.append,
            )
        )
        with patch("venus_evcharger.dbus_adapter.async_broker.logging.exception"):
            starter.succeed("value")

        self.assertEqual(str(fallback_errors[0]), "cannot finalize")
        self.assertEqual(self.broker.health()["callback_errors"], 1)

    def test_callback_failure_route_is_bounded_when_its_handler_also_fails(self) -> None:
        def broken_success(_value: object) -> None:
            raise RuntimeError("cannot finalize")

        def broken_fallback(_error: BaseException) -> None:
            raise RuntimeError("cannot retain command")

        starter = _Starter()
        self.broker.submit(
            _operation(
                starter,
                self.successes,
                self.errors,
                on_success=broken_success,
                on_callback_failure=broken_fallback,
            )
        )
        with patch("venus_evcharger.dbus_adapter.async_broker.logging.exception") as logged:
            starter.succeed("value")

        self.assertEqual(
            logged.call_args_list,
            [
                call("Asynchronous DBus success callback failed"),
                call("Asynchronous DBus callback-failure handler failed"),
            ],
        )
        self.assertEqual(self.broker.health()["callback_errors"], 2)
        self.assertFalse(self.broker.busy)


if __name__ == "__main__":
    unittest.main()

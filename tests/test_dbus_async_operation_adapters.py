#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for adapting dbus-python calls to broker operations."""

from __future__ import annotations

import unittest
from collections.abc import Callable
from dataclasses import replace
from typing import cast
from unittest.mock import MagicMock

from tests.dbus_adapter_venus_stubs import install_venus_adapter_stubs

install_venus_adapter_stubs()

from venus_evcharger.dbus_adapter.async_broker import (
    DbusErrorHandler,
    dbus_call_operation,
    dbus_call_starter,
    DbusMethodCall,
)
from venus_evcharger.dbus_adapter.async_request import DbusWireRequest


class DbusAsyncOperationAdapterContracts(unittest.TestCase):
    """Pin low-level callback adaptation independently of broker lifecycle."""

    def test_call_starter_adapts_zero_single_and_multiple_reply_values(self) -> None:
        connection = MagicMock()
        pending = object()
        connection.send_async.return_value = pending
        call = DbusMethodCall(
            service="service",
            path="/Path",
            interface="com.victronenergy.BusItem",
            method_name="SetValue",
            signature="v",
            rate_kind="write",
            metric_kind="write",
            source="source",
            priority="user",
            timeout_seconds=0.75,
            args=(1,),
            owner_path="command.json",
        )

        replies: list[object] = []
        errors: list[BaseException] = []
        returned = dbus_call_starter(connection, call)(replies.append, errors.append)

        self.assertIs(returned, pending)
        positional = connection.send_async.call_args.args
        self.assertEqual(
            positional[0],
            DbusWireRequest(
                service="service",
                path="/Path",
                interface="com.victronenergy.BusItem",
                method_name="SetValue",
                signature="v",
                timeout_seconds=0.75,
                args=(1,),
            ),
        )
        self.assertEqual(connection.send_async.call_args.kwargs, {})
        reply_handler = cast(Callable[..., None], positional[1])
        error_handler = cast(Callable[[object], None], positional[2])
        reply_handler()
        reply_handler(1)
        reply_handler(1, 2)
        error_handler("failed")
        self.assertEqual(replies, [None, 1, (1, 2)])
        self.assertEqual(str(errors[0]), "failed")

    def test_call_operation_preserves_transport_policy_and_callback_fallback(self) -> None:
        connection = MagicMock()
        connection.send_async.return_value = "pending"
        replies: list[object] = []
        errors: list[BaseException] = []
        operation = dbus_call_operation(
            connection,
            DbusMethodCall(
                service="service",
                path="/Path",
                interface="com.victronenergy.BusItem",
                method_name="GetValue",
                signature="",
                rate_kind="read",
                metric_kind="read",
                source="source",
                priority="user",
                timeout_seconds=0.4,
                args=("arg",),
                optional_failure=True,
                owner_path="command.json",
            ),
            on_success=replies.append,
            on_error=errors.append,
        )

        pending = operation.starter(replies.append, errors.append)

        self.assertEqual(pending, "pending")
        request = connection.send_async.call_args.args[0]
        self.assertEqual(request.args, ("arg",))
        self.assertEqual(request.timeout_seconds, 0.4)
        self.assertTrue(operation.optional_failure)
        self.assertEqual(operation.owner_path, "command.json")
        self.assertEqual(operation.on_callback_failure, errors.append)

    def test_method_call_rejects_incomplete_targets_and_nonfinite_deadlines(self) -> None:
        valid = DbusMethodCall(
            service="service",
            path="/Path",
            interface="interface",
            method_name="Method",
            signature="",
            rate_kind="read",
            metric_kind="read",
            source="source",
            priority="read",
            timeout_seconds=1.0,
        )
        for key in ("service", "path", "interface", "method_name"):
            with self.assertRaises(ValueError) as raised:
                replace(valid, **{key: ""})
            self.assertEqual(str(raised.exception), "DBus method target must be complete")
        for timeout in (0.0, float("nan"), float("inf")):
            with self.assertRaises(ValueError) as raised:
                replace(valid, timeout_seconds=timeout)
            self.assertEqual(
                str(raised.exception),
                "DBus method timeout must be finite and positive",
            )


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: GPL-3.0-or-later
"""Asynchronous DBus transport for one scheduled external read."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from venus_evcharger.core.shared import coerce_dbus_numeric
from venus_evcharger.dbus_adapter.async_broker import DbusMethodCall, dbus_call_operation
from venus_evcharger.dbus_adapter.read.protocols import DbusReadAdapter

DBUS_READ_TIMEOUT_SECONDS = 1.0


@dataclass(frozen=True)
class BusItemReadCall:
    """Validated target and scheduling class for one external read."""

    service: str
    path: str
    optional: bool = False


def submit_busitem_read(
    adapter: DbusReadAdapter,
    call: BusItemReadCall,
    *,
    on_success: Callable[[object], None],
    on_error: Callable[[BaseException], None],
) -> None:
    """Submit one typed BusItem.GetValue call to the single-flight broker."""
    if not call.service or not call.path:
        raise ValueError("DBus read target requires service and path")
    source = f"{call.service}{call.path}"
    adapter.operation_broker.submit(
        dbus_call_operation(
            adapter.connection,
            DbusMethodCall(
                service=call.service,
                path=call.path,
                interface="com.victronenergy.BusItem",
                method_name="GetValue",
                signature="",
                rate_kind="read",
                metric_kind="optional_read" if call.optional else "read",
                source=source,
                priority="optional" if call.optional else "read",
                timeout_seconds=DBUS_READ_TIMEOUT_SECONDS,
                optional_failure=call.optional,
            ),
            on_success=lambda value: on_success(coerce_dbus_numeric(value)),
            on_error=on_error,
        )
    )

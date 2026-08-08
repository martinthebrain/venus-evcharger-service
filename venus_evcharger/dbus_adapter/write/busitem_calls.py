# SPDX-License-Identifier: GPL-3.0-or-later
"""Construct asynchronous Venus BusItem method calls."""

from __future__ import annotations

from venus_evcharger.dbus_adapter.async_broker import DbusMethodCall

_BUS_ITEM_INTERFACE = "com.victronenergy.BusItem"
_DBUS_TIMEOUT_SECONDS = 1.0


def busitem_read_call(
    service: str,
    path: str,
    priority: str,
    owner_path: str,
) -> DbusMethodCall:
    """Build one asynchronous BusItem ``GetValue`` operation."""
    return DbusMethodCall(
        service=service,
        path=path,
        interface=_BUS_ITEM_INTERFACE,
        method_name="GetValue",
        signature="",
        rate_kind="read",
        metric_kind="read",
        source=f"{service}{path}",
        priority=priority,
        timeout_seconds=_DBUS_TIMEOUT_SECONDS,
        owner_path=owner_path,
    )


def busitem_write_call(
    service: str,
    path: str,
    value: object,
    priority: str,
    owner_path: str,
) -> DbusMethodCall:
    """Build one asynchronous BusItem ``SetValue`` operation."""
    return DbusMethodCall(
        service=service,
        path=path,
        interface=_BUS_ITEM_INTERFACE,
        method_name="SetValue",
        signature="v",
        rate_kind="write",
        metric_kind="write",
        source=f"{service}{path}",
        priority=priority,
        timeout_seconds=_DBUS_TIMEOUT_SECONDS,
        args=(value,),
        owner_path=owner_path,
    )

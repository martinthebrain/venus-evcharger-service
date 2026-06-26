#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Composed structural contract for the full DBus adapter process."""

from __future__ import annotations

from typing import Protocol

from venus_evcharger.dbus_adapter_process_protocol_health import DbusAdapterHealthContext
from venus_evcharger.dbus_adapter_process_protocol_introspection import (
    DbusAdapterIntrospectionContext,
    DbusAdapterIntrospectionSnapshotContext,
)
from venus_evcharger.dbus_adapter_process_protocol_io import DbusAdapterIoContext
from venus_evcharger.dbus_adapter_process_protocol_loop import DbusAdapterLoopContext
from venus_evcharger.dbus_adapter_process_protocol_runtime import (
    DbusAdapterIdentityContext,
    DbusAdapterRuntimeContext,
    DbusAdapterSocketContext,
)


class DbusAdapterProcessContext(  # pragma: no cover
    DbusAdapterRuntimeContext,
    DbusAdapterSocketContext,
    DbusAdapterIdentityContext,
    DbusAdapterLoopContext,
    DbusAdapterIoContext,
    DbusAdapterHealthContext,
    DbusAdapterIntrospectionContext,
    DbusAdapterIntrospectionSnapshotContext,
    Protocol,
):
    """Full structural adapter contract for integration-oriented type checks."""

#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Composed structural contract for the full adapter process."""

from __future__ import annotations

from typing import Protocol

from venus_evcharger.dbus_adapter.process.protocols.health import DbusAdapterHealthContext
from venus_evcharger.dbus_adapter.process.protocols.introspection import (
    DbusAdapterIntrospectionContext,
    DbusAdapterIntrospectionSnapshotContext,
)
from venus_evcharger.dbus_adapter.process.protocols.io import DbusAdapterIoContext
from venus_evcharger.dbus_adapter.process.protocols.loop import DbusAdapterLoopContext
from venus_evcharger.dbus_adapter.process.protocols.runtime import (
    DbusAdapterIdentityContext,
    DbusAdapterRuntimeContext,
    DbusAdapterSocketContext,
)


class DbusAdapterProcessContext(
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

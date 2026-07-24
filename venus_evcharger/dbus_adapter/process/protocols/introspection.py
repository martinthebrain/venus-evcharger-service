#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Introspection contracts for adapter process components."""

from __future__ import annotations

import configparser
from typing import Protocol

from venus_evcharger.dbus_adapter.process.protocols.roles import IoRole
from venus_evcharger.dbus_adapter.rate import DbusCircuitBreaker, DbusConnectionManager
from venus_evcharger.dbus_adapter.read.discovery import DbusEnergyDiscoveryManager
from venus_evcharger.dbus_adapter.read.executor import DbusReadExecutor
from venus_evcharger.dbus_adapter.scheduling import DbusDiscoveryManager, DbusReadScheduler
from venus_evcharger.dbus_gateway import DbusCacheStore, DbusGatewayCommandInbox


class DbusAdapterIntrospectionContext(Protocol):  # pragma: no cover
    """Introspection request and background-discovery surface."""

    config: configparser.ConfigParser
    connection: DbusConnectionManager
    circuit: DbusCircuitBreaker
    cache: DbusCacheStore
    commands: DbusGatewayCommandInbox
    discovery: DbusDiscoveryManager
    energy_discovery: DbusEnergyDiscoveryManager
    read_executor: DbusReadExecutor
    read_scheduler: DbusReadScheduler
    dbus_introspection_enabled: bool
    _introspection_queue_depth: int
    _last_introspection_full_scan_at: float
    @property
    def io_role(self) -> IoRole: ...


class DbusAdapterIntrospectionSnapshotContext(Protocol):  # pragma: no cover
    """Snapshot surface for advisory introspection findings."""

    cache: DbusCacheStore
    dbus_introspection_enabled: bool
    dbus_introspection_snapshot_path: str
    _introspection_queue_depth: int
    _last_introspection_full_scan_at: float

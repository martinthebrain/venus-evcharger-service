#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Semantic publication capability of the dedicated DBus adapter."""

from __future__ import annotations

from venus_evcharger.dbus_adapter.process.io import DbusAdapterIo
from venus_evcharger.dbus_adapter.process.protocols.runtime import DbusAdapterPublicationContext


class DbusAdapterPublication(DbusAdapterIo):
    """Expose publication health without leaking concrete DBus services."""

    @property
    def evcs_service_registered(self: DbusAdapterPublicationContext) -> bool:
        return self.publication_registry.evcs_registered

    @property
    def registered_publication_path_count(self: DbusAdapterPublicationContext) -> int:
        return self.publication_registry.registered_path_count

#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Semantic publication capability of the dedicated DBus adapter."""

from __future__ import annotations

from venus_evcharger.dbus_adapter.process.protocols.runtime import DbusAdapterPublicationContext


class DbusAdapterPublication:
    """Expose publication health without leaking concrete DBus services."""

    def __init__(self, context: DbusAdapterPublicationContext) -> None:
        self._context = context

    @property
    def evcs_service_registered(self) -> bool:
        return self._context.publication_registry.evcs_registered

    @property
    def registered_publication_path_count(self) -> int:
        return self._context.publication_registry.registered_path_count

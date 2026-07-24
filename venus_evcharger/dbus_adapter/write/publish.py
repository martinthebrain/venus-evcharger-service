# SPDX-License-Identifier: GPL-3.0-or-later
"""Semantic EVCS and companion publication execution."""

from __future__ import annotations

from venus_evcharger.dbus_adapter.contracts import CommandOutcome
from venus_evcharger.dbus_adapter.write.protocols import PublicationRegistry
from venus_evcharger.ipc.command_types import CommandMapping
from venus_evcharger.ipc.gateway_publication import (
    parse_publish_companion_fields,
    parse_publish_evcs_fields,
    parse_register_companion,
    parse_register_evcs,
)


class GatewayPublicationExecutor:
    """Apply validated semantic publications through the gateway registry."""

    def __init__(self, registry: PublicationRegistry) -> None:
        self._registry = registry

    def process(self, command: CommandMapping) -> CommandOutcome:
        register_evcs = parse_register_evcs(command)
        if register_evcs is not None:
            return self._registry.register_evcs(register_evcs)
        publish_evcs = parse_publish_evcs_fields(command)
        if publish_evcs is not None:
            return self._registry.publish_evcs(publish_evcs)
        register_companion = parse_register_companion(command)
        if register_companion is not None:
            return self._registry.register_companion(register_companion)
        publish_companion = parse_publish_companion_fields(command)
        if publish_companion is not None:
            return self._registry.publish_companion(publish_companion)
        return "dropped"

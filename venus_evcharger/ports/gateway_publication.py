# SPDX-License-Identifier: GPL-3.0-or-later
"""Transport-neutral publication contract implemented by the system gateway."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

PublicationPriority = Literal["critical", "live", "diagnostic"]
CompanionServiceKind = Literal["battery", "grid", "pv_inverter"]


@dataclass(frozen=True, slots=True)
class PublicationReceipt:
    """Acceptance result for one asynchronous publication request."""

    accepted: bool
    command_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class EvcsServiceIdentity:
    """Semantic identity of the EV charger exposed by the gateway."""

    product_name: str
    custom_name: str
    firmware_version: str
    hardware_version: str
    serial: str
    connection_name: str
    process_name: str
    process_version: str


@dataclass(frozen=True, slots=True)
class CompanionServiceIdentity:
    """Semantic identity of one optional energy companion service."""

    service_id: str
    kind: CompanionServiceKind
    product_name: str
    custom_name: str
    firmware_version: str
    hardware_version: str
    serial: str
    connection_name: str
    process_name: str
    process_version: str


@runtime_checkable
class GatewayPublicationPort(Protocol):  # pragma: no cover
    """Publication operations available to DBus-independent domain code."""

    def register_evcs(
        self,
        identity: EvcsServiceIdentity,
        initial_fields: Mapping[str, object],
    ) -> PublicationReceipt: ...

    def publish_evcs_fields(
        self,
        fields: Mapping[str, object],
        *,
        priority: PublicationPriority,
    ) -> PublicationReceipt: ...

    def register_companion(
        self,
        identity: CompanionServiceIdentity,
        initial_fields: Mapping[str, object],
    ) -> PublicationReceipt: ...

    def publish_companion_fields(
        self,
        service_id: str,
        fields: Mapping[str, object],
        *,
        priority: PublicationPriority,
    ) -> PublicationReceipt: ...


def require_gateway_publication(host: object) -> GatewayPublicationPort:
    """Return the composed publication port or fail at the boundary."""
    publication = getattr(host, "gateway_publication", None)
    if not isinstance(publication, GatewayPublicationPort):
        raise RuntimeError("Semantic gateway publication is not configured")
    return publication


__all__ = [
    "CompanionServiceIdentity",
    "CompanionServiceKind",
    "EvcsServiceIdentity",
    "GatewayPublicationPort",
    "PublicationPriority",
    "PublicationReceipt",
    "require_gateway_publication",
]

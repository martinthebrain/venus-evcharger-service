# SPDX-License-Identifier: GPL-3.0-or-later
"""Validated wire contracts for semantic gateway publication."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import TypeGuard

from venus_evcharger.ipc.command_types import CommandMapping, CommandPayload
from venus_evcharger.ports.gateway_publication import (
    CompanionServiceIdentity,
    CompanionServiceKind,
    EvcsServiceIdentity,
    PublicationPriority,
)

REGISTER_EVCS_KIND = "register_evcs"
PUBLISH_EVCS_FIELDS_KIND = "publish_evcs_fields"
REGISTER_COMPANION_KIND = "register_companion"
PUBLISH_COMPANION_FIELDS_KIND = "publish_companion_fields"
SEMANTIC_PUBLICATION_KINDS = frozenset(
    (
        REGISTER_EVCS_KIND,
        PUBLISH_EVCS_FIELDS_KIND,
        REGISTER_COMPANION_KIND,
        PUBLISH_COMPANION_FIELDS_KIND,
    )
)

_COMPANION_KINDS = frozenset(("battery", "grid", "pv_inverter"))
_PUBLICATION_PRIORITIES = frozenset(("critical", "live", "diagnostic"))
_QUEUE_PRIORITY = {
    "critical": "safety",
    "live": "publish",
    "diagnostic": "diagnostic",
}
_COMMON_TEXT_FIELDS = (
    "product_name",
    "custom_name",
    "firmware_version",
    "hardware_version",
    "serial",
    "connection_name",
    "process_name",
    "process_version",
)


@dataclass(frozen=True, slots=True)
class RegisterEvcsPublication:
    identity: EvcsServiceIdentity
    initial_fields: dict[str, object]


@dataclass(frozen=True, slots=True)
class PublishEvcsFields:
    fields: dict[str, object]
    priority: PublicationPriority


@dataclass(frozen=True, slots=True)
class RegisterCompanionPublication:
    identity: CompanionServiceIdentity
    initial_fields: dict[str, object]


@dataclass(frozen=True, slots=True)
class PublishCompanionFields:
    service_id: str
    fields: dict[str, object]
    priority: PublicationPriority


@dataclass(frozen=True, slots=True)
class _CommonIdentity:
    product_name: str
    custom_name: str
    firmware_version: str
    hardware_version: str
    serial: str
    connection_name: str
    process_name: str
    process_version: str


def register_evcs_command(
    identity: EvcsServiceIdentity,
    initial_fields: Mapping[str, object],
) -> CommandPayload:
    """Build the single coalesced EVCS service-registration command."""
    return {
        "kind": REGISTER_EVCS_KIND,
        "source": "core",
        "identity": asdict(identity),
        "fields": _fields(initial_fields),
        "priority": "publish",
        "queue_class": "startup/register",
        "coalesce_key": "gateway-publication:evcs:registration",
    }


def publish_evcs_fields_command(
    fields: Mapping[str, object],
    *,
    priority: PublicationPriority,
) -> CommandPayload:
    """Build one latest-wins EVCS field publication command."""
    normalized_priority = _publication_priority(priority)
    return {
        "kind": PUBLISH_EVCS_FIELDS_KIND,
        "source": "core",
        "fields": _fields(fields),
        "publication_priority": normalized_priority,
        "priority": _QUEUE_PRIORITY[normalized_priority],
        "coalesce_key": f"gateway-publication:evcs:{normalized_priority}",
    }


def register_companion_command(
    identity: CompanionServiceIdentity,
    initial_fields: Mapping[str, object],
) -> CommandPayload:
    """Build one registration command for an opaque companion identity."""
    service_id = _service_id(identity.service_id)
    return {
        "kind": REGISTER_COMPANION_KIND,
        "source": "companion",
        "identity": asdict(identity),
        "fields": _fields(initial_fields),
        "priority": "publish",
        "queue_class": "startup/register",
        "coalesce_key": f"gateway-publication:companion:{service_id}:registration",
    }


def publish_companion_fields_command(
    service_id: str,
    fields: Mapping[str, object],
    *,
    priority: PublicationPriority,
) -> CommandPayload:
    """Build one latest-wins field command for an opaque companion identity."""
    normalized_id = _service_id(service_id)
    normalized_priority = _publication_priority(priority)
    return {
        "kind": PUBLISH_COMPANION_FIELDS_KIND,
        "source": "companion",
        "service_id": normalized_id,
        "fields": _fields(fields),
        "publication_priority": normalized_priority,
        "priority": _QUEUE_PRIORITY[normalized_priority],
        "coalesce_key": f"gateway-publication:companion:{normalized_id}:{normalized_priority}",
    }


def parse_register_evcs(command: CommandMapping) -> RegisterEvcsPublication | None:
    if command.get("kind") != REGISTER_EVCS_KIND:
        return None
    identity = _parse_evcs_identity(command.get("identity"))
    fields = _parsed_fields(command.get("fields"))
    if identity is None or fields is None:
        return None
    return RegisterEvcsPublication(identity, fields)


def parse_publish_evcs_fields(command: CommandMapping) -> PublishEvcsFields | None:
    if command.get("kind") != PUBLISH_EVCS_FIELDS_KIND:
        return None
    fields = _parsed_fields(command.get("fields"))
    priority = command.get("publication_priority")
    if fields is None or not _is_publication_priority(priority):
        return None
    return PublishEvcsFields(fields, priority)


def parse_register_companion(command: CommandMapping) -> RegisterCompanionPublication | None:
    if command.get("kind") != REGISTER_COMPANION_KIND:
        return None
    identity = _parse_companion_identity(command.get("identity"))
    fields = _parsed_fields(command.get("fields"))
    if identity is None or fields is None:
        return None
    return RegisterCompanionPublication(identity, fields)


def parse_publish_companion_fields(command: CommandMapping) -> PublishCompanionFields | None:
    if command.get("kind") != PUBLISH_COMPANION_FIELDS_KIND:
        return None
    service_id = command.get("service_id")
    fields = _parsed_fields(command.get("fields"))
    priority = command.get("publication_priority")
    if not _is_service_id(service_id) or fields is None or not _is_publication_priority(priority):
        return None
    return PublishCompanionFields(service_id, fields, priority)


def _parse_evcs_identity(value: object) -> EvcsServiceIdentity | None:
    parsed = _common_identity(value, extra_fields=frozenset())
    if parsed is None:
        return None
    identity, _payload = parsed
    return EvcsServiceIdentity(
        product_name=identity.product_name,
        custom_name=identity.custom_name,
        firmware_version=identity.firmware_version,
        hardware_version=identity.hardware_version,
        serial=identity.serial,
        connection_name=identity.connection_name,
        process_name=identity.process_name,
        process_version=identity.process_version,
    )


def _parse_companion_identity(value: object) -> CompanionServiceIdentity | None:
    parsed = _common_identity(value, extra_fields=frozenset(("service_id", "kind")))
    if parsed is None:
        return None
    identity, payload = parsed
    service_id = payload["service_id"]
    kind = payload["kind"]
    if not _is_service_id(service_id) or not _is_companion_kind(kind):
        return None
    return CompanionServiceIdentity(
        service_id=service_id,
        kind=kind,
        product_name=identity.product_name,
        custom_name=identity.custom_name,
        firmware_version=identity.firmware_version,
        hardware_version=identity.hardware_version,
        serial=identity.serial,
        connection_name=identity.connection_name,
        process_name=identity.process_name,
        process_version=identity.process_version,
    )


def _common_identity(
    value: object,
    *,
    extra_fields: frozenset[str],
) -> tuple[_CommonIdentity, dict[str, object]] | None:
    payload = _identity_payload(value, extra_fields)
    if payload is None:
        return None
    identity = _common_identity_values(payload)
    return None if identity is None else (identity, payload)


def _identity_payload(value: object, extra_fields: frozenset[str]) -> dict[str, object] | None:
    payload = _string_keyed_mapping(value)
    if payload is None:
        return None
    expected = {*_COMMON_TEXT_FIELDS, *extra_fields}
    return payload if set(payload) == expected else None


def _common_identity_values(payload: Mapping[str, object]) -> _CommonIdentity | None:
    try:
        return _CommonIdentity(
            *(_required_text(payload[field]) for field in _COMMON_TEXT_FIELDS),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _required_text(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("identity text fields must be strings")
    return value


def _fields(fields: Mapping[str, object]) -> dict[str, object]:
    normalized = _parsed_fields(fields)
    if normalized is None or not normalized:
        raise ValueError("Gateway publication fields must be a non-empty string-keyed mapping")
    return normalized


def _parsed_fields(value: object) -> dict[str, object] | None:
    normalized = _string_keyed_mapping(value)
    if normalized is None:
        return None
    if not all(_valid_field_name(field) for field in normalized):
        return None
    return normalized or None


def _string_keyed_mapping(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    if not all(isinstance(key, str) for key in value):
        return None
    return {str(key): item for key, item in value.items()}


def _valid_field_name(value: object) -> bool:
    return isinstance(value, str) and bool(value) and value == value.strip()


def _service_id(value: str) -> str:
    normalized = value.strip()
    if not _is_service_id(normalized):
        raise ValueError("Companion service_id must be a non-empty opaque identifier")
    return normalized


def _publication_priority(value: object) -> PublicationPriority:
    if not _is_publication_priority(value):
        raise ValueError("Gateway publication priority is invalid")
    return value


def _is_service_id(value: object) -> TypeGuard[str]:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and not any(char.isspace() for char in value)
    )


def _is_publication_priority(value: object) -> TypeGuard[PublicationPriority]:
    return isinstance(value, str) and value in _PUBLICATION_PRIORITIES


def _is_companion_kind(value: object) -> TypeGuard[CompanionServiceKind]:
    return isinstance(value, str) and value in _COMPANION_KINDS


__all__ = [
    "PUBLISH_COMPANION_FIELDS_KIND",
    "PUBLISH_EVCS_FIELDS_KIND",
    "REGISTER_COMPANION_KIND",
    "REGISTER_EVCS_KIND",
    "SEMANTIC_PUBLICATION_KINDS",
    "PublishCompanionFields",
    "PublishEvcsFields",
    "RegisterCompanionPublication",
    "RegisterEvcsPublication",
    "parse_publish_companion_fields",
    "parse_publish_evcs_fields",
    "parse_register_companion",
    "parse_register_evcs",
    "publish_companion_fields_command",
    "publish_evcs_fields_command",
    "register_companion_command",
    "register_evcs_command",
]

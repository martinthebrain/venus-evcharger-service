# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus-specific policy for the gateway command mailbox."""

from __future__ import annotations

from venus_evcharger.dbus_gateway_core import DBUS_GATEWAY_SCHEMA_VERSION
from venus_evcharger.dbus_gateway_policy import command_queue_class
from venus_evcharger.ipc.command_mailbox import (
    FileCommandMailbox,
    command_float,
    command_priority_rank,
    normalized_mapping,
    same_command_priority,
)
from venus_evcharger.ipc.command_types import CommandMapping, CommandOrderKey, CommandPayload
from venus_evcharger.ipc.gateway_publication import (
    PUBLISH_COMPANION_FIELDS_KIND,
    PUBLISH_EVCS_FIELDS_KIND,
    REGISTER_COMPANION_KIND,
    REGISTER_EVCS_KIND,
)

_PUBLISH_FIELDS_KINDS = frozenset((PUBLISH_EVCS_FIELDS_KIND, PUBLISH_COMPANION_FIELDS_KIND))


class DbusGatewayCommandQueuePolicy:
    """Ordering and merge rules that belong specifically to Victron DBus work."""

    schema_version = DBUS_GATEWAY_SCHEMA_VERSION

    @staticmethod
    def normalize(command: CommandMapping) -> CommandPayload:
        return dict(command)

    @staticmethod
    def queue_class(command: CommandMapping) -> str:
        return command_queue_class(command)

    @staticmethod
    def merge_coalesced(existing: CommandMapping | None, payload: CommandPayload) -> None:
        _merge_publish_fields(existing, payload)

    @staticmethod
    def order_key(command: CommandMapping) -> CommandOrderKey:
        return (
            command_priority_rank(command.get("priority")),
            _command_kind_rank(command),
            command_float(command.get("created_at")),
            0,
            str(command.get("id") or ""),
        )


class DbusGatewayCommandInbox(FileCommandMailbox):
    """Mailbox carrying scheduled operations toward the DBus adapter."""

    def __init__(self, command_dir: str) -> None:
        super().__init__(command_dir, policy=DbusGatewayCommandQueuePolicy())


def _merge_publish_fields(existing: object, payload: CommandPayload) -> None:
    existing_fields, payload_fields = _coalesced_publish_field_maps(existing, payload)
    if existing_fields is None or payload_fields is None:
        return
    payload["fields"] = {**dict(existing_fields), **dict(payload_fields)}


def _coalesced_publish_field_maps(
    existing: object,
    payload: CommandMapping,
) -> tuple[CommandMapping | None, CommandMapping | None]:
    existing_mapping = normalized_mapping(existing)
    if existing_mapping is None or not _same_publish_fields_payload(existing_mapping, payload):
        return None, None
    return _field_mapping(existing_mapping), _field_mapping(payload)


def _same_publish_fields_payload(existing: object, payload: CommandMapping) -> bool:
    existing_mapping = normalized_mapping(existing)
    if existing_mapping is None:
        return False
    kind = _compatible_publish_fields_kind(existing_mapping, payload)
    if kind is None:
        return False
    return _same_publication_target(kind, existing_mapping, payload)


def _compatible_publish_fields_kind(
    existing: CommandMapping,
    payload: CommandMapping,
) -> str | None:
    if not _same_kind(existing, payload):
        return None
    if not same_command_priority(existing, payload):
        return None
    kind = _command_kind(payload)
    return kind if kind in _PUBLISH_FIELDS_KINDS else None


def _same_publication_target(
    kind: str,
    existing: CommandMapping,
    payload: CommandMapping,
) -> bool:
    if kind != PUBLISH_COMPANION_FIELDS_KIND:
        return True
    return existing.get("service_id") == payload.get("service_id")


def _same_kind(existing: CommandMapping, payload: CommandMapping) -> bool:
    return _command_kind(existing) == _command_kind(payload)


def _field_mapping(command: CommandMapping) -> CommandMapping | None:
    return normalized_mapping(command.get("fields"))


def _command_kind_rank(command: CommandMapping) -> int:
    kind = _command_kind(command)
    if kind in {REGISTER_EVCS_KIND, REGISTER_COMPANION_KIND}:
        return 0
    if kind in {PUBLISH_EVCS_FIELDS_KIND, PUBLISH_COMPANION_FIELDS_KIND}:
        return 1
    return 2


def _command_kind(command: CommandMapping) -> str:
    return _command_text(command, "kind") or _command_text(command, "type")


def _command_text(command: CommandMapping, key: str) -> str:
    if key not in command:
        return ""
    return str(command[key] or "")

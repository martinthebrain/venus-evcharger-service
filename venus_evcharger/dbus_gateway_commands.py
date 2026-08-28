# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus-specific policy for the gateway command mailbox."""

from __future__ import annotations

from venus_evcharger.dbus_gateway_core import DBUS_GATEWAY_SCHEMA_VERSION
from venus_evcharger.dbus_gateway_policy import command_queue_class
from venus_evcharger.ipc.command_mailbox import (
    MAILBOX_LOCK_TIMEOUT_SECONDS,
    MAILBOX_REVISION_FIELD,
    FileCommandMailbox,
    command_float,
    command_priority_rank,
    normalized_mapping,
    should_replace_command,
)
from venus_evcharger.ipc.command_types import CommandMapping, CommandOrderKey, CommandPayload
from venus_evcharger.ipc.deadline import normalized_transient_deadline
from venus_evcharger.ipc.gateway_publication import (
    PUBLISH_COMPANION_FIELDS_KIND,
    PUBLISH_EVCS_FIELDS_KIND,
    REGISTER_COMPANION_KIND,
    REGISTER_EVCS_KIND,
)
from venus_evcharger.ipc.publication_order import PUBLICATION_FIELD_ORDERS_FIELD
from venus_evcharger.ipc.publication_payload import (
    merge_publication_payload,
    publication_payload_limit_reason,
)

_PUBLISH_FIELDS_KINDS = frozenset((PUBLISH_EVCS_FIELDS_KIND, PUBLISH_COMPANION_FIELDS_KIND))
_TRANSIENT_PUBLICATION_PRIORITIES = frozenset(("live", "diagnostic"))


class DbusGatewayCommandQueuePolicy:
    """Ordering and merge rules that belong specifically to Victron DBus work."""

    schema_version = DBUS_GATEWAY_SCHEMA_VERSION

    @staticmethod
    def normalize(command: CommandMapping) -> CommandPayload:
        payload = dict(command)
        if _is_transient_publication(payload):
            payload["deadline_s"] = normalized_transient_deadline(payload.get("deadline_s"))
        return payload

    @staticmethod
    def queue_class(command: CommandMapping) -> str:
        return command_queue_class(command)

    @staticmethod
    def merge_coalesced(existing: CommandMapping | None, payload: CommandPayload) -> bool:
        return _merge_publish_fields(existing, payload)

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

    def __init__(
        self,
        command_dir: str,
        *,
        lock_timeout_seconds: float = MAILBOX_LOCK_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__(
            command_dir,
            policy=DbusGatewayCommandQueuePolicy(),
            lock_timeout_seconds=lock_timeout_seconds,
        )

    def enqueue(self, command: CommandMapping) -> str:
        normalized = DbusGatewayCommandQueuePolicy.normalize(command)
        _raise_for_oversized_publication(normalized)
        return super().enqueue(normalized)


def _merge_publish_fields(
    existing: CommandMapping | None,
    payload: CommandPayload,
) -> bool:
    if existing is None or not _same_publish_fields_payload(existing, payload):
        return False
    accepted_fields = _accepted_lower_priority_fields(existing, payload)
    if accepted_fields == ():
        return False
    merged = _merged_publish_command(existing, payload, accepted_fields)
    _raise_for_oversized_publication(merged)
    payload.clear()
    payload.update(merged)
    return True


def _merged_publish_command(
    existing: CommandMapping,
    candidate: CommandMapping,
    accepted_fields: tuple[str, ...] | None,
) -> CommandPayload:
    merged_fields = merge_publication_payload(
        existing,
        candidate,
        accepted_fields=accepted_fields,
    )
    winner = candidate if should_replace_command(existing, candidate) else existing
    merged = dict(winner)
    merged["fields"] = merged_fields["fields"]
    _set_merged_field_orders(merged, merged_fields)
    _set_candidate_revision(merged, candidate)
    return merged


def _set_merged_field_orders(
    merged: CommandPayload,
    merged_fields: CommandMapping,
) -> None:
    field_orders = merged_fields.get(PUBLICATION_FIELD_ORDERS_FIELD)
    if field_orders is None:
        merged.pop(PUBLICATION_FIELD_ORDERS_FIELD, None)
        return
    merged[PUBLICATION_FIELD_ORDERS_FIELD] = field_orders


def _set_candidate_revision(
    merged: CommandPayload,
    candidate: CommandMapping,
) -> None:
    revision = candidate.get(MAILBOX_REVISION_FIELD)
    if revision is None:
        merged.pop(MAILBOX_REVISION_FIELD, None)
        return
    merged[MAILBOX_REVISION_FIELD] = revision


def _same_publish_fields_payload(
    existing: CommandMapping,
    payload: CommandMapping,
) -> bool:
    kind = _compatible_publish_fields_kind(existing, payload)
    if kind is None:
        return False
    return _same_publication_target(kind, existing, payload)


def _compatible_publish_fields_kind(
    existing: CommandMapping,
    payload: CommandMapping,
) -> str | None:
    if not _same_kind(existing, payload):
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


def _accepted_lower_priority_fields(
    existing: CommandMapping,
    candidate: CommandMapping,
) -> tuple[str, ...] | None:
    if not _candidate_has_lower_priority(existing, candidate):
        return None
    existing_fields = normalized_mapping(existing.get("fields")) or {}
    candidate_fields = normalized_mapping(candidate.get("fields")) or {}
    return _candidate_only_fields(existing_fields, candidate_fields)


def _candidate_only_fields(
    existing_fields: CommandMapping,
    candidate_fields: CommandMapping,
) -> tuple[str, ...]:
    return tuple(field for field in candidate_fields if field not in existing_fields)


def _candidate_has_lower_priority(
    existing: CommandMapping,
    candidate: CommandMapping,
) -> bool:
    existing_rank = command_priority_rank(existing.get("priority"))
    candidate_rank = command_priority_rank(candidate.get("priority"))
    return candidate_rank > existing_rank


def _command_kind_rank(command: CommandMapping) -> int:
    kind = _command_kind(command)
    if kind in {REGISTER_EVCS_KIND, REGISTER_COMPANION_KIND}:
        return 0
    if kind in {PUBLISH_EVCS_FIELDS_KIND, PUBLISH_COMPANION_FIELDS_KIND}:
        return 1
    return 2


def _command_kind(command: CommandMapping) -> str:
    return _command_text(command, "kind")


def _is_transient_publication(command: CommandMapping) -> bool:
    return (
        _command_kind(command) in _PUBLISH_FIELDS_KINDS
        and str(command.get("publication_priority") or "") in _TRANSIENT_PUBLICATION_PRIORITIES
    )


def _raise_for_oversized_publication(command: CommandMapping) -> None:
    if _command_kind(command) not in _PUBLISH_FIELDS_KINDS:
        return
    reason = publication_payload_limit_reason(command)
    if reason:
        raise ValueError(f"Gateway publication violates {reason}")


def _command_text(command: CommandMapping, key: str) -> str:
    if key not in command:
        return ""
    return str(command[key] or "")

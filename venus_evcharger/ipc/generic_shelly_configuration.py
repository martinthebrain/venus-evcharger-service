# SPDX-License-Identifier: GPL-3.0-or-later
"""Validated IPC contract for generic Shelly configuration."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TypeGuard

from venus_evcharger.ipc.command_types import CommandMapping, CommandPayload
from venus_evcharger.ports.generic_shelly_configuration import (
    DisableMatchingGenericShellyOnceRequest,
    GenericShellyDeviceSelector,
    GenericShellySelectorKind,
)

DISABLE_MATCHING_GENERIC_SHELLY_ONCE_KIND = "disable_matching_generic_shelly_once"
GENERIC_SHELLY_CONFIGURATION_SOURCE = "generic-shelly-configuration"
GENERIC_SHELLY_CONFIGURATION_QUEUE_CLASS = "remote-write"
GENERIC_SHELLY_CONFIGURATION_SCHEMA_VERSION = 1

_BASE_FIELDS = frozenset(
    (
        "kind",
        "source",
        "selector",
        "channel",
        "execution",
        "persistence",
        "priority",
        "coalesce_key",
    )
)
_TRANSPORT_REQUIRED_FIELDS = frozenset(
    ("schema_version", "id", "created_at", "queue_class", "lifecycle_state")
)
_TRANSPORT_OPTIONAL_FIELDS = frozenset(("updated_at",))
_LIFECYCLE_STATES = frozenset(("queued", "coalesced", "deferred", "applied", "dropped", "expired"))


@dataclass(frozen=True, slots=True)
class DisableMatchingGenericShellyOnceOperation:
    """Validated operation consumed by the gateway adapter."""

    request: DisableMatchingGenericShellyOnceRequest


def disable_matching_generic_shelly_once_command(
    request: DisableMatchingGenericShellyOnceRequest,
) -> CommandPayload:
    """Build a deterministic, latest-wins persistent disable command."""
    coalesce_key = _coalesce_key(request)
    return {
        "kind": DISABLE_MATCHING_GENERIC_SHELLY_ONCE_KIND,
        "source": GENERIC_SHELLY_CONFIGURATION_SOURCE,
        "selector": {
            "kind": request.selector.kind,
            "value": request.selector.value,
        },
        "channel": request.channel,
        "execution": "once",
        "persistence": "persistent",
        "priority": "user",
        "coalesce_key": coalesce_key,
    }


def parse_disable_matching_generic_shelly_once(
    command: CommandMapping,
) -> DisableMatchingGenericShellyOnceOperation | None:
    """Validate an untrusted initial or mailbox-enveloped configuration command."""
    if not _valid_field_set(command):
        return None
    request = _parsed_request(command)
    if request is None or not _valid_semantic_envelope(command, request):
        return None
    if not _valid_transport_envelope(command):
        return None
    return DisableMatchingGenericShellyOnceOperation(request)


def _parsed_request(command: CommandMapping) -> DisableMatchingGenericShellyOnceRequest | None:
    selector = _parsed_selector(command.get("selector"))
    channel = command.get("channel")
    if selector is None or type(channel) is not int:
        return None
    try:
        return DisableMatchingGenericShellyOnceRequest(selector, channel)
    except ValueError:
        return None


def _parsed_selector(value: object) -> GenericShellyDeviceSelector | None:
    payload = _selector_payload(value)
    if payload is None:
        return None
    kind = payload.get("kind")
    selector_value = payload.get("value")
    if not _is_selector_kind(kind) or not isinstance(selector_value, str):
        return None
    return _canonical_selector(kind, selector_value)


def _selector_payload(value: object) -> Mapping[str, object] | None:
    if not _is_string_mapping(value):
        return None
    return value if set(value) == {"kind", "value"} else None


def _canonical_selector(kind: GenericShellySelectorKind, value: str) -> GenericShellyDeviceSelector | None:
    try:
        selector = GenericShellyDeviceSelector(kind, value)
    except (TypeError, ValueError):
        return None
    return selector if selector.value == value else None


def _valid_semantic_envelope(
    command: CommandMapping,
    request: DisableMatchingGenericShellyOnceRequest,
) -> bool:
    return all(
        (
            command.get("kind") == DISABLE_MATCHING_GENERIC_SHELLY_ONCE_KIND,
            command.get("source") == GENERIC_SHELLY_CONFIGURATION_SOURCE,
            command.get("execution") == "once",
            command.get("persistence") == "persistent",
            command.get("priority") == "user",
            command.get("coalesce_key") == _coalesce_key(request),
        )
    )


def _valid_field_set(command: CommandMapping) -> bool:
    fields = frozenset(command)
    transport_fields = fields - _BASE_FIELDS
    if not transport_fields:
        return fields == _BASE_FIELDS
    allowed = _TRANSPORT_REQUIRED_FIELDS | _TRANSPORT_OPTIONAL_FIELDS
    return fields >= (_BASE_FIELDS | _TRANSPORT_REQUIRED_FIELDS) and transport_fields <= allowed


def _valid_transport_envelope(command: CommandMapping) -> bool:
    if "schema_version" not in command:
        return True
    if not _valid_required_transport_fields(command):
        return False
    updated_at = command.get("updated_at")
    return updated_at is None or _is_positive_finite_number(updated_at)


def _valid_required_transport_fields(command: CommandMapping) -> bool:
    schema_version = command.get("schema_version")
    command_id = command.get("id")
    created_at = command.get("created_at")
    lifecycle_state = command.get("lifecycle_state")
    return all(
        (
            type(schema_version) is int,
            schema_version == GENERIC_SHELLY_CONFIGURATION_SCHEMA_VERSION,
            _is_non_empty_text(command_id),
            _is_positive_finite_number(created_at),
            command.get("queue_class") == GENERIC_SHELLY_CONFIGURATION_QUEUE_CLASS,
            isinstance(lifecycle_state, str) and lifecycle_state in _LIFECYCLE_STATES,
        )
    )


def _coalesce_key(request: DisableMatchingGenericShellyOnceRequest) -> str:
    selector = request.selector
    return f"generic-shelly-configuration:disable-once:{selector.kind}:{selector.value}:channel:{request.channel}"


def _is_positive_finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    normalized = float(value)
    return normalized > 0.0 and math.isfinite(normalized)


def _is_non_empty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_string_mapping(value: object) -> TypeGuard[Mapping[str, object]]:
    return isinstance(value, Mapping) and all(isinstance(key, str) for key in value)


def _is_selector_kind(value: object) -> TypeGuard[GenericShellySelectorKind]:
    return isinstance(value, str) and value in ("ip", "mac")

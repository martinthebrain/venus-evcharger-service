# SPDX-License-Identifier: GPL-3.0-or-later
"""Semantic command contract between control adapters and the core process."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TypeGuard

from venus_evcharger.control.models import ControlCommandName, ControlCommandSource
from venus_evcharger.core.contracts_control import CONTROL_COMMAND_SOURCES
from venus_evcharger.core.contracts_control_surface import (
    CONTROL_AUTO_RUNTIME_TARGETS,
    CONTROL_COMMAND_DEFAULT_TARGETS,
    CONTROL_COMMAND_NAMES,
    CONTROL_CURRENT_SETTING_TARGETS,
)
from venus_evcharger.ipc.command_mailbox import (
    FileCommandMailbox,
    command_float,
    command_priority_rank,
)
from venus_evcharger.ipc.command_types import CommandMapping, CommandOrderKey, CommandPayload

CORE_COMMAND_SCHEMA_VERSION = 1
CORE_COMMAND_QUEUE_CLASS = "core-control"
CORE_USER_COMMAND_KIND = "user_command"
DEFAULT_CORE_COMMAND_DIR = "/run/venus-evcharger/core-commands"
CORE_COMMAND_RETRY_INITIAL_SECONDS = 0.5
CORE_COMMAND_RETRY_MAX_SECONDS = 30.0
CORE_COMMAND_RETRY_MAX_EXPONENT = 6


@dataclass(frozen=True)
class CoreControlCommand:
    """Validated external control command consumed by the core."""

    name: ControlCommandName
    target: str
    value: object
    source: ControlCommandSource
    origin: str
    command_id: str
    created_at: float


@dataclass(frozen=True, slots=True)
class _CoreControlEnvelope:
    """Normalized fields shared by validation and command construction."""

    name: ControlCommandName
    target: str
    source: ControlCommandSource
    origin: str
    command_id: str
    created_at: float

    def to_command(self, value: object) -> CoreControlCommand:
        return CoreControlCommand(
            name=self.name,
            target=self.target,
            value=value,
            source=self.source,
            origin=self.origin,
            command_id=self.command_id,
            created_at=self.created_at,
        )


class CoreCommandQueuePolicy:
    """Ordering and envelope policy for commands entering the core."""

    schema_version = CORE_COMMAND_SCHEMA_VERSION

    @staticmethod
    def normalize(command: CommandMapping) -> CommandPayload:
        return dict(command)

    @staticmethod
    def queue_class(command: CommandMapping) -> str:
        del command
        return CORE_COMMAND_QUEUE_CLASS

    @staticmethod
    def merge_coalesced(existing: CommandMapping | None, payload: CommandPayload) -> bool:
        del existing, payload
        return False

    @staticmethod
    def order_key(command: CommandMapping) -> CommandOrderKey:
        return (
            command_priority_rank(command.get("priority")),
            0,
            command_float(command.get("created_at")),
            0,
            str(command.get("id") or ""),
        )


class CoreCommandMailbox(FileCommandMailbox):
    """File-backed mailbox carrying validated control envelopes to the core."""

    def __init__(self, command_dir: str) -> None:
        super().__init__(command_dir, policy=CoreCommandQueuePolicy())


def core_command_retry_delay(failure_count: int) -> float:
    """Return bounded exponential delay for a durable core command retry."""
    exponent = min(max(0, failure_count - 1), CORE_COMMAND_RETRY_MAX_EXPONENT)
    return min(
        CORE_COMMAND_RETRY_MAX_SECONDS,
        CORE_COMMAND_RETRY_INITIAL_SECONDS * float(2**exponent),
    )


def core_control_command_payload(
    name: ControlCommandName,
    target: str,
    value: object,
    *,
    source: ControlCommandSource,
    origin: str,
) -> CommandPayload:
    """Build one valid control envelope for delivery to the core."""
    normalized_target = _control_target(target)
    _require_supported_route(name, normalized_target)
    return {
        "kind": CORE_USER_COMMAND_KIND,
        "name": name,
        "target": normalized_target,
        "source": _required_text(source, "source"),
        "origin": _required_text(origin, "origin"),
        "value": value,
        "priority": "user",
        "coalesce_key": f"core:{name}:{normalized_target}",
    }


def parse_core_control_command(payload: CommandMapping) -> CoreControlCommand | None:
    """Validate an untrusted mailbox payload before core dispatch."""
    envelope = _parse_core_control_envelope(payload)
    return None if envelope is None else envelope.to_command(payload.get("value"))


def _parse_core_control_envelope(payload: CommandMapping) -> _CoreControlEnvelope | None:
    text_fields = _core_control_text_fields(payload)
    if text_fields is None:
        return None
    kind, target, origin, command_id = text_fields
    route = _typed_control_route(payload)
    if route is None:
        return None
    name, source = route
    created_at = command_float(payload.get("created_at"))
    if not _valid_core_control_envelope(
        payload,
        kind=kind,
        name=name,
        target=target,
        created_at=created_at,
    ):
        return None
    return _CoreControlEnvelope(name, target, source, origin, command_id, created_at)


def _core_control_text_fields(payload: CommandMapping) -> tuple[str, str, str, str] | None:
    kind = _normalized_string(payload.get("kind"))
    target = _normalized_string(payload.get("target"))
    origin = _normalized_string(payload.get("origin"))
    command_id = _normalized_string(payload.get("id"))
    if kind is None or target is None or origin is None or command_id is None:
        return None
    return kind, target, origin, command_id


def _typed_control_route(
    payload: CommandMapping,
) -> tuple[ControlCommandName, ControlCommandSource] | None:
    name = payload.get("name")
    if not _is_control_command_name(name):
        return None
    source = payload.get("source")
    if not _is_control_command_source(source):
        return None
    return name, source


def _valid_core_control_envelope(
    payload: CommandMapping,
    *,
    kind: str,
    name: ControlCommandName,
    target: str,
    created_at: float,
) -> bool:
    return all(
        (
            _valid_transport_header(payload),
            _valid_control_route(payload, kind=kind, name=name, target=target),
            _valid_created_at(created_at),
        )
    )


def _valid_transport_header(payload: CommandMapping) -> bool:
    schema_version = payload.get("schema_version")
    return all(
        (
            type(schema_version) is int,
            schema_version == CORE_COMMAND_SCHEMA_VERSION,
            payload.get("queue_class") == CORE_COMMAND_QUEUE_CLASS,
        )
    )


def _valid_control_route(
    payload: CommandMapping,
    *,
    kind: str,
    name: ControlCommandName,
    target: str,
) -> bool:
    return all(
        (
            kind == CORE_USER_COMMAND_KIND,
            _route_supported(name, target),
            payload.get("priority") == "user",
            payload.get("coalesce_key") == f"core:{name}:{target}",
        )
    )


def _valid_created_at(created_at: float) -> bool:
    return all((created_at > 0.0, math.isfinite(created_at)))


def _control_target(target: str) -> str:
    normalized = target.strip()
    if not normalized:
        raise ValueError("Core control target must not be empty")
    return normalized


def _require_supported_route(name: ControlCommandName, target: str) -> None:
    if not _route_supported(name, target):
        raise ValueError(f"Unsupported core control route: {name}:{target}")


def _route_supported(name: ControlCommandName, target: str) -> bool:
    if name == "set_auto_runtime_setting":
        return target in CONTROL_AUTO_RUNTIME_TARGETS
    if name == "set_current_setting":
        return target in CONTROL_CURRENT_SETTING_TARGETS
    return bool(CONTROL_COMMAND_DEFAULT_TARGETS.get(name) == target)


def _required_text(value: str, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"Core control {field} must not be empty")
    return normalized


def _normalized_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _is_control_command_source(value: object) -> TypeGuard[ControlCommandSource]:
    return isinstance(value, str) and value in CONTROL_COMMAND_SOURCES


def _is_control_command_name(value: object) -> TypeGuard[ControlCommandName]:
    return isinstance(value, str) and value in CONTROL_COMMAND_NAMES

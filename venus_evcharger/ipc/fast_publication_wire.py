# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded binary framing for the local fast-publication socket."""

from __future__ import annotations

import plistlib
import struct
from collections.abc import Mapping
from typing import TypeGuard

from venus_evcharger.ipc.command_types import CommandMapping, CommandPayload
from venus_evcharger.ipc.publication_order import (
    PUBLICATION_FIELD_ORDERS_FIELD,
    PUBLICATION_ORDER_FIELD,
)
from venus_evcharger.ipc.publication_payload import (
    MAX_PUBLICATION_COALESCE_KEY_BYTES,
    MAX_PUBLICATION_FIELD_NAME_BYTES,
    MAX_PUBLICATION_FIELDS_PER_KEY,
    MAX_PUBLICATION_PAYLOAD_BYTES,
    publication_fields,
)

FAST_PUBLICATION_WIRE_MAGIC = b"EVCF"
FAST_PUBLICATION_WIRE_VERSION = 1
FAST_PUBLICATION_WIRE_MAX_PAYLOAD_BYTES = 64 * 1024
_FRAME_HEADER = struct.Struct("!4sBI")
FAST_PUBLICATION_WIRE_HEADER_BYTES = _FRAME_HEADER.size


class FastPublicationWireError(ValueError):
    """Reject malformed, unsupported, or oversized local IPC frames."""


def encode_fast_publication_frame(payload: CommandMapping) -> bytes:
    """Encode one string-keyed mapping without external runtime dependencies."""
    body = _encode_body(payload)
    _validate_body_size(len(body))
    header = _FRAME_HEADER.pack(
        FAST_PUBLICATION_WIRE_MAGIC,
        FAST_PUBLICATION_WIRE_VERSION,
        len(body),
    )
    return header + body


def fast_publication_payload_limit_reason(payload: CommandMapping) -> str:
    """Validate fast payload bounds without a JSON serialization pass."""
    fields = publication_fields(payload)
    for reason in (
        _coalesce_key_limit_reason(payload),
        _field_shape_limit_reason(fields),
        _binary_payload_limit_reason(payload),
    ):
        if reason:
            return reason
    return ""


def _coalesce_key_limit_reason(payload: CommandMapping) -> str:
    raw_key = payload.get("coalesce_key")
    if raw_key is None:
        return ""
    key = str(raw_key)
    too_large = len(key.encode("utf-8")) > MAX_PUBLICATION_COALESCE_KEY_BYTES
    return "coalesce-key-too-large" if too_large else ""


def _field_shape_limit_reason(fields: Mapping[str, object]) -> str:
    if len(fields) > MAX_PUBLICATION_FIELDS_PER_KEY:
        return "field-limit"
    names_too_large = any(
        len(field.encode("utf-8")) > MAX_PUBLICATION_FIELD_NAME_BYTES
        for field in fields
    )
    return "field-name-too-large" if names_too_large else ""


def _binary_payload_limit_reason(payload: CommandMapping) -> str:
    try:
        body = _encode_body(payload)
    except FastPublicationWireError:
        return "payload-not-encodable"
    return "payload-limit" if len(body) > MAX_PUBLICATION_PAYLOAD_BYTES else ""


def decode_fast_publication_frame(frame: bytes) -> CommandPayload:
    """Decode one complete frame and validate its transport-neutral shape."""
    body_size = fast_publication_frame_size(frame)
    if len(frame) != body_size:
        raise FastPublicationWireError("frame-size-mismatch")
    try:
        decoded: object = plistlib.loads(frame[FAST_PUBLICATION_WIRE_HEADER_BYTES:])
    except (plistlib.InvalidFileException, OverflowError, TypeError, ValueError) as error:
        raise FastPublicationWireError("payload-not-decodable") from error
    return _restore_publication_orders(_string_mapping(decoded))


def fast_publication_frame_size(data: bytes | bytearray) -> int:
    """Return the complete frame size once its header is available."""
    if len(data) < FAST_PUBLICATION_WIRE_HEADER_BYTES:
        return 0
    unpacked = _FRAME_HEADER.unpack_from(data)
    magic = bytes(unpacked[0])
    version = int(unpacked[1])
    body_size = int(unpacked[2])
    if magic != FAST_PUBLICATION_WIRE_MAGIC:
        raise FastPublicationWireError("invalid-frame-magic")
    if version != FAST_PUBLICATION_WIRE_VERSION:
        raise FastPublicationWireError("unsupported-frame-version")
    _validate_body_size(body_size)
    return FAST_PUBLICATION_WIRE_HEADER_BYTES + body_size


def _validate_body_size(body_size: int) -> None:
    if body_size <= 0:
        raise FastPublicationWireError("empty-frame")
    if body_size > FAST_PUBLICATION_WIRE_MAX_PAYLOAD_BYTES:
        raise FastPublicationWireError("frame-too-large")


def _encode_body(payload: CommandMapping) -> bytes:
    try:
        return plistlib.dumps(
            _plist_ready_payload(payload),
            fmt=plistlib.FMT_BINARY,
        )
    except (
        OverflowError,
        TypeError,
        ValueError,
        plistlib.InvalidFileException,
    ) as error:
        raise FastPublicationWireError("payload-not-encodable") from error


def _plist_ready_payload(payload: CommandMapping) -> CommandPayload:
    ready = dict(payload)
    order = ready.get(PUBLICATION_ORDER_FIELD)
    if type(order) is int:
        ready[PUBLICATION_ORDER_FIELD] = str(order)
    field_orders = ready.get(PUBLICATION_FIELD_ORDERS_FIELD)
    if _is_object_mapping(field_orders):
        ready[PUBLICATION_FIELD_ORDERS_FIELD] = {
            str(field): str(value) if type(value) is int else value
            for field, value in field_orders.items()
        }
    return ready


def _restore_publication_orders(payload: CommandPayload) -> CommandPayload:
    restored = dict(payload)
    restored[PUBLICATION_ORDER_FIELD] = _decimal_integer_or_original(
        restored.get(PUBLICATION_ORDER_FIELD)
    )
    field_orders = restored.get(PUBLICATION_FIELD_ORDERS_FIELD)
    if _is_object_mapping(field_orders):
        restored[PUBLICATION_FIELD_ORDERS_FIELD] = {
            str(field): _decimal_integer_or_original(value)
            for field, value in field_orders.items()
        }
    if PUBLICATION_ORDER_FIELD not in payload:
        restored.pop(PUBLICATION_ORDER_FIELD)
    return restored


def _decimal_integer_or_original(value: object) -> object:
    if isinstance(value, str) and value.removeprefix("-").isdecimal():
        return int(value)
    return value


def _string_mapping(value: object) -> CommandPayload:
    if not _is_object_mapping(value):
        raise FastPublicationWireError("payload-must-be-object")
    if not all(isinstance(key, str) for key in value):
        raise FastPublicationWireError("payload-keys-must-be-strings")
    return {str(key): item for key, item in value.items()}


def _is_object_mapping(value: object) -> TypeGuard[Mapping[object, object]]:
    return isinstance(value, Mapping)


__all__ = [
    "FAST_PUBLICATION_WIRE_HEADER_BYTES",
    "FAST_PUBLICATION_WIRE_MAGIC",
    "FAST_PUBLICATION_WIRE_MAX_PAYLOAD_BYTES",
    "FAST_PUBLICATION_WIRE_VERSION",
    "FastPublicationWireError",
    "decode_fast_publication_frame",
    "encode_fast_publication_frame",
    "fast_publication_payload_limit_reason",
    "fast_publication_frame_size",
]

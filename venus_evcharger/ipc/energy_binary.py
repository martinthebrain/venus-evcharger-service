# SPDX-License-Identifier: GPL-3.0-or-later
"""Compact, dependency-free wire format for frequently read energy inputs."""

from __future__ import annotations

import struct
import time
from pathlib import Path
from typing import cast

from venus_evcharger.core.shared import write_bytes_atomically
from venus_evcharger.ipc.energy import (
    ENERGY_IPC_SCHEMA_VERSION,
    EnergyInputsSnapshot,
    EnergyValueStatus,
    MeasuredValue,
)

_MAGIC = b"VEI1"
_MAX_PAYLOAD_BYTES = 65536
_MAX_SOURCE_IDS = 64
_HEADER = struct.Struct(">4sBQQd")
_MEASUREMENT = struct.Struct(">BBdddH")
_TEXT_LENGTH = struct.Struct(">H")
_MeasurementFields = tuple[int, int, float, float, float, int]
_PAYLOAD_SIZE_ERROR = "energy inputs binary payload exceeds the size limit"
_INVALID_MAGIC_ERROR = "energy inputs binary payload has invalid magic"
_UNSUPPORTED_SCHEMA_ERROR = "energy inputs binary payload has unsupported schema_version"
_TOO_MANY_SOURCES_ERROR = "energy measurement has too many source_ids"
_INVALID_STATUS_ERROR = "energy measurement has invalid status"
_INVALID_VALUE_MARKER_ERROR = "energy measurement has invalid value marker"
_TEXT_SIZE_ERROR = "energy inputs text field exceeds the size limit"
_WIRE_RANGE_ERROR = "energy inputs value is outside the binary wire range"
_TRUNCATED_PAYLOAD_ERROR = "energy inputs binary payload is truncated"
_TRUNCATED_TEXT_ERROR = "energy inputs binary text field is truncated"
_INVALID_UTF8_ERROR = "energy inputs binary text field is not UTF-8"
_TRAILING_DATA_ERROR = "energy inputs binary payload has trailing data"
_STATUS_TO_CODE: dict[EnergyValueStatus, int] = {
    "fresh": 0,
    "stale": 1,
    "unavailable": 2,
    "error": 3,
    "unknown": 4,
}
_CODE_TO_STATUS: dict[int, EnergyValueStatus] = {
    code: status for status, code in _STATUS_TO_CODE.items()
}


def encode_energy_inputs(snapshot: EnergyInputsSnapshot) -> bytes:
    """Encode one validated semantic snapshot without intermediate JSON objects."""
    chunks = [
        _pack(
            _HEADER,
            _MAGIC,
            snapshot.schema_version,
            snapshot.sequence,
            snapshot.topology_generation,
            snapshot.captured_at,
        )
    ]
    for measurement in (snapshot.grid_power_w, snapshot.pv_power_w, snapshot.battery_soc):
        chunks.extend(_encode_measurement(measurement))
    payload = b"".join(chunks)
    if len(payload) > _MAX_PAYLOAD_BYTES:
        raise ValueError(_PAYLOAD_SIZE_ERROR)
    return payload


def decode_energy_inputs(payload: bytes) -> EnergyInputsSnapshot:
    """Decode and validate one complete semantic energy-input snapshot."""
    if len(payload) > _MAX_PAYLOAD_BYTES:
        raise ValueError(_PAYLOAD_SIZE_ERROR)
    reader = _BinaryReader(payload)
    unpacked_header = reader.unpack(_HEADER)
    header = cast(tuple[bytes, int, int, int, float], unpacked_header)  # pragma: no mutate
    magic, schema_version, sequence, topology_generation, captured_at = header
    if magic != _MAGIC:
        raise ValueError(_INVALID_MAGIC_ERROR)
    if schema_version != ENERGY_IPC_SCHEMA_VERSION:
        raise ValueError(_UNSUPPORTED_SCHEMA_ERROR)
    measurements = tuple(_decode_measurement(reader) for _unused in range(3))
    reader.require_complete()
    return EnergyInputsSnapshot(
        sequence=sequence,
        captured_at=captured_at,
        topology_generation=topology_generation,
        grid_power_w=measurements[0],
        pv_power_w=measurements[1],
        battery_soc=measurements[2],
    )


def write_energy_inputs_file(path: str, snapshot: EnergyInputsSnapshot) -> None:
    """Publish one binary snapshot atomically."""
    write_bytes_atomically(path, encode_energy_inputs(snapshot))


def load_energy_inputs_file(
    path: str,
    *,
    max_age_seconds: float,
    now: float | None = None,
) -> EnergyInputsSnapshot | None:
    """Load a fresh binary snapshot, returning ``None`` for absent or invalid data."""
    try:
        snapshot = decode_energy_inputs(Path(path).read_bytes())
    except (OSError, TypeError, ValueError):
        return None
    current = time.time() if now is None else float(now)
    if max_age_seconds >= 0.0 and current - snapshot.captured_at > float(max_age_seconds):
        return None
    return snapshot


def _encode_measurement(measurement: MeasuredValue) -> list[bytes]:
    source_ids = measurement.source_ids
    if len(source_ids) > _MAX_SOURCE_IDS:
        raise ValueError(_TOO_MANY_SOURCES_ERROR)
    has_value = measurement.value is not None
    chunks = [
        _pack(
            _MEASUREMENT,
            _STATUS_TO_CODE[measurement.status],
            int(has_value),
            measurement.value if has_value else 0.0,
            measurement.observed_at,
            measurement.confidence,
            len(source_ids),
        )
    ]
    chunks.extend(_encode_text(source_id) for source_id in source_ids)
    chunks.append(_encode_text(measurement.reason_code))
    return chunks


def _decode_measurement(reader: _BinaryReader) -> MeasuredValue:
    unpacked_fields = reader.unpack(_MEASUREMENT)
    fields = cast(_MeasurementFields, unpacked_fields)  # pragma: no mutate
    status_code, has_value, value, observed_at, confidence, source_count = fields
    status = _decoded_status(status_code)
    decoded_value = _decoded_value(has_value, value)
    source_ids = tuple(reader.text() for _unused in range(_validated_source_count(source_count)))
    return MeasuredValue(
        value=decoded_value,
        observed_at=observed_at,
        status=status,
        confidence=confidence,
        source_ids=source_ids,
        reason_code=reader.text(),
    )


def _decoded_status(status_code: int) -> EnergyValueStatus:
    if status_code not in _CODE_TO_STATUS:
        raise ValueError(_INVALID_STATUS_ERROR)
    return _CODE_TO_STATUS[status_code]


def _decoded_value(has_value: int, value: float) -> float | None:
    if has_value not in (0, 1):
        raise ValueError(_INVALID_VALUE_MARKER_ERROR)
    return value if has_value else None


def _validated_source_count(source_count: int) -> int:
    if source_count > _MAX_SOURCE_IDS:
        raise ValueError(_TOO_MANY_SOURCES_ERROR)
    return source_count


def _encode_text(value: str) -> bytes:
    encoded = value.encode()
    if len(encoded) > 65535:
        raise ValueError(_TEXT_SIZE_ERROR)
    return _TEXT_LENGTH.pack(len(encoded)) + encoded


def _pack(formatter: struct.Struct, *values: object) -> bytes:
    try:
        return formatter.pack(*values)
    except struct.error as error:
        raise ValueError(_WIRE_RANGE_ERROR) from error


class _BinaryReader:
    def __init__(self, payload: bytes) -> None:
        self._payload = memoryview(payload)
        self._offset = 0

    def unpack(self, formatter: struct.Struct) -> tuple[object, ...]:
        end = self._offset + formatter.size
        if end > len(self._payload):
            raise ValueError(_TRUNCATED_PAYLOAD_ERROR)
        values = formatter.unpack_from(self._payload, self._offset)
        self._offset = end
        return values

    def text(self) -> str:
        unpacked_size = self.unpack(_TEXT_LENGTH)
        (size,) = cast(tuple[int], unpacked_size)  # pragma: no mutate
        end = self._offset + int(size)
        if end > len(self._payload):
            raise ValueError(_TRUNCATED_TEXT_ERROR)
        try:
            value = self._payload[self._offset : end].tobytes().decode()
        except UnicodeDecodeError as error:
            raise ValueError(_INVALID_UTF8_ERROR) from error
        self._offset = end
        return value

    def require_complete(self) -> None:
        if self._offset != len(self._payload):
            raise ValueError(_TRAILING_DATA_ERROR)


__all__ = [
    "decode_energy_inputs",
    "encode_energy_inputs",
    "load_energy_inputs_file",
    "write_energy_inputs_file",
]

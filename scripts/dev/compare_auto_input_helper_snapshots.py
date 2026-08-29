#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compare two bounded schema-v2 auto-input snapshots semantically."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypeGuard

MAX_SNAPSHOT_BYTES: Final = 256 * 1024
MAX_SNAPSHOT_DEPTH: Final = 16
MAX_SNAPSHOT_NODES: Final = 4096
MAX_REPORTED_DIFFERENCES: Final = 32
SNAPSHOT_VERSION: Final = 2

IDENTITY_FIELDS: Final = frozenset(
    {
        "snapshot_sequence",
        "writer_pid",
        "helper_generation",
        "runtime_instance_id",
    }
)
DIRECT_TIME_FIELDS: Final = frozenset(
    {
        "captured_at",
        "captured_monotonic",
        "heartbeat_at",
        "heartbeat_monotonic",
        "attempted_at",
        "next_poll_at",
        "observed_at",
        "observed_monotonic",
        "age_seconds",
    }
)
TIME_FIELD_SUFFIXES: Final = (
    "_captured_at",
    "_observed_at",
    "_observed_monotonic",
    "_age_seconds",
)
REQUIRED_FIELDS: Final = frozenset(
    {
        "snapshot_version",
        "snapshot_sequence",
        "captured_at",
        "captured_monotonic",
        "heartbeat_at",
        "heartbeat_monotonic",
        "pv_captured_at",
        "pv_observed_monotonic",
        "pv_power",
        "battery_captured_at",
        "battery_observed_monotonic",
        "battery_soc",
        "grid_captured_at",
        "grid_observed_monotonic",
        "grid_power",
        "writer_pid",
        "helper_generation",
        "runtime_instance_id",
    }
)


class SnapshotParityError(ValueError):
    """Describe an invalid or unsafe snapshot input."""


@dataclass(frozen=True, slots=True)
class ComparisonResult:
    """Bounded semantic comparison result."""

    differences: tuple[str, ...]
    truncated: bool = False

    @property
    def equal(self) -> bool:
        """Return whether both normalized snapshots are semantically equal."""
        return not self.differences


@dataclass(slots=True)
class _DifferenceCollector:
    items: list[str]
    truncated: bool = False

    def add(self, message: str) -> None:
        if len(self.items) < MAX_REPORTED_DIFFERENCES:
            self.items.append(message)
        else:
            self.truncated = True


def _reject_json_constant(value: str) -> object:
    raise SnapshotParityError(f"non-finite JSON number {value!r} is not allowed")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SnapshotParityError(f"duplicate JSON field {key!r}")
        result[key] = value
    return result


def load_snapshot(path: Path) -> dict[str, object]:
    """Read and validate one size-bounded schema-v2 snapshot."""
    content = _read_bounded(path)
    text = _decode_utf8(content, path)
    payload = _parse_json(text, path)
    if not isinstance(payload, dict):
        raise SnapshotParityError(f"snapshot {path} must contain a JSON object")
    _validate_resource_bounds(payload)
    _validate_snapshot_contract(payload, path)
    return payload


def _read_bounded(path: Path) -> bytes:
    try:
        with path.open("rb") as snapshot_file:
            content = snapshot_file.read(MAX_SNAPSHOT_BYTES + 1)
    except OSError as error:
        raise SnapshotParityError(f"cannot read snapshot {path}: {error}") from error
    if len(content) > MAX_SNAPSHOT_BYTES:
        raise SnapshotParityError(f"snapshot {path} exceeds {MAX_SNAPSHOT_BYTES} bytes")
    return content


def _decode_utf8(content: bytes, path: Path) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SnapshotParityError(f"snapshot {path} is not UTF-8") from error


def _parse_json(text: str, path: Path) -> object:
    try:
        return json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, SnapshotParityError) as error:
        raise SnapshotParityError(f"snapshot {path} is invalid JSON: {error}") from error


def _validate_resource_bounds(payload: object) -> None:
    remaining = MAX_SNAPSHOT_NODES
    stack: list[tuple[object, int]] = [(payload, 1)]
    while stack:
        value, depth = stack.pop()
        remaining -= 1
        _validate_node(value, depth, remaining)
        stack.extend((item, depth + 1) for item in _children(value))


def _validate_node(value: object, depth: int, remaining: int) -> None:
    if remaining < 0:
        raise SnapshotParityError(f"snapshot exceeds {MAX_SNAPSHOT_NODES} JSON nodes")
    if depth > MAX_SNAPSHOT_DEPTH:
        raise SnapshotParityError(f"snapshot exceeds nesting depth {MAX_SNAPSHOT_DEPTH}")
    if isinstance(value, float) and not math.isfinite(value):
        raise SnapshotParityError("snapshot contains a non-finite number")


def _children(value: object) -> tuple[object, ...]:
    if isinstance(value, Mapping):
        return tuple(value.values())
    if isinstance(value, list):
        return tuple(value)
    return ()


def _validate_snapshot_contract(payload: Mapping[str, object], path: Path) -> None:
    missing = sorted(REQUIRED_FIELDS.difference(payload))
    if missing:
        raise SnapshotParityError(f"snapshot {path} is missing required fields: {', '.join(missing)}")
    version = payload.get("snapshot_version")
    if not _is_exact_integer(version) or version != SNAPSHOT_VERSION:
        raise SnapshotParityError(
            f"snapshot {path} has unsupported snapshot_version={version!r}; expected {SNAPSHOT_VERSION}"
        )
    _positive_integer(payload.get("snapshot_sequence"), path, "snapshot_sequence")
    _positive_integer(payload.get("writer_pid"), path, "writer_pid")
    _nonnegative_integer(payload.get("helper_generation"), path, "helper_generation")
    runtime_id = payload.get("runtime_instance_id")
    if not _valid_runtime_id(runtime_id):
        raise SnapshotParityError(f"snapshot {path} has invalid runtime_instance_id")


def _positive_integer(value: object, path: Path, field: str) -> None:
    if not _is_exact_integer(value) or value <= 0:
        raise SnapshotParityError(f"snapshot {path} has invalid {field}")


def _nonnegative_integer(value: object, path: Path, field: str) -> None:
    if not _is_exact_integer(value) or value < 0:
        raise SnapshotParityError(f"snapshot {path} has invalid {field}")


def _is_exact_integer(value: object) -> TypeGuard[int]:
    return type(value) is int


def _valid_runtime_id(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def normalize_snapshot(payload: Mapping[str, object]) -> dict[str, object]:
    """Normalize only volatile identity, clock, age, and sequence values."""
    normalized = _normalize_value(payload, field_name=None)
    if not isinstance(normalized, dict):
        raise SnapshotParityError("normalized snapshot is not an object")
    return normalized


def _normalize_value(value: object, field_name: str | None) -> object:
    if field_name in IDENTITY_FIELDS:
        return "<identity>"
    if _is_time_field(field_name):
        return _normalize_time(value, field_name)
    if isinstance(value, Mapping):
        return _normalize_mapping(value)
    if isinstance(value, list):
        return _normalize_list(value)
    return value


def _normalize_mapping(value: Mapping[object, object]) -> dict[str, object]:
    return {str(key): _normalize_value(item, str(key)) for key, item in value.items()}


def _normalize_list(value: list[object]) -> list[object]:
    return [_normalize_value(item, field_name=None) for item in value]


def _is_time_field(field_name: str | None) -> bool:
    return field_name in DIRECT_TIME_FIELDS or (field_name or "").endswith(TIME_FIELD_SUFFIXES)


def _normalize_time(value: object, field_name: str | None) -> object:
    if value is None:
        return None
    if not _is_finite_number(value):
        raise SnapshotParityError(f"volatile field {field_name!r} must be a finite number or null")
    return "<time>"


def _is_finite_number(value: object) -> bool:
    return _is_number(value) and math.isfinite(float(value))


def compare_snapshots(
    left: Mapping[str, object],
    right: Mapping[str, object],
    *,
    absolute_tolerance: float = 1e-6,
    relative_tolerance: float = 1e-9,
) -> ComparisonResult:
    """Compare normalized snapshots with exact structure and tolerant floats."""
    if not _valid_tolerance(absolute_tolerance):
        raise ValueError("numeric tolerances must be finite and non-negative")
    if not _valid_tolerance(relative_tolerance):
        raise ValueError("numeric tolerances must be finite and non-negative")
    _validate_resource_bounds(left)
    _validate_resource_bounds(right)
    _validate_snapshot_contract(left, Path("<left>"))
    _validate_snapshot_contract(right, Path("<right>"))
    collector = _DifferenceCollector([])
    _compare_values(
        normalize_snapshot(left),
        normalize_snapshot(right),
        path="$",
        absolute_tolerance=absolute_tolerance,
        relative_tolerance=relative_tolerance,
        collector=collector,
    )
    return ComparisonResult(tuple(collector.items), collector.truncated)


def _valid_tolerance(value: float) -> bool:
    return math.isfinite(value) and value >= 0.0


def _compare_values(
    left: object,
    right: object,
    *,
    path: str,
    absolute_tolerance: float,
    relative_tolerance: float,
    collector: _DifferenceCollector,
) -> None:
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        _compare_mappings(
            left,
            right,
            path=path,
            absolute_tolerance=absolute_tolerance,
            relative_tolerance=relative_tolerance,
            collector=collector,
        )
        return
    if isinstance(left, list) and isinstance(right, list):
        _compare_lists(
            left,
            right,
            path=path,
            absolute_tolerance=absolute_tolerance,
            relative_tolerance=relative_tolerance,
            collector=collector,
        )
        return
    _compare_scalars(left, right, path, absolute_tolerance, relative_tolerance, collector)


def _compare_mappings(
    left: Mapping[str, object],
    right: Mapping[str, object],
    *,
    path: str,
    absolute_tolerance: float,
    relative_tolerance: float,
    collector: _DifferenceCollector,
) -> None:
    left_keys = set(left)
    right_keys = set(right)
    for key in sorted(left_keys - right_keys):
        collector.add(f"{path}.{key}: missing on right")
    for key in sorted(right_keys - left_keys):
        collector.add(f"{path}.{key}: missing on left")
    for key in sorted(left_keys & right_keys):
        _compare_values(
            left[key],
            right[key],
            path=f"{path}.{key}",
            absolute_tolerance=absolute_tolerance,
            relative_tolerance=relative_tolerance,
            collector=collector,
        )


def _compare_lists(
    left: list[object],
    right: list[object],
    *,
    path: str,
    absolute_tolerance: float,
    relative_tolerance: float,
    collector: _DifferenceCollector,
) -> None:
    if len(left) != len(right):
        collector.add(f"{path}: list length differs ({len(left)} != {len(right)})")
    for index, (left_item, right_item) in enumerate(zip(left, right, strict=False)):
        _compare_values(
            left_item,
            right_item,
            path=f"{path}[{index}]",
            absolute_tolerance=absolute_tolerance,
            relative_tolerance=relative_tolerance,
            collector=collector,
        )


def _compare_scalars(
    left: object,
    right: object,
    path: str,
    absolute_tolerance: float,
    relative_tolerance: float,
    collector: _DifferenceCollector,
) -> None:
    if not _scalars_equal(left, right, absolute_tolerance, relative_tolerance):
        collector.add(f"{path}: {_display(left)} != {_display(right)}")


def _scalars_equal(left: object, right: object, absolute_tolerance: float, relative_tolerance: float) -> bool:
    if _is_number(left) and _is_number(right):
        return _numbers_equal(left, right, absolute_tolerance, relative_tolerance)
    return type(left) is type(right) and left == right


def _numbers_equal(
    left: int | float,
    right: int | float,
    absolute_tolerance: float,
    relative_tolerance: float,
) -> bool:
    if isinstance(left, int) and isinstance(right, int):
        return left == right
    left_number = float(left)
    right_number = float(right)
    return math.isclose(
        left_number,
        right_number,
        rel_tol=relative_tolerance,
        abs_tol=absolute_tolerance,
    )


def _is_number(value: object) -> TypeGuard[int | float]:
    return type(value) in (int, float)


def _display(value: object) -> str:
    rendered = repr(value)
    return rendered if len(rendered) <= 120 else f"{rendered[:117]}..."


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("left", type=Path, help="first schema-v2 auto-input snapshot")
    parser.add_argument("right", type=Path, help="second schema-v2 auto-input snapshot")
    parser.add_argument("--absolute-tolerance", type=float, default=1e-6)
    parser.add_argument("--relative-tolerance", type=float, default=1e-9)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the bounded semantic snapshot comparison CLI."""
    args = _parser().parse_args(argv)
    try:
        left = load_snapshot(args.left)
        right = load_snapshot(args.right)
        result = compare_snapshots(
            left,
            right,
            absolute_tolerance=args.absolute_tolerance,
            relative_tolerance=args.relative_tolerance,
        )
    except (SnapshotParityError, ValueError) as error:
        print(f"Snapshot parity check failed: {error}", file=sys.stderr)
        return 2
    if result.equal:
        print("Auto-input snapshots are semantically equivalent.")
        return 0
    print("Auto-input snapshot parity mismatch:", file=sys.stderr)
    for difference in result.differences:
        print(f"- {difference}", file=sys.stderr)
    if result.truncated:
        print(f"- additional differences omitted after {MAX_REPORTED_DIFFERENCES} findings", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

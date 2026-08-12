#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded latency summaries for adaptive DBus scheduling."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypeGuard

from venus_evcharger.dbus_gateway_core import float_or_zero


def operation_p95_ms(circuit_health: Mapping[str, object]) -> float:
    """Return the slowest overall or per-operation p95 latency."""
    maximum = float(float_or_zero(circuit_health.get("p95_latency_ms")))
    operations = circuit_health.get("operations")
    if not _object_mapping(operations):
        return maximum
    for candidate in operations.values():
        if _object_mapping(candidate):
            maximum = max(
                maximum,
                float(float_or_zero(candidate.get("p95_latency_ms"))),
            )
    return maximum


def _object_mapping(value: object) -> TypeGuard[Mapping[object, object]]:
    return isinstance(value, Mapping)


__all__ = ["operation_p95_ms"]

# SPDX-License-Identifier: GPL-3.0-or-later
"""Strict JSON codec and file reader for semantic gateway diagnostics.

Producer integration hook: the gateway constructs ``GatewayDiagnosticsSnapshot``
and atomically writes ``gateway_diagnostics_payload(snapshot)`` to the configured
transport. Consumers depend only on ``GatewayDiagnosticsReader`` and therefore
never inspect gateway cache keys, DBus identities, paths, or inspection XML.

Adapter-side integration is intentionally outside this module: it only needs to
map private health, discovery, and value state into the DTO and invoke its atomic
writer. No consumer or wire-contract change is required for that integration.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from venus_evcharger.ports.gateway_diagnostics import (
    GatewayDiagnosticsSnapshot,
    GatewayDiagnosticsUnavailable,
)

DEFAULT_GATEWAY_DIAGNOSTICS_PATH = "/run/venus-evcharger/gateway-diagnostics.json"
GATEWAY_DIAGNOSTICS_FILENAME = "gateway-diagnostics.json"


def gateway_diagnostics_path(run_dir: str | None = None) -> str:
    """Return the diagnostics artifact path for one gateway runtime directory."""
    if run_dir is None:
        return DEFAULT_GATEWAY_DIAGNOSTICS_PATH
    normalized = str(run_dir).strip()
    if not normalized:
        return DEFAULT_GATEWAY_DIAGNOSTICS_PATH
    return os.path.join(normalized, GATEWAY_DIAGNOSTICS_FILENAME)


def gateway_diagnostics_payload(snapshot: object) -> dict[str, object]:
    """Return the canonical producer payload for a validated snapshot."""
    if not isinstance(snapshot, GatewayDiagnosticsSnapshot):
        raise TypeError("snapshot must be GatewayDiagnosticsSnapshot")
    return snapshot.to_payload()


def decode_gateway_diagnostics(payload: object) -> GatewayDiagnosticsSnapshot:
    """Decode and validate one transport payload."""
    return GatewayDiagnosticsSnapshot.from_payload(payload)


def encode_gateway_diagnostics(snapshot: GatewayDiagnosticsSnapshot) -> str:
    """Encode one validated snapshot as deterministic compact JSON."""
    return json.dumps(gateway_diagnostics_payload(snapshot), separators=(",", ":"), sort_keys=True)


@dataclass(frozen=True, slots=True)
class GatewayDiagnosticsFileReader:
    """Read semantic diagnostics from one replaceable JSON-file transport."""

    path: str = DEFAULT_GATEWAY_DIAGNOSTICS_PATH

    def __post_init__(self) -> None:
        _validated_path(self.path)

    def read_snapshot(self) -> GatewayDiagnosticsSnapshot:
        try:
            payload: object = json.loads(Path(self.path).read_text(encoding="utf-8"))
            return decode_gateway_diagnostics(payload)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise GatewayDiagnosticsUnavailable(f"gateway diagnostics unavailable: {error}") from error


def semantic_gateway_diagnostics_document(payload: object) -> Mapping[str, object]:
    """Validate an external document and return its canonical mapping."""
    return gateway_diagnostics_payload(decode_gateway_diagnostics(payload))


def _validated_path(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("gateway diagnostics path must be non-empty text")
    return value


__all__ = [
    "DEFAULT_GATEWAY_DIAGNOSTICS_PATH",
    "GATEWAY_DIAGNOSTICS_FILENAME",
    "GatewayDiagnosticsFileReader",
    "decode_gateway_diagnostics",
    "encode_gateway_diagnostics",
    "gateway_diagnostics_payload",
    "gateway_diagnostics_path",
    "semantic_gateway_diagnostics_document",
]

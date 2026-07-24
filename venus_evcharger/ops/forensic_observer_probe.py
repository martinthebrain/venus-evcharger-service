# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed, explicitly configured direct probes for the forensic observer."""

from __future__ import annotations

import configparser
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from venus_evcharger.backend.config_diagnostics import BackendSelectionView
from venus_evcharger.ops.forensic_observer_schema import (
    BackendProbePayload,
    BackendProbeRole,
    BackendProbeStatus,
)

_PROBE_KEY = "ForensicBackendProbe"
_PROBE_ROLE_KEY = "ForensicBackendProbeRole"
_HOST_KEY = "Host"
_SHELLY_PROBE = "shelly-rpc"
_SHELLY_STATUS_PATH = "/rpc/Shelly.GetStatus"
_DISABLED_PROBE_VALUES = frozenset({"", "disabled", "none", "off"})
_HTTP_ERRORS = (OSError, RuntimeError, TimeoutError, UnicodeDecodeError, ValueError)


@dataclass(frozen=True, slots=True)
class BackendProbeResult:
    """Bounded backend-specific probe result suitable for forensic artifacts."""

    status: BackendProbeStatus
    probe_type: str
    role: str
    backend_type: str
    reason_code: str = ""
    payload: str = ""

    def to_payload(self) -> BackendProbePayload:
        return {
            "status": self.status,
            "probe_type": self.probe_type,
            "role": self.role,
            "backend_type": self.backend_type,
            "reason_code": self.reason_code,
            "payload": self.payload,
        }


@runtime_checkable
class ForensicBackendProbePort(Protocol):
    """Optional direct device probe selected by normalized backend semantics."""

    def probe(self) -> BackendProbeResult: ...


@dataclass(frozen=True, slots=True)
class DisabledBackendProbe:
    """No-op probe used unless direct probing is explicitly configured."""

    reason_code: str = "direct-probe-disabled"

    def probe(self) -> BackendProbeResult:
        return BackendProbeResult(
            status="disabled",
            probe_type="none",
            role="",
            backend_type="",
            reason_code=self.reason_code,
        )


@dataclass(frozen=True, slots=True)
class RejectedBackendProbe:
    """Fail-closed result for incompatible or incomplete probe configuration."""

    probe_type: str
    role: str
    backend_type: str
    reason_code: str

    def probe(self) -> BackendProbeResult:
        return BackendProbeResult(
            status="skipped",
            probe_type=self.probe_type,
            role=self.role,
            backend_type=self.backend_type,
            reason_code=self.reason_code,
        )


@dataclass(frozen=True, slots=True)
class ShellyRpcBackendProbe:
    """Shelly-specific HTTP probe isolated behind the forensic probe port."""

    host: str
    role: BackendProbeRole
    backend_type: str
    timeout_seconds: float = 2.0

    def probe(self) -> BackendProbeResult:
        url = f"http://{self.host}{_SHELLY_STATUS_PATH}"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout_seconds) as response:
                payload = response.read(65536).decode(errors="replace")
        except _HTTP_ERRORS as error:
            return BackendProbeResult(
                status="error",
                probe_type=_SHELLY_PROBE,
                role=self.role,
                backend_type=self.backend_type,
                reason_code="backend-probe-failed",
                payload=str(error),
            )
        return BackendProbeResult(
            status="ok",
            probe_type=_SHELLY_PROBE,
            role=self.role,
            backend_type=self.backend_type,
            payload=payload,
        )


def configured_backend_probe(
    config: configparser.ConfigParser,
    selection: BackendSelectionView,
    *,
    config_path: str,
) -> ForensicBackendProbePort:
    """Build the optional direct probe only after backend-type validation."""
    defaults = config["DEFAULT"]
    configured_probe = defaults.get(_PROBE_KEY)
    if configured_probe is None:
        return DisabledBackendProbe()
    probe_type = configured_probe.strip().lower()
    if probe_type in _DISABLED_PROBE_VALUES:
        return DisabledBackendProbe()
    return _configured_active_probe(
        config,
        selection,
        probe_type=probe_type,
        config_path=config_path,
    )


def _configured_active_probe(
    config: configparser.ConfigParser,
    selection: BackendSelectionView,
    *,
    probe_type: str,
    config_path: str,
) -> ForensicBackendProbePort:
    role = _configured_probe_role(config["DEFAULT"])
    if role is None:
        return RejectedBackendProbe(probe_type, "", "", "invalid-probe-role")
    backend_type, backend_path = _selected_backend(selection, role)
    if probe_type != _SHELLY_PROBE:
        return RejectedBackendProbe(probe_type, role, backend_type, "unsupported-probe-type")
    if not backend_type.startswith("shelly"):
        return RejectedBackendProbe(probe_type, role, backend_type, "backend-type-mismatch")
    host = _backend_host(config, backend_path, config_path=config_path)
    if not host:
        return RejectedBackendProbe(probe_type, role, backend_type, "backend-host-missing")
    return ShellyRpcBackendProbe(host, role, backend_type)


def _configured_probe_role(
    defaults: configparser.SectionProxy,
) -> BackendProbeRole | None:
    configured_role = defaults.get(_PROBE_ROLE_KEY)
    if configured_role is None:
        return "switch"
    return _probe_role(configured_role)


def _probe_role(value: object) -> BackendProbeRole | None:
    normalized = str(value).strip().lower()
    if normalized == "meter":
        return "meter"
    if normalized == "switch":
        return "switch"
    if normalized == "charger":
        return "charger"
    return None


def _selected_backend(
    selection: BackendSelectionView,
    role: BackendProbeRole,
) -> tuple[str, Path | None]:
    backend_type: str | None
    config_path: Path | None
    if role == "meter":
        backend_type, config_path = selection["meter_type"], selection["meter_config_path"]
    elif role == "switch":
        backend_type, config_path = selection["switch_type"], selection["switch_config_path"]
    else:
        backend_type, config_path = selection["charger_type"], selection["charger_config_path"]
    return ("" if backend_type is None else backend_type, config_path)


def _backend_host(
    main_config: configparser.ConfigParser,
    backend_path: Path | None,
    *,
    config_path: str,
) -> str:
    parser = main_config if backend_path is None else _read_backend_config(backend_path, config_path)
    section = parser["Adapter"] if parser.has_section("Adapter") else parser["DEFAULT"]
    return str(section.get(_HOST_KEY, "")).strip()


def _read_backend_config(path: Path, main_config_path: str) -> configparser.ConfigParser:
    resolved = path if path.is_absolute() else Path(main_config_path).resolve().parent / path
    parser = configparser.ConfigParser()
    if not parser.read(resolved):
        return configparser.ConfigParser()
    return parser


__all__ = [
    "BackendProbeResult",
    "DisabledBackendProbe",
    "ForensicBackendProbePort",
    "RejectedBackendProbe",
    "ShellyRpcBackendProbe",
    "configured_backend_probe",
]

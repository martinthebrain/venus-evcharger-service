# SPDX-License-Identifier: GPL-3.0-or-later
"""Stable vocabulary for forensic observer inputs and artifacts."""

from __future__ import annotations

from typing import Literal, TypeAlias, TypedDict

from venus_evcharger.backend.models import BackendMode

UTF8 = "utf-8"
DEFAULT_DEVICE_INSTANCE = 60
DEFAULT_GATEWAY_RUN_DIR = "/run/venus-evcharger"
RUNTIME_LOG_DIR = "/var/volatile/log/dbus-venus-evcharger"
DEFAULT_MOUNTS_PATH = "/proc/mounts"
DEFAULT_FORENSIC_SUBDIR = "venus-evcharger-forensics"
INCIDENT_TIME_FORMAT = "%Y%m%d-%H%M%S"
SNAPSHOT_FILENAME = "snapshot.json"
REDACTED_CONFIG_FILENAME = "config.redacted.ini"
WRITE_PROBE_FILENAME = ".write-test"
SLUG_TRIM_CHARS = "-"

DEVICE_INSTANCE_KEY = "DeviceInstance"
AUTO_INPUT_SNAPSHOT_PATH_KEY = "AutoInputSnapshotPath"
GATEWAY_CACHE_PATH_KEY = "DbusGatewayCachePath"
GATEWAY_RUN_DIR_KEY = "DbusGatewayRunDir"

BackendProbeRole: TypeAlias = Literal["meter", "switch", "charger"]
BackendProbeStatus: TypeAlias = Literal["disabled", "skipped", "ok", "error"]
JsonObjectPayload: TypeAlias = dict[str, object]
RuntimeLogPayload: TypeAlias = dict[str, str]


class CommandCompletedPayload(TypedDict):
    """Bounded subprocess result, including failed process exit codes."""

    ok: bool
    returncode: int
    stdout: str
    stderr: str


class CommandErrorPayload(TypedDict):
    """Subprocess invocation failure before a process result was available."""

    ok: Literal[False]
    error: str


CommandPayload: TypeAlias = CommandCompletedPayload | CommandErrorPayload


class GatewayDiagnosticsAvailablePayload(TypedDict):
    """Available semantic gateway diagnostics."""

    available: Literal[True]
    snapshot: JsonObjectPayload


class GatewayDiagnosticsUnavailablePayload(TypedDict):
    """Unavailable semantic gateway diagnostics."""

    available: Literal[False]
    error: str


GatewayDiagnosticsPayload: TypeAlias = (
    GatewayDiagnosticsAvailablePayload | GatewayDiagnosticsUnavailablePayload
)


class BackendSelectionPayload(TypedDict):
    """JSON-ready projection of the selected backend roles."""

    mode: BackendMode
    meter_type: str
    switch_type: str
    charger_type: str | None
    meter_config_path: str
    switch_config_path: str
    charger_config_path: str


class BackendDiagnosticsAvailablePayload(TypedDict):
    """Successfully normalized backend selection."""

    available: Literal[True]
    selection: BackendSelectionPayload


class BackendDiagnosticsUnavailablePayload(TypedDict):
    """Backend configuration that could not be normalized."""

    available: Literal[False]
    reason_code: str
    error: str


BackendDiagnosticsPayload: TypeAlias = (
    BackendDiagnosticsAvailablePayload | BackendDiagnosticsUnavailablePayload
)


class BackendProbePayload(TypedDict):
    """Stable artifact emitted by an optional backend probe."""

    status: BackendProbeStatus
    probe_type: str
    role: str
    backend_type: str
    reason_code: str
    payload: str


class ProcessPayload(TypedDict):
    """One process line selected from a bounded process listing."""

    pid: str
    line: str


class ForensicSnapshotPayload(TypedDict):
    """Complete, stable observer snapshot written to incident artifacts."""

    timestamp: float
    config_path: str
    gateway_diagnostics: GatewayDiagnosticsPayload
    backend_diagnostics: BackendDiagnosticsPayload
    backend_probe: BackendProbePayload
    auto_input_snapshot: JsonObjectPayload
    runtime_state: JsonObjectPayload
    helper_processes: list[ProcessPayload]
    svstat: CommandPayload
    ps: CommandPayload
    uptime: CommandPayload
    runtime_logs: RuntimeLogPayload
    trace_markers: list[str]

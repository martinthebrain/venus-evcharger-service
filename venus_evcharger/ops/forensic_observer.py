# SPDX-License-Identifier: GPL-3.0-or-later
"""External forensic observer for the Venus EV charger service.

The observer is deliberately read-only. It runs in a separate process so it can
capture evidence when the main EV charger service is slow, wedged, or gone.
"""

from __future__ import annotations

import configparser
import json
import os
import subprocess
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TypeGuard

from venus_evcharger.backend.config_diagnostics import BackendSelectionView, backend_selection_view_from_config
from venus_evcharger.ipc.gateway_diagnostics import DEFAULT_GATEWAY_DIAGNOSTICS_PATH, GatewayDiagnosticsFileReader
from venus_evcharger.ops import forensic_observer_artifacts as artifacts
from venus_evcharger.ops import forensic_observer_schema as schema
from venus_evcharger.ops.forensic_observer_probe import DisabledBackendProbe, ForensicBackendProbePort
from venus_evcharger.ops.forensic_observer_probe import configured_backend_probe
from venus_evcharger.ops.removable_storage_coordination import (
    DEFAULT_REMOVABLE_STORAGE_MAINTENANCE_LOCK_PATH,
)
from venus_evcharger.ports.gateway_diagnostics import (
    GatewayDiagnosticsReader,
    GatewayDiagnosticsSnapshot,
    GatewayDiagnosticsUnavailable,
)


GATEWAY_DIAGNOSTICS_SNAPSHOT_PATH_KEY = "GatewayDiagnosticsSnapshotPath"
TRACE_MARKERS = ("Traceback", "malloc()", "NoReply", "dbus down", "Watchdog recovery", "stale")
FORENSIC_COMMAND_ERRORS = (OSError, RuntimeError, subprocess.SubprocessError, ValueError)
FORENSIC_JSON_READ_ERRORS = (OSError, RuntimeError, UnicodeDecodeError, json.JSONDecodeError)


class _CaseSensitiveConfigParser(configparser.ConfigParser):
    def optionxform(self, optionstr: str) -> str:
        return optionstr


def load_config(config_path: str) -> configparser.ConfigParser:
    parser = _CaseSensitiveConfigParser()
    if not parser.read(config_path):
        raise FileNotFoundError(config_path)
    return parser


def device_instance(defaults: configparser.SectionProxy) -> int:
    try:
        return int(str(defaults.get(schema.DEVICE_INSTANCE_KEY)).strip())
    except ValueError:
        return int(schema.DEFAULT_DEVICE_INSTANCE)


def auto_input_snapshot_path(defaults: configparser.SectionProxy) -> str:
    configured = str(defaults.get(schema.AUTO_INPUT_SNAPSHOT_PATH_KEY, "")).strip()
    if configured:
        return configured
    return f"/run/dbus-venus-evcharger-auto-{device_instance(defaults)}.json"


def gateway_diagnostics_snapshot_path(defaults: configparser.SectionProxy) -> str:
    configured = str(defaults.get(GATEWAY_DIAGNOSTICS_SNAPSHOT_PATH_KEY, "")).strip()
    return configured or DEFAULT_GATEWAY_DIAGNOSTICS_PATH


def runtime_state_path(defaults: configparser.SectionProxy) -> str:
    return f"/run/dbus-venus-evcharger-{device_instance(defaults)}.json"


def command_output(args: list[str], timeout: float = 3.0) -> schema.CommandPayload:
    try:
        completed = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        payload: schema.CommandCompletedPayload = {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
        }
        return payload
    except FORENSIC_COMMAND_ERRORS as error:
        failure: schema.CommandErrorPayload = {"ok": False, "error": str(error)}
        return failure


def read_gateway_diagnostics(reader: GatewayDiagnosticsReader) -> schema.GatewayDiagnosticsPayload:
    """Return a stable forensic envelope around one semantic gateway snapshot."""
    try:
        snapshot = reader.read_snapshot()
    except GatewayDiagnosticsUnavailable as error:
        unavailable: schema.GatewayDiagnosticsUnavailablePayload = {
            "available": False,
            "error": str(error),
        }
        return unavailable
    available: schema.GatewayDiagnosticsAvailablePayload = {
        "available": True,
        "snapshot": snapshot.to_payload(),
    }
    return available


def tail_file(path: str, max_bytes: int = 20000) -> str:
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            return handle.read().decode(errors="replace")
    except OSError as error:
        return f"<unavailable: {error}>"


def tail_log_dir(path: str, max_bytes: int = 20000) -> schema.RuntimeLogPayload:
    log_dir = Path(path)
    if not log_dir.is_dir():
        return {}
    files = sorted((item for item in log_dir.iterdir() if item.is_file()), key=lambda item: item.stat().st_mtime)
    return {item.name: tail_file(str(item), max_bytes=max_bytes) for item in files[-4:]}


def trace_markers_in_text(text: str) -> list[str]:
    return [marker for marker in TRACE_MARKERS if marker in text]


def _string_key_mapping(value: object) -> dict[str, object] | None:
    if not _is_object_mapping(value):
        return None
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _is_object_mapping(value: object) -> TypeGuard[Mapping[object, object]]:
    return isinstance(value, Mapping)


def _command_stdout(value: object) -> str:
    payload = _string_key_mapping(value)
    if payload is None:
        return ""
    stdout = payload.get("stdout")
    return stdout if isinstance(stdout, str) else ""


def read_json_file(path: str) -> schema.JsonObjectPayload:
    try:
        decoded: object = json.loads(Path(path).read_text(encoding=schema.UTF8))
    except FORENSIC_JSON_READ_ERRORS as error:
        return {"ok": False, "path": path, "error": str(error)}
    payload = _string_key_mapping(decoded)
    if payload is None:
        return {"ok": False, "path": path, "error": "not-a-json-object"}
    payload["ok"] = True
    payload["path"] = path
    return payload


def matching_processes(ps_text: str, marker: str) -> list[schema.ProcessPayload]:
    processes: list[schema.ProcessPayload] = []
    for line in ps_text.splitlines():
        if marker not in line:
            continue
        fields = line.split()
        processes.append(
            {
                "pid": fields[0],
                "line": line,
            }
        )
    return processes


def helper_processes(ps_text: str) -> list[schema.ProcessPayload]:
    return matching_processes(ps_text, "venus_evcharger_auto_input_helper.py")


def backend_diagnostics(
    config: configparser.ConfigParser,
) -> tuple[BackendSelectionView | None, schema.BackendDiagnosticsPayload]:
    """Return canonical backend selection without transport-specific assumptions."""
    try:
        selection = backend_selection_view_from_config(config)
    except (OSError, TypeError, ValueError) as error:
        return None, {
            "available": False,
            "reason_code": "backend-configuration-invalid",
            "error": str(error),
        }
    return selection, {
        "available": True,
        "selection": _backend_selection_payload(selection),
    }


def _backend_selection_payload(selection: BackendSelectionView) -> schema.BackendSelectionPayload:
    return {
        "mode": selection["mode"],
        "meter_type": selection["meter_type"],
        "switch_type": selection["switch_type"],
        "charger_type": selection["charger_type"],
        "meter_config_path": _path_text(selection["meter_config_path"]),
        "switch_config_path": _path_text(selection["switch_config_path"]),
        "charger_config_path": _path_text(selection["charger_config_path"]),
    }


def _path_text(path: Path | None) -> str:
    return "" if path is None else str(path)


def collect_snapshot(
    config_path: str,
    *,
    diagnostics_reader: GatewayDiagnosticsReader | None = None,
    backend_probe: ForensicBackendProbePort | None = None,
) -> schema.ForensicSnapshotPayload:
    config = load_config(config_path)
    defaults = config["DEFAULT"]
    selection, backend_diagnostic_payload = backend_diagnostics(config)
    probe = _effective_probe(backend_probe, config, selection, config_path=config_path)
    reader = _effective_diagnostics_reader(diagnostics_reader, defaults)
    log_dir = schema.RUNTIME_LOG_DIR
    runtime_log_tail = tail_log_dir(log_dir)
    ps_snapshot = command_output(["ps", "w"])
    ps_text = _command_stdout(ps_snapshot)
    return {
        "timestamp": time.time(),
        "config_path": config_path,
        "gateway_diagnostics": read_gateway_diagnostics(reader),
        "backend_diagnostics": backend_diagnostic_payload,
        "backend_probe": probe.probe().to_payload(),
        "auto_input_snapshot": read_json_file(auto_input_snapshot_path(defaults)),
        "runtime_state": read_json_file(runtime_state_path(defaults)),
        "helper_processes": helper_processes(ps_text),
        "svstat": command_output(["svstat", "/service/dbus-venus-evcharger"]),
        "ps": ps_snapshot,
        "uptime": command_output(["uptime"]),
        "runtime_logs": runtime_log_tail,
        "trace_markers": _runtime_markers(runtime_log_tail),
    }


def _effective_probe(
    backend_probe: ForensicBackendProbePort | None,
    config: configparser.ConfigParser,
    selection: BackendSelectionView | None,
    *,
    config_path: str,
) -> ForensicBackendProbePort:
    return backend_probe or _configured_probe(config, selection, config_path=config_path)


def _effective_diagnostics_reader(
    diagnostics_reader: GatewayDiagnosticsReader | None,
    defaults: configparser.SectionProxy,
) -> GatewayDiagnosticsReader:
    return diagnostics_reader or GatewayDiagnosticsFileReader(
        gateway_diagnostics_snapshot_path(defaults)
    )


def _runtime_markers(runtime_log_tail: Mapping[str, str]) -> list[str]:
    markers = {
        marker for text in runtime_log_tail.values() for marker in trace_markers_in_text(text)
    }
    return sorted(markers)


def _configured_probe(
    config: configparser.ConfigParser,
    selection: BackendSelectionView | None,
    *,
    config_path: str,
) -> ForensicBackendProbePort:
    if selection is None:
        return DisabledBackendProbe("backend-diagnostics-unavailable")
    return configured_backend_probe(config, selection, config_path=config_path)


def incident_reasons(snapshot: Mapping[str, object]) -> list[str]:
    reasons = _gateway_incident_reasons(snapshot)
    reasons.extend(_runit_incident_reasons(snapshot))
    reasons.extend(_marker_incident_reasons(snapshot.get("trace_markers")))
    return sorted(set(reasons))


def _marker_incident_reasons(value: object) -> list[str]:
    return [
        f"log-marker-{artifacts.slug_text(marker)}"
        for marker in _string_sequence(value)
    ]


def _string_sequence(value: object) -> tuple[str, ...]:
    if not _is_object_sequence(value):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _is_object_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return not isinstance(value, (str, bytes, bytearray)) and isinstance(value, Sequence)


def _gateway_incident_reasons(snapshot: Mapping[str, object]) -> list[str]:
    try:
        gateway = _gateway_diagnostics_from_artifact(snapshot)
    except (TypeError, ValueError):
        return ["gateway-diagnostics-invalid"]
    if gateway is None:
        return []
    reasons = [f"gateway-{name.replace('_', '-')}-unavailable" for name in gateway.critical_unavailable_fields()]
    health_reason = _gateway_health_incident_reason(gateway)
    if health_reason:
        reasons.append(health_reason)
    return reasons


def _gateway_diagnostics_from_artifact(snapshot: Mapping[str, object]) -> GatewayDiagnosticsSnapshot | None:
    diagnostics = _string_key_mapping(snapshot.get("gateway_diagnostics"))
    if diagnostics is None:
        return None
    if not diagnostics.get("available"):
        return None
    return GatewayDiagnosticsSnapshot.from_payload(diagnostics.get("snapshot"))


def _gateway_health_incident_reason(gateway: GatewayDiagnosticsSnapshot) -> str:
    if gateway.health.state in {"protective", "unavailable"}:
        return f"gateway-health-{gateway.health.state}"
    return ""


def _runit_incident_reasons(snapshot: Mapping[str, object]) -> list[str]:
    svstat = _string_key_mapping(snapshot.get("svstat"))
    if svstat is None:
        return []
    if not svstat.get("ok"):
        return ["runit-status-failed"]
    stdout = svstat.get("stdout")
    runit_is_up = isinstance(stdout, str) and " up " in f" {stdout} "
    return [] if runit_is_up else ["runit-not-up"]


def _incident_is_due(
    reasons: Sequence[str],
    *,
    now: float,
    last_incident_at: float,
    incident_cooldown: float,
) -> bool:
    """Return whether an incident has reasons and its cooldown has elapsed."""
    return bool(reasons) and (now - last_incident_at) >= incident_cooldown


def _observer_iteration(
    config_path: str,
    last_incident_at: float,
    *,
    incident_cooldown: float,
    mounts_path: str,
    diagnostics_reader: GatewayDiagnosticsReader | None,
    backend_probe: ForensicBackendProbePort | None,
    storage_lock_path: str = DEFAULT_REMOVABLE_STORAGE_MAINTENANCE_LOCK_PATH,
) -> float:
    mounts = artifacts.read_mounts(mounts_path)
    if not artifacts.mounted_storage_candidates(mounts):
        return last_incident_at
    snapshot = collect_snapshot(
        config_path,
        diagnostics_reader=diagnostics_reader,
        backend_probe=backend_probe,
    )
    reasons = incident_reasons(snapshot)
    now = time.time()
    if not _incident_is_due(
        reasons,
        now=now,
        last_incident_at=last_incident_at,
        incident_cooldown=incident_cooldown,
    ):
        return last_incident_at
    if artifacts.write_incident_with_storage_lease(
        config_path,
        snapshot,
        reasons,
        mounts_path=mounts_path,
        storage_lock_path=storage_lock_path,
    ):
        return now
    return last_incident_at


def observer_loop(
    config_path: str,
    *,
    start_delay: float = 180.0,
    interval: float = 30.0,
    incident_cooldown: float = 900.0,
    mounts_path: str = schema.DEFAULT_MOUNTS_PATH,
    storage_lock_path: str = DEFAULT_REMOVABLE_STORAGE_MAINTENANCE_LOCK_PATH,
    diagnostics_reader: GatewayDiagnosticsReader | None = None,
    backend_probe: ForensicBackendProbePort | None = None,
) -> None:
    time.sleep(max(0.0, start_delay))
    last_incident_at = 0.0
    while True:
        last_incident_at = _observer_iteration(
            config_path,
            last_incident_at,
            incident_cooldown=incident_cooldown,
            mounts_path=mounts_path,
            diagnostics_reader=diagnostics_reader,
            backend_probe=backend_probe,
            storage_lock_path=storage_lock_path,
        )
        time.sleep(max(1.0, interval))


__all__ = [
    "collect_snapshot",
    "backend_diagnostics",
    "device_instance",
    "gateway_diagnostics_snapshot_path",
    "incident_reasons",
    "observer_loop",
]

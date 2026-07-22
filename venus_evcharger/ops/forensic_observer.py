# SPDX-License-Identifier: GPL-3.0-or-later
"""External forensic observer for the Venus EV charger service.

The observer is deliberately read-only. It runs in a separate process so it can
capture evidence when the main EV charger service is slow, wedged, or gone.
"""

from __future__ import annotations

import configparser
import json
import os
import re
import subprocess
import time
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from venus_evcharger.ipc.gateway_diagnostics import (
    DEFAULT_GATEWAY_DIAGNOSTICS_PATH,
    GatewayDiagnosticsFileReader,
)
from venus_evcharger.ops.forensic_observer_schema import (
    AUTO_INPUT_SNAPSHOT_PATH_KEY,
    DEFAULT_DEVICE_INSTANCE,
    DEFAULT_FORENSIC_SUBDIR,
    DEFAULT_MOUNTS_PATH,
    DEVICE_INSTANCE_KEY,
    HOST_KEY,
    INCIDENT_TIME_FORMAT,
    REDACTED_CONFIG_FILENAME,
    RUNTIME_LOG_DIR,
    SLUG_TRIM_CHARS,
    SNAPSHOT_FILENAME,
    UTF8,
    WRITE_PROBE_FILENAME,
)
from venus_evcharger.ports.gateway_diagnostics import (
    GatewayDiagnosticsReader,
    GatewayDiagnosticsSnapshot,
    GatewayDiagnosticsUnavailable,
)


GATEWAY_DIAGNOSTICS_SNAPSHOT_PATH_KEY = "GatewayDiagnosticsSnapshotPath"
SECRET_KEYS = ("password", "token", "secret", "auth")
TRACE_MARKERS = ("Traceback", "malloc()", "NoReply", "dbus down", "Watchdog recovery", "stale")
MOUNT_PREFIXES = ("/media/", "/run/media/", "/mnt/")
DEVICE_PREFIXES = ("/dev/sd", "/dev/mmcblk", "/dev/disk/")
FORENSIC_COMMAND_ERRORS = (OSError, RuntimeError, subprocess.SubprocessError, ValueError)
FORENSIC_HTTP_ERRORS = (OSError, RuntimeError, TimeoutError, UnicodeDecodeError, ValueError)
FORENSIC_JSON_READ_ERRORS = (OSError, RuntimeError, UnicodeDecodeError, json.JSONDecodeError)


class _CaseSensitiveConfigParser(configparser.ConfigParser):
    def optionxform(self, optionstr: str) -> str:
        return optionstr


def load_defaults(config_path: str) -> configparser.SectionProxy:
    parser = _CaseSensitiveConfigParser()
    parser.read(config_path)
    return parser["DEFAULT"]


def device_instance(defaults: configparser.SectionProxy) -> int:
    try:
        return int(str(defaults.get(DEVICE_INSTANCE_KEY)).strip())
    except ValueError:
        return int(DEFAULT_DEVICE_INSTANCE)


def auto_input_snapshot_path(defaults: configparser.SectionProxy) -> str:
    configured = str(defaults.get(AUTO_INPUT_SNAPSHOT_PATH_KEY, "")).strip()
    if configured:
        return configured
    return f"/run/dbus-venus-evcharger-auto-{device_instance(defaults)}.json"


def gateway_diagnostics_snapshot_path(defaults: configparser.SectionProxy) -> str:
    configured = str(defaults.get(GATEWAY_DIAGNOSTICS_SNAPSHOT_PATH_KEY, "")).strip()
    return configured or DEFAULT_GATEWAY_DIAGNOSTICS_PATH


def runtime_state_path(defaults: configparser.SectionProxy) -> str:
    return f"/run/dbus-venus-evcharger-{device_instance(defaults)}.json"


def configured_host(defaults: configparser.SectionProxy) -> str:
    backend_host = str(defaults.get(HOST_KEY, "")).strip()
    return backend_host


def _redacted_config_line(line: str) -> str:
    if "=" not in line:
        return line
    key, _value = line.split("=", 1)
    if any(secret in key.strip().lower() for secret in SECRET_KEYS):
        return f"{key}=<redacted>"
    return line


def redact_config_text(text: str) -> str:
    return "\n".join(_redacted_config_line(line) for line in text.splitlines()) + "\n"


def mounted_storage_candidates(mounts_text: str) -> list[str]:
    candidates: list[str] = []
    for raw_line in mounts_text.splitlines():
        parts = raw_line.split()
        if len(parts) < 2:
            continue
        device, mount_point = parts[0], parts[1].replace("\\040", " ")
        if not device.startswith(DEVICE_PREFIXES):
            continue
        if not mount_point.startswith(MOUNT_PREFIXES):
            continue
        candidates.append(mount_point)
    return candidates


def read_mounts(path: str = "/proc/mounts") -> str:
    try:
        return Path(path).read_text(encoding=UTF8)
    except OSError:
        return ""


def first_writable_log_dir(candidates: Iterable[str], subdir: str = DEFAULT_FORENSIC_SUBDIR) -> str:
    for candidate in candidates:
        log_dir = Path(candidate) / subdir
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            probe_path = log_dir / WRITE_PROBE_FILENAME
            probe_path.touch()
            probe_path.unlink()
            return str(log_dir)
        except OSError:
            continue
    return ""


def command_output(args: list[str], timeout: float = 3.0) -> dict[str, Any]:
    try:
        completed = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
        }
    except FORENSIC_COMMAND_ERRORS as error:
        return {"ok": False, "error": str(error)}


def read_gateway_diagnostics(reader: GatewayDiagnosticsReader) -> dict[str, object]:
    """Return a stable forensic envelope around one semantic gateway snapshot."""
    try:
        snapshot = reader.read_snapshot()
    except GatewayDiagnosticsUnavailable as error:
        return {"available": False, "error": str(error)}
    return {"available": True, "snapshot": snapshot.to_payload()}


def fetch_shelly_status(host: str, timeout: float = 2.0) -> dict[str, Any]:
    if not host:
        return {"ok": False, "skipped": "no-host"}
    url = f"http://{host}/rpc/Shelly.GetStatus"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            payload = response.read(65536).decode(errors="replace")
        return {"ok": True, "url": url, "payload": payload}
    except FORENSIC_HTTP_ERRORS as error:
        return {"ok": False, "url": url, "error": str(error)}


def tail_file(path: str, max_bytes: int = 20000) -> str:
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            return handle.read().decode(errors="replace")
    except OSError as error:
        return f"<unavailable: {error}>"


def tail_log_dir(path: str, max_bytes: int = 20000) -> dict[str, str]:
    log_dir = Path(path)
    if not log_dir.is_dir():
        return {}
    files = sorted((item for item in log_dir.iterdir() if item.is_file()), key=lambda item: item.stat().st_mtime)
    return {item.name: tail_file(str(item), max_bytes=max_bytes) for item in files[-4:]}


def trace_markers_in_text(text: str) -> list[str]:
    return [marker for marker in TRACE_MARKERS if marker in text]


def read_text_safe(path: str) -> str:
    try:
        return Path(path).read_text(encoding=UTF8)
    except OSError as error:
        return f"<unavailable: {error}>\n"


def read_json_file(path: str) -> dict[str, object]:
    try:
        decoded: object = json.loads(Path(path).read_text(encoding=UTF8))
    except FORENSIC_JSON_READ_ERRORS as error:
        return {"ok": False, "path": path, "error": str(error)}
    if not isinstance(decoded, dict):
        return {"ok": False, "path": path, "error": "not-a-json-object"}
    untyped = cast(dict[object, object], decoded)
    payload: dict[str, object] = {str(key): value for key, value in untyped.items()}
    payload["ok"] = True
    payload["path"] = path
    return payload


def matching_processes(ps_text: str, marker: str) -> list[dict[str, Any]]:
    processes: list[dict[str, Any]] = []
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


def helper_processes(ps_text: str) -> list[dict[str, Any]]:
    return matching_processes(ps_text, "venus_evcharger_auto_input_helper.py")


def collect_snapshot(
    config_path: str,
    *,
    diagnostics_reader: GatewayDiagnosticsReader | None = None,
) -> dict[str, Any]:
    defaults = load_defaults(config_path)
    reader = diagnostics_reader or GatewayDiagnosticsFileReader(gateway_diagnostics_snapshot_path(defaults))
    log_dir = RUNTIME_LOG_DIR
    runtime_log_tail = tail_log_dir(log_dir)
    runtime_markers = sorted(
        {marker for text in runtime_log_tail.values() for marker in trace_markers_in_text(text)}
    )
    ps_snapshot = command_output(["ps", "w"])
    ps_text = str(ps_snapshot.get("stdout") or "")
    return {
        "timestamp": time.time(),
        "config_path": config_path,
        "gateway_diagnostics": read_gateway_diagnostics(reader),
        "auto_input_snapshot": read_json_file(auto_input_snapshot_path(defaults)),
        "runtime_state": read_json_file(runtime_state_path(defaults)),
        "helper_processes": helper_processes(ps_text),
        "shelly": fetch_shelly_status(configured_host(defaults)),
        "svstat": command_output(["svstat", "/service/dbus-venus-evcharger"]),
        "ps": ps_snapshot,
        "uptime": command_output(["uptime"]),
        "runtime_logs": runtime_log_tail,
        "trace_markers": runtime_markers,
    }


def incident_reasons(snapshot: Mapping[str, object]) -> list[str]:
    reasons = _gateway_incident_reasons(snapshot)
    reasons.extend(_runit_incident_reasons(snapshot))
    reasons.extend(_marker_incident_reasons(snapshot.get("trace_markers")))
    return sorted(set(reasons))


def _marker_incident_reasons(value: object) -> list[str]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        return []
    markers = cast(Sequence[object], value)
    return [f"log-marker-{_slug(marker)}" for marker in markers if isinstance(marker, str)]


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
    raw_diagnostics = snapshot.get("gateway_diagnostics")
    if not isinstance(raw_diagnostics, Mapping):
        return None
    diagnostics = cast(Mapping[str, object], raw_diagnostics)
    if not diagnostics.get("available"):
        return None
    return GatewayDiagnosticsSnapshot.from_payload(diagnostics.get("snapshot"))


def _gateway_health_incident_reason(gateway: GatewayDiagnosticsSnapshot) -> str:
    if gateway.health.state in {"protective", "unavailable"}:
        return f"gateway-health-{gateway.health.state}"
    return ""


def _runit_incident_reasons(snapshot: Mapping[str, object]) -> list[str]:
    raw_svstat = snapshot.get("svstat")
    if not isinstance(raw_svstat, Mapping):
        return []
    svstat = cast(Mapping[str, object], raw_svstat)
    if not svstat.get("ok"):
        return ["runit-status-failed"]
    stdout = svstat.get("stdout")
    runit_is_up = isinstance(stdout, str) and " up " in f" {stdout} "
    return [] if runit_is_up else ["runit-not-up"]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip(SLUG_TRIM_CHARS) or "event"


def write_incident(
    log_dir: str,
    snapshot: Mapping[str, object],
    config_path: str,
    reasons: list[str],
) -> str:
    stamp = time.strftime(INCIDENT_TIME_FORMAT, time.localtime(_artifact_timestamp(snapshot)))
    incident_dir = Path(log_dir) / f"incident-{stamp}-{_slug('-'.join(reasons))[:80]}"
    incident_dir.mkdir(parents=True, exist_ok=True)
    payload = dict(snapshot)
    payload["reasons"] = list(reasons)
    (incident_dir / SNAPSHOT_FILENAME).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding=UTF8)
    (incident_dir / REDACTED_CONFIG_FILENAME).write_text(
        redact_config_text(read_text_safe(config_path)), encoding=UTF8
    )
    return str(incident_dir)


def _artifact_timestamp(snapshot: Mapping[str, object]) -> float:
    value = snapshot.get("timestamp")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("forensic snapshot timestamp must be numeric")
    return float(value)


def _observer_iteration(
    config_path: str,
    last_incident_at: float,
    *,
    incident_cooldown: float,
    mounts_path: str,
    diagnostics_reader: GatewayDiagnosticsReader | None,
) -> float:
    mounts = read_mounts(mounts_path)
    log_dir = first_writable_log_dir(mounted_storage_candidates(mounts))
    if not log_dir:
        return last_incident_at
    snapshot = collect_snapshot(config_path, diagnostics_reader=diagnostics_reader)
    reasons = incident_reasons(snapshot)
    now = time.time()
    if reasons and (now - last_incident_at) >= incident_cooldown:
        write_incident(log_dir, snapshot, config_path, reasons)
        return now
    return last_incident_at


def observer_loop(
    config_path: str,
    *,
    start_delay: float = 180.0,
    interval: float = 30.0,
    incident_cooldown: float = 900.0,
    mounts_path: str = DEFAULT_MOUNTS_PATH,
    diagnostics_reader: GatewayDiagnosticsReader | None = None,
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
        )
        time.sleep(max(1.0, interval))


__all__ = [
    "collect_snapshot",
    "device_instance",
    "first_writable_log_dir",
    "gateway_diagnostics_snapshot_path",
    "incident_reasons",
    "mounted_storage_candidates",
    "observer_loop",
    "redact_config_text",
    "write_incident",
]

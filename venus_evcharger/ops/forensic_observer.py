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
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from venus_evcharger.dbus_gateway import DbusCacheStore, dbus_path_key, gateway_paths


EVCHARGER_PATHS = (
    "/Mode",
    "/StartStop",
    "/AutoStart",
    "/Ac/Power",
    "/Status",
    "/Auto/DecisionReason",
    "/Auto/DecisionState",
    "/Auto/LastHealthReason",
    "/Auto/RuntimeOverridesActive",
    "/Auto/RuntimeOverridesPath",
)
SECRET_KEYS = ("password", "token", "secret", "auth")
TRACE_MARKERS = ("Traceback", "malloc()", "NoReply", "dbus down", "Watchdog recovery", "stale")
MOUNT_PREFIXES = ("/media/", "/run/media/", "/mnt/")
DEVICE_PREFIXES = ("/dev/sd", "/dev/mmcblk", "/dev/disk/")


class _CaseSensitiveConfigParser(configparser.ConfigParser):
    def optionxform(self, optionstr: str) -> str:
        return optionstr


def load_defaults(config_path: str) -> configparser.SectionProxy:
    parser = _CaseSensitiveConfigParser()
    parser.read(config_path)
    return parser["DEFAULT"]


def device_instance(defaults: configparser.SectionProxy) -> int:
    try:
        return int(str(defaults.get("DeviceInstance", "60")).strip() or "60")
    except ValueError:
        return 60


def evcharger_service_name(defaults: configparser.SectionProxy) -> str:
    base = str(defaults.get("ServiceName", "com.victronenergy.evcharger")).strip()
    return f"{base or 'com.victronenergy.evcharger'}.http_{device_instance(defaults)}"


def auto_input_snapshot_path(defaults: configparser.SectionProxy) -> str:
    configured = str(defaults.get("AutoInputSnapshotPath", "")).strip()
    if configured:
        return configured
    return f"/run/dbus-venus-evcharger-auto-{device_instance(defaults)}.json"


def dbus_introspection_snapshot_path(defaults: configparser.SectionProxy) -> str:
    configured = str(defaults.get("DbusIntrospectionSnapshotPath", "")).strip()
    if configured:
        return configured
    return f"/run/dbus-venus-evcharger-dbus-map-{device_instance(defaults)}.json"


def dbus_gateway_cache_path(defaults: configparser.SectionProxy) -> str:
    configured = str(defaults.get("DbusGatewayCachePath", "")).strip()
    if configured:
        return configured
    run_dir = str(defaults.get("DbusGatewayRunDir", "/run/venus-evcharger")).strip()
    return gateway_paths(run_dir or None).cache_path


def runtime_state_path(defaults: configparser.SectionProxy) -> str:
    return f"/run/dbus-venus-evcharger-{device_instance(defaults)}.json"


def configured_host(defaults: configparser.SectionProxy) -> str:
    backend_host = str(defaults.get("Host", "")).strip()
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
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""


def first_writable_log_dir(candidates: Iterable[str], subdir: str = "venus-evcharger-forensics") -> str:
    for candidate in candidates:
        log_dir = Path(candidate) / subdir
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            probe_path = log_dir / ".write-test"
            probe_path.write_text("ok\n", encoding="utf-8")
            probe_path.unlink(missing_ok=True)
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
    except Exception as error:  # pylint: disable=broad-except
        return {"ok": False, "error": str(error)}


def json_ready(value: Any) -> Any:
    """Return one JSON-serializable representation of DBus or Python values."""
    if _is_json_scalar(value):
        return value
    return _json_ready_complex(value)


def _json_ready_complex(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    return str(value)


def _is_json_scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool)) or value is None


def read_dbus_paths(
    service_name: str,
    paths: Iterable[str] = EVCHARGER_PATHS,
    *,
    bus_factory: Callable[[], Any] | None = None,
    cache_path: str = "",
) -> dict[str, Any]:
    del bus_factory
    snapshot = DbusCacheStore.load_snapshot(cache_path or gateway_paths().cache_path)
    values: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for path in paths:
        entry = DbusCacheStore.value_entry(snapshot, dbus_path_key(service_name, path))
        if entry is None:
            errors[path] = "missing-from-gateway-cache"
            continue
        values[path] = json_ready(entry.get("value"))
    return {"ok": not errors, "values": values, "errors": errors}


def fetch_shelly_status(host: str, timeout: float = 2.0) -> dict[str, Any]:
    if not host:
        return {"ok": False, "skipped": "no-host"}
    url = f"http://{host}/rpc/Shelly.GetStatus"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            payload = response.read(65536).decode("utf-8", errors="replace")
        return {"ok": True, "url": url, "payload": payload}
    except Exception as error:  # pylint: disable=broad-except
        return {"ok": False, "url": url, "error": str(error)}


def tail_file(path: str, max_bytes: int = 20000) -> str:
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes), os.SEEK_SET)
            return handle.read(max_bytes).decode("utf-8", errors="replace")
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
        return Path(path).read_text(encoding="utf-8")
    except OSError as error:
        return f"<unavailable: {error}>\n"


def read_json_file(path: str) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as error:  # pylint: disable=broad-except
        return {"ok": False, "path": path, "error": str(error)}
    if not isinstance(payload, dict):
        return {"ok": False, "path": path, "error": "not-a-json-object"}
    payload["ok"] = True
    payload["path"] = path
    return payload


def matching_processes(ps_text: str, marker: str) -> list[dict[str, Any]]:
    processes: list[dict[str, Any]] = []
    for line in ps_text.splitlines():
        if marker not in line:
            continue
        fields = line.split(None, 4)
        processes.append(
            {
                "pid": fields[0] if fields else "",
                "line": line,
            }
        )
    return processes


def helper_processes(ps_text: str) -> list[dict[str, Any]]:
    return matching_processes(ps_text, "venus_evcharger_auto_input_helper.py")


def collect_snapshot(config_path: str, *, bus_factory: Callable[[], Any] | None = None) -> dict[str, Any]:
    defaults = load_defaults(config_path)
    service_name = evcharger_service_name(defaults)
    log_dir = "/var/volatile/log/dbus-venus-evcharger"
    runtime_log_tail = tail_log_dir(log_dir)
    runtime_log_text = "\n".join(runtime_log_tail.values())
    ps_snapshot = command_output(["ps", "w"])
    ps_text = str(ps_snapshot.get("stdout", "")) if isinstance(ps_snapshot, dict) else ""
    return {
        "timestamp": time.time(),
        "service_name": service_name,
        "config_path": config_path,
        "dbus": read_dbus_paths(service_name, bus_factory=bus_factory, cache_path=dbus_gateway_cache_path(defaults)),
        "auto_input_snapshot": read_json_file(auto_input_snapshot_path(defaults)),
        "dbus_introspection_snapshot": read_json_file(dbus_introspection_snapshot_path(defaults)),
        "runtime_state": read_json_file(runtime_state_path(defaults)),
        "helper_processes": helper_processes(ps_text),
        "shelly": fetch_shelly_status(configured_host(defaults)),
        "svstat": command_output(["svstat", "/service/dbus-venus-evcharger"]),
        "ps": ps_snapshot,
        "uptime": command_output(["uptime"]),
        "runtime_logs": runtime_log_tail,
        "trace_markers": trace_markers_in_text(runtime_log_text),
    }


def incident_reasons(snapshot: dict[str, Any]) -> list[str]:
    reasons = _dbus_incident_reasons(snapshot)
    reasons.extend(_runit_incident_reasons(snapshot))
    reasons.extend(f"log-marker-{_slug(marker)}" for marker in snapshot.get("trace_markers", []))
    return sorted(set(reasons))


def _dbus_incident_reasons(snapshot: dict[str, Any]) -> list[str]:
    dbus_state = snapshot.get("dbus", {})
    dbus_errors = dbus_state.get("errors", {}) if isinstance(dbus_state, dict) else {}
    reasons: list[str] = []
    for path in ("/Mode", "/StartStop", "/Ac/Power"):
        if path in dbus_errors:
            reasons.append(f"dbus-{path}-failed")
    return reasons


def _runit_incident_reasons(snapshot: dict[str, Any]) -> list[str]:
    svstat = snapshot.get("svstat", {})
    if not isinstance(svstat, dict):
        return []
    reasons: list[str] = []
    if svstat.get("ok") and " up " not in f" {svstat.get('stdout', '')} ":
        reasons.append("runit-not-up")
    if not svstat.get("ok"):
        reasons.append("runit-status-failed")
    return reasons


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "event"


def write_incident(log_dir: str, snapshot: dict[str, Any], config_path: str, reasons: list[str]) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(float(snapshot.get("timestamp", time.time()))))
    incident_dir = Path(log_dir) / f"incident-{stamp}-{_slug('-'.join(reasons))[:80]}"
    incident_dir.mkdir(parents=True, exist_ok=True)
    payload = dict(snapshot)
    payload["reasons"] = list(reasons)
    (incident_dir / "snapshot.json").write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    (incident_dir / "config.redacted.ini").write_text(redact_config_text(read_text_safe(config_path)), encoding="utf-8")
    return str(incident_dir)


def _observer_iteration(
    config_path: str,
    last_incident_at: float,
    *,
    incident_cooldown: float,
    mounts_path: str,
    bus_factory: Callable[[], Any] | None,
) -> float:
    mounts = read_mounts(mounts_path)
    log_dir = first_writable_log_dir(mounted_storage_candidates(mounts))
    if not log_dir:
        return last_incident_at
    snapshot = collect_snapshot(config_path, bus_factory=bus_factory)
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
    mounts_path: str = "/proc/mounts",
    bus_factory: Callable[[], Any] | None = None,
) -> None:
    time.sleep(max(0.0, start_delay))
    last_incident_at = 0.0
    while True:
        last_incident_at = _observer_iteration(
            config_path,
            last_incident_at,
            incident_cooldown=incident_cooldown,
            mounts_path=mounts_path,
            bus_factory=bus_factory,
        )
        time.sleep(max(1.0, interval))


__all__ = [
    "collect_snapshot",
    "device_instance",
    "evcharger_service_name",
    "first_writable_log_dir",
    "incident_reasons",
    "mounted_storage_candidates",
    "observer_loop",
    "redact_config_text",
    "write_incident",
]

# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus-gateway IPC primitives used by non-DBus EV charger processes.

This module intentionally does not import ``dbus`` or ``vedbus``.  It defines
the file/socket protocol used between the core service and the dedicated DBus
adapter process, plus a small ``VeDbusService``-like proxy for the core.
"""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import socket
import time
import uuid
from typing import Any, Mapping

from venus_evcharger.core.shared import compact_json, write_text_atomically

DBUS_GATEWAY_SCHEMA_VERSION = 1
DEFAULT_GATEWAY_RUN_DIR = "/run/venus-evcharger"
DEFAULT_GATEWAY_SOCKET_NAME = "gateway.sock"
DEFAULT_DBUS_CACHE_NAME = "dbus-cache.json"
DEFAULT_DBUS_CACHE_SEQUENCE_NAME = "dbus-cache.seq"
DEFAULT_DBUS_HEALTH_NAME = "dbus-health.json"
DEFAULT_DBUS_COMMAND_DIR_NAME = "dbus-commands"
DEFAULT_CORE_COMMAND_DIR_NAME = "core-commands"

PRIORITY_VALUES = {
    "safety": 0,
    "user": 1,
    "publish": 2,
    "read": 3,
    "optional": 4,
    "discovery": 5,
    "diagnostic": 6,
}

PUBLISH_PATH_RANKS = {
    "/Mode": 0,
    "/StartStop": 0,
    "/AutoStart": 0,
    "/SetCurrent": 0,
    "/Current": 0,
    "/Status": 0,
    "/Ac/Power": 0,
    "/Ac/Current": 0,
    "/Session/Time": 0,
    "/Session/Energy": 0,
    "/ChargingTime": 0,
    "/Ac/L1/Power": 0,
    "/Ac/L1/Current": 0,
    "/Ac/Energy/Forward": 2,
    "/Ac/L1/Energy/Forward": 2,
    "/Ac/L2/Power": 3,
    "/Ac/L2/Current": 3,
    "/Ac/L2/Energy/Forward": 3,
    "/Ac/L3/Power": 3,
    "/Ac/L3/Current": 3,
    "/Ac/L3/Energy/Forward": 3,
}

GUI_CRITICAL_PUBLISH_PATHS = {
    "/Mode",
    "/StartStop",
    "/Enable",
    "/AutoStart",
    "/Status",
    "/SetCurrent",
    "/Ac/Power",
    "/Ac/Current",
    "/Ac/Energy/Forward",
    "/Session/Time",
    "/Session/Energy",
}

FAST_READ_KEYS = {"grid_power_w", "pv_power_w", "battery_soc"}

@dataclass(frozen=True)
class GatewayPaths:
    """Runtime paths shared by gateway, core, and observer processes."""

    run_dir: str
    socket_path: str
    cache_path: str
    cache_sequence_path: str
    health_path: str
    command_dir: str
    core_command_dir: str

def gateway_paths(run_dir: str | None = None) -> GatewayPaths:
    base = str(run_dir or os.environ.get("VENUS_EVCHARGER_GATEWAY_RUN_DIR") or DEFAULT_GATEWAY_RUN_DIR).strip()
    return GatewayPaths(
        run_dir=base,
        socket_path=os.path.join(base, DEFAULT_GATEWAY_SOCKET_NAME),
        cache_path=os.path.join(base, DEFAULT_DBUS_CACHE_NAME),
        cache_sequence_path=os.path.join(base, DEFAULT_DBUS_CACHE_SEQUENCE_NAME),
        health_path=os.path.join(base, DEFAULT_DBUS_HEALTH_NAME),
        command_dir=os.path.join(base, DEFAULT_DBUS_COMMAND_DIR_NAME),
        core_command_dir=os.path.join(base, DEFAULT_CORE_COMMAND_DIR_NAME),
    )

def _now() -> float:
    return time.time()


def _priority_rank(priority: object) -> int:
    return PRIORITY_VALUES.get(str(priority or "diagnostic").strip().lower(), PRIORITY_VALUES["diagnostic"])

def _json_ready(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return str(value)

def dbus_path_key(service_name: str, path: str) -> str:
    """Return the canonical cache key for one raw Victron DBus path."""
    return f"path:{str(service_name)}{str(path)}"

def read_json_file(path: str, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return default

def write_json_file(path: str, payload: Mapping[str, Any]) -> None:
    write_text_atomically(path, compact_json(_json_ready(payload)) + "\n")

class DbusCacheStore:
    """RAM-owned DBus value cache with freshness and health metadata."""

    def __init__(self, paths: GatewayPaths | None = None, *, stale_after_seconds: float = 10.0) -> None:
        self.paths = paths or gateway_paths()
        self.stale_after_seconds = max(0.0, float(stale_after_seconds))
        self.sequence = 0
        self.values: dict[str, dict[str, Any]] = {}
        self.services: dict[str, dict[str, Any]] = {}
        self.health: dict[str, Any] = {
            "state": "init",
            "degraded_until": 0.0,
            "timeouts_60s": 0,
            "avg_latency_ms": 0.0,
            "max_latency_ms": 0.0,
        }

    def update_value(
        self,
        key: str,
        value: Any,
        *,
        source: str,
        status: str = "fresh",
        confidence: float = 1.0,
        last_error: str = "",
        now: float | None = None,
    ) -> None:
        current = _now() if now is None else float(now)
        self.values[str(key)] = {
            "value": _json_ready(value),
            "source": str(source),
            "updated_at": current,
            "age_s": 0.0,
            "status": str(status),
            "last_error": str(last_error),
            "confidence": float(confidence),
        }
        self.sequence += 1

    def mark_error(self, key: str, *, source: str, error: BaseException | str, now: float | None = None) -> None:
        current = _now() if now is None else float(now)
        current_value = self.values.get(str(key), {})
        self.values[str(key)] = {
            "value": current_value.get("value"),
            "source": str(source),
            "updated_at": float(current_value.get("updated_at", 0.0) or 0.0),
            "age_s": max(0.0, current - float(current_value.get("updated_at", 0.0) or 0.0)),
            "status": "error",
            "last_error": str(error),
            "confidence": 0.0,
        }
        self.sequence += 1

    def update_services(self, names: list[str], *, now: float | None = None) -> None:
        current = _now() if now is None else float(now)
        self.services = {str(name): {"seen_at": current, "status": "present"} for name in names}
        self.sequence += 1

    def snapshot(self, *, now: float | None = None) -> dict[str, Any]:
        current = _now() if now is None else float(now)
        values = {key: self._value_snapshot(item, current) for key, item in self.values.items()}
        return {
            "schema_version": DBUS_GATEWAY_SCHEMA_VERSION,
            "sequence": self.sequence,
            "captured_at": current,
            "dbus_health": dict(self.health),
            "values": values,
            "services": dict(self.services),
        }

    def _value_snapshot(self, item: Mapping[str, Any], now: float) -> dict[str, Any]:
        updated_at = float(item.get("updated_at", 0.0) or 0.0)
        age = max(0.0, now - updated_at) if updated_at else 0.0
        status = str(item.get("status", "unknown"))
        if status == "fresh" and self.stale_after_seconds > 0.0 and age > self.stale_after_seconds:
            status = "stale"
        return {
            **dict(item),
            "age_s": age,
            "status": status,
        }

    def write_snapshot_files(self) -> None:
        os.makedirs(self.paths.run_dir, exist_ok=True)
        snapshot = self.snapshot()
        write_json_file(self.paths.cache_path, snapshot)
        write_text_atomically(self.paths.cache_sequence_path, f"{self.sequence}\n")
        write_json_file(
            self.paths.health_path,
            {
                "schema_version": DBUS_GATEWAY_SCHEMA_VERSION,
                "sequence": self.sequence,
                "captured_at": snapshot["captured_at"],
                "dbus_health": snapshot["dbus_health"],
            },
        )

    @staticmethod
    def load_snapshot(path: str, *, max_age_seconds: float = 30.0, now: float | None = None) -> dict[str, Any]:
        payload = read_json_file(path, {})
        if not isinstance(payload, dict):
            return {}
        captured_at = float(payload.get("captured_at", 0.0) or 0.0)
        if captured_at <= 0.0:
            return {}
        current = _now() if now is None else float(now)
        if max_age_seconds >= 0.0 and current - captured_at > float(max_age_seconds):
            return {}
        return payload

    @staticmethod
    def value_entry(snapshot: Mapping[str, Any], key: str) -> dict[str, Any] | None:
        values = snapshot.get("values")
        if not isinstance(values, Mapping):
            return None
        item = values.get(key)
        return dict(item) if isinstance(item, Mapping) else None


class DbusCommandInbox:
    """Atomic JSON command directory used for writes toward the DBus adapter."""

    def __init__(self, command_dir: str) -> None:
        self.command_dir = command_dir

    def enqueue(self, command: Mapping[str, Any]) -> str:
        os.makedirs(self.command_dir, exist_ok=True)
        normalized = self._normalized_command(command)
        command_id = self._command_id(normalized)
        payload = {
            "schema_version": DBUS_GATEWAY_SCHEMA_VERSION,
            "id": command_id,
            "created_at": float(normalized.get("created_at", _now())),
            **normalized,
            "queue_class": command_queue_class(normalized),
        }
        target = os.path.join(self.command_dir, f"{command_id}.json")
        if str(normalized.get("coalesce_key") or "").strip() and os.path.exists(target):
            existing = read_json_file(target, {})
            if isinstance(existing, Mapping):
                if not self._should_replace_existing_payload(existing, payload):
                    return target
                payload["lifecycle_state"] = "coalesced"
                if _priority_rank(existing.get("priority")) == _priority_rank(payload.get("priority")):
                    payload["created_at"] = _float_or_zero(existing.get("created_at")) or payload["created_at"]
                    payload["updated_at"] = _now()
        payload.setdefault("lifecycle_state", "queued")
        write_json_file(target, payload)
        return target

    @staticmethod
    def _should_replace_existing(path: str, payload: Mapping[str, Any]) -> bool:
        existing = read_json_file(path, {})
        if not isinstance(existing, Mapping):
            return True
        return DbusCommandInbox._should_replace_existing_payload(existing, payload)

    @staticmethod
    def _should_replace_existing_payload(existing: Mapping[str, Any], payload: Mapping[str, Any]) -> bool:
        existing_rank = _priority_rank(existing.get("priority"))
        new_rank = _priority_rank(payload.get("priority"))
        if new_rank < existing_rank:
            return True
        if new_rank > existing_rank:
            return False
        return _float_or_zero(payload.get("created_at")) >= _float_or_zero(existing.get("created_at"))

    @staticmethod
    def _normalized_command(command: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(command)
        kind = str(payload.get("kind") or payload.get("type") or "")
        if kind == "refresh_services":
            payload["coalesce_key"] = "refresh:services"
        return payload

    @staticmethod
    def _command_id(command: Mapping[str, Any]) -> str:
        coalesce_key = str(command.get("coalesce_key") or "").strip()
        if coalesce_key:
            digest = hashlib.sha256(coalesce_key.encode("utf-8")).hexdigest()[:24]
            return f"coalesced-{digest}"
        return f"cmd-{time.time_ns()}-{uuid.uuid4().hex[:8]}"

    def load_pending(self) -> list[tuple[str, dict[str, Any]]]:
        try:
            paths = sorted(Path(self.command_dir).glob("*.json"))
        except OSError:
            return []
        pending: list[tuple[str, dict[str, Any]]] = []
        for path in paths:
            payload = read_json_file(str(path), {})
            if isinstance(payload, dict):
                pending.append((str(path), self._normalized_command(payload)))
        return pending

    def remove(self, path: str) -> None:
        try:
            os.unlink(path)
        except FileNotFoundError:
            return

    @staticmethod
    def coalesce(commands: list[tuple[str, dict[str, Any]]]) -> list[tuple[str, dict[str, Any]]]:
        """Return commands with latest command per coalesce key, priority-aware."""
        selected: "OrderedDict[str, tuple[str, dict[str, Any]]]" = OrderedDict()
        passthrough: list[tuple[str, dict[str, Any]]] = []
        for path, command in commands:
            key = str(command.get("coalesce_key") or "")
            if not key:
                passthrough.append((path, command))
                continue
            if key in selected:
                old_path, old_command = selected[key]
                old_rank = _priority_rank(old_command.get("priority"))
                new_rank = _priority_rank(command.get("priority"))
                old_created = _float_or_zero(old_command.get("created_at"))
                new_created = _float_or_zero(command.get("created_at"))
                if new_rank < old_rank or (new_rank == old_rank and new_created >= old_created):
                    del selected[key]
                    selected[key] = (path, command)
                else:
                    selected[key] = (old_path, old_command)
            else:
                selected[key] = (path, command)
        return sorted(passthrough + list(selected.values()), key=lambda item: _command_order_key(item[1]))


def _float_or_zero(value: object) -> float:
    if isinstance(value, (str, bytes, int, float)):
        try:
            return float(value)
        except ValueError:
            return 0.0
    try:
        method = getattr(value, "__float__")
    except AttributeError:
        return 0.0
    try:
        return float(method())
    except (TypeError, ValueError):
        return 0.0


def _command_order_key(command: Mapping[str, Any]) -> tuple[int, int, float, int, str]:
    return (
        _priority_rank(command.get("priority")),
        _command_kind_rank(command),
        _float_or_zero(command.get("created_at")),
        _publish_path_rank(command),
        str(command.get("id") or ""),
    )


def _command_kind_rank(command: Mapping[str, Any]) -> int:
    kind = str(command.get("kind") or command.get("type") or "")
    if kind == "register_service":
        return 0
    if kind == "register_path":
        return 1
    return 2


def _publish_path_rank(command: Mapping[str, Any]) -> int:
    if str(command.get("priority") or "").strip().lower() != "publish":
        return 0
    kind = str(command.get("kind") or command.get("type") or "")
    if kind != "publish_value":
        return 0
    return PUBLISH_PATH_RANKS.get(str(command.get("path") or ""), 3)


class GatewayClient:
    """Small Unix-socket and command-file client used by non-DBus processes."""

    def __init__(self, paths: GatewayPaths | None = None, *, timeout_seconds: float = 0.5) -> None:
        self.paths = paths or gateway_paths()
        self.timeout_seconds = max(0.05, float(timeout_seconds))
        self.commands = DbusCommandInbox(self.paths.command_dir)
        self._backpressure_cache: tuple[float, str] = (0.0, "unknown")

    def send(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(self.timeout_seconds)
                sock.connect(self.paths.socket_path)
                sock.sendall((compact_json(_json_ready(dict(payload))) + "\n").encode("utf-8"))
                data = sock.recv(65536)
            if not data:
                return {"ok": True}
            response = json.loads(data.decode("utf-8").strip())
            return response if isinstance(response, dict) else {"ok": False, "error": "invalid-response"}
        except Exception as error:  # pylint: disable=broad-except
            return {"ok": False, "error": str(error)}

    def enqueue_command(self, command: Mapping[str, Any]) -> str:
        if not command_allowed_by_backpressure(command, self.backpressure_state(max_age_seconds=2.0)):
            return ""
        return self.commands.enqueue(command)

    def publish_path(self, path: str, value: Any, *, priority: str = "publish", source: str = "core") -> None:
        self.enqueue_command(
            {
                "kind": "publish_value",
                "source": source,
                "path": str(path),
                "value": _json_ready(value),
                "priority": priority,
                "coalesce_key": f"publish:{path}",
            }
        )

    def register_path(self, path: str, value: Any, *, writeable: bool = False, source: str = "core") -> None:
        self.enqueue_command(
            {
                "kind": "register_path",
                "source": source,
                "path": str(path),
                "value": _json_ready(value),
                "writeable": bool(writeable),
                "priority": "publish",
                "coalesce_key": f"register:{path}",
            }
        )

    def request_read(
        self,
        key_or_service: str,
        path: str = "",
        *,
        priority: str = "read",
        source: str = "core",
        reason: str = "",
    ) -> None:
        command = {
            "kind": "refresh_value",
            "source": source,
            "priority": priority,
            "reason": reason,
        }
        if path:
            service = str(key_or_service)
            command.update(
                {
                    "service": service,
                    "path": str(path),
                    "coalesce_key": f"refresh:{service}:{path}",
                }
            )
        else:
            key = str(key_or_service)
            command.update({"key": key, "coalesce_key": f"refresh:{key}"})
        self.enqueue_command(command)

    def load_cache(self, *, max_age_seconds: float = 10.0) -> dict[str, Any]:
        return DbusCacheStore.load_snapshot(self.paths.cache_path, max_age_seconds=max_age_seconds)

    def load_health(self, *, max_age_seconds: float = 10.0) -> dict[str, Any]:
        payload = DbusCacheStore.load_snapshot(self.paths.health_path, max_age_seconds=max_age_seconds)
        health = payload.get("dbus_health") if isinstance(payload, Mapping) else None
        return dict(health) if isinstance(health, Mapping) else payload

    def backpressure_state(self, *, max_age_seconds: float = 10.0) -> str:
        cached_at, cached_state = self._backpressure_cache
        now = _now()
        if now - cached_at < 1.0 and cached_state != "unknown":
            return cached_state
        health = self.load_health(max_age_seconds=max_age_seconds)
        backpressure = health.get("backpressure") if isinstance(health, Mapping) else None
        if isinstance(backpressure, Mapping):
            state = str(backpressure.get("state", "unknown") or "unknown")
            self._backpressure_cache = (now, state)
            return state
        self._backpressure_cache = (now, "unknown")
        return "unknown"


class GatewayDbusServiceProxy:
    """Minimal ``VeDbusService`` facade that only talks to the DBus gateway."""

    def __init__(self, name: str, *, paths: GatewayPaths | None = None, client: GatewayClient | None = None) -> None:
        self.name = str(name)
        self._client = client or GatewayClient(paths)
        self._values: dict[str, Any] = {}
        self._writeable: set[str] = set()
        self._callbacks: dict[str, Any] = {}

    def add_path(
        self,
        path: str,
        value: Any,
        gettextcallback: Any = None,
        writeable: bool = False,
        onchangecallback: Any = None,
    ) -> None:
        del gettextcallback
        path = str(path)
        self._values[path] = value
        if writeable:
            self._writeable.add(path)
        if onchangecallback is not None:
            self._callbacks[path] = onchangecallback
        self._client.register_path(path, value, writeable=writeable)

    def register(self) -> None:
        self._client.enqueue_command(
            {
                "kind": "register_service",
                "service": self.name,
                "source": "core",
                "priority": "publish",
                "coalesce_key": "register-service",
            }
        )

    def __getitem__(self, path: str) -> Any:
        return self._values[str(path)]

    def __setitem__(self, path: str, value: Any) -> None:
        path = str(path)
        self._values[path] = value
        self._client.publish_path(path, value)

    def apply_gateway_write(self, path: str, value: Any) -> bool:
        """Compatibility hook for tests and future in-process gateway delivery."""
        callback = self._callbacks.get(str(path))
        if callback is None:
            self._values[str(path)] = value
            return True
        return bool(callback(str(path), value))


def gateway_value(snapshot: Mapping[str, Any], key: str, *, max_age_seconds: float) -> Any:
    entry = DbusCacheStore.value_entry(snapshot, key)
    if entry is None:
        return None
    if str(entry.get("status", "")) not in ("fresh", "stale"):
        return None
    if float(entry.get("age_s", 0.0) or 0.0) > float(max_age_seconds):
        return None
    return entry.get("value")


def command_queue_class(command: Mapping[str, Any]) -> str:
    kind = str(command.get("kind") or command.get("type") or "")
    if kind in {"register_path", "register_service"}:
        return "startup/register"
    if kind in {"publish_value", "publish_desired"}:
        if _is_gui_critical_publish(command):
            return "gui-critical-publish"
        return "local-publish"
    if kind == "set_value":
        return "remote-write"
    if kind == "refresh_value":
        key = str(command.get("key") or "")
        return "read-fast" if key in FAST_READ_KEYS else "read-slow"
    if kind == "refresh_services":
        return "discovery"
    if kind == "introspect":
        return "introspection"
    return "diagnostic"


def command_allowed_by_backpressure(command: Mapping[str, Any], state: str) -> bool:
    normalized_state = str(state or "unknown")
    if normalized_state in {"ok", "unknown"}:
        return True
    priority = str(command.get("priority") or "diagnostic").strip().lower()
    queue_class = str(command.get("queue_class") or command_queue_class(command))
    if queue_class == "startup/register":
        return True
    if normalized_state == "congested":
        return priority not in {"optional", "diagnostic"} and queue_class != "diagnostic"
    if normalized_state == "slow":
        return queue_class == "gui-critical-publish" or priority in {"safety", "user"}
    if normalized_state == "protective":
        return priority == "safety" or (priority == "user" and queue_class == "gui-critical-publish")
    return True


def _is_gui_critical_publish(command: Mapping[str, Any]) -> bool:
    path = str(command.get("path") or "")
    if path in GUI_CRITICAL_PUBLISH_PATHS:
        return True
    paths = command.get("paths")
    if isinstance(paths, Mapping):
        return any(str(item) in GUI_CRITICAL_PUBLISH_PATHS for item in paths)
    return False


class LatencyWindow:
    """Rolling latency/error window used by the adapter circuit breaker."""

    def __init__(self, window_seconds: float = 60.0) -> None:
        self.window_seconds = max(1.0, float(window_seconds))
        self._latencies: deque[tuple[float, float]] = deque()
        self._timeouts: deque[float] = deque()

    def record_latency(self, latency_ms: float, *, now: float | None = None) -> None:
        current = _now() if now is None else float(now)
        self._latencies.append((current, max(0.0, float(latency_ms))))
        self._prune(current)

    def record_timeout(self, *, now: float | None = None) -> None:
        current = _now() if now is None else float(now)
        self._timeouts.append(current)
        self._prune(current)

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._latencies and self._latencies[0][0] < cutoff:
            self._latencies.popleft()
        while self._timeouts and self._timeouts[0] < cutoff:
            self._timeouts.popleft()

    def summary(self, *, now: float | None = None) -> dict[str, Any]:
        current = _now() if now is None else float(now)
        self._prune(current)
        latencies = [latency for _timestamp, latency in self._latencies]
        return {
            "timeouts_60s": len(self._timeouts),
            "avg_latency_ms": sum(latencies) / len(latencies) if latencies else 0.0,
            "max_latency_ms": max(latencies) if latencies else 0.0,
        }

# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus gateway client and VeDbusService-like proxy."""

from __future__ import annotations

import json
import socket
from typing import Any, Mapping

from venus_evcharger.core.shared import compact_json
from venus_evcharger.dbus_gateway_cache import DbusCacheStore
from venus_evcharger.dbus_gateway_commands import DbusCommandInbox
from venus_evcharger.dbus_gateway_core import GatewayPaths, _json_ready, _now, gateway_paths
from venus_evcharger.dbus_gateway_policy import command_allowed_by_backpressure

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
        if _backpressure_cache_fresh(cached_at, cached_state, now):
            return cached_state
        health = self.load_health(max_age_seconds=max_age_seconds)
        state = _backpressure_state_from_health(health)
        self._backpressure_cache = (now, "unknown")
        if state == "unknown":
            return state
        self._backpressure_cache = (now, state)
        return state


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


def _backpressure_cache_fresh(cached_at: float, cached_state: str, now: float) -> bool:
    return now - cached_at < 1.0 and cached_state != "unknown"


def _backpressure_state_from_health(health: Mapping[str, Any]) -> str:
    backpressure = health.get("backpressure") if isinstance(health, Mapping) else None
    if not isinstance(backpressure, Mapping):
        return "unknown"
    return str(backpressure.get("state", "unknown") or "unknown")


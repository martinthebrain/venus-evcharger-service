# SPDX-License-Identifier: GPL-3.0-or-later
"""Write scheduling for the dedicated DBus adapter."""

from __future__ import annotations

import logging
from typing import Any, Mapping

import dbus

from venus_evcharger.dbus_adapter_components import CommandOutcome, DbusOperationDeferred
from venus_evcharger.dbus_gateway import DbusCommandInbox, dbus_path_key


class DbusWriteScheduler:
    """Schedule and coalesce DBus writes owned by the adapter process."""

    def __init__(self, adapter: Any) -> None:
        self.adapter = adapter
        self.registered_paths: set[str] = set()
        self.last_values: dict[str, Any] = {}

    def process_one(self) -> bool:
        pending = self.adapter.commands.load_pending()
        coalesced = DbusCommandInbox.coalesce(pending)
        if not coalesced:
            return False
        path, command = coalesced[0]
        try:
            outcome = self.process_command(command, command_file=path)
        except DbusOperationDeferred:
            outcome = "deferred"
        except Exception as error:  # pylint: disable=broad-except
            logging.exception("Gateway command failed; keeping for retry path=%s: %s", path, error)
            outcome = "deferred"
        if outcome in ("applied", "dropped"):
            self.adapter.commands.remove(path)
            self.drop_stale_coalesced_commands(path, command)
        return True

    def process_command(self, command: Mapping[str, Any], *, command_file: str = "") -> CommandOutcome:
        kind = str(command.get("kind") or command.get("type") or "")
        priority = str(command.get("priority") or "diagnostic")
        if not self.adapter.circuit.allows_priority(priority):
            return "deferred"
        if kind == "register_service":
            self.adapter._ensure_dbus_service()
            return "applied"
        if kind == "register_path":
            return self.register_path(command)
        if kind in ("publish_value", "publish_desired"):
            return self.publish_command(command, command_file=command_file)
        if kind == "set_value":
            return self.set_remote_value(command)
        return self.adapter._process_non_write_command(command)

    def drop_stale_coalesced_commands(self, processed_path: str, processed_command: Mapping[str, Any]) -> None:
        key = str(processed_command.get("coalesce_key") or "")
        if not key:
            return
        for path, command in self.adapter.commands.load_pending():
            if path != processed_path and str(command.get("coalesce_key") or "") == key:
                self.adapter.commands.remove(path)

    def register_path(self, command: Mapping[str, Any]) -> CommandOutcome:
        self.adapter._ensure_dbus_service()
        path = str(command.get("path") or "")
        if not path or path in self.registered_paths:
            return "applied"
        value = command.get("value")
        writeable = bool(command.get("writeable"))
        self.adapter._dbusservice.add_path(
            path,
            value,
            writeable=writeable,
            onchangecallback=self.handle_gui_write if writeable else None,
        )
        self.registered_paths.add(path)
        self.last_values[path] = value
        return "applied"

    def handle_gui_write(self, path: str, value: Any) -> bool:
        self.last_values[str(path)] = value
        self.adapter.core_commands.enqueue(
            {
                "kind": "user_command",
                "source": "dbus-gui",
                "path": str(path),
                "value": self.adapter._json_ready(value),
                "priority": "user",
                "coalesce_key": f"core:{path}",
            }
        )
        return True

    def publish_command(self, command: Mapping[str, Any], *, command_file: str = "") -> CommandOutcome:
        if str(command.get("kind")) == "publish_desired":
            paths = command.get("paths")
            if isinstance(paths, Mapping):
                items = list(paths.items())
                if not items:
                    return "applied"
                path, value = items[0]
                item_outcome = self.publish_path(str(path), value)
                if item_outcome != "applied":
                    return item_outcome
                remaining = {str(remaining_path): remaining_value for remaining_path, remaining_value in items[1:]}
                if remaining and command_file:
                    self.adapter.json_writer.write(command_file, {**dict(command), "paths": remaining})
                    return "deferred"
                return "applied"
            return "dropped"
        return self.publish_path(str(command.get("path") or ""), command.get("value"))

    def publish_path(self, path: str, value: Any) -> CommandOutcome:
        if not path or self.last_values.get(path) == value:
            return "applied"
        self.adapter._ensure_dbus_service()
        if path not in self.registered_paths:
            logging.debug("Deferring publish for unregistered DBus path %s", path)
            return "deferred"
        self.adapter._timed("write", lambda: self.adapter._dbusservice.__setitem__(path, value))
        self.last_values[path] = value
        source = f"{self.adapter.service_name}{path}"
        self.adapter.cache.update_value(
            dbus_path_key(self.adapter.service_name, path),
            value,
            source=source,
            confidence=1.0,
        )
        return "applied"

    def set_remote_value(self, command: Mapping[str, Any]) -> CommandOutcome:
        service = str(command.get("service") or "")
        path = str(command.get("path") or "")
        if not service or not path:
            return "dropped"

        def _write() -> None:
            obj = self.adapter.connection.bus().get_object(service, path, introspect=False)
            iface = dbus.Interface(obj, "com.victronenergy.BusItem")
            iface.SetValue(command.get("value"), timeout=float(command.get("timeout", 1.0)))

        self.adapter._timed("write", _write)
        self.adapter.cache.update_value(
            dbus_path_key(service, path),
            command.get("value"),
            source=f"{service}{path}",
            confidence=0.9,
        )
        return "applied"

# SPDX-License-Identifier: GPL-3.0-or-later
"""Read execution for the dedicated DBus adapter."""

from __future__ import annotations

import logging
from typing import Any, Mapping

import dbus

from venus_evcharger.core.shared import coerce_dbus_numeric
from venus_evcharger.dbus_adapter_components import CommandOutcome, DbusOperationDeferred
from venus_evcharger.dbus_gateway import dbus_path_key


class DbusReadExecutor:
    """Execute scheduled DBus reads and update the adapter cache."""

    def __init__(self, adapter: Any) -> None:
        self.adapter = adapter

    def refresh_requested_value(self, command: Mapping[str, Any]) -> CommandOutcome:
        key = str(command.get("key") or "")
        if key in self.adapter.read_scheduler.specs:
            return self.poll_read_spec(key, self.adapter.read_scheduler.specs[key])
        service = str(command.get("service") or "")
        path = str(command.get("path") or "")
        if service and path:
            value = self.read_busitem(service, path)
            self.adapter.cache.update_value(dbus_path_key(service, path), value, source=f"{service}{path}")
            return "applied"
        return "dropped"

    def poll_read_spec(self, key: str, spec: Mapping[str, Any]) -> CommandOutcome:
        try:
            if spec.get("aggregate") == "sum":
                value = sum(
                    float(self.read_busitem(str(spec.get("service")), str(path)) or 0.0)
                    for path in spec.get("paths", ())
                )
                paths = [str(path) for path in spec.get("paths", ())]
                self.adapter.cache.update_value(key, value, source=f"{spec.get('service')}:{','.join(paths)}")
                return "applied"
            if spec.get("aggregate") == "services-sum":
                value, sources = self.read_prefixed_service_sum(spec)
                self.adapter.cache.update_value(key, value, source=",".join(sources) if sources else str(spec.get("prefix", "")))
                return "applied"
            service = str(spec.get("service") or "")
            path = str(spec.get("path") or "")
            value = self.read_busitem(service, path)
            self.adapter.cache.update_value(key, value, source=f"{service}{path}")
            return "applied"
        except DbusOperationDeferred:
            return "deferred"
        except Exception as error:  # pylint: disable=broad-except
            self.adapter.cache.mark_error(key, source=str(spec.get("service") or spec.get("prefix") or ""), error=error)
            self.adapter.circuit.record_error(error)
            logging.debug("DBus adapter read failed key=%s: %s", key, error)
            return "applied"

    def read_prefixed_service_sum(self, spec: Mapping[str, Any]) -> tuple[float, list[str]]:
        explicit = str(spec.get("service") or "")
        if explicit:
            services = [explicit]
        else:
            prefix = str(spec.get("prefix") or "")
            services = [name for name in self.adapter.cache.services if name.startswith(prefix)]
        if not services:
            raise RuntimeError(f"No cached services for prefix '{spec.get('prefix', '')}'")
        path = str(spec.get("path") or "")
        total = 0.0
        sources: list[str] = []
        for service in services:
            value = self.read_busitem(service, path)
            if value is None:
                continue
            total += float(value)
            sources.append(f"{service}{path}")
        return total, sources

    def read_busitem(self, service: str, path: str) -> Any:
        if not service or not path:
            return None

        def _read() -> Any:
            obj = self.adapter.connection.bus().get_object(service, path, introspect=False)
            iface = dbus.Interface(obj, "com.victronenergy.BusItem")
            return coerce_dbus_numeric(iface.GetValue(timeout=1.0))

        return self.adapter._timed("read", _read)

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
        self._aggregate_state: dict[str, dict[str, Any]] = {}
        self.last_operation_performed = False

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
        self.last_operation_performed = False
        try:
            if spec.get("aggregate") == "sum":
                return self._poll_sum_step(key, spec)
            if spec.get("aggregate") == "services-sum":
                return self._poll_services_sum_step(key, spec)
            if spec.get("aggregate") == "first-service":
                return self._poll_first_service(key, spec)
            service = str(spec.get("service") or "")
            path = str(spec.get("path") or "")
            value = self.read_busitem(service, path)
            self.adapter.cache.update_value(key, value, source=f"{service}{path}")
            return "applied"
        except DbusOperationDeferred:
            return "deferred"
        except Exception as error:  # pylint: disable=broad-except
            self._aggregate_state.pop(key, None)
            self.adapter.cache.mark_error(key, source=str(spec.get("service") or spec.get("prefix") or ""), error=error)
            logging.debug("DBus adapter read failed key=%s: %s", key, error)
            return "dropped"

    def _poll_sum_step(self, key: str, spec: Mapping[str, Any]) -> CommandOutcome:
        service = str(spec.get("service"))
        members = [(service, str(path)) for path in spec.get("paths", ()) if str(path)]
        if not members:
            self.adapter.cache.update_value(key, 0.0, source=str(spec.get("service", "")))
            return "applied"
        return self._poll_aggregate_step(key, ("sum", tuple(members)), members)

    def _poll_services_sum_step(self, key: str, spec: Mapping[str, Any]) -> CommandOutcome:
        path = str(spec.get("path") or "")
        explicit = str(spec.get("service") or "")
        if explicit:
            services = [explicit]
        else:
            prefix = str(spec.get("prefix") or "")
            services = sorted(name for name in self.adapter.cache.services if name.startswith(prefix))
        if not services:
            raise RuntimeError(f"No cached services for prefix '{spec.get('prefix', '')}'")
        return self._poll_aggregate_step(key, ("services-sum", path, tuple(services)), [(service, path) for service in services])

    def _poll_first_service(self, key: str, spec: Mapping[str, Any]) -> CommandOutcome:
        path = str(spec.get("path") or "")
        prefix = str(spec.get("prefix") or "")
        services = sorted(name for name in self.adapter.cache.services if name.startswith(prefix))
        if not services:
            raise RuntimeError(f"No cached services for prefix '{prefix}'")
        service = services[0]
        value = self.read_busitem(service, path)
        self.adapter.cache.update_value(key, value, source=f"{service}{path}")
        return "applied"

    def _poll_aggregate_step(
        self,
        key: str,
        signature: tuple[Any, ...],
        members: list[tuple[str, str]],
    ) -> CommandOutcome:
        state = self._aggregate_state.get(key)
        if state is None or state.get("signature") != signature:
            state = {"signature": signature, "index": 0, "total": 0.0, "sources": []}
            self._aggregate_state[key] = state
        index = int(state.get("index", 0))
        service, path = members[index]
        value = self.read_busitem(service, path)
        self.last_operation_performed = True
        if value is not None:
            state["total"] = float(state.get("total", 0.0)) + float(value)
            state["sources"] = [*list(state.get("sources", [])), f"{service}{path}"]
        state["index"] = index + 1
        if state["index"] < len(members):
            return "deferred"
        sources = list(state.get("sources", []))
        self.adapter.cache.update_value(key, state.get("total", 0.0), source=",".join(sources) if sources else key)
        self._aggregate_state.pop(key, None)
        return "applied"

    def read_busitem(self, service: str, path: str) -> Any:
        if not service or not path:
            return None

        return self.adapter._timed("read", lambda: self._read_busitem_now(service, path))

    def _read_busitem_now(self, service: str, path: str) -> Any:
        obj = self.adapter.connection.bus().get_object(service, path, introspect=False)
        iface = dbus.Interface(obj, "com.victronenergy.BusItem")
        return coerce_dbus_numeric(iface.GetValue(timeout=1.0))

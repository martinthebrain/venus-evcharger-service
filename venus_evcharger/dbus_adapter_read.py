# SPDX-License-Identifier: GPL-3.0-or-later
"""Read execution for the dedicated DBus adapter."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import dbus

from venus_evcharger.core.shared import coerce_dbus_numeric
from venus_evcharger.dbus_adapter_components import CommandOutcome, DbusOperationDeferred
from venus_evcharger.dbus_gateway import dbus_path_key

PV_MEMBER_ERROR_BACKOFF_SECONDS = 300.0
DbusPathKey = str
_OPTIONAL_MEMBER_FAILED = object()


@dataclass(frozen=True)
class ReadTarget:
    service: str
    path: str

    @property
    def source(self) -> str:
        return f"{self.service}{self.path}"

    @property
    def cache_key(self) -> DbusPathKey:
        return dbus_path_key(self.service, self.path)


def read_target(service: object, path: object) -> ReadTarget | None:  # pragma: no mutate block
    service_name = str(service or "").strip()
    dbus_path = str(path or "").strip()
    if not service_name or not dbus_path.startswith("/"):
        return None
    return ReadTarget(service_name, dbus_path)


class _ReadCacheProtocol(Protocol):
    @property
    def services(self) -> Mapping[str, Mapping[str, Any]]: ...  # pragma: no cover

    @property
    def values(self) -> Mapping[str, Mapping[str, Any]]: ...  # pragma: no cover

    def update_value(
        self,
        key: str,
        value: Any,
        *,
        metadata: Any | None = None,
        **metadata_fields: Any,
    ) -> None: ...  # pragma: no cover

    def mark_error(  # pragma: no cover
        self, key: str, *, source: str, error: BaseException | str, now: float | None = None
    ) -> None: ...


class _ReadSchedulerProtocol(Protocol):
    @property
    def specs(self) -> Mapping[str, Mapping[str, Any]]: ...  # pragma: no cover


class _ConnectionProtocol(Protocol):
    def bus(self) -> Any: ...  # pragma: no cover


class _RateLimiterProtocol(Protocol):
    def require_due(self, kind: str) -> None: ...  # pragma: no cover


class _CircuitProtocol(Protocol):
    def record_success(self, latency_ms: float, *, kind: str = "dbus") -> None: ...  # pragma: no cover


class _ReadAdapterProtocol(Protocol):
    @property
    def cache(self) -> _ReadCacheProtocol: ...  # pragma: no cover

    @property
    def connection(self) -> _ConnectionProtocol: ...  # pragma: no cover

    @property
    def read_scheduler(self) -> _ReadSchedulerProtocol: ...  # pragma: no cover

    @property
    def rate_limiter(self) -> _RateLimiterProtocol: ...  # pragma: no cover

    @property
    def circuit(self) -> _CircuitProtocol: ...  # pragma: no cover

    def _timed(self, kind: str, operation: Callable[[], Any]) -> Any: ...  # pragma: no cover


class DbusReadExecutor:
    """Execute scheduled DBus reads and update the adapter cache."""

    def __init__(self, adapter: _ReadAdapterProtocol) -> None:  # pragma: no mutate block
        self.adapter = adapter
        self._aggregate_state: dict[str, dict[str, Any]] = {}
        self.last_operation_performed = False

    def refresh_requested_value(self, command: Mapping[str, Any]) -> CommandOutcome:  # pragma: no mutate block
        key = str(command.get("key") or "")
        if key in self.adapter.read_scheduler.specs:
            return self.poll_read_spec(key, self.adapter.read_scheduler.specs[key])
        return self._refresh_direct_value(command)

    def _refresh_direct_value(self, command: Mapping[str, Any]) -> CommandOutcome:  # pragma: no mutate block
        target = self._direct_refresh_target(command)
        if target is None:
            return "dropped"
        return self._read_direct_refresh(target)

    @staticmethod
    def _direct_refresh_target(command: Mapping[str, Any]) -> ReadTarget | None:  # pragma: no mutate block
        return read_target(command.get("service"), command.get("path"))

    def _read_direct_refresh(self, target: ReadTarget) -> CommandOutcome:  # pragma: no mutate block
        try:
            value = self.read_busitem(target.service, target.path)
        except DbusOperationDeferred:
            return "deferred"
        except Exception as error:  # pylint: disable=broad-except
            self.adapter.cache.mark_error(target.cache_key, source=target.source, error=error)
            logging.debug("DBus adapter direct refresh failed key=%s: %s", target.cache_key, error)
            return "dropped"
        self.adapter.cache.update_value(target.cache_key, value, source=target.source)
        return "applied"

    def poll_read_spec(self, key: str, spec: Mapping[str, Any]) -> CommandOutcome:  # pragma: no mutate block
        self.last_operation_performed = False
        try:
            return self._poll_read_spec_unchecked(key, spec)
        except DbusOperationDeferred:
            return "deferred"
        except Exception as error:  # pylint: disable=broad-except
            if self._optional_zero_on_error(spec):
                self._mark_optional_zero(key, spec, error)
                return "applied"
            self._mark_read_error(key, spec, error)
            return "dropped"

    def _poll_read_spec_unchecked(self, key: str, spec: Mapping[str, Any]) -> CommandOutcome:  # pragma: no mutate block
        aggregate = str(spec.get("aggregate") or "")
        handlers = {
            "sum": self._poll_sum_step,
            "services-sum": self._poll_services_sum_step,
            "first-service": self._poll_first_service,
            "pv-total": self._poll_pv_total_step,
        }
        handler = handlers.get(aggregate)
        return handler(key, spec) if handler else self._poll_direct_spec(key, spec)

    def _poll_direct_spec(self, key: str, spec: Mapping[str, Any]) -> CommandOutcome:  # pragma: no mutate block
        target = read_target(spec.get("service"), spec.get("path"))
        if target is None:
            return "dropped"
        value = self.read_busitem(target.service, target.path)
        self._update_read_value(key, target, value)
        return "applied"

    def _mark_read_error(self, key: str, spec: Mapping[str, Any], error: BaseException) -> None:  # pragma: no mutate block
        self._aggregate_state.pop(key, None)
        self.adapter.cache.mark_error(key, source=str(spec.get("service") or spec.get("prefix") or ""), error=error)
        logging.debug("DBus adapter read failed key=%s: %s", key, error)

    def _mark_optional_zero(self, key: str, spec: Mapping[str, Any], error: BaseException) -> None:  # pragma: no mutate block
        self._aggregate_state.pop(key, None)
        source = str(spec.get("service") or spec.get("prefix") or key)
        self.adapter.cache.update_value(
            key,
            0.0,
            source=source,
            confidence=float(spec.get("optional_confidence", 0.2) or 0.2),
            last_error=str(error),
        )
        logging.debug("DBus adapter optional read fell back to zero key=%s: %s", key, error)

    @staticmethod
    def _optional_zero_on_error(spec: Mapping[str, Any]) -> bool:  # pragma: no mutate block
        return str(spec.get("optional_zero_on_error", "")).strip().lower() in {"1", "true", "yes", "on"}

    def has_pending_aggregate(self) -> bool:
        return bool(self._aggregate_state)

    def _poll_sum_step(self, key: str, spec: Mapping[str, Any]) -> CommandOutcome:  # pragma: no mutate block
        service = str(spec.get("service"))
        members = [(service, str(path)) for path in spec.get("paths", ()) if str(path)]
        if not members:
            self.adapter.cache.update_value(key, 0.0, source=str(spec.get("service", "")))
            return "applied"
        return self._poll_aggregate_step(key, ("sum", tuple(members)), members)

    def _poll_services_sum_step(self, key: str, spec: Mapping[str, Any]) -> CommandOutcome:  # pragma: no mutate block
        path = str(spec.get("path") or "")
        services = self._services_for_sum(spec)
        if not services:
            raise RuntimeError(f"No cached services for prefix '{spec.get('prefix', '')}'")
        return self._poll_aggregate_step(key, ("services-sum", path, tuple(services)), [(service, path) for service in services])

    def _poll_pv_total_step(self, key: str, spec: Mapping[str, Any]) -> CommandOutcome:  # pragma: no mutate block
        members = self._pv_total_members(spec)
        if not members:
            raise RuntimeError("No cached AC PV services or configured DC PV path")
        return self._poll_aggregate_step(
            key,
            ("pv-total", tuple(members)),
            members,
            ignore_member_errors=True,
            empty_confidence=float(spec.get("optional_confidence", 0.2) or 0.2),
        )

    def _poll_first_service(self, key: str, spec: Mapping[str, Any]) -> CommandOutcome:  # pragma: no mutate block
        path = str(spec.get("path") or "")
        prefix = str(spec.get("prefix") or "")
        services = self._prefixed_services(prefix)
        if not services:
            raise RuntimeError(f"No cached services for prefix '{prefix}'")
        target = read_target(services[0], path)
        if target is None:
            return "dropped"
        value = self.read_busitem(target.service, target.path)
        self._update_read_value(key, target, value)
        return "applied"

    def _services_for_sum(self, spec: Mapping[str, Any]) -> list[str]:  # pragma: no mutate block
        explicit = str(spec.get("service") or "")
        return [explicit] if explicit else self._prefixed_services(str(spec.get("prefix") or ""))

    def _prefixed_services(self, prefix: str) -> list[str]:  # pragma: no mutate block
        return sorted(name for name in self.adapter.cache.services if name.startswith(prefix))

    def _pv_total_members(self, spec: Mapping[str, Any]) -> list[tuple[str, str]]:
        return [*self._ac_pv_members(spec), *self._dc_pv_members(spec)]

    def _ac_pv_members(self, spec: Mapping[str, Any]) -> list[tuple[str, str]]:
        path = str(spec.get("path") or "")
        return [
            (service, path)
            for service in self._services_for_sum(spec)
            if path and not self._pv_member_recently_failed(service, path)
        ]

    def _dc_pv_members(self, spec: Mapping[str, Any]) -> list[tuple[str, str]]:
        if not self._use_dc_pv(spec):
            return []
        target = self._dc_pv_target(spec)
        if target is None or self._pv_member_recently_failed(*target):
            return []
        return [target]

    @staticmethod
    def _dc_pv_target(spec: Mapping[str, Any]) -> tuple[str, str] | None:
        target = read_target(spec.get("dc_service"), spec.get("dc_path"))
        if target is None:
            return None
        return target.service, target.path

    @staticmethod
    def _use_dc_pv(spec: Mapping[str, Any]) -> bool:  # pragma: no mutate block
        return str(spec.get("use_dc_pv", "")).strip().lower() in {"1", "true", "yes", "on"}

    def _pv_member_recently_failed(self, service: str, path: str) -> bool:  # pragma: no mutate block
        entry = self.adapter.cache.values.get(dbus_path_key(service, path), {})
        if entry.get("status") != "error":
            return False
        error_at = float(entry.get("error_at", 0.0) or 0.0)
        return error_at > 0.0 and time.time() - error_at < PV_MEMBER_ERROR_BACKOFF_SECONDS

    def _poll_aggregate_step(
        self,
        key: str,
        signature: tuple[Any, ...],
        members: list[tuple[str, str]],
        *,
        ignore_member_errors: bool = False,
        empty_confidence: float = 1.0,
    ) -> CommandOutcome:  # pragma: no mutate block
        state = self._aggregate_state_for(key, signature, empty_confidence)
        index = int(state.get("index", 0))
        service, path = members[index]
        value = self._read_aggregate_member(service, path, state, ignore_member_errors=ignore_member_errors)
        self.last_operation_performed = True
        self._record_aggregate_member(state, service, path, value)
        state["index"] = index + 1
        return self._aggregate_step_outcome(key, state, len(members))

    def _aggregate_state_for(
        self,
        key: str,
        signature: tuple[Any, ...],
        empty_confidence: float,
    ) -> dict[str, Any]:  # pragma: no mutate block
        state = self._aggregate_state.get(key)
        if state is None or state.get("signature") != signature:
            state = {
                "signature": signature,
                "index": 0,
                "total": 0.0,
                "sources": [],
                "errors": [],
                "empty_confidence": empty_confidence,
            }
            self._aggregate_state[key] = state
        return state

    def _aggregate_step_outcome(
        self,
        key: str,
        state: Mapping[str, Any],
        member_count: int,
    ) -> CommandOutcome:  # pragma: no mutate block
        if int(state.get("index", 0)) < member_count:
            return "deferred"
        self._complete_aggregate(key, state)
        return "applied"

    def _read_aggregate_member(
        self,
        service: str,
        path: str,
        state: dict[str, Any],
        *,
        ignore_member_errors: bool,
    ) -> Any:  # pragma: no mutate block
        value = self._read_aggregate_member_value(service, path, state, ignore_member_errors=ignore_member_errors)
        if value is _OPTIONAL_MEMBER_FAILED:
            return None
        target = read_target(service, path)
        if target is not None:
            self.adapter.cache.update_value(target.cache_key, value, source=target.source)
        return value

    def _read_aggregate_member_value(
        self,
        service: str,
        path: str,
        state: dict[str, Any],
        *,
        ignore_member_errors: bool,
    ) -> Any:
        try:
            read = self.read_optional_busitem if ignore_member_errors else self.read_busitem
            return read(service, path)
        except DbusOperationDeferred:
            raise
        except Exception as error:
            if not ignore_member_errors:
                raise
            self._record_optional_aggregate_error(service, path, state, error)
            return _OPTIONAL_MEMBER_FAILED

    def _record_optional_aggregate_error(
        self,
        service: str,
        path: str,
        state: dict[str, Any],
        error: BaseException,
    ) -> None:  # pragma: no mutate block
        target = read_target(service, path)
        if target is not None:
            self.adapter.cache.mark_error(target.cache_key, source=target.source, error=error)
        state["errors"] = [*list(state.get("errors", [])), f"{service}{path}: {error}"]
        logging.debug("DBus adapter optional aggregate member failed %s%s: %s", service, path, error)

    def _update_read_value(self, key: str, target: ReadTarget, value: Any) -> None:  # pragma: no mutate block
        path_key = target.cache_key
        self.adapter.cache.update_value(path_key, value, source=target.source)
        if key != path_key:
            self.adapter.cache.update_value(key, value, source=target.source)

    @staticmethod
    def _record_aggregate_member(state: dict[str, Any], service: str, path: str, value: Any) -> None:  # pragma: no mutate block
        if value is None:
            return
        state["total"] = float(state.get("total", 0.0)) + float(value)
        state["sources"] = [*list(state.get("sources", [])), f"{service}{path}"]

    def _complete_aggregate(self, key: str, state: Mapping[str, Any]) -> None:  # pragma: no mutate block
        sources = list(state.get("sources", []))
        errors = list(state.get("errors", []))
        self.adapter.cache.update_value(
            key,
            state.get("total", 0.0),
            source=",".join(sources) if sources else key,
            confidence=1.0 if sources else float(state.get("empty_confidence", 1.0) or 1.0),
            last_error="; ".join(str(error) for error in errors),
        )
        self._aggregate_state.pop(key, None)

    def read_busitem(self, service: str, path: str) -> Any:  # pragma: no mutate block
        if not service or not path:
            return None

        return self.adapter._timed("read", lambda: self._read_busitem_now(service, path))

    def read_optional_busitem(self, service: str, path: str) -> Any:  # pragma: no mutate block
        if not service or not path:
            return None

        self.adapter.rate_limiter.require_due("read")
        started = time.monotonic()
        value = self._read_busitem_now(service, path)
        self.adapter.circuit.record_success((time.monotonic() - started) * 1000.0, kind="optional_read")
        return value

    def _read_busitem_now(self, service: str, path: str) -> Any:  # pragma: no mutate block
        obj = self.adapter.connection.bus().get_object(service, path, introspect=False)
        iface = dbus.Interface(obj, "com.victronenergy.BusItem")
        return coerce_dbus_numeric(iface.GetValue(timeout=1.0))

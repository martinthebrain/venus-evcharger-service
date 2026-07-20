# SPDX-License-Identifier: GPL-3.0-or-later
"""Scheduled read execution for the DBus adapter."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import dbus

from venus_evcharger.core.shared import coerce_dbus_numeric
from venus_evcharger.dbus_adapter.contracts import CommandOutcome
from venus_evcharger.dbus_adapter.rate import DBUS_GATEWAY_OPERATION_ERRORS, DbusOperationDeferred
from venus_evcharger.dbus_adapter.read.aggregate import (
    OPTIONAL_MEMBER_FAILED,
    PV_TOTAL_AGGREGATE,
    AggregateState,
    AggregateStore,
)
from venus_evcharger.dbus_adapter.read.protocols import DbusReadAdapter
from venus_evcharger.dbus_adapter.read.pv import PV_MEMBER_ERROR_BACKOFF_SECONDS, pv_total_members
from venus_evcharger.dbus_adapter.read.spec import ReadSpec
from venus_evcharger.dbus_adapter.read.targets import ReadTarget, read_target
from venus_evcharger.dbus_gateway_command_types import CommandMapping

DBUS_READ_ERRORS = DBUS_GATEWAY_OPERATION_ERRORS


def _spec_text(spec: ReadSpec, key: str) -> str:
    value = spec.get(key)
    return value if isinstance(value, str) else ""


def _spec_stale_after_seconds(spec: ReadSpec) -> float | None:
    configured = spec.get("stale_after_seconds")
    if configured is not None:
        return max(0.0, float(configured))
    interval = spec.get("interval")
    return max(1.0, float(interval) * 3.0) if interval is not None else None


class DbusReadExecutor:
    """Execute scheduled DBus reads and update the adapter cache."""

    def __init__(self, adapter: DbusReadAdapter) -> None:
        self.adapter = adapter
        self._aggregates = AggregateStore()
        self._stale_after_by_key: dict[str, float | None] = {}
        self.last_operation_performed = False

    def refresh_requested_value(self, command: CommandMapping) -> CommandOutcome:
        key = str(command["key"] or "") if "key" in command else ""
        if key in self.adapter.read_scheduler.specs:
            return self.poll_read_spec(key, self.adapter.read_scheduler.specs[key])
        return self._refresh_direct_value(command)

    def _refresh_direct_value(self, command: CommandMapping) -> CommandOutcome:
        target = self._direct_refresh_target(command)
        if target is None:
            return "dropped"
        return self._read_direct_refresh(target)

    @staticmethod
    def _direct_refresh_target(command: CommandMapping) -> ReadTarget | None:
        return read_target(command.get("service"), command.get("path"))

    def _read_direct_refresh(self, target: ReadTarget) -> CommandOutcome:
        try:
            value = self.read_busitem(target.service, target.path)
        except DbusOperationDeferred:
            return "deferred"
        except DBUS_READ_ERRORS as error:
            self.adapter.cache.mark_error(target.cache_key, source=target.source, error=error)
            logging.debug("DBus adapter direct refresh failed key=%s: %s", target.cache_key, error)
            return "dropped"
        self.adapter.cache.update_external_read(
            target.cache_key,
            value,
            source=target.source,
        )
        return "applied"

    def poll_read_spec(self, key: str, spec: ReadSpec) -> CommandOutcome:
        self.last_operation_performed = False
        self._stale_after_by_key[key] = _spec_stale_after_seconds(spec)
        try:
            outcome = self._poll_read_spec_unchecked(key, spec)
            if outcome != "deferred":
                self._stale_after_by_key.pop(key, None)
            return outcome
        except DbusOperationDeferred:
            return "deferred"
        except DBUS_READ_ERRORS as error:
            if self._optional_zero_on_error(spec):
                self._mark_optional_zero(key, spec, error)
                return "applied"
            self._mark_read_error(key, spec, error)
            return "dropped"

    def _poll_read_spec_unchecked(self, key: str, spec: ReadSpec) -> CommandOutcome:
        aggregate = _spec_text(spec, "aggregate")
        handlers: dict[str, Callable[[str, ReadSpec], CommandOutcome]] = {
            "sum": self._poll_sum_step,
            "services-sum": self._poll_services_sum_step,
            "first-service": self._poll_first_service,
            PV_TOTAL_AGGREGATE: self._poll_pv_total_step,
        }
        handler = handlers.get(aggregate, self._poll_direct_spec)
        return handler(key, spec)

    def _poll_direct_spec(self, key: str, spec: ReadSpec) -> CommandOutcome:
        target = read_target(spec.get("service"), spec.get("path"))
        if target is None:
            return "dropped"
        value = self.read_busitem(target.service, target.path)
        self._update_read_value(key, target, value, spec=spec)
        return "applied"

    def _mark_read_error(self, key: str, spec: ReadSpec, error: BaseException) -> None:
        self._aggregates.discard(key)
        self._stale_after_by_key.pop(key, None)
        self.adapter.cache.mark_error(key, source=self._spec_source(spec), error=error)
        logging.debug("DBus adapter read failed key=%s: %s", key, error)

    def _mark_optional_zero(self, key: str, spec: ReadSpec, error: BaseException) -> None:
        self._aggregates.discard(key)
        self._stale_after_by_key.pop(key, None)
        self.adapter.cache.update_external_read(
            key,
            0.0,
            source=self._spec_source(spec, fallback=key),
            confidence=self._optional_confidence(spec),
            last_error=str(error),
            stale_after_seconds=_spec_stale_after_seconds(spec),
        )
        logging.debug("DBus adapter optional read fell back to zero key=%s: %s", key, error)

    @staticmethod
    def _optional_zero_on_error(spec: ReadSpec) -> bool:
        if "optional_zero_on_error" not in spec:
            return False
        return str(spec["optional_zero_on_error"]).strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _optional_confidence(spec: ReadSpec) -> float:
        if "optional_confidence" not in spec:
            return 0.2
        return float(spec["optional_confidence"] or 0.2)

    @staticmethod
    def _spec_source(spec: ReadSpec, *, fallback: str = "") -> str:
        return _spec_text(spec, "service") or _spec_text(spec, "prefix") or str(fallback)

    def has_pending_aggregate(self) -> bool:
        return self._aggregates.has_pending()

    def _poll_sum_step(self, key: str, spec: ReadSpec) -> CommandOutcome:
        service = _spec_text(spec, "service")
        paths = spec.get("paths", [])
        members = [(service, str(path)) for path in paths if str(path)]
        if not members:
            self.adapter.cache.update_external_read(
                key,
                0.0,
                source=service,
                stale_after_seconds=_spec_stale_after_seconds(spec),
            )
            return "applied"
        return self._poll_aggregate_step(key, ("sum", tuple(members)), members)

    def _poll_services_sum_step(self, key: str, spec: ReadSpec) -> CommandOutcome:
        path = _spec_text(spec, "path")
        services = self._services_for_sum(spec)
        if not services:
            raise RuntimeError(f"No cached services for prefix '{_spec_text(spec, 'prefix')}'")
        return self._poll_aggregate_step(key, ("services-sum", path, tuple(services)), [(service, path) for service in services])

    def _poll_pv_total_step(self, key: str, spec: ReadSpec) -> CommandOutcome:
        members = self._in_progress_pv_total_members(key) or pv_total_members(
            spec,
            self._services_for_sum(spec),
            self.adapter.cache.values,
        )
        if not members:
            raise RuntimeError("No available AC or DC PV source candidates")
        return self._poll_aggregate_step(
            key,
            (PV_TOTAL_AGGREGATE, tuple(members)),
            members,
            ignore_member_errors=True,
            empty_confidence=self._optional_confidence(spec),
        )

    def _in_progress_pv_total_members(self, key: str) -> list[tuple[str, str]] | None:
        return self._aggregates.signature_members(key, PV_TOTAL_AGGREGATE)

    def _poll_first_service(self, key: str, spec: ReadSpec) -> CommandOutcome:
        path = _spec_text(spec, "path")
        prefix = _spec_text(spec, "prefix")
        services = self._prefixed_services(prefix)
        if not services:
            raise RuntimeError(f"No cached services for prefix '{prefix}'")
        target = read_target(services[0], path)
        if target is None:
            return "dropped"
        value = self.read_busitem(target.service, target.path)
        self._update_read_value(key, target, value, spec=spec)
        return "applied"

    def _services_for_sum(self, spec: ReadSpec) -> list[str]:
        explicit = _spec_text(spec, "service")
        return [explicit] if explicit else self._prefixed_services(_spec_text(spec, "prefix"))

    def _prefixed_services(self, prefix: str) -> list[str]:
        return sorted(name for name in self.adapter.cache.services if name.startswith(prefix))

    def _poll_aggregate_step(
        self,
        key: str,
        signature: tuple[object, ...],
        members: list[tuple[str, str]],
        *,
        ignore_member_errors: bool = False,
        empty_confidence: float = 1.0,
    ) -> CommandOutcome:
        state = self._aggregates.state_for(key, signature, empty_confidence)
        index = state.index
        service, path = members[index]
        value = self._read_aggregate_member(service, path, state, ignore_member_errors=ignore_member_errors)
        self.last_operation_performed = True
        self._record_aggregate_member(state, service, path, value)
        state.index = index + 1
        return self._aggregate_step_outcome(key, state, len(members))

    def _aggregate_step_outcome(
        self,
        key: str,
        state: AggregateState,
        member_count: int,
    ) -> CommandOutcome:
        if not state.complete(member_count):
            return "deferred"
        self._complete_aggregate(key, state)
        return "applied"

    def _read_aggregate_member(
        self,
        service: str,
        path: str,
        state: AggregateState,
        *,
        ignore_member_errors: bool,
    ) -> object:
        value = self._read_aggregate_member_value(service, path, state, ignore_member_errors=ignore_member_errors)
        if value is OPTIONAL_MEMBER_FAILED:
            return None
        target = read_target(service, path)
        if target is not None:
            self.adapter.cache.update_external_read(
                target.cache_key,
                value,
                source=target.source,
            )
        return value

    def _read_aggregate_member_value(
        self,
        service: str,
        path: str,
        state: AggregateState,
        *,
        ignore_member_errors: bool,
    ) -> object:
        try:
            read = self.read_optional_busitem if ignore_member_errors else self.read_busitem
            value: object = read(service, path)
            return value
        except DbusOperationDeferred:
            raise
        except DBUS_READ_ERRORS as error:
            if not ignore_member_errors:
                raise
            self._record_optional_aggregate_error(service, path, state, error)
            return OPTIONAL_MEMBER_FAILED

    def _record_optional_aggregate_error(
        self,
        service: str,
        path: str,
        state: AggregateState,
        error: BaseException,
    ) -> None:
        target = read_target(service, path)
        if target is not None:
            self.adapter.cache.mark_unavailable(
                target.cache_key,
                source=target.source,
                error=error,
                retry_after_seconds=PV_MEMBER_ERROR_BACKOFF_SECONDS,
            )
        state.record_error(service, path, error)
        logging.debug("DBus adapter optional aggregate member failed %s%s: %s", service, path, error)

    def _update_read_value(
        self,
        key: str,
        target: ReadTarget,
        value: object,
        *,
        spec: ReadSpec | None = None,
    ) -> None:
        path_key = target.cache_key
        self.adapter.cache.update_external_read(
            path_key,
            value,
            source=target.source,
        )
        if key != path_key:
            self.adapter.cache.update_external_read(
                key,
                value,
                source=target.source,
                stale_after_seconds=_spec_stale_after_seconds(spec) if spec is not None else None,
            )

    @staticmethod
    def _record_aggregate_member(state: AggregateState, service: str, path: str, value: object) -> None:
        state.record_member(service, path, value)

    def _complete_aggregate(self, key: str, state: AggregateState) -> None:
        payload = state.payload(key)
        self.adapter.cache.update_external_read(
            key,
            payload["value"],
            source=payload["source"],
            confidence=payload["confidence"],
            last_error=payload["last_error"],
            stale_after_seconds=self._stale_after_by_key.pop(key, None),
        )
        self._aggregates.discard(key)

    def read_busitem(self, service: str, path: str) -> object:
        if not service or not path:
            return None

        value: object = self.adapter.timed_dbus_operation("read", lambda: self.read_busitem_now(service, path))
        return value

    def read_optional_busitem(self, service: str, path: str) -> object:
        if not service or not path:
            return None

        self.adapter.rate_limiter.require_due("read")
        started = time.monotonic()
        value: object = self.read_busitem_now(service, path)
        self.adapter.circuit.record_success((time.monotonic() - started) * 1000.0, kind="optional_read")
        return value

    def read_busitem_now(self, service: str, path: str) -> object:
        obj = self.adapter.connection.get_object(service, path, introspect=False)
        iface = dbus.Interface(obj, "com.victronenergy.BusItem")
        value: object = coerce_dbus_numeric(iface.GetValue(timeout=1.0))
        return value

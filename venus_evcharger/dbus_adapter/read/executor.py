# SPDX-License-Identifier: GPL-3.0-or-later
"""Scheduled read execution for the DBus adapter."""

from __future__ import annotations

import logging
from collections.abc import Callable

from venus_evcharger.dbus_adapter.contracts import CommandOutcome
from venus_evcharger.dbus_adapter.rate import DBUS_GATEWAY_OPERATION_ERRORS, DbusOperationDeferred
from venus_evcharger.dbus_adapter.read.aggregate import (
    PV_TOTAL_AGGREGATE,
    AggregateState,
    AggregateStepContinuation,
    AggregateStepPlan,
    AggregateStore,
)
from venus_evcharger.dbus_adapter.read.protocols import DbusReadAdapter
from venus_evcharger.dbus_adapter.read.pv import PV_MEMBER_ERROR_BACKOFF_SECONDS
from venus_evcharger.dbus_adapter.read.pv_last_good import PvAggregateContinuity
from venus_evcharger.dbus_adapter.read.spec import (
    ReadSpec,
    read_spec_optional_confidence,
    read_spec_optional_zero_on_error,
    read_spec_source,
    read_spec_stale_after_seconds,
    read_spec_text,
)
from venus_evcharger.dbus_adapter.read.targets import ReadTarget, read_target
from venus_evcharger.dbus_adapter.read.transport import BusItemReadCall, submit_busitem_read

DBUS_READ_ERRORS = DBUS_GATEWAY_OPERATION_ERRORS
ReadCompletion = Callable[[CommandOutcome], None]


class DbusReadExecutor:
    """Execute scheduled DBus reads and update the adapter cache."""

    def __init__(self, adapter: DbusReadAdapter, *, monotonic: Callable[[], float] | None = None) -> None:
        self.adapter = adapter
        self._aggregates = AggregateStore()
        self._stale_after_by_key: dict[str, float | None] = {}
        self._interval_factors: dict[str, float] = {}
        self._operation_counts: dict[str, int] = {}
        self._pv_continuity = PvAggregateContinuity(adapter, self._aggregates, monotonic=monotonic)
        self.last_operation_performed = False

    def poll_read_spec(
        self, key: str, spec: ReadSpec, *, completion: ReadCompletion | None = None
    ) -> CommandOutcome:
        self.last_operation_performed = False
        self._stale_after_by_key[key] = read_spec_stale_after_seconds(spec)
        completed: list[CommandOutcome] = []

        def _complete(outcome: CommandOutcome) -> None:
            if outcome != "deferred":
                self._stale_after_by_key.pop(key, None)
            completed.append(outcome)
            if completion is not None:
                completion(outcome)

        self._poll_read_with_recovery(key, spec, completed, _complete)
        return completed[-1] if completed else "deferred"

    def _poll_read_with_recovery(
        self, key: str, spec: ReadSpec, completed: list[CommandOutcome], completion: ReadCompletion
    ) -> None:
        try:
            outcome = self._poll_read_spec_unchecked(key, spec, completion)
        except DbusOperationDeferred:
            return
        except DBUS_READ_ERRORS as error:
            completion(self._read_error_outcome(key, spec, error))
            return
        if outcome != "deferred" and not completed:
            completion(outcome)

    def _poll_read_spec_unchecked(
        self,
        key: str,
        spec: ReadSpec,
        completion: ReadCompletion,
    ) -> CommandOutcome:
        aggregate = read_spec_text(spec, "aggregate")
        self._operation_counts[key] = 1
        if aggregate != PV_TOTAL_AGGREGATE:
            self._interval_factors.pop(key, None)
        handlers: dict[str, Callable[[str, ReadSpec, ReadCompletion], CommandOutcome]] = {
            "sum": self._poll_sum_step,
            "services-sum": self._poll_services_sum_step,
            "first-service": self._poll_first_service,
            PV_TOTAL_AGGREGATE: self._poll_pv_total_step,
        }
        handler = handlers.get(aggregate, self._poll_direct_spec)
        return handler(key, spec, completion)

    def _poll_direct_spec(
        self,
        key: str,
        spec: ReadSpec,
        completion: ReadCompletion,
    ) -> CommandOutcome:
        target = read_target(spec.get("service"), spec.get("path"))
        if target is None:
            return "dropped"
        self._submit_busitem(
            target.service,
            target.path,
            on_success=lambda value: self._complete_direct_read(
                key,
                target,
                value,
                spec,
                completion,
            ),
            on_error=lambda error: completion(self._read_error_outcome(key, spec, error)),
        )
        return "deferred"

    def _complete_direct_read(
        self,
        key: str,
        target: ReadTarget,
        value: object,
        spec: ReadSpec,
        completion: ReadCompletion,
    ) -> None:
        self._update_read_value(key, target, value, spec=spec)
        completion("applied")

    def _read_error_outcome(
        self,
        key: str,
        spec: ReadSpec,
        error: BaseException,
    ) -> CommandOutcome:
        if read_spec_optional_zero_on_error(spec):
            self._mark_optional_zero(key, spec, error)
            return "applied"
        self._mark_read_error(key, spec, error)
        return "dropped"

    def _mark_read_error(self, key: str, spec: ReadSpec, error: BaseException) -> None:
        self._aggregates.discard(key)
        self._pv_continuity.discard(key)
        self._stale_after_by_key.pop(key, None)
        self._interval_factors.pop(key, None)
        self._operation_counts.pop(key, None)
        self.adapter.cache.mark_error(key, source=read_spec_source(spec), error=error)
        logging.debug("DBus adapter read failed key=%s: %s", key, error)

    def _mark_optional_zero(self, key: str, spec: ReadSpec, error: BaseException) -> None:
        self._aggregates.discard(key)
        self._pv_continuity.discard(key)
        self._stale_after_by_key.pop(key, None)
        self._interval_factors.pop(key, None)
        self._operation_counts.pop(key, None)
        self.adapter.cache.update_external_read(
            key,
            0.0,
            source=read_spec_source(spec, fallback=key),
            confidence=read_spec_optional_confidence(spec),
            last_error=str(error),
            stale_after_seconds=read_spec_stale_after_seconds(spec),
        )
        logging.debug("DBus adapter optional read fell back to zero key=%s: %s", key, error)

    def has_pending_aggregate(self) -> bool:
        return self._aggregates.has_pending()

    def consume_interval_factor(self, key: str) -> float:
        return max(1.0, self._interval_factors.pop(str(key), 1.0))

    def consume_operation_count(self, key: str) -> int:
        """Return and clear the DBus-operation count for one completed read cycle."""
        return max(1, self._operation_counts.pop(str(key), 1))

    def _poll_sum_step(
        self,
        key: str,
        spec: ReadSpec,
        completion: ReadCompletion,
    ) -> CommandOutcome:
        service = read_spec_text(spec, "service")
        paths = spec.get("paths", [])
        members = [(service, str(path)) for path in paths if str(path)]
        if not members:
            self.adapter.cache.update_external_read(
                key,
                0.0,
                source=service,
                stale_after_seconds=read_spec_stale_after_seconds(spec),
            )
            return "applied"
        return self._poll_aggregate_step(
            AggregateStepPlan(
                key=key,
                signature=("sum", tuple(members)),
                members=tuple(members),
                completion=completion,
            )
        )

    def _poll_services_sum_step(
        self,
        key: str,
        spec: ReadSpec,
        completion: ReadCompletion,
    ) -> CommandOutcome:
        path = read_spec_text(spec, "path")
        services = self._services_for_sum(spec)
        if not services:
            raise RuntimeError(f"No cached services for prefix '{read_spec_text(spec, 'prefix')}'")
        return self._poll_aggregate_step(
            AggregateStepPlan(
                key=key,
                signature=("services-sum", path, tuple(services)),
                members=tuple((service, path) for service in services),
                completion=completion,
            )
        )

    def _poll_pv_total_step(
        self,
        key: str,
        spec: ReadSpec,
        completion: ReadCompletion,
    ) -> CommandOutcome:
        members, held_state = self._pv_continuity.plan(key, spec)
        if held_state is not None:
            self._complete_aggregate(key, held_state)
            return "applied"
        if not members:
            raise RuntimeError("No available AC or DC PV source candidates")
        return self._poll_aggregate_step(
            AggregateStepPlan(
                key=key,
                signature=(PV_TOTAL_AGGREGATE, tuple(members)),
                members=tuple(members),
                completion=completion,
                ignore_member_errors=True,
                empty_confidence=read_spec_optional_confidence(spec),
            )
        )

    def _poll_first_service(
        self,
        key: str,
        spec: ReadSpec,
        completion: ReadCompletion,
    ) -> CommandOutcome:
        path = read_spec_text(spec, "path")
        prefix = read_spec_text(spec, "prefix")
        service = self.adapter.energy_discovery.first_service(spec)
        if service is None:
            raise RuntimeError(f"No cached services for prefix '{prefix}'")
        target = read_target(service, path)
        if target is None:
            return "dropped"
        self._submit_busitem(
            target.service,
            target.path,
            on_success=lambda value: self._complete_direct_read(
                key,
                target,
                value,
                spec,
                completion,
            ),
            on_error=lambda error: completion(self._read_error_outcome(key, spec, error)),
        )
        return "deferred"

    def _services_for_sum(self, spec: ReadSpec) -> list[str]:
        return self.adapter.energy_discovery.services_for(spec)

    def _poll_aggregate_step(
        self,
        plan: AggregateStepPlan,
    ) -> CommandOutcome:
        self._operation_counts[plan.key] = len(plan.members)
        state = self._aggregates.state_for(
            plan.key,
            plan.signature,
            plan.empty_confidence,
        )
        index = state.index
        if index == 0:
            self._interval_factors[plan.key] = 1.0
        service, path = plan.members[index]
        continuation = AggregateStepContinuation(
            key=plan.key,
            state=state,
            service=service,
            path=path,
            member_count=len(plan.members),
            ignore_member_errors=plan.ignore_member_errors,
            completion=plan.completion,
        )
        self._submit_busitem(
            service,
            path,
            optional=plan.ignore_member_errors,
            on_success=lambda value: self._complete_aggregate_member(
                continuation,
                value,
            ),
            on_error=lambda error: self._complete_aggregate_error(
                continuation,
                error,
            ),
        )
        return "deferred"

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

    def _complete_aggregate_member(
        self,
        continuation: AggregateStepContinuation,
        value: object,
    ) -> None:
        self._pv_continuity.record_confirmed(
            continuation.state,
            continuation.service,
            continuation.path,
            value,
        )
        self.adapter.energy_discovery.record_pv_value(
            continuation.service,
            continuation.path,
            value,
        )
        target = read_target(continuation.service, continuation.path)
        if target is not None:
            self.adapter.cache.update_external_read(
                target.cache_key,
                value,
                source=target.source,
            )
        if continuation.ignore_member_errors:
            self._record_optional_interval_factor(
                continuation.key,
                continuation.service,
                continuation.path,
            )
        self._record_aggregate_member(
            continuation.state,
            continuation.service,
            continuation.path,
            value,
        )
        continuation.state.index += 1
        continuation.completion(
            self._aggregate_step_outcome(
                continuation.key,
                continuation.state,
                continuation.member_count,
            )
        )

    def _complete_aggregate_error(
        self,
        continuation: AggregateStepContinuation,
        error: BaseException,
    ) -> None:
        if not continuation.ignore_member_errors:
            self._mark_read_error(
                continuation.key,
                {
                    "service": continuation.service,
                    "path": continuation.path,
                },
                error,
            )
            continuation.completion("dropped")
            return
        self._record_optional_aggregate_error(
            continuation.service,
            continuation.path,
            continuation.state,
            error,
        )
        self._record_optional_interval_factor(
            continuation.key,
            continuation.service,
            continuation.path,
        )
        self._record_aggregate_member(
            continuation.state,
            continuation.service,
            continuation.path,
            None,
        )
        continuation.state.index += 1
        continuation.completion(
            self._aggregate_step_outcome(
                continuation.key,
                continuation.state,
                continuation.member_count,
            )
        )

    def _record_optional_aggregate_error(
        self,
        service: str,
        path: str,
        state: AggregateState,
        error: BaseException,
    ) -> None:
        self._pv_continuity.record_error(state, service, path, error)
        self.adapter.energy_discovery.record_pv_error(
            service,
            path,
            error,
        )
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
                stale_after_seconds=(read_spec_stale_after_seconds(spec) if spec is not None else None),
            )

    @staticmethod
    def _record_aggregate_member(state: AggregateState, service: str, path: str, value: object) -> None:
        state.record_member(service, path, value)

    def _complete_aggregate(self, key: str, state: AggregateState) -> None:
        self._pv_continuity.complete(
            key,
            stale_after_seconds=self._stale_after_by_key.pop(key, None),
            state=state,
        )

    def _record_optional_interval_factor(
        self,
        key: str,
        service: str,
        path: str,
    ) -> None:
        source = f"{service}{path}"
        factor = self.adapter.circuit.optional_source_interval_factor(source)
        self._interval_factors[key] = max(
            self._interval_factors.get(key, 1.0),
            factor,
        )

    def _submit_busitem(
        self,
        service: str,
        path: str,
        *,
        on_success: Callable[[object], None],
        on_error: Callable[[BaseException], None],
        optional: bool = False,
    ) -> None:
        submit_busitem_read(
            self.adapter,
            BusItemReadCall(service, path, optional),
            on_success=on_success,
            on_error=on_error,
        )
        self.last_operation_performed = True

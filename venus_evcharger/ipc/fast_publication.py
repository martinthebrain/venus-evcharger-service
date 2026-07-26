# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded in-memory transport for transient semantic publications.

The queue owns only volatile work and coalesces values field by field.  Its
ordering collaborator reserves accepted fast fields in memory, but checkpoints
them only after the publication executor reports ``applied``.  Durable mailbox
fallbacks therefore remain authoritative after a gateway crash before apply.

Deferred work receives a short retry delay so another queue class can run in
the same bounded burst.  Expired or dropped work releases only matching
volatile marks; it cannot erase a newer or already durable high-water mark.
Payload count, name, encoded-size, queue-depth, and TTL limits bound memory and
per-tick work independently.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass

from venus_evcharger.ipc.command_mailbox import command_float, command_priority_rank
from venus_evcharger.ipc.command_types import CommandMapping, CommandPayload
from venus_evcharger.ipc.deadline import remaining_transient_ttl
from venus_evcharger.ipc.fast_publication_metrics import FastPublicationMetrics
from venus_evcharger.ipc.fast_publication_ordering import FastPublicationOrdering
from venus_evcharger.ipc.fast_publication_policy import (
    fast_command_id,
    is_transient_publication,
)
from venus_evcharger.ipc.fast_publication_work import (
    FastPublicationWork,
    fast_field_selection,
    fast_requeue_candidate,
    merge_fast_work,
    retained_fast_work,
)
from venus_evcharger.ipc.fast_publication_wire import (
    fast_publication_payload_limit_reason,
)
from venus_evcharger.ipc.publication_order import (
    PUBLICATION_ORDER_RETENTION_SECONDS,
    PublicationFieldKey,
    PublicationFieldClaim,
    PublicationOrderCapacityError,
    PublicationOrderPendingFastError,
    publication_field_orders,
)
from venus_evcharger.ipc.publication_payload import (
    filter_publication_payload,
    merge_publication_payload,
)

FAST_PUBLICATION_CAPACITY = 64
FAST_PUBLICATION_ORDER_CAPACITY_FACTOR = 64
FAST_PUBLICATION_RETRY_SECONDS = 0.25
FAST_PUBLICATION_DEFERRED_AGING_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class FastPublicationEnqueueResult:
    """Acceptance result returned over the local socket boundary."""

    accepted: bool
    command_id: str = ""
    reason: str = ""

    def to_payload(self) -> CommandPayload:
        return {
            "ok": self.accepted,
            "accepted": self.accepted,
            "command_id": self.command_id,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class _FastEnqueueCandidate:
    key: str
    existing: FastPublicationWork | None
    ttl: float


class FastPublicationQueue:
    """Gateway-owned field-wise latest-wins queue for transient publications."""

    def __init__(
        self,
        capacity: int = FAST_PUBLICATION_CAPACITY,
        *,
        order_capacity: int | None = None,
        order_retention_seconds: float = PUBLICATION_ORDER_RETENTION_SECONDS,
        order_state_path: str | None = None,
    ) -> None:
        self.capacity = max(1, int(capacity))
        default_order_capacity = self.capacity * FAST_PUBLICATION_ORDER_CAPACITY_FACTOR
        configured_order_capacity = default_order_capacity if order_capacity is None else int(order_capacity)
        self.order_capacity = max(1, configured_order_capacity)
        self.order_retention_seconds = max(1.0, float(order_retention_seconds))
        self._commands: OrderedDict[str, FastPublicationWork] = OrderedDict()
        self._ordering = FastPublicationOrdering(
            capacity=self.order_capacity,
            retention_seconds=self.order_retention_seconds,
            state_path=order_state_path,
        )
        self._metrics = FastPublicationMetrics()

    def __len__(self) -> int:
        return len(self._commands)

    def enqueue(self, command: CommandMapping) -> FastPublicationEnqueueResult:
        rejection = self._rejection_reason(command)
        if rejection:
            self._increment("rejected")
            return FastPublicationEnqueueResult(False, reason=rejection)
        return self._enqueue_valid(command)

    def _enqueue_valid(self, command: CommandMapping) -> FastPublicationEnqueueResult:
        candidate = self._enqueue_candidate(command)
        if isinstance(candidate, FastPublicationEnqueueResult):
            return candidate
        claim = self._ordering.claim_fast(
            candidate.key,
            command,
            active_fields=self._active_fields(),
        )
        failure = self._claim_failure(candidate.key, claim)
        if failure is not None:
            return failure
        return self._accept_claim(command, candidate, claim)

    def _enqueue_candidate(
        self,
        command: CommandMapping,
    ) -> _FastEnqueueCandidate | FastPublicationEnqueueResult:
        key = str(command.get("coalesce_key") or "").strip()
        existing = self._commands.get(key)
        payload = self._queue_payload(existing, command, key)
        limit_reason = fast_publication_payload_limit_reason(payload)
        if limit_reason:
            return self._rejected_enqueue(limit_reason)
        if self._queue_at_capacity(existing):
            return self._rejected_enqueue("queue-full", metric="full")
        ttl = remaining_transient_ttl(command, time.time())
        if ttl <= 0.0:
            return self._rejected_enqueue("expired")
        return _FastEnqueueCandidate(key, existing, ttl)

    def _queue_at_capacity(self, existing: FastPublicationWork | None) -> bool:
        return existing is None and len(self._commands) >= self.capacity

    def _claim_failure(
        self,
        key: str,
        claim: PublicationFieldClaim,
    ) -> FastPublicationEnqueueResult | None:
        if claim.state == "superseded":
            self._increment("superseded")
            return FastPublicationEnqueueResult(True, fast_command_id(key), "superseded")
        if claim.state == "full":
            return self._rejected_enqueue("order-history-full")
        return None

    def _accept_claim(
        self,
        command: CommandMapping,
        candidate: _FastEnqueueCandidate,
        claim: PublicationFieldClaim,
    ) -> FastPublicationEnqueueResult:
        work = self._merged_work(
            candidate.existing,
            command,
            candidate.key,
            accepted_fields=claim.accepted_fields,
            ttl=candidate.ttl,
        )
        self._commands[candidate.key] = work
        self._commands.move_to_end(candidate.key)
        self._increment("accepted")
        if candidate.existing is not None:
            self._increment("coalesced")
        if claim.superseded_fields:
            self._increment("fields_superseded", len(claim.superseded_fields))
        return FastPublicationEnqueueResult(True, str(work.command["id"]))

    def _rejected_enqueue(
        self,
        reason: str,
        *,
        metric: str | None = None,
    ) -> FastPublicationEnqueueResult:
        self._increment(metric or reason.replace("-", "_"))
        return FastPublicationEnqueueResult(False, reason=reason)

    def prepare_durable(self, command: CommandMapping) -> CommandPayload | None:
        """Return only durable fields not superseded by newer fast work."""
        self._prune_expired(time.monotonic())
        key = str(command.get("coalesce_key") or "").strip()
        claim = self._durable_claim(key, command)
        return self._prepared_durable_payload(command, claim)

    def _durable_claim(
        self,
        key: str,
        command: CommandMapping,
    ) -> PublicationFieldClaim:
        try:
            return self._ordering.prepare_durable(
                key,
                command,
                active_fields=self._active_fields(),
                queued_work=self._commands.get(key),
            )
        except PublicationOrderPendingFastError:
            self._increment("durable_waiting_for_fast")
            raise
        except PublicationOrderCapacityError:
            self._increment("durable_order_history_full")
            raise

    def _prepared_durable_payload(
        self,
        command: CommandMapping,
        claim: PublicationFieldClaim,
    ) -> CommandPayload | None:
        accepted = claim.accepted_fields
        if not accepted:
            self._increment("durable_superseded")
            return None
        if claim.superseded_fields:
            self._increment("durable_fields_superseded", len(claim.superseded_fields))
        return filter_publication_payload(command, accepted)

    def record_durable_outcome(
        self,
        command: CommandMapping,
        state: str,
    ) -> None:
        if state != "applied":
            return
        key = str(command.get("coalesce_key") or "").strip()
        orders = publication_field_orders(command)
        persisted = self._ordering.confirm_durable_applied(
            command,
            active_fields=self._active_fields(),
        )
        self._remove_fast_fields(key, tuple(orders))
        if not persisted:
            self._increment("order_state_write_errors")

    def pop_next(self, *, now: float | None = None) -> FastPublicationWork | None:
        current = time.monotonic() if now is None else float(now)
        self._prune_expired(current)
        eligible = [key for key, work in self._commands.items() if work.retry_at <= current]
        if not eligible:
            return None
        key = min(
            eligible,
            key=lambda item: _fast_work_priority(
                self._commands[item],
                current,
            ),
        )
        return self._commands.pop(key)

    def requeue(
        self,
        work: FastPublicationWork,
        *,
        deferred: bool = False,
        now: float | None = None,
    ) -> None:
        current = time.monotonic() if now is None else float(now)
        candidate = fast_requeue_candidate(work, current)
        if candidate is None:
            self._release_fast_work(work)
            self._increment("expired")
            return
        key, retained = candidate
        existing = self._commands.get(key)
        retry_at = current + FAST_PUBLICATION_RETRY_SECONDS if deferred else current
        merged = merge_fast_work(
            retained,
            existing,
            retry_at=retry_at,
            deferred=deferred,
        )
        self._commands[key] = merged
        self._commands.move_to_end(key)

    def record_outcome(
        self,
        work: FastPublicationWork,
        state: str,
        *,
        now: float | None = None,
    ) -> bool:
        """Record an outcome and return whether an applied event should be journaled."""
        normalized = str(state or "unknown")
        self._record_order_outcome(work, normalized)
        current = time.time() if now is None else float(now)
        return self._metrics.record_outcome(normalized, current)

    def _record_order_outcome(
        self,
        work: FastPublicationWork,
        state: str,
    ) -> None:
        if state == "applied":
            self._confirm_fast_applied(work)
            return
        if state == "dropped":
            self._release_fast_work(work)

    def _confirm_fast_applied(self, work: FastPublicationWork) -> None:
        if not self._ordering.confirm_fast_applied(work):
            self._increment("order_state_write_errors")

    def snapshot(self) -> CommandPayload:
        self._prune_expired(time.monotonic())
        return {
            "capacity": self.capacity,
            "depth": len(self._commands),
            "field_depth": sum(len(work.field_expires_at) for work in self._commands.values()),
            **self._metrics.snapshot(),
            **self._ordering.snapshot(),
        }

    def _queue_payload(
        self,
        existing: FastPublicationWork | None,
        command: CommandMapping,
        key: str,
    ) -> CommandPayload:
        payload = merge_publication_payload(
            existing.command if existing is not None else None,
            command,
        )
        payload["id"] = fast_command_id(key)
        payload["created_at"] = command_float(payload.get("created_at")) or time.time()
        payload["queue_class"] = "local-publish"
        return payload

    def _merged_work(
        self,
        existing: FastPublicationWork | None,
        command: CommandMapping,
        key: str,
        *,
        accepted_fields: tuple[str, ...],
        ttl: float,
    ) -> FastPublicationWork:
        now = time.monotonic()
        payload = merge_publication_payload(
            existing.command if existing is not None else None,
            command,
            accepted_fields=accepted_fields,
        )
        payload["id"] = fast_command_id(key)
        payload["created_at"] = command_float(payload.get("created_at")) or time.time()
        payload["queue_class"] = "local-publish"
        expiry = dict(existing.field_expires_at) if existing is not None else {}
        expiry.update({field: now + ttl for field in accepted_fields})
        return FastPublicationWork(payload, expiry)

    def _remove_fast_fields(self, key: str, fields: tuple[str, ...]) -> None:
        work = self._commands.get(key)
        if work is None:
            return
        selection = fast_field_selection(work, fields)
        if selection is None:
            return
        removed, remaining = selection
        self._store_remaining_fast_fields(key, work, remaining)
        self._increment("fast_fields_superseded", len(removed))

    def _store_remaining_fast_fields(
        self,
        key: str,
        work: FastPublicationWork,
        remaining: tuple[str, ...],
    ) -> None:
        payload = filter_publication_payload(work.command, remaining)
        if payload is None:
            del self._commands[key]
            return
        expiry = {field: work.field_expires_at[field] for field in remaining}
        self._commands[key] = FastPublicationWork(
            payload,
            expiry,
            work.retry_at,
            work.deferred,
        )

    def _rejection_reason(self, command: CommandMapping) -> str:
        if not is_transient_publication(command):
            return "durable-command-required"
        key = str(command.get("coalesce_key") or "").strip()
        if not key:
            return "missing-coalesce-key"
        return fast_publication_payload_limit_reason(command)

    def _prune_expired(self, now: float) -> None:
        for key, work in tuple(self._commands.items()):
            self._prune_fast_work(key, work, now)

    def _prune_fast_work(
        self,
        key: str,
        work: FastPublicationWork,
        now: float,
    ) -> None:
        retained = retained_fast_work(work, now)
        if retained is None:
            self._drop_expired_work(key, work)
            return
        if len(retained.field_expires_at) != len(work.field_expires_at):
            self._retain_unexpired_fields(key, work, retained)

    def _drop_expired_work(self, key: str, work: FastPublicationWork) -> None:
        del self._commands[key]
        self._release_fast_work(work)
        self._increment("expired")

    def _retain_unexpired_fields(
        self,
        key: str,
        work: FastPublicationWork,
        retained: FastPublicationWork,
    ) -> None:
        expired_fields = set(work.field_expires_at) - set(retained.field_expires_at)
        self._ordering.release_fields(
            key,
            {
                field: order
                for field, order in publication_field_orders(work.command).items()
                if field in expired_fields
            },
        )
        self._commands[key] = retained
        self._increment("fields_expired", len(expired_fields))

    def _release_fast_work(self, work: FastPublicationWork) -> None:
        self._ordering.release_fast(work)

    def _active_fields(self) -> set[PublicationFieldKey]:
        return {(key, field) for key, work in self._commands.items() for field in work.field_expires_at}

    def _increment(self, key: str, amount: int = 1) -> None:
        self._metrics.increment(key, amount)


def _fast_work_priority(work: FastPublicationWork, now: float) -> tuple[int, int, float]:
    priority = command_priority_rank(work.command.get("priority"))
    deferred_age = max(0.0, now - work.retry_at)
    if work.deferred and deferred_age >= FAST_PUBLICATION_DEFERRED_AGING_SECONDS:
        fairness_rank = 0
    elif work.deferred:
        fairness_rank = 2
    else:
        fairness_rank = 1
    return priority, fairness_rank, work.retry_at


__all__ = [
    "FAST_PUBLICATION_CAPACITY",
    "FAST_PUBLICATION_RETRY_SECONDS",
    "FAST_PUBLICATION_DEFERRED_AGING_SECONDS",
    "FastPublicationEnqueueResult",
    "FastPublicationQueue",
    "FastPublicationWork",
    "is_transient_publication",
]

# SPDX-License-Identifier: GPL-3.0-or-later
"""Field-wise ordering for transient and durable gateway publications."""

from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from typing import Literal, TypeGuard

from venus_evcharger.ipc.command_types import CommandMapping, CommandPayload
from venus_evcharger.ipc.publication_order_state import (
    PublicationFieldKey,
    PublicationLane,
    PublicationOrderMark,
    checkpoint_publication_order_marks,
    load_publication_order_marks,
)

PUBLICATION_ORDER_FIELD = "transport_order"
PUBLICATION_FIELD_ORDERS_FIELD = "transport_field_orders"
PUBLICATION_ORDER_RETENTION_SECONDS = 60.0
PUBLICATION_ORDER_STATE_NAME = "publication-order-state.json"
PUBLICATION_ORDER_PROCESS_BITS = 32
PUBLICATION_ORDER_PROCESS_MASK = (1 << PUBLICATION_ORDER_PROCESS_BITS) - 1
PublicationOrderClaim = Literal["accepted", "superseded", "full"]


class PublicationOrderDeferredError(RuntimeError):
    """A durable publication must remain queued without being applied."""


class PublicationOrderCapacityError(PublicationOrderDeferredError):
    """The bounded order history cannot safely admit another field."""


class PublicationOrderPendingFastError(PublicationOrderDeferredError):
    """A volatile publication must resolve before its fallback is considered."""


@dataclass(frozen=True, slots=True)
class PublicationFieldClaim:
    """Fields accepted by one atomic ordering decision."""

    state: PublicationOrderClaim
    accepted_fields: tuple[str, ...] = ()
    superseded_fields: tuple[str, ...] = ()


class PublicationOrderSequence:
    """Thread-safe monotone sequence that can be shared or injected."""

    def __init__(self, process_id: int | None = None) -> None:
        self._last_order = 0
        self._process_id = os.getpid() if process_id is None else int(process_id)
        self._lock = threading.Lock()

    def remember(self, order: int) -> None:
        with self._lock:
            self._last_order = max(order, self._last_order)

    def next_order(self) -> int:
        with self._lock:
            monotonic_order = int(time.monotonic_ns())
            if self._process_id == 0:
                order = max(monotonic_order, self._last_order + 1)
            else:
                epoch = max(
                    monotonic_order,
                    self._last_order >> PUBLICATION_ORDER_PROCESS_BITS,
                )
                order = (epoch << PUBLICATION_ORDER_PROCESS_BITS) | (
                    self._process_id & PUBLICATION_ORDER_PROCESS_MASK
                )
                if order <= self._last_order:
                    order = ((epoch + 1) << PUBLICATION_ORDER_PROCESS_BITS) | (
                        self._process_id & PUBLICATION_ORDER_PROCESS_MASK
                    )
            self._last_order = order
            return order


_PROCESS_PUBLICATION_ORDER_SEQUENCE = PublicationOrderSequence()


class PublicationOrderHistory:
    """Retain bounded field high-water marks across gateway process restarts."""

    def __init__(
        self,
        *,
        capacity: int,
        retention_seconds: float,
        state_path: str | None = None,
    ) -> None:
        self.capacity = max(1, int(capacity))
        self.retention_seconds = max(1.0, float(retention_seconds))
        self.state_path = str(state_path or "")
        self._marks = self._load_marks(time.monotonic())

    def claim_fast(
        self,
        key: str,
        field_orders: Mapping[str, int],
        *,
        active_fields: Collection[PublicationFieldKey],
    ) -> PublicationFieldClaim:
        candidate = self._pruned_marks(time.monotonic(), active_fields)
        claim = _field_claim(key, field_orders, candidate, _fast_order_claim_blocked)
        if not _claim_fits_capacity(
            key,
            field_orders,
            claim,
            candidate,
            self.capacity,
        ):
            return PublicationFieldClaim("full")
        self._marks = _with_claimed_marks(
            key,
            field_orders,
            "fast",
            claim,
            candidate,
        )
        return claim

    def claim_durable(
        self,
        key: str,
        field_orders: Mapping[str, int],
        *,
        active_fields: Collection[PublicationFieldKey],
    ) -> PublicationFieldClaim:
        candidate = self._pruned_marks(time.monotonic(), active_fields)
        claim = _field_claim(
            key,
            field_orders,
            candidate,
            _durable_order_claim_blocked,
        )
        if not _claim_fits_capacity(
            key,
            field_orders,
            claim,
            candidate,
            self.capacity,
        ):
            return PublicationFieldClaim("full")
        return claim

    def confirm_fast_applied(
        self,
        key: str,
        field_orders: Mapping[str, int],
    ) -> bool:
        """Persist only fast marks matching the publication just applied."""
        now = time.monotonic()
        updated = OrderedDict(self._marks)
        changed: OrderedDict[PublicationFieldKey, PublicationOrderMark] = OrderedDict()
        for field, order in field_orders.items():
            field_key = (key, field)
            current = updated.get(field_key)
            if _fast_mark_matches(current, order):
                mark = PublicationOrderMark(order, "durable", now)
                updated[field_key] = mark
                updated.move_to_end(field_key)
                changed[field_key] = mark
        self._marks = updated
        return self._persist(changed, updated)

    def confirm_durable_applied(
        self,
        key: str,
        field_orders: Mapping[str, int],
        *,
        active_fields: Collection[PublicationFieldKey],
    ) -> bool:
        """Confirm a durable claim after publication succeeded."""
        candidate = self._pruned_marks(time.monotonic(), active_fields)
        claim = _field_claim(
            key,
            field_orders,
            candidate,
            _durable_order_claim_blocked,
        )
        if not _claim_fits_capacity(
            key,
            field_orders,
            claim,
            candidate,
            self.capacity,
        ):
            return False
        self._marks = _with_claimed_marks(
            key,
            field_orders,
            "durable",
            claim,
            candidate,
        )
        changed = OrderedDict(
            (field_key, self._marks[field_key])
            for field_key in _claimed_field_keys(key, field_orders, claim)
        )
        return self._persist(changed, self._marks)

    def release_fast(
        self,
        key: str,
        field_orders: Mapping[str, int],
    ) -> None:
        """Release failed or expired volatile claims without touching newer marks."""
        for field, order in field_orders.items():
            field_key = (key, field)
            current = self._marks.get(field_key)
            if _fast_mark_matches(current, order):
                del self._marks[field_key]

    def snapshot(self) -> CommandPayload:
        return {
            "order_capacity": self.capacity,
            "order_retention_seconds": self.retention_seconds,
            "ordered_keys": len({key for key, _field in self._marks}),
            "ordered_fields": len(self._marks),
            "order_state_persistent": bool(self.state_path),
        }

    def _pruned_marks(
        self,
        now: float,
        active_fields: Collection[PublicationFieldKey],
    ) -> OrderedDict[PublicationFieldKey, PublicationOrderMark]:
        cutoff = now - self.retention_seconds
        return OrderedDict(
            (field_key, mark)
            for field_key, mark in self._marks.items()
            if field_key in active_fields or mark.seen_at > cutoff
        )

    def _persist(
        self,
        changed: OrderedDict[PublicationFieldKey, PublicationOrderMark],
        current: OrderedDict[PublicationFieldKey, PublicationOrderMark],
    ) -> bool:
        return checkpoint_publication_order_marks(
            self.state_path,
            changed,
            current,
        )

    def _load_marks(
        self,
        now: float,
    ) -> OrderedDict[PublicationFieldKey, PublicationOrderMark]:
        return load_publication_order_marks(
            self.state_path,
            now=now,
            retention_seconds=self.retention_seconds,
        )


def _field_claim(
    key: str,
    field_orders: Mapping[str, int],
    marks: Mapping[PublicationFieldKey, PublicationOrderMark],
    order_blocked: Callable[[int, PublicationOrderMark], bool],
) -> PublicationFieldClaim:
    accepted: list[str] = []
    superseded: list[str] = []
    for field, order in field_orders.items():
        current = marks.get((key, field))
        if current is not None and order_blocked(order, current):
            superseded.append(field)
        else:
            accepted.append(field)
    state: PublicationOrderClaim = "accepted" if accepted else "superseded"
    return PublicationFieldClaim(state, tuple(accepted), tuple(superseded))


def _with_claimed_marks(
    key: str,
    field_orders: Mapping[str, int],
    lane: PublicationLane,
    claim: PublicationFieldClaim,
    marks: OrderedDict[PublicationFieldKey, PublicationOrderMark],
) -> OrderedDict[PublicationFieldKey, PublicationOrderMark]:
    updated = OrderedDict(marks)
    now = time.monotonic()
    for field in claim.accepted_fields:
        field_key = (key, field)
        mark = _claimed_order_mark(field_orders[field], lane, now)
        if mark is None:
            continue
        updated[field_key] = mark
        updated.move_to_end(field_key)
    return updated


def _claimed_order_mark(
    order: int,
    lane: PublicationLane,
    now: float,
) -> PublicationOrderMark | None:
    if order <= 0:
        return None
    return PublicationOrderMark(order, lane, now)


def _fast_mark_matches(
    current: PublicationOrderMark | None,
    order: int,
) -> bool:
    return (
        current is not None
        and current.lane == "fast"
        and current.order == order
    )


def _claim_fits_capacity(
    key: str,
    field_orders: Mapping[str, int],
    claim: PublicationFieldClaim,
    marks: Mapping[PublicationFieldKey, PublicationOrderMark],
    capacity: int,
) -> bool:
    new_fields = {
        field_key
        for field_key in _claimed_field_keys(key, field_orders, claim)
        if field_key not in marks
    }
    return len(marks) + len(new_fields) <= capacity


def _claimed_field_keys(
    key: str,
    field_orders: Mapping[str, int],
    claim: PublicationFieldClaim,
) -> tuple[PublicationFieldKey, ...]:
    return tuple(
        (key, field)
        for field in claim.accepted_fields
        if field_orders[field] > 0
    )


def _fast_order_claim_blocked(
    order: int,
    current: PublicationOrderMark,
) -> bool:
    return order <= current.order


def _durable_order_claim_blocked(
    order: int,
    current: PublicationOrderMark,
) -> bool:
    if order < current.order:
        return True
    if order > current.order:
        return False
    return current.lane != "durable"


def publication_order(command: CommandMapping) -> int:
    """Return a validated cross-lane publication order."""
    return _positive_integer(command.get(PUBLICATION_ORDER_FIELD))


def publication_field_orders(command: CommandMapping) -> dict[str, int]:
    """Return one validated order for every publication field."""
    fields = _string_mapping(command.get("fields"))
    if fields is None:
        return {}
    fallback = publication_order(command)
    configured = _string_mapping(command.get(PUBLICATION_FIELD_ORDERS_FIELD)) or {}
    return {
        field: _positive_integer(configured.get(field)) or fallback
        for field in fields
    }


class PublicationOrderIssuer:
    """Issue orders from one process-global sequence by default."""

    def __init__(self, sequence: PublicationOrderSequence | None = None) -> None:
        self._sequence = sequence or _PROCESS_PUBLICATION_ORDER_SEQUENCE

    def ordered(self, command: CommandMapping) -> CommandPayload:
        key = str(command.get("coalesce_key") or "").strip()
        if not key:
            return dict(command)
        payload = dict(command)
        order = publication_order(command)
        if order:
            self._sequence.remember(order)
        else:
            order = self._sequence.next_order()
            payload[PUBLICATION_ORDER_FIELD] = order
        field_orders = publication_field_orders({**payload, PUBLICATION_ORDER_FIELD: order})
        if field_orders:
            highest = max(field_orders.values())
            self._sequence.remember(highest)
            payload[PUBLICATION_ORDER_FIELD] = max(order, highest)
            payload[PUBLICATION_FIELD_ORDERS_FIELD] = field_orders
        return payload


def _string_mapping(value: object) -> dict[str, object] | None:
    if not _is_mapping(value):
        return None
    if not all(isinstance(key, str) for key in value):
        return None
    return {str(key): item for key, item in value.items()}


def _is_mapping(value: object) -> TypeGuard[Mapping[object, object]]:
    return isinstance(value, Mapping)


def _positive_integer(value: object) -> int:
    return max(0, value) if type(value) is int else 0


__all__ = [
    "PUBLICATION_FIELD_ORDERS_FIELD",
    "PUBLICATION_ORDER_FIELD",
    "PUBLICATION_ORDER_RETENTION_SECONDS",
    "PUBLICATION_ORDER_STATE_NAME",
    "PublicationFieldKey",
    "PublicationFieldClaim",
    "PublicationOrderCapacityError",
    "PublicationOrderDeferredError",
    "PublicationOrderHistory",
    "PublicationOrderIssuer",
    "PublicationOrderPendingFastError",
    "PublicationOrderSequence",
    "publication_field_orders",
    "publication_order",
]

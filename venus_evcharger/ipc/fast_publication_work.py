# SPDX-License-Identifier: GPL-3.0-or-later
"""Immutable leases and pure merge helpers for fast publication work."""

from __future__ import annotations

from dataclasses import dataclass

from venus_evcharger.ipc.command_types import CommandPayload
from venus_evcharger.ipc.publication_order import publication_field_orders
from venus_evcharger.ipc.publication_payload import (
    filter_publication_payload,
    merge_publication_payload,
    publication_fields,
)


@dataclass(frozen=True, slots=True)
class FastPublicationWork:
    """One explicit queue lease with per-field expiry and retry state."""

    command: CommandPayload
    field_expires_at: dict[str, float]
    retry_at: float = 0.0
    deferred: bool = False


def retained_fast_work(
    work: FastPublicationWork,
    now: float,
) -> FastPublicationWork | None:
    fields = tuple(
        field
        for field, expires_at in work.field_expires_at.items()
        if expires_at > now
    )
    payload = filter_publication_payload(work.command, fields)
    if payload is None:
        return None
    expiry = {field: work.field_expires_at[field] for field in fields}
    return FastPublicationWork(payload, expiry, work.retry_at, work.deferred)


def fast_requeue_candidate(
    work: FastPublicationWork,
    now: float,
) -> tuple[str, FastPublicationWork] | None:
    retained = retained_fast_work(work, now)
    if retained is None:
        return None
    key = str(retained.command.get("coalesce_key") or "").strip()
    return (key, retained) if key else None


def fast_field_selection(
    work: FastPublicationWork,
    fields: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    queued_fields = publication_fields(work.command)
    removed = _present_fields(fields, queued_fields)
    if not removed:
        return None
    return removed, _remaining_fields(queued_fields, removed)


def merge_fast_work(
    requeued: FastPublicationWork,
    queued: FastPublicationWork | None,
    *,
    retry_at: float,
    deferred: bool,
) -> FastPublicationWork:
    if queued is None:
        return FastPublicationWork(
            requeued.command,
            requeued.field_expires_at,
            retry_at,
            deferred,
        )
    return FastPublicationWork(
        merge_publication_payload(requeued.command, queued.command),
        _merged_field_expiry(requeued, queued),
        min(retry_at, queued.retry_at),
        deferred and queued.deferred,
    )


def _present_fields(
    candidates: tuple[str, ...],
    queued: dict[str, object],
) -> tuple[str, ...]:
    return tuple(field for field in candidates if field in queued)


def _remaining_fields(
    queued: dict[str, object],
    removed: tuple[str, ...],
) -> tuple[str, ...]:
    return tuple(field for field in queued if field not in removed)


def _merged_field_expiry(
    requeued: FastPublicationWork,
    queued: FastPublicationWork,
) -> dict[str, float]:
    requeued_orders = publication_field_orders(requeued.command)
    queued_orders = publication_field_orders(queued.command)
    expiry = dict(requeued.field_expires_at)
    for field, expires_at in queued.field_expires_at.items():
        missing = field not in expiry
        newer = queued_orders.get(field, 0) >= requeued_orders.get(field, 0)
        if missing or newer:
            expiry[field] = expires_at
    return expiry


__all__ = [
    "FastPublicationWork",
    "fast_field_selection",
    "fast_requeue_candidate",
    "merge_fast_work",
    "retained_fast_work",
]

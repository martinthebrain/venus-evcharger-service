# SPDX-License-Identifier: GPL-3.0-or-later
"""Transactional ordering for volatile publications and durable fallbacks."""

from __future__ import annotations

from collections.abc import Collection, Mapping

from venus_evcharger.ipc.command_types import CommandMapping, CommandPayload
from venus_evcharger.ipc.fast_publication_work import FastPublicationWork
from venus_evcharger.ipc.publication_order import (
    PublicationFieldClaim,
    PublicationFieldKey,
    PublicationOrderCapacityError,
    PublicationOrderHistory,
    PublicationOrderPendingFastError,
    publication_field_orders,
)


class FastPublicationOrdering:
    """Own cross-lane claims while persisting only applied field values."""

    def __init__(
        self,
        *,
        capacity: int,
        retention_seconds: float,
        state_path: str | None,
    ) -> None:
        self._history = PublicationOrderHistory(
            capacity=capacity,
            retention_seconds=retention_seconds,
            state_path=state_path,
        )

    def claim_fast(
        self,
        key: str,
        command: CommandMapping,
        *,
        active_fields: Collection[PublicationFieldKey],
    ) -> PublicationFieldClaim:
        return self._history.claim_fast(
            key,
            publication_field_orders(command),
            active_fields=active_fields,
        )

    def prepare_durable(
        self,
        key: str,
        command: CommandMapping,
        *,
        active_fields: Collection[PublicationFieldKey],
        queued_work: FastPublicationWork | None,
    ) -> PublicationFieldClaim:
        orders = publication_field_orders(command)
        if _durable_waits_for_fast(orders, queued_work):
            raise PublicationOrderPendingFastError(
                "publication fallback is waiting for volatile work"
            )
        claim = self._history.claim_durable(
            key,
            orders,
            active_fields=active_fields,
        )
        if claim.state == "full":
            raise PublicationOrderCapacityError("publication order history is full")
        return claim

    def confirm_fast_applied(self, work: FastPublicationWork) -> bool:
        return self._history.confirm_fast_applied(
            _command_key(work.command),
            publication_field_orders(work.command),
        )

    def confirm_durable_applied(
        self,
        command: CommandMapping,
        *,
        active_fields: Collection[PublicationFieldKey],
    ) -> bool:
        return self._history.confirm_durable_applied(
            _command_key(command),
            publication_field_orders(command),
            active_fields=active_fields,
        )

    def release_fast(self, work: FastPublicationWork) -> None:
        self._history.release_fast(
            _command_key(work.command),
            publication_field_orders(work.command),
        )

    def release_fields(self, key: str, orders: Mapping[str, int]) -> None:
        self._history.release_fast(key, orders)

    def snapshot(self) -> CommandPayload:
        return self._history.snapshot()


def _durable_waits_for_fast(
    durable_orders: Mapping[str, int],
    queued_work: FastPublicationWork | None,
) -> bool:
    if queued_work is None:
        return False
    fast_orders = publication_field_orders(queued_work.command)
    return any(
        field in fast_orders and order <= fast_orders[field]
        for field, order in durable_orders.items()
    )


def _command_key(command: CommandMapping) -> str:
    return str(command.get("coalesce_key") or "").strip()


__all__ = ["FastPublicationOrdering"]

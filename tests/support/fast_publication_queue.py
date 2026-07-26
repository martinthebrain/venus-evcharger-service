#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared builders for transient-publication queue contract tests."""

from __future__ import annotations

from venus_evcharger.ipc.fast_publication_policy import fast_command_id
from venus_evcharger.ipc.fast_publication_work import FastPublicationWork
from venus_evcharger.ipc.gateway_publication import publish_evcs_fields_command
from venus_evcharger.ipc.publication_order import (
    PUBLICATION_FIELD_ORDERS_FIELD,
    PUBLICATION_ORDER_FIELD,
)


def publication_command(
    fields: dict[str, object] | None = None,
    *,
    order: int = 10,
    key: str = "evcs:fields",
) -> dict[str, object]:
    values = {"mode": 1} if fields is None else fields
    return {
        **publish_evcs_fields_command(values, priority="live"),
        "coalesce_key": key,
        PUBLICATION_ORDER_FIELD: order,
        PUBLICATION_FIELD_ORDERS_FIELD: {field: order for field in values},
    }


def publication_work(
    fields: dict[str, object] | None = None,
    *,
    order: int = 10,
    key: str = "evcs:fields",
    expires_at: float = 1_000_000_000.0,
    retry_at: float = 0.0,
    deferred: bool = False,
) -> FastPublicationWork:
    command = {
        **publication_command(fields, order=order, key=key),
        "id": fast_command_id(key),
    }
    values = {"mode": 1} if fields is None else fields
    return FastPublicationWork(
        command,
        {field: expires_at for field in values},
        retry_at,
        deferred,
    )

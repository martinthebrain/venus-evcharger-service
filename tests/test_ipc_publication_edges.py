#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Defensive boundary contracts for publication IPC components."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from venus_evcharger.dbus_gateway_commands import DbusGatewayCommandQueuePolicy
from venus_evcharger.ipc.fast_publication import FastPublicationQueue
from venus_evcharger.ipc.fast_publication_work import (
    FastPublicationWork,
    merge_fast_work,
)
from venus_evcharger.ipc.gateway_publication import (
    publish_companion_fields_command,
    publish_evcs_fields_command,
)
from venus_evcharger.ipc.pending_snapshot import PendingCommandSnapshot
from venus_evcharger.ipc.publication_order import (
    PUBLICATION_FIELD_ORDERS_FIELD,
    PUBLICATION_ORDER_FIELD,
    PublicationOrderIssuer,
    PublicationOrderSequence,
    publication_field_orders,
)
from venus_evcharger.ipc.publication_order_state import load_publication_order_marks
from venus_evcharger.ipc.publication_payload import (
    merge_publication_payload,
    publication_fields,
)


def _ordered(fields: dict[str, object], orders: dict[str, int]) -> dict[str, object]:
    return {
        **publish_evcs_fields_command(fields, priority="live"),
        PUBLICATION_ORDER_FIELD: max(orders.values()),
        PUBLICATION_FIELD_ORDERS_FIELD: orders,
    }


class PublicationQueueEdgeContracts(unittest.TestCase):
    def test_stale_fast_work_is_superseded_and_partial_claim_keeps_newer_field(self) -> None:
        queue = FastPublicationQueue()
        first = _ordered({"mode": 1, "power": 10.0}, {"mode": 10, "power": 10})
        partial = _ordered({"mode": 2, "power": 20.0}, {"mode": 9, "power": 11})

        self.assertTrue(queue.enqueue(first).accepted)
        partial_result = queue.enqueue(partial)
        stale_result = queue.enqueue(
            _ordered({"mode": 3}, {"mode": 8})
        )
        work = queue.pop_next()

        self.assertTrue(partial_result.accepted)
        self.assertEqual(stale_result.reason, "superseded")
        self.assertIsNotNone(work)
        assert work is not None
        self.assertEqual(work.command["fields"], {"mode": 1, "power": 20.0})
        self.assertEqual(queue.snapshot()["counts"], {
            "accepted": 2,
            "coalesced": 1,
            "fields_superseded": 1,
            "superseded": 1,
        })

    def test_durable_apply_reports_checkpoint_failure_and_removes_last_fast_field(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            queue = FastPublicationQueue(order_state_path=temp_dir)
            command = _ordered({"mode": 1}, {"mode": 10})
            prepared = queue.prepare_durable(command)
            queue.record_durable_outcome("applied")

        self.assertEqual(prepared, command)
        self.assertEqual(queue.snapshot()["counts"], {"order_state_write_errors": 1})

        queue = FastPublicationQueue()
        self.assertTrue(queue.enqueue(command).accepted)
        newer = _ordered({"mode": 2}, {"mode": 11})
        self.assertEqual(queue.prepare_durable(newer), newer)
        self.assertEqual(len(queue), 0)

    def test_snapshot_prunes_a_fully_expired_fast_command(self) -> None:
        queue = FastPublicationQueue()
        with (
            patch("venus_evcharger.ipc.fast_publication.time.time", return_value=100.0),
            patch("venus_evcharger.ipc.fast_publication.time.monotonic", return_value=10.0),
        ):
            queue.enqueue(
                {
                    **publish_evcs_fields_command({"mode": 1}, priority="live"),
                    "created_at": 100.0,
                    "deadline_s": 1.0,
                }
            )
        with patch("venus_evcharger.ipc.fast_publication.time.monotonic", return_value=11.0):
            snapshot = queue.snapshot()

        self.assertEqual(snapshot["depth"], 0)
        self.assertEqual(snapshot["counts"], {"accepted": 1, "expired": 1})

    def test_requeue_does_not_take_expiry_from_an_older_queued_field(self) -> None:
        newer = FastPublicationWork(
            _ordered({"mode": 2}, {"mode": 11}),
            {"mode": 20.0},
        )
        older = FastPublicationWork(
            _ordered({"mode": 1}, {"mode": 10}),
            {"mode": 30.0},
        )

        merged = merge_fast_work(newer, older, retry_at=5.0, deferred=True)

        self.assertEqual(merged.command["fields"], {"mode": 2})
        self.assertEqual(merged.field_expires_at, {"mode": 20.0})


class PublicationStateEdgeContracts(unittest.TestCase):
    def test_state_loader_bounds_capacity_and_accepts_durable_marks(self) -> None:
        records = [
            {"key": "first", "field": "mode", "order": 1, "lane": "durable", "seen_at": 9.0},
            {"key": "second", "field": "mode", "order": 2, "lane": "fast", "seen_at": 9.0},
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "orders.json"
            path.write_text(
                json.dumps({"schema_version": 1, "marks": records}),
                encoding="utf-8",
            )
            marks = load_publication_order_marks(
                str(path),
                now=10.0,
                retention_seconds=5.0,
                capacity=1,
            )

        self.assertEqual(tuple(marks), (("first", "mode"),))
        self.assertEqual(marks[("first", "mode")].lane, "durable")

    def test_state_loader_ignores_nonmapping_and_bad_timestamp_records(self) -> None:
        records: list[object] = [
            [],
            {"key": "type", "field": "mode", "order": 1, "lane": "fast", "seen_at": {}},
            {"key": "text", "field": "mode", "order": 1, "lane": "fast", "seen_at": "bad"},
            {"key": "lane", "field": "mode", "order": 1, "lane": "other", "seen_at": 9.0},
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "orders.json"
            path.write_text(
                json.dumps({"schema_version": 1, "marks": records}),
                encoding="utf-8",
            )
            marks = load_publication_order_marks(
                str(path),
                now=10.0,
                retention_seconds=5.0,
                capacity=4,
            )

        self.assertEqual(marks, {})


class PublicationPayloadEdgeContracts(unittest.TestCase):
    def test_older_field_order_cannot_replace_newer_value(self) -> None:
        existing = _ordered({"mode": 2}, {"mode": 11})
        candidate = _ordered({"mode": 1}, {"mode": 10})

        merged = merge_publication_payload(existing, candidate)

        self.assertEqual(merged["fields"], {"mode": 2})
        self.assertEqual(merged[PUBLICATION_FIELD_ORDERS_FIELD], {"mode": 11})

    def test_nonstring_field_keys_are_rejected_by_order_contract(self) -> None:
        self.assertEqual(publication_field_orders({"fields": {1: "bad"}}), {})
        self.assertEqual(publication_fields({"fields": {1: "bad"}}), {})
        self.assertEqual(publication_fields({"fields": []}), {})
        issuer = PublicationOrderIssuer(PublicationOrderSequence())
        command = {
            **publish_evcs_fields_command({"mode": 1}, priority="live"),
            "fields": {1: "bad"},
        }

        ordered = issuer.ordered(command)

        self.assertNotIn(PUBLICATION_FIELD_ORDERS_FIELD, ordered)

    def test_lower_priority_overlap_does_not_replace_durable_payload(self) -> None:
        existing = {
            **_ordered({"mode": 1}, {"mode": 10}),
            "priority": "safety",
        }
        candidate = {
            **_ordered({"mode": 2}, {"mode": 11}),
            "priority": "diagnostic",
        }

        merged = DbusGatewayCommandQueuePolicy.merge_coalesced(existing, candidate)

        self.assertFalse(merged)
        self.assertEqual(candidate["fields"], {"mode": 2})

    def test_durable_merge_enforces_kind_and_companion_target_boundaries(self) -> None:
        existing = publish_evcs_fields_command({"mode": 1}, priority="live")
        candidate = publish_evcs_fields_command(
            {"ac_power_w": 500.0},
            priority="live",
        )

        self.assertTrue(
            DbusGatewayCommandQueuePolicy.merge_coalesced(existing, candidate)
        )
        self.assertEqual(
            candidate["fields"],
            {"mode": 1, "ac_power_w": 500.0},
        )
        self.assertNotIn(PUBLICATION_FIELD_ORDERS_FIELD, candidate)
        self.assertFalse(
            DbusGatewayCommandQueuePolicy.merge_coalesced(
                existing,
                {"type": "not-a-publication", "fields": {"mode": 2}},
            )
        )

        first_companion = publish_companion_fields_command(
            "first",
            {"power_w": 10.0},
            priority="live",
        )
        second_companion = publish_companion_fields_command(
            "second",
            {"power_w": 20.0},
            priority="live",
        )
        self.assertFalse(
            DbusGatewayCommandQueuePolicy.merge_coalesced(
                first_companion,
                second_companion,
            )
        )
        self.assertEqual(
            DbusGatewayCommandQueuePolicy.order_key({"type": "other"})[1],
            2,
        )

    def test_empty_path_removal_returns_same_immutable_snapshot(self) -> None:
        snapshot = PendingCommandSnapshot((), ())
        self.assertIs(snapshot.without_paths(frozenset()), snapshot)


if __name__ == "__main__":
    unittest.main()

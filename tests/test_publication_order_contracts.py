#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for field-wise, restart-safe publication ordering."""

from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from venus_evcharger.dbus_gateway import gateway_paths
from venus_evcharger.dbus_gateway_client import GatewayClient
from venus_evcharger.ipc.deadline import TRANSIENT_PUBLICATION_DEADLINE_SECONDS
from venus_evcharger.ipc.gateway_publication import publish_evcs_fields_command
from venus_evcharger.ipc.publication_order import (
    PUBLICATION_FIELD_ORDERS_FIELD,
    PUBLICATION_ORDER_FIELD,
    PUBLICATION_ORDER_RETENTION_SECONDS,
    PublicationOrderHistory,
    PublicationOrderIssuer,
    PublicationOrderSequence,
    publication_field_orders,
    publication_order,
)


def _ordered_fields(order: int, *fields: str) -> dict[str, int]:
    return {field: order for field in fields}


class PublicationOrderIssuerTests(unittest.TestCase):
    def test_retention_exceeds_every_transient_fallback_deadline(self) -> None:
        self.assertGreater(
            PUBLICATION_ORDER_RETENTION_SECONDS,
            TRANSIENT_PUBLICATION_DEADLINE_SECONDS,
        )

    def test_issuer_preserves_retry_and_assigns_every_field(self) -> None:
        sequence = PublicationOrderSequence(process_id=0)
        issuer = PublicationOrderIssuer(sequence)
        command = publish_evcs_fields_command(
            {"mode": 1, "ac_power_w": 100.0},
            priority="live",
        )
        with patch(
            "venus_evcharger.ipc.publication_order.time.monotonic_ns",
            side_effect=(50, 50),
        ):
            first = issuer.ordered(command)
            retry = issuer.ordered(first)
            second = issuer.ordered(command)

        self.assertEqual(first[PUBLICATION_ORDER_FIELD], 50)
        self.assertEqual(
            first[PUBLICATION_FIELD_ORDERS_FIELD],
            {"mode": 50, "ac_power_w": 50},
        )
        self.assertEqual(retry, first)
        self.assertEqual(second[PUBLICATION_ORDER_FIELD], 51)
        self.assertEqual(issuer.ordered({"kind": "other"}), {"kind": "other"})

    def test_sequence_starts_at_the_first_positive_clock_order(self) -> None:
        sequence = PublicationOrderSequence(process_id=0)
        with patch("venus_evcharger.ipc.publication_order.time.monotonic_ns", return_value=1):
            self.assertEqual(sequence.next_order(), 1)

    def test_fieldless_coalesced_command_receives_a_positive_order(self) -> None:
        issuer = PublicationOrderIssuer(PublicationOrderSequence(process_id=0))
        with patch("venus_evcharger.ipc.publication_order.time.monotonic_ns", return_value=3):
            ordered = issuer.ordered({"kind": "refresh", "coalesce_key": "refresh:key"})

        self.assertEqual(ordered[PUBLICATION_ORDER_FIELD], 3)
        self.assertNotIn(PUBLICATION_FIELD_ORDERS_FIELD, ordered)

    def test_existing_field_orders_advance_the_shared_sequence(self) -> None:
        sequence = PublicationOrderSequence(process_id=0)
        issuer = PublicationOrderIssuer(sequence)
        command = {
            **publish_evcs_fields_command({"mode": 1, "connected": 1}, priority="live"),
            PUBLICATION_ORDER_FIELD: 10,
            PUBLICATION_FIELD_ORDERS_FIELD: {"mode": 20, "connected": -1},
        }
        remembered = issuer.ordered(command)
        with patch("venus_evcharger.ipc.publication_order.time.monotonic_ns", return_value=1):
            following = issuer.ordered(
                publish_evcs_fields_command({"mode": 2}, priority="live")
            )

        self.assertEqual(
            remembered[PUBLICATION_FIELD_ORDERS_FIELD],
            {"mode": 20, "connected": 10},
        )
        self.assertEqual(remembered[PUBLICATION_ORDER_FIELD], 20)
        self.assertEqual(following[PUBLICATION_ORDER_FIELD], 21)

    def test_two_default_issuers_are_process_global_at_identical_clock_value(self) -> None:
        first = PublicationOrderIssuer()
        second = PublicationOrderIssuer()
        command = publish_evcs_fields_command({"mode": 1}, priority="live")
        with patch("venus_evcharger.ipc.publication_order.time.monotonic_ns", return_value=7):
            first_order = publication_order(first.ordered(command))
            second_order = publication_order(second.ordered(command))

        self.assertEqual(
            second_order,
            first_order + (1 << 32),
        )

    def test_two_gateway_clients_cannot_issue_the_same_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = gateway_paths(temp_dir)
            first = GatewayClient(paths)
            second = GatewayClient(paths)
            command = publish_evcs_fields_command({"mode": 1}, priority="live")
            sent: list[int] = []

            def accept(payload: dict[str, object]) -> dict[str, object]:
                sent.append(publication_order(payload))
                return {"ok": True, "accepted": True, "command_id": "fast"}

            with (
                patch.object(first, "backpressure_state", return_value="ok"),
                patch.object(second, "backpressure_state", return_value="ok"),
                patch.object(first, "send", side_effect=accept),
                patch.object(second, "send", side_effect=accept),
                patch("venus_evcharger.ipc.publication_order.time.monotonic_ns", return_value=11),
            ):
                self.assertEqual(first.enqueue_command(command).command_id, "fast")
                self.assertEqual(second.enqueue_command(command).command_id, "fast")

        self.assertEqual(len(set(sent)), 2)
        self.assertEqual(
            sent[1],
            sent[0] + (1 << 32),
        )

    def test_shared_sequence_is_unique_under_concurrency(self) -> None:
        sequence = PublicationOrderSequence(process_id=17)
        command = publish_evcs_fields_command({"mode": 1}, priority="live")

        def issue(_index: int) -> int:
            return publication_order(PublicationOrderIssuer(sequence).ordered(command))

        with (
            patch("venus_evcharger.ipc.publication_order.time.monotonic_ns", return_value=100),
            ThreadPoolExecutor(max_workers=8) as pool,
        ):
            orders = list(pool.map(issue, range(40)))

        self.assertEqual(len(set(orders)), 40)
        self.assertEqual(
            sorted(orders),
            [((100 + index) << 32) | 17 for index in range(40)],
        )

    def test_sequences_are_unique_across_process_ids_at_identical_clock_values(self) -> None:
        sequences = tuple(PublicationOrderSequence(process_id=process_id) for process_id in range(1, 5))
        with patch("venus_evcharger.ipc.publication_order.time.monotonic_ns", return_value=200):
            orders = [
                sequence.next_order()
                for sequence in sequences
                for _index in range(100)
            ]

        self.assertEqual(len(orders), 400)
        self.assertEqual(len(set(orders)), 400)

    def test_order_validation_rejects_bool_zero_and_non_integer_values(self) -> None:
        self.assertEqual(publication_order({PUBLICATION_ORDER_FIELD: 1}), 1)
        self.assertEqual(publication_order({PUBLICATION_ORDER_FIELD: 3}), 3)
        for invalid in (True, 0, -1, 1.0, "1", None):
            with self.subTest(value=invalid):
                self.assertEqual(publication_order({PUBLICATION_ORDER_FIELD: invalid}), 0)
        self.assertEqual(
            publication_field_orders(
                {
                    "fields": {"mode": 1, "connected": 1},
                    PUBLICATION_ORDER_FIELD: 7,
                    PUBLICATION_FIELD_ORDERS_FIELD: {"mode": 8, "connected": True},
                }
            ),
            {"mode": 8, "connected": 7},
        )
        self.assertEqual(
            publication_field_orders(
                {
                    "fields": {"mode": 1},
                    PUBLICATION_ORDER_FIELD: 9,
                    PUBLICATION_FIELD_ORDERS_FIELD: {"mode": 1},
                }
            ),
            {"mode": 1},
        )
        self.assertEqual(publication_field_orders({"fields": []}), {})


class PublicationOrderHistoryTests(unittest.TestCase):
    def test_configuration_floors_and_snapshot_schema_are_exact(self) -> None:
        history = PublicationOrderHistory(capacity=0, retention_seconds=0.0)

        self.assertEqual(
            history.snapshot(),
            {
                "order_capacity": 1,
                "order_retention_seconds": 1.0,
                "ordered_keys": 0,
                "ordered_fields": 0,
                "order_state_persistent": False,
            },
        )

    def test_claims_are_fieldwise_across_fast_and_durable_lanes(self) -> None:
        history = PublicationOrderHistory(capacity=8, retention_seconds=60.0)
        first = history.claim_fast("evcs", _ordered_fields(10, "mode", "power"), active_fields=set())
        partial = history.claim_fast(
            "evcs",
            {"mode": 9, "power": 11, "current": 11},
            active_fields=set(),
        )
        durable = history.claim_durable(
            "evcs",
            {"mode": 10, "power": 10, "energy": 10},
            active_fields=set(),
        )

        self.assertEqual(first.state, "accepted")
        self.assertEqual(first.accepted_fields, ("mode", "power"))
        self.assertEqual(partial.accepted_fields, ("power", "current"))
        self.assertEqual(partial.superseded_fields, ("mode",))
        self.assertEqual(durable.accepted_fields, ("energy",))
        self.assertEqual(durable.superseded_fields, ("mode", "power"))

    def test_equal_order_allows_only_a_durable_retry_on_the_same_lane(self) -> None:
        history = PublicationOrderHistory(capacity=4, retention_seconds=60.0)
        accepted = history.claim_durable("evcs", {"mode": 5}, active_fields=set())
        retry = history.claim_durable("evcs", {"mode": 5}, active_fields=set())
        fast = history.claim_fast("evcs", {"mode": 5}, active_fields=set())
        fast_history = PublicationOrderHistory(capacity=4, retention_seconds=60.0)
        first_fast = fast_history.claim_fast("evcs", {"mode": 5}, active_fields=set())
        repeated_fast = fast_history.claim_fast("evcs", {"mode": 5}, active_fields=set())

        self.assertEqual(accepted.state, "accepted")
        self.assertEqual(retry.state, "accepted")
        self.assertEqual(fast.state, "superseded")
        self.assertEqual(first_fast.state, "accepted")
        self.assertEqual(repeated_fast.state, "superseded")

    def test_fast_capacity_is_atomic_while_durable_work_is_not_blocked(self) -> None:
        history = PublicationOrderHistory(capacity=1, retention_seconds=60.0)
        self.assertEqual(
            history.claim_fast("evcs", {"mode": 1}, active_fields=set()).state,
            "accepted",
        )
        rejected = history.claim_fast(
            "evcs",
            {"power": 2, "current": 2},
            active_fields=set(),
        )
        durable = history.claim_durable(
            "evcs",
            {"energy": 3},
            active_fields=set(),
        )

        self.assertEqual(rejected.state, "full")
        self.assertEqual(rejected.accepted_fields, ())
        self.assertEqual(durable.state, "accepted")
        self.assertEqual(history.snapshot()["ordered_fields"], 1)

    def test_durable_update_of_existing_field_is_allowed_at_capacity(self) -> None:
        history = PublicationOrderHistory(capacity=1, retention_seconds=60.0)
        history.claim_durable("evcs", {"mode": 1}, active_fields=set())
        updated = history.claim_durable("evcs", {"mode": 3}, active_fields=set())
        stale_fast = history.claim_fast("evcs", {"mode": 2}, active_fields=set())

        self.assertEqual(updated.state, "accepted")
        self.assertEqual(stale_fast.state, "superseded")

    def test_invalid_order_does_not_hide_a_following_valid_field(self) -> None:
        history = PublicationOrderHistory(capacity=4, retention_seconds=60.0)
        history.claim_durable(
            "evcs",
            {"invalid": 0, "mode": 3},
            active_fields=set(),
        )
        stale_fast = history.claim_fast("evcs", {"mode": 2}, active_fields=set())

        self.assertEqual(history.snapshot()["ordered_fields"], 1)
        self.assertEqual(stale_fast.state, "superseded")

    def test_inactive_marks_expire_but_active_marks_remain(self) -> None:
        history = PublicationOrderHistory(capacity=4, retention_seconds=10.0)
        with patch("venus_evcharger.ipc.publication_order.time.monotonic", return_value=1.0):
            history.claim_fast(
                "evcs",
                {"mode": 1, "power": 1},
                active_fields=set(),
            )
        with patch("venus_evcharger.ipc.publication_order.time.monotonic", return_value=11.0):
            claim = history.claim_fast(
                "other",
                {"power": 2},
                active_fields={("evcs", "mode")},
            )

        self.assertEqual(claim.state, "accepted")
        snapshot = history.snapshot()
        self.assertEqual(snapshot["ordered_keys"], 2)
        self.assertEqual(snapshot["ordered_fields"], 2)
        retained = history.claim_fast(
            "evcs",
            {"mode": 1},
            active_fields={("evcs", "mode")},
        )
        self.assertEqual(retained.state, "superseded")

    def test_fast_state_survives_restart_and_filters_only_stale_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = str(Path(temp_dir) / "orders.json")
            with patch("venus_evcharger.ipc.publication_order.time.monotonic", return_value=10.0):
                first = PublicationOrderHistory(
                    capacity=8,
                    retention_seconds=60.0,
                    state_path=state_path,
                )
                claim = first.claim_fast(
                    "evcs",
                    {"mode": 11, "power": 11},
                    active_fields=set(),
                )
            self.assertEqual(claim.state, "accepted")
            self.assertTrue(Path(state_path).is_file())

            with patch("venus_evcharger.ipc.publication_order.time.monotonic", return_value=20.0):
                restarted = PublicationOrderHistory(
                    capacity=8,
                    retention_seconds=60.0,
                    state_path=state_path,
                )
                durable = restarted.claim_durable(
                    "evcs",
                    {"mode": 10, "energy": 10},
                    active_fields=set(),
                )

        self.assertEqual(durable.accepted_fields, ("energy",))
        self.assertEqual(durable.superseded_fields, ("mode",))

    def test_state_write_failure_rejects_fast_without_mutating_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            history = PublicationOrderHistory(
                capacity=2,
                retention_seconds=60.0,
                state_path=temp_dir,
            )
            failed = history.claim_fast("evcs", {"mode": 1}, active_fields=set())

        self.assertEqual(failed.state, "state-error")
        self.assertEqual(history.snapshot()["ordered_fields"], 0)

    def test_superseded_fast_claim_is_read_only_when_state_path_breaks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "orders.json"
            history = PublicationOrderHistory(
                capacity=2,
                retention_seconds=60.0,
                state_path=str(state_path),
            )
            self.assertEqual(
                history.claim_fast("evcs", {"mode": 2}, active_fields=set()).state,
                "accepted",
            )
            state_path.unlink()
            state_path.mkdir()

            superseded = history.claim_fast(
                "evcs",
                {"mode": 1},
                active_fields=set(),
            )

        self.assertEqual(superseded.state, "superseded")

    def test_corrupt_expired_future_and_wrong_schema_state_are_ignored(self) -> None:
        records = [
            {"key": "old", "field": "mode", "order": 1, "lane": "fast", "seen_at": 1.0},
            {"key": "future", "field": "mode", "order": 2, "lane": "fast", "seen_at": 101.0},
            {"key": "", "field": "mode", "order": 3, "lane": "fast", "seen_at": 95.0},
            {"key": "bad", "field": "mode", "order": True, "lane": "fast", "seen_at": 95.0},
            {"key": "ok", "field": "mode", "order": 4, "lane": "fast", "seen_at": 95.0},
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "orders.json"
            path.write_text(
                json.dumps({"schema_version": 1, "marks": records}),
                encoding="utf-8",
            )
            with patch("venus_evcharger.ipc.publication_order.time.monotonic", return_value=100.0):
                history = PublicationOrderHistory(
                    capacity=8,
                    retention_seconds=10.0,
                    state_path=str(path),
                )
            self.assertEqual(history.snapshot()["ordered_fields"], 1)

            path.write_text('{"schema_version":2,"marks":[]}', encoding="utf-8")
            replacement = PublicationOrderHistory(
                capacity=8,
                retention_seconds=10.0,
                state_path=str(path),
            )
            self.assertEqual(replacement.snapshot()["ordered_fields"], 0)


if __name__ == "__main__":
    unittest.main()

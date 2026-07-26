#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Mutation-sensitive construction and enqueue contracts for the fast queue."""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from tests.support.fast_publication_queue import (
    publication_command,
    publication_work,
)
from venus_evcharger.ipc.fast_publication import (
    FAST_PUBLICATION_ORDER_CAPACITY_FACTOR,
    FastPublicationEnqueueResult,
    FastPublicationQueue,
    _FastEnqueueCandidate,
)
from venus_evcharger.ipc.fast_publication_policy import fast_command_id
from venus_evcharger.ipc.publication_order import PublicationFieldClaim


class FastPublicationConstructionMutationContracts(unittest.TestCase):
    def test_constructor_clamps_each_bound_and_forwards_order_state(self) -> None:
        ordering = Mock()
        with patch(
            "venus_evcharger.ipc.fast_publication.FastPublicationOrdering",
            return_value=ordering,
        ) as factory:
            queue = FastPublicationQueue(
                capacity=0,
                order_capacity=0,
                order_retention_seconds=0.5,
                order_state_path="/run/order-state.json",
            )

        self.assertEqual(queue.capacity, 1)
        self.assertEqual(queue.order_capacity, 1)
        self.assertEqual(queue.order_retention_seconds, 1.0)
        factory.assert_called_once_with(
            capacity=1,
            retention_seconds=1.0,
            state_path="/run/order-state.json",
        )

    def test_default_order_capacity_scales_with_queue_capacity(self) -> None:
        with patch("venus_evcharger.ipc.fast_publication.FastPublicationOrdering") as factory:
            queue = FastPublicationQueue(
                capacity=3,
                order_retention_seconds=7.5,
            )

        self.assertEqual(
            queue.order_capacity,
            3 * FAST_PUBLICATION_ORDER_CAPACITY_FACTOR,
        )
        self.assertEqual(queue.order_retention_seconds, 7.5)
        factory.assert_called_once_with(
            capacity=3 * FAST_PUBLICATION_ORDER_CAPACITY_FACTOR,
            retention_seconds=7.5,
            state_path=None,
        )


class FastPublicationEnqueueMutationContracts(unittest.TestCase):
    def test_preflight_rejection_is_complete_and_does_not_claim(self) -> None:
        queue = FastPublicationQueue()
        command = publication_command()
        with (
            patch.object(queue, "_rejection_reason", return_value="blocked"),
            patch.object(queue, "_enqueue_valid") as enqueue_valid,
        ):
            result = queue.enqueue(command)

        self.assertEqual(
            result,
            FastPublicationEnqueueResult(False, "", "blocked"),
        )
        self.assertEqual(
            result.to_payload(),
            {
                "ok": False,
                "accepted": False,
                "command_id": "",
                "reason": "blocked",
            },
        )
        self.assertEqual(queue.snapshot()["counts"], {"rejected": 1})
        enqueue_valid.assert_not_called()

    def test_candidate_uses_merged_payload_limit_capacity_and_remaining_ttl(self) -> None:
        queue = FastPublicationQueue(capacity=2)
        command = publication_command(key="  evcs:fields  ")
        merged = {**command, "id": "merged"}
        with (
            patch.object(queue, "_queue_payload", return_value=merged) as merge,
            patch.object(queue, "_queue_at_capacity", return_value=False) as full,
            patch(
                "venus_evcharger.ipc.fast_publication.fast_publication_payload_limit_reason",
                return_value="",
            ) as limit,
            patch(
                "venus_evcharger.ipc.fast_publication.remaining_transient_ttl",
                return_value=12.5,
            ) as ttl,
            patch(
                "venus_evcharger.ipc.fast_publication.time.time",
                return_value=123.0,
            ),
        ):
            candidate = queue._enqueue_candidate(command)

        self.assertEqual(
            candidate,
            _FastEnqueueCandidate("evcs:fields", None, 12.5),
        )
        merge.assert_called_once_with(None, command, "evcs:fields")
        limit.assert_called_once_with(merged)
        full.assert_called_once_with(None)
        ttl.assert_called_once_with(command, 123.0)

    def test_candidate_rejections_have_distinct_metrics_and_stop_early(self) -> None:
        command = publication_command()
        cases = (
            ("payload-limit", False, 5.0, "payload_limit"),
            ("", True, 5.0, "full"),
            ("", False, 0.0, "expired"),
        )
        for limit_reason, full, ttl, metric in cases:
            with self.subTest(metric=metric):
                queue = FastPublicationQueue()
                with (
                    patch(
                        "venus_evcharger.ipc.fast_publication.fast_publication_payload_limit_reason",
                        return_value=limit_reason,
                    ),
                    patch.object(queue, "_queue_at_capacity", return_value=full),
                    patch(
                        "venus_evcharger.ipc.fast_publication.remaining_transient_ttl",
                        return_value=ttl,
                    ),
                ):
                    result = queue._enqueue_candidate(command)

                self.assertIsInstance(result, FastPublicationEnqueueResult)
                assert isinstance(result, FastPublicationEnqueueResult)
                expected_reason = limit_reason or ("queue-full" if full else "expired")
                self.assertEqual(result.reason, expected_reason)
                self.assertEqual(queue.snapshot()["counts"], {metric: 1})

    def test_candidate_without_key_uses_empty_key_at_internal_boundary(self) -> None:
        queue = FastPublicationQueue()
        command = publication_command()
        del command["coalesce_key"]
        with (
            patch.object(queue, "_queue_payload", return_value=command) as merge,
            patch(
                "venus_evcharger.ipc.fast_publication.fast_publication_payload_limit_reason",
                return_value="",
            ),
            patch(
                "venus_evcharger.ipc.fast_publication.remaining_transient_ttl",
                return_value=1.0,
            ),
        ):
            candidate = queue._enqueue_candidate(command)

        self.assertEqual(candidate, _FastEnqueueCandidate("", None, 1.0))
        merge.assert_called_once_with(None, command, "")

    def test_existing_key_can_coalesce_while_queue_is_at_capacity(self) -> None:
        queue = FastPublicationQueue(capacity=1)
        first = publication_command({"mode": 1})
        second = publication_command({"power": 500.0}, order=11)

        self.assertTrue(queue.enqueue(first).accepted)
        self.assertTrue(queue.enqueue(second).accepted)
        self.assertEqual(len(queue), 1)

    def test_claim_states_have_exact_results_and_metrics(self) -> None:
        queue = FastPublicationQueue()

        superseded = queue._claim_failure(
            "evcs:fields",
            PublicationFieldClaim("superseded"),
        )
        full = queue._claim_failure(
            "evcs:fields",
            PublicationFieldClaim("full"),
        )
        accepted = queue._claim_failure(
            "evcs:fields",
            PublicationFieldClaim("accepted", ("mode",)),
        )

        self.assertEqual(
            superseded,
            FastPublicationEnqueueResult(
                True,
                fast_command_id("evcs:fields"),
                "superseded",
            ),
        )
        self.assertEqual(
            full,
            FastPublicationEnqueueResult(
                False,
                "",
                "order-history-full",
            ),
        )
        self.assertIsNone(accepted)
        self.assertEqual(
            queue.snapshot()["counts"],
            {"order_history_full": 1, "superseded": 1},
        )

    def test_accept_claim_stores_tail_and_counts_fieldwise_coalescing(self) -> None:
        queue = FastPublicationQueue()
        existing = publication_work({"mode": 1})
        candidate = _FastEnqueueCandidate("evcs:fields", existing, 8.0)
        merged = publication_work({"mode": 2, "power": 500.0}, order=11)
        claim = PublicationFieldClaim(
            "accepted",
            ("mode",),
            ("power", "energy"),
        )
        queue._commands["older"] = publication_work(key="older")
        with patch.object(
            queue,
            "_merged_work",
            return_value=merged,
        ) as merge:
            result = queue._accept_claim(publication_command(), candidate, claim)

        self.assertEqual(tuple(queue._commands), ("older", "evcs:fields"))
        self.assertIs(queue._commands["evcs:fields"], merged)
        self.assertEqual(result.command_id, str(merged.command["id"]))
        merge.assert_called_once_with(
            existing,
            publication_command(),
            "evcs:fields",
            accepted_fields=("mode",),
            ttl=8.0,
        )
        self.assertEqual(
            queue.snapshot()["counts"],
            {
                "accepted": 1,
                "coalesced": 1,
                "fields_superseded": 2,
            },
        )

    def test_rejected_enqueue_normalizes_metric_name_and_honors_override(self) -> None:
        queue = FastPublicationQueue()

        first = queue._rejected_enqueue("field-name-too-large")
        second = queue._rejected_enqueue("queue-full", metric="full")

        self.assertEqual(first, FastPublicationEnqueueResult(False, "", "field-name-too-large"))
        self.assertEqual(second, FastPublicationEnqueueResult(False, "", "queue-full"))
        self.assertEqual(
            queue.snapshot()["counts"],
            {"field_name_too_large": 1, "full": 1},
        )


if __name__ == "__main__":
    unittest.main()

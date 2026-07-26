#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Queue, TTL, fairness, and cross-lane contracts for fast publication."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from venus_evcharger.ipc.command_mailbox import normalized_mapping
from venus_evcharger.ipc.fast_publication import (
    FAST_PUBLICATION_DEFERRED_AGING_SECONDS,
    FAST_PUBLICATION_RETRY_SECONDS,
    FastPublicationQueue,
    FastPublicationWork,
    _fast_work_priority,
)
from venus_evcharger.ipc.gateway_publication import (
    publish_companion_fields_command,
    publish_evcs_fields_command,
)
from venus_evcharger.ipc.publication_order import (
    PUBLICATION_FIELD_ORDERS_FIELD,
    PUBLICATION_ORDER_FIELD,
    PublicationOrderCapacityError,
    PublicationOrderPendingFastError,
)
from venus_evcharger.ipc.publication_payload import (
    MAX_PUBLICATION_COALESCE_KEY_BYTES,
    MAX_PUBLICATION_FIELD_NAME_BYTES,
    MAX_PUBLICATION_FIELDS_PER_KEY,
    MAX_PUBLICATION_PAYLOAD_BYTES,
    publication_payload_limit_reason,
)


def _ordered(command: dict[str, object], order: int) -> dict[str, object]:
    fields = normalized_mapping(command.get("fields"))
    if fields is None:
        raise AssertionError("Publication fields must be a mapping")
    return {
        **command,
        PUBLICATION_ORDER_FIELD: order,
        PUBLICATION_FIELD_ORDERS_FIELD: {field: order for field in fields},
    }


def _fields(work: FastPublicationWork) -> dict[str, object]:
    values = normalized_mapping(work.command.get("fields"))
    if values is None:
        raise AssertionError("Queued publication fields must be a mapping")
    return values


class FastPublicationQueueCoreTests(unittest.TestCase):
    def test_queue_coalesces_fields_and_prefers_live_over_diagnostic(self) -> None:
        queue = FastPublicationQueue(capacity=3)
        diagnostic = publish_companion_fields_command(
            "grid",
            {"ac_power_w": 10.0},
            priority="diagnostic",
        )
        first = publish_evcs_fields_command(
            {"mode": 1, "ac_power_w": 100.0},
            priority="live",
        )
        second = publish_evcs_fields_command({"ac_power_w": 200.0}, priority="live")

        self.assertTrue(queue.enqueue(diagnostic).accepted)
        first_result = queue.enqueue(first)
        second_result = queue.enqueue(second)
        live = queue.pop_next()

        self.assertTrue(first_result.accepted)
        self.assertEqual(second_result.command_id, first_result.command_id)
        self.assertEqual(len(queue), 1)
        self.assertIsNotNone(live)
        assert live is not None
        self.assertEqual(_fields(live), {"mode": 1, "ac_power_w": 200.0})
        self.assertEqual(live.command["publication_priority"], "live")
        remaining = queue.pop_next()
        self.assertIsNotNone(remaining)
        assert remaining is not None
        self.assertEqual(remaining.command["service_id"], "grid")

    def test_queue_rejects_non_transient_missing_key_and_capacity_overflow(self) -> None:
        queue = FastPublicationQueue(capacity=1)
        critical = publish_evcs_fields_command({"connected": 0}, priority="critical")
        live = publish_evcs_fields_command({"mode": 1}, priority="live")
        other = publish_companion_fields_command(
            "grid",
            {"ac_power_w": 1.0},
            priority="live",
        )

        self.assertEqual(queue.enqueue(critical).reason, "durable-command-required")
        self.assertTrue(queue.enqueue(live).accepted)
        self.assertEqual(queue.enqueue(other).reason, "queue-full")
        missing_key = dict(live)
        missing_key.pop("coalesce_key")
        self.assertEqual(queue.enqueue(missing_key).reason, "missing-coalesce-key")
        self.assertEqual(
            queue.enqueue(
                {
                    "kind": "other",
                    "publication_priority": "live",
                    "coalesce_key": "other",
                }
            ).reason,
            "durable-command-required",
        )
        self.assertEqual(
            queue.snapshot()["counts"],
            {"accepted": 1, "full": 1, "rejected": 3},
        )

    def test_requeue_preserves_work_and_restart_drops_only_transient_payload(self) -> None:
        queue = FastPublicationQueue()
        queue.enqueue(publish_evcs_fields_command({"mode": 2}, priority="live"))
        work = queue.pop_next()
        self.assertIsNotNone(work)
        assert work is not None
        queue.requeue(work, now=5.0)

        self.assertEqual(len(queue), 1)
        requeued = queue.pop_next(now=5.0)
        self.assertIsNotNone(requeued)
        assert requeued is not None
        self.assertEqual(requeued.command, work.command)
        self.assertEqual(requeued.field_expires_at, work.field_expires_at)
        self.assertEqual(requeued.retry_at, 5.0)
        self.assertEqual(len(FastPublicationQueue()), 0)

    def test_success_lifecycle_is_sampled_while_failures_are_always_reported(self) -> None:
        queue = FastPublicationQueue()
        queue.enqueue(publish_evcs_fields_command({"mode": 1}, priority="live"))
        work = queue.pop_next()
        self.assertIsNotNone(work)
        assert work is not None

        self.assertTrue(queue.record_outcome(work, "applied", now=10.0))
        self.assertFalse(queue.record_outcome(work, "applied", now=10.1))
        self.assertTrue(queue.record_outcome(work, "deferred", now=10.2))
        for offset in range(23):
            self.assertFalse(
                queue.record_outcome(work, "applied", now=10.3 + offset * 0.01)
            )
        self.assertTrue(queue.record_outcome(work, "applied", now=10.9))
        self.assertTrue(queue.record_outcome(work, "dropped", now=11.0))
        self.assertEqual(
            queue.snapshot()["counts"],
            {
                "accepted": 1,
                "applied": 26,
                "applied_samples": 2,
                "deferred": 1,
                "dropped": 1,
            },
        )


class FastPublicationFieldArbitrationTests(unittest.TestCase):
    def test_new_fast_fields_do_not_discard_unrelated_durable_fields(self) -> None:
        queue = FastPublicationQueue()
        new_fast = _ordered(
            publish_evcs_fields_command({"power": 900.0}, priority="live"),
            11,
        )
        old_fallback = _ordered(
            publish_evcs_fields_command(
                {"power": 100.0, "energy": 4.2},
                priority="live",
            ),
            10,
        )

        self.assertTrue(queue.enqueue(new_fast).accepted)
        work = queue.pop_next()
        self.assertIsNotNone(work)
        assert work is not None
        queue.record_outcome(work, "applied")
        durable = queue.prepare_durable(old_fallback)

        self.assertIsNotNone(durable)
        assert durable is not None
        self.assertEqual(durable["fields"], {"energy": 4.2})
        self.assertIsNone(queue.pop_next())

    def test_newer_durable_field_removes_only_its_fast_counterpart(self) -> None:
        queue = FastPublicationQueue()
        fast = _ordered(
            publish_evcs_fields_command(
                {"mode": 1, "power": 500.0},
                priority="live",
            ),
            10,
        )
        durable = _ordered(
            publish_evcs_fields_command({"mode": 2}, priority="live"),
            11,
        )

        self.assertTrue(queue.enqueue(fast).accepted)
        prepared = queue.prepare_durable(durable)
        remaining = queue.pop_next()

        self.assertIsNotNone(prepared)
        assert prepared is not None
        self.assertEqual(prepared["fields"], {"mode": 2})
        self.assertIsNotNone(remaining)
        assert remaining is not None
        self.assertEqual(_fields(remaining), {"mode": 1, "power": 500.0})
        queue.requeue(remaining)
        queue.record_durable_outcome(prepared, "applied")
        after_apply = queue.pop_next()
        self.assertIsNotNone(after_apply)
        assert after_apply is not None
        self.assertEqual(_fields(after_apply), {"power": 500.0})

    def test_same_order_fallback_waits_until_fast_work_has_resolved(self) -> None:
        queue = FastPublicationQueue()
        command = _ordered(
            publish_evcs_fields_command({"mode": 1}, priority="live"),
            10,
        )

        self.assertTrue(queue.enqueue(command).accepted)
        with self.assertRaises(PublicationOrderPendingFastError):
            queue.prepare_durable(command)
        counts = normalized_mapping(queue.snapshot().get("counts"))
        self.assertIsNotNone(counts)
        assert counts is not None
        self.assertEqual(counts["durable_waiting_for_fast"], 1)

    def test_unordered_durable_fields_cannot_override_queued_unordered_fast_fields(self) -> None:
        queue = FastPublicationQueue()
        fast = publish_evcs_fields_command({"mode": 2}, priority="live")
        durable = publish_evcs_fields_command(
            {"mode": 1, "energy": 2.0},
            priority="live",
        )

        self.assertTrue(queue.enqueue(fast).accepted)
        with self.assertRaises(PublicationOrderPendingFastError):
            queue.prepare_durable(durable)

    def test_gateway_crash_before_fast_apply_keeps_fallback_admissible(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = str(Path(temp_dir) / "orders.json")
            command = _ordered(
                publish_evcs_fields_command({"mode": 1}, priority="live"),
                10,
            )
            running = FastPublicationQueue(order_state_path=state_path)
            self.assertTrue(running.enqueue(command).accepted)
            with self.assertRaises(PublicationOrderPendingFastError):
                running.prepare_durable(command)

            restarted = FastPublicationQueue(order_state_path=state_path)
            prepared = restarted.prepare_durable(command)

        self.assertEqual(prepared, command)

    def test_restart_state_blocks_only_stale_fields_from_old_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = str(Path(temp_dir) / "orders.json")
            first = FastPublicationQueue(order_state_path=state_path)
            new_fast = _ordered(
                publish_evcs_fields_command({"power": 900.0}, priority="live"),
                11,
            )
            old_fallback = _ordered(
                publish_evcs_fields_command(
                    {"power": 100.0, "energy": 4.2},
                    priority="live",
                ),
                10,
            )
            self.assertTrue(first.enqueue(new_fast).accepted)
            work = first.pop_next()
            self.assertIsNotNone(work)
            assert work is not None
            first.record_outcome(work, "applied")

            restarted = FastPublicationQueue(order_state_path=state_path)
            prepared = restarted.prepare_durable(old_fallback)

        self.assertEqual(len(restarted), 0)
        self.assertIsNotNone(prepared)
        assert prepared is not None
        self.assertEqual(prepared["fields"], {"energy": 4.2})
        self.assertTrue(restarted.snapshot()["order_state_persistent"])

    def test_failed_applied_checkpoint_is_reported_without_rejecting_fast_work(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            queue = FastPublicationQueue(order_state_path=temp_dir)
            command = _ordered(
                publish_evcs_fields_command({"mode": 1}, priority="live"),
                10,
            )
            result = queue.enqueue(command)
            work = queue.pop_next()
            self.assertIsNotNone(work)
            assert work is not None
            queue.record_outcome(work, "applied")

        self.assertTrue(result.accepted)
        self.assertEqual(len(queue), 0)
        self.assertEqual(
            queue.snapshot()["counts"],
            {
                "accepted": 1,
                "applied": 1,
                "applied_samples": 1,
                "order_state_write_errors": 1,
            },
        )

    def test_applied_durable_removes_older_fast_field_when_checkpoint_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            queue = FastPublicationQueue(order_state_path=temp_dir)
            fast = _ordered(
                publish_evcs_fields_command({"mode": 1}, priority="live"),
                10,
            )
            durable = _ordered(
                publish_evcs_fields_command({"mode": 2}, priority="live"),
                11,
            )
            self.assertTrue(queue.enqueue(fast).accepted)
            prepared = queue.prepare_durable(durable)
            self.assertIsNotNone(prepared)
            assert prepared is not None

            queue.record_durable_outcome(prepared, "applied")

        self.assertEqual(len(queue), 0)
        self.assertIsNone(queue.pop_next())
        self.assertEqual(queue.snapshot()["counts"]["order_state_write_errors"], 1)

    def test_order_capacity_rejects_fast_and_defers_new_durable_field(self) -> None:
        queue = FastPublicationQueue(order_capacity=1)
        first = _ordered(
            publish_evcs_fields_command({"mode": 1}, priority="live"),
            1,
        )
        second = _ordered(
            publish_evcs_fields_command({"power": 2.0}, priority="live"),
            2,
        )

        self.assertTrue(queue.enqueue(first).accepted)
        rejection = queue.enqueue(second)

        self.assertEqual(rejection.reason, "order-history-full")
        with self.assertRaises(PublicationOrderCapacityError):
            queue.prepare_durable(second)

    def test_removing_absent_fast_fields_leaves_work_unchanged(self) -> None:
        queue = FastPublicationQueue()
        command = _ordered(
            publish_evcs_fields_command({"mode": 1}, priority="live"),
            1,
        )
        self.assertTrue(queue.enqueue(command).accepted)
        before = queue.snapshot()
        key = str(command["coalesce_key"])

        queue._remove_fast_fields(key, ("not-present",))

        self.assertEqual(queue.snapshot(), before)


class FastPublicationTtlAndFairnessTests(unittest.TestCase):
    def test_merged_fields_keep_independent_ttl(self) -> None:
        queue = FastPublicationQueue()
        first = publish_evcs_fields_command({"mode": 1}, priority="live")
        second = publish_evcs_fields_command({"power": 100.0}, priority="live")
        with (
            patch("venus_evcharger.ipc.fast_publication.time.time", return_value=100.0),
            patch("venus_evcharger.ipc.fast_publication.time.monotonic", return_value=0.0),
            patch("venus_evcharger.ipc.publication_order.time.monotonic", return_value=0.0),
        ):
            self.assertTrue(queue.enqueue(first).accepted)
        with (
            patch("venus_evcharger.ipc.fast_publication.time.time", return_value=110.0),
            patch("venus_evcharger.ipc.fast_publication.time.monotonic", return_value=10.0),
            patch("venus_evcharger.ipc.publication_order.time.monotonic", return_value=10.0),
        ):
            self.assertTrue(queue.enqueue(second).accepted)

        work = queue.pop_next(now=30.0)
        self.assertIsNotNone(work)
        assert work is not None
        self.assertEqual(_fields(work), {"power": 100.0})
        self.assertIsNone(queue.pop_next(now=40.0))

    def test_expired_command_and_expired_requeue_are_dropped(self) -> None:
        queue = FastPublicationQueue()
        expired = {
            **publish_evcs_fields_command({"mode": 1}, priority="live"),
            "created_at": 100.0,
            "deadline_s": 5.0,
        }
        with patch("venus_evcharger.ipc.fast_publication.time.time", return_value=105.1):
            self.assertEqual(queue.enqueue(expired).reason, "expired")

        with (
            patch("venus_evcharger.ipc.fast_publication.time.time", return_value=100.0),
            patch("venus_evcharger.ipc.fast_publication.time.monotonic", return_value=10.0),
        ):
            queue.enqueue(
                {
                    **publish_evcs_fields_command({"mode": 1}, priority="live"),
                    "deadline_s": 1.0,
                    "created_at": 100.0,
                }
            )
        work = queue.pop_next(now=10.5)
        self.assertIsNotNone(work)
        assert work is not None
        queue.requeue(work, deferred=True, now=11.0)
        self.assertEqual(len(queue), 0)

    def test_deferred_live_work_cannot_starve_other_work(self) -> None:
        queue = FastPublicationQueue()
        live = publish_companion_fields_command(
            "blocked",
            {"power": 1.0},
            priority="live",
        )
        diagnostic = publish_companion_fields_command(
            "healthy",
            {"power": 2.0},
            priority="diagnostic",
        )
        queue.enqueue(live)
        queue.enqueue(diagnostic)

        blocked = queue.pop_next(now=10.0)
        self.assertIsNotNone(blocked)
        assert blocked is not None
        self.assertEqual(blocked.command["service_id"], "blocked")
        queue.requeue(blocked, deferred=True, now=10.0)
        healthy = queue.pop_next(now=10.0)

        self.assertIsNotNone(healthy)
        assert healthy is not None
        self.assertEqual(healthy.command["service_id"], "healthy")
        self.assertIsNone(queue.pop_next(now=10.0))
        retried = queue.pop_next(now=10.0 + FAST_PUBLICATION_RETRY_SECONDS)
        self.assertIsNotNone(retried)
        assert retried is not None
        self.assertEqual(retried.command["service_id"], "blocked")

    def test_aged_deferred_work_cannot_starve_under_continuous_same_priority_load(self) -> None:
        queue = FastPublicationQueue()
        queue.enqueue(
            publish_companion_fields_command(
                "recovering",
                {"power": 1.0},
                priority="live",
            )
        )
        blocked = queue.pop_next(now=10.0)
        self.assertIsNotNone(blocked)
        assert blocked is not None
        queue.requeue(blocked, deferred=True, now=10.0)
        eligible_at = 10.0 + FAST_PUBLICATION_RETRY_SECONDS

        for index in range(5):
            queue.enqueue(
                publish_companion_fields_command(
                    f"continuous-{index}",
                    {"power": float(index)},
                    priority="live",
                )
            )
            selected = queue.pop_next(
                now=eligible_at + FAST_PUBLICATION_DEFERRED_AGING_SECONDS + 0.1
            )
            self.assertIsNotNone(selected)
            assert selected is not None
            if index == 0:
                self.assertEqual(selected.command["service_id"], "recovering")
                break
        else:
            self.fail("aged deferred work starved behind continuous publications")

    def test_deferred_aging_policy_has_explicit_boundary_ranks(self) -> None:
        command = {"priority": "publish"}
        recent = FastPublicationWork(command, {}, retry_at=10.0, deferred=True)
        fresh = FastPublicationWork(command, {}, retry_at=10.0)

        self.assertEqual(_fast_work_priority(recent, 10.5), (2, 2, 10.0))
        self.assertEqual(
            _fast_work_priority(
                recent,
                10.0 + FAST_PUBLICATION_DEFERRED_AGING_SECONDS,
            ),
            (2, 0, 10.0),
        )
        self.assertEqual(_fast_work_priority(fresh, 9.0), (2, 1, 10.0))

    def test_aged_diagnostic_work_does_not_overtake_fresh_live_work(self) -> None:
        queue = FastPublicationQueue()
        queue.enqueue(
            publish_companion_fields_command(
                "diagnostic",
                {"power": 1.0},
                priority="diagnostic",
            )
        )
        deferred = queue.pop_next(now=10.0)
        self.assertIsNotNone(deferred)
        assert deferred is not None
        queue.requeue(deferred, deferred=True, now=10.0)
        queue.enqueue(
            publish_evcs_fields_command(
                {"ac_power_w": 500.0},
                priority="live",
            )
        )

        selected = queue.pop_next(
            now=10.0
            + FAST_PUBLICATION_RETRY_SECONDS
            + FAST_PUBLICATION_DEFERRED_AGING_SECONDS
        )

        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected.command["publication_priority"], "live")

    def test_requeue_merges_work_arriving_while_an_item_is_in_flight(self) -> None:
        queue = FastPublicationQueue()
        first = _ordered(
            publish_evcs_fields_command({"mode": 1}, priority="live"),
            10,
        )
        second = _ordered(
            publish_evcs_fields_command({"power": 20.0}, priority="live"),
            11,
        )
        queue.enqueue(first)
        in_flight = queue.pop_next(now=10.0)
        self.assertIsNotNone(in_flight)
        assert in_flight is not None
        queue.enqueue(second)
        queue.requeue(in_flight, deferred=True, now=10.0)

        merged = queue.pop_next(now=10.0)
        self.assertIsNotNone(merged)
        assert merged is not None
        self.assertEqual(_fields(merged), {"mode": 1, "power": 20.0})


class FastPublicationBoundsTests(unittest.TestCase):
    def test_individual_field_key_and_payload_limits_are_rejected(self) -> None:
        queue = FastPublicationQueue()
        cases = (
            (
                {
                    **publish_evcs_fields_command({"mode": 1}, priority="live"),
                    "coalesce_key": "x" * (MAX_PUBLICATION_COALESCE_KEY_BYTES + 1),
                },
                "coalesce-key-too-large",
            ),
            (
                publish_evcs_fields_command(
                    {"x" * (MAX_PUBLICATION_FIELD_NAME_BYTES + 1): 1},
                    priority="live",
                ),
                "field-name-too-large",
            ),
            (
                publish_evcs_fields_command(
                    {f"field_{index}": index for index in range(MAX_PUBLICATION_FIELDS_PER_KEY + 1)},
                    priority="live",
                ),
                "field-limit",
            ),
            (
                publish_evcs_fields_command(
                    {"diagnostic_text": "x" * MAX_PUBLICATION_PAYLOAD_BYTES},
                    priority="live",
                ),
                "payload-limit",
            ),
            (
                publish_evcs_fields_command({"value": object()}, priority="live"),
                "payload-not-encodable",
            ),
        )
        for command, expected in cases:
            with self.subTest(reason=expected):
                self.assertEqual(queue.enqueue(command).reason, expected)

    def test_serialization_contract_reports_non_json_payload(self) -> None:
        self.assertEqual(
            publication_payload_limit_reason({"fields": {"value": object()}}),
            "payload-not-json",
        )

    def test_coalesced_field_growth_is_bounded_without_replacing_existing_work(self) -> None:
        queue = FastPublicationQueue()
        initial = publish_evcs_fields_command(
            {f"field_{index}": index for index in range(MAX_PUBLICATION_FIELDS_PER_KEY)},
            priority="live",
        )
        overflow = publish_evcs_fields_command({"overflow": 1}, priority="live")

        self.assertTrue(queue.enqueue(initial).accepted)
        self.assertEqual(queue.enqueue(overflow).reason, "field-limit")
        work = queue.pop_next()
        self.assertIsNotNone(work)
        assert work is not None
        self.assertEqual(len(_fields(work)), MAX_PUBLICATION_FIELDS_PER_KEY)
        self.assertNotIn("overflow", _fields(work))


if __name__ == "__main__":
    unittest.main()

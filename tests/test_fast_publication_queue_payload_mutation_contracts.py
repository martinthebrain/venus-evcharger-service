#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Mutation-sensitive payload, expiry, and metric contracts for the fast queue."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from tests.support.fast_publication_queue import (
    publication_command,
    publication_work,
)
from venus_evcharger.ipc.fast_publication import FastPublicationQueue
from venus_evcharger.ipc.fast_publication_policy import fast_command_id
from venus_evcharger.ipc.fast_publication_work import FastPublicationWork


class FastPublicationPayloadMutationContracts(unittest.TestCase):
    def test_queue_payload_rewrites_transport_metadata_and_keeps_creation_time(self) -> None:
        queue = FastPublicationQueue()
        command = publication_command({"mode": 2})
        merged = {**command, "created_at": "12.5", "id": "old"}
        with (
            patch(
                "venus_evcharger.ipc.fast_publication.merge_publication_payload",
                return_value=merged,
            ) as merge,
            patch(
                "venus_evcharger.ipc.fast_publication.time.time",
                return_value=99.0,
            ),
        ):
            payload = queue._queue_payload(None, command, "evcs:fields")

        merge.assert_called_once_with(None, command)
        self.assertEqual(payload["id"], fast_command_id("evcs:fields"))
        self.assertEqual(payload["created_at"], 12.5)
        self.assertEqual(payload["queue_class"], "local-publish")

    def test_queue_payload_uses_current_time_when_creation_time_is_invalid(self) -> None:
        queue = FastPublicationQueue()
        command = publication_command()
        with (
            patch(
                "venus_evcharger.ipc.fast_publication.merge_publication_payload",
                return_value={**command, "created_at": "invalid"},
            ),
            patch(
                "venus_evcharger.ipc.fast_publication.time.time",
                return_value=99.25,
            ),
        ):
            payload = queue._queue_payload(publication_work(), command, "evcs:fields")

        self.assertEqual(payload["created_at"], 99.25)

    def test_merged_work_has_fieldwise_expiry_and_exact_transport_metadata(self) -> None:
        queue = FastPublicationQueue()
        existing = publication_work(
            {"mode": 1, "energy": 3.0},
            expires_at=30.0,
            retry_at=5.0,
            deferred=True,
        )
        command = publication_command({"mode": 2, "power": 700.0}, order=11)
        merged_payload = {**command, "created_at": None}
        with (
            patch(
                "venus_evcharger.ipc.fast_publication.merge_publication_payload",
                return_value=merged_payload,
            ) as merge,
            patch(
                "venus_evcharger.ipc.fast_publication.time.monotonic",
                return_value=20.0,
            ),
            patch(
                "venus_evcharger.ipc.fast_publication.time.time",
                return_value=100.0,
            ),
        ):
            work = queue._merged_work(
                existing,
                command,
                "evcs:fields",
                accepted_fields=("mode", "power"),
                ttl=12.0,
            )

        merge.assert_called_once_with(
            existing.command,
            command,
            accepted_fields=("mode", "power"),
        )
        self.assertEqual(work.command["id"], fast_command_id("evcs:fields"))
        self.assertEqual(work.command["created_at"], 100.0)
        self.assertEqual(work.command["queue_class"], "local-publish")
        self.assertEqual(
            work.field_expires_at,
            {"mode": 32.0, "energy": 30.0, "power": 32.0},
        )
        self.assertEqual(work.retry_at, 0.0)
        self.assertFalse(work.deferred)

    def test_merged_work_preserves_valid_creation_time(self) -> None:
        queue = FastPublicationQueue()
        command = publication_command()
        with (
            patch(
                "venus_evcharger.ipc.fast_publication.merge_publication_payload",
                return_value={**command, "created_at": "12.5"},
            ),
            patch(
                "venus_evcharger.ipc.fast_publication.time.monotonic",
                return_value=20.0,
            ),
            patch(
                "venus_evcharger.ipc.fast_publication.time.time",
                return_value=100.0,
            ),
        ):
            work = queue._merged_work(
                None,
                command,
                "evcs:fields",
                accepted_fields=("mode",),
                ttl=12.0,
            )

        self.assertEqual(work.command["created_at"], 12.5)


class FastPublicationExpiryAndMetricsMutationContracts(unittest.TestCase):
    def test_snapshot_prunes_at_monotonic_now_and_combines_exact_metrics(self) -> None:
        queue = FastPublicationQueue(capacity=4)
        queue._commands["one"] = publication_work({"mode": 1, "power": 2.0})
        queue._commands["two"] = publication_work({"energy": 3.0}, key="two")
        with (
            patch.object(
                queue._metrics,
                "snapshot",
                return_value={
                    "counts": {"accepted": 2},
                    "last_success_sample_at": 4.0,
                    "successes_since_sample": 3,
                },
            ),
            patch.object(
                queue._ordering,
                "snapshot",
                return_value={
                    "order_state_entries": 3,
                    "order_state_persistent": True,
                },
            ),
            patch.object(queue, "_prune_expired") as prune,
            patch(
                "venus_evcharger.ipc.fast_publication.time.monotonic",
                return_value=12.0,
            ),
        ):
            snapshot = queue.snapshot()

        prune.assert_called_once_with(12.0)
        self.assertEqual(
            snapshot,
            {
                "capacity": 4,
                "depth": 2,
                "field_depth": 3,
                "counts": {"accepted": 2},
                "last_success_sample_at": 4.0,
                "successes_since_sample": 3,
                "order_state_entries": 3,
                "order_state_persistent": True,
            },
        )

    def test_partial_expiry_releases_only_expired_orders_and_keeps_lease_state(self) -> None:
        queue = FastPublicationQueue()
        work = FastPublicationWork(
            publication_command({"mode": 1, "energy": 3.0, "power": 500.0}, order=10),
            {"mode": 9.0, "energy": 8.0, "power": 20.0},
            retry_at=15.0,
            deferred=True,
        )
        retained = FastPublicationWork(
            publication_command({"power": 500.0}, order=10),
            {"power": 20.0},
            retry_at=15.0,
            deferred=True,
        )
        queue._commands["evcs:fields"] = work
        with patch.object(queue._ordering, "release_fields") as release:
            queue._retain_unexpired_fields(
                "evcs:fields",
                work,
                retained,
            )

        release.assert_called_once_with(
            "evcs:fields",
            {"mode": 10, "energy": 10},
        )
        self.assertIs(queue._commands["evcs:fields"], retained)
        self.assertEqual(
            queue._metrics.snapshot()["counts"],
            {"fields_expired": 2},
        )

    def test_removing_fields_preserves_remaining_expiry_retry_and_deferred_state(self) -> None:
        queue = FastPublicationQueue()
        work = FastPublicationWork(
            publication_command({"mode": 1, "power": 500.0}, order=10),
            {"mode": 20.0, "power": 30.0},
            retry_at=12.0,
            deferred=True,
        )
        queue._commands["evcs:fields"] = work

        queue._remove_fast_fields("evcs:fields", ("mode",))

        remaining = queue._commands["evcs:fields"]
        self.assertEqual(remaining.command["fields"], {"power": 500.0})
        self.assertEqual(remaining.field_expires_at, {"power": 30.0})
        self.assertEqual(remaining.retry_at, 12.0)
        self.assertTrue(remaining.deferred)
        self.assertEqual(
            queue._metrics.snapshot()["counts"],
            {"fast_fields_superseded": 1},
        )

    def test_removing_all_fields_deletes_queue_entry_and_counts_each_field(self) -> None:
        queue = FastPublicationQueue()
        queue._commands["evcs:fields"] = publication_work({"mode": 1, "power": 500.0})

        queue._remove_fast_fields("evcs:fields", ("mode", "power"))

        self.assertEqual(len(queue), 0)
        self.assertEqual(
            queue._metrics.snapshot()["counts"],
            {"fast_fields_superseded": 2},
        )

    def test_full_expiry_removes_work_releases_claim_and_counts_once(self) -> None:
        queue = FastPublicationQueue()
        work = publication_work()
        queue._commands["evcs:fields"] = work
        with patch.object(queue._ordering, "release_fast") as release:
            queue._drop_expired_work("evcs:fields", work)

        self.assertEqual(len(queue), 0)
        release.assert_called_once_with(work)
        self.assertEqual(queue.snapshot()["counts"], {"expired": 1})

    def test_increment_honors_amount_and_active_fields_include_every_lease(self) -> None:
        queue = FastPublicationQueue()
        queue._commands["evcs:fields"] = publication_work({"mode": 1, "power": 2.0})
        queue._commands["grid"] = publication_work({"power": 3.0}, key="grid")

        queue._increment("fields_superseded", 3)

        self.assertEqual(
            queue._active_fields(),
            {
                ("evcs:fields", "mode"),
                ("evcs:fields", "power"),
                ("grid", "power"),
            },
        )
        self.assertEqual(
            queue.snapshot()["counts"],
            {"fields_superseded": 3},
        )


if __name__ == "__main__":
    unittest.main()

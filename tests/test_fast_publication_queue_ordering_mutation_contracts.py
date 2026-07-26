#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Mutation-sensitive ordering and retry contracts for the fast queue."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from tests.support.fast_publication_queue import (
    publication_command,
    publication_work,
)
from venus_evcharger.ipc.fast_publication import (
    FAST_PUBLICATION_RETRY_SECONDS,
    FastPublicationQueue,
)
from venus_evcharger.ipc.publication_order import (
    PublicationFieldClaim,
    PublicationOrderCapacityError,
    PublicationOrderPendingFastError,
)


class FastPublicationOrderingMutationContracts(unittest.TestCase):
    def test_prepare_durable_prunes_then_forwards_stripped_key_and_active_work(self) -> None:
        queue = FastPublicationQueue()
        queued = publication_work({"mode": 1}, key="evcs:fields")
        queue._commands["evcs:fields"] = queued
        command = publication_command({"power": 500.0}, order=11, key="  evcs:fields  ")
        claim = PublicationFieldClaim("accepted", ("power",))
        with (
            patch.object(
                queue._ordering,
                "prepare_durable",
                return_value=claim,
            ) as prepare,
            patch.object(queue, "_prune_expired") as prune,
            patch(
                "venus_evcharger.ipc.fast_publication.time.monotonic",
                return_value=22.0,
            ),
        ):
            prepared = queue.prepare_durable(command)

        prune.assert_called_once_with(22.0)
        prepare.assert_called_once_with(
            "evcs:fields",
            command,
            active_fields={("evcs:fields", "mode")},
            queued_work=queued,
        )
        self.assertIsNotNone(prepared)
        assert prepared is not None
        self.assertEqual(prepared["fields"], {"power": 500.0})

    def test_durable_defer_reasons_are_counted_and_reraised(self) -> None:
        command = publication_command()
        cases = (
            (
                PublicationOrderPendingFastError("pending"),
                "durable_waiting_for_fast",
            ),
            (
                PublicationOrderCapacityError("full"),
                "durable_order_history_full",
            ),
        )
        for error, metric in cases:
            with self.subTest(metric=metric):
                queue = FastPublicationQueue()
                with (
                    patch.object(
                        queue._ordering,
                        "prepare_durable",
                        side_effect=error,
                    ),
                    self.assertRaises(type(error)),
                ):
                    queue._durable_claim("evcs:fields", command)
                self.assertEqual(queue.snapshot()["counts"], {metric: 1})

    def test_missing_durable_key_is_forwarded_as_empty_string(self) -> None:
        queue = FastPublicationQueue()
        command = publication_command()
        del command["coalesce_key"]
        claim = PublicationFieldClaim("accepted", ("mode",))
        with patch.object(
            queue._ordering,
            "prepare_durable",
            return_value=claim,
        ) as prepare:
            prepared = queue.prepare_durable(command)

        prepare.assert_called_once_with(
            "",
            command,
            active_fields=set(),
            queued_work=None,
        )
        self.assertIsNotNone(prepared)

    def test_durable_payload_is_filtered_and_reports_each_superseded_field(self) -> None:
        queue = FastPublicationQueue()
        command = publication_command({"mode": 2, "power": 700.0, "energy": 4.0})
        partial = PublicationFieldClaim(
            "accepted",
            ("power",),
            ("mode", "energy"),
        )

        prepared = queue._prepared_durable_payload(command, partial)
        rejected = queue._prepared_durable_payload(
            command,
            PublicationFieldClaim("superseded"),
        )

        self.assertIsNotNone(prepared)
        assert prepared is not None
        self.assertEqual(prepared["fields"], {"power": 700.0})
        self.assertIsNone(rejected)
        self.assertEqual(
            queue.snapshot()["counts"],
            {
                "durable_fields_superseded": 2,
                "durable_superseded": 1,
            },
        )

    def test_only_applied_durable_work_is_confirmed_and_removes_fast_fields(self) -> None:
        queue = FastPublicationQueue()
        command = publication_command({"mode": 2, "power": 700.0})
        with (
            patch.object(
                queue._ordering,
                "confirm_durable_applied",
                return_value=False,
            ) as confirm,
            patch.object(queue, "_remove_fast_fields") as remove,
        ):
            for state in ("deferred", "dropped", ""):
                queue.record_durable_outcome(command, state)
            queue.record_durable_outcome(command, "applied")

        confirm.assert_called_once_with(
            command,
            active_fields=set(),
        )
        remove.assert_called_once_with("evcs:fields", ("mode", "power"))
        self.assertEqual(
            queue.snapshot()["counts"],
            {"order_state_write_errors": 1},
        )

    def test_applied_durable_work_without_key_removes_from_empty_lane(self) -> None:
        queue = FastPublicationQueue()
        command = publication_command()
        del command["coalesce_key"]
        with (
            patch.object(
                queue._ordering,
                "confirm_durable_applied",
                return_value=True,
            ),
            patch.object(queue, "_remove_fast_fields") as remove,
        ):
            queue.record_durable_outcome(command, "applied")

        remove.assert_called_once_with("", ("mode",))

    def test_fast_outcomes_confirm_release_or_leave_claim_untouched(self) -> None:
        queue = FastPublicationQueue()
        work = publication_work()
        unknown_work = publication_work({"power": 700.0}, order=11)
        with (
            patch.object(
                queue._ordering,
                "confirm_fast_applied",
                return_value=False,
            ) as confirm,
            patch.object(queue._ordering, "release_fast") as release,
        ):
            self.assertTrue(queue.record_outcome(work, "applied", now=10.0))
            self.assertTrue(queue.record_outcome(work, "dropped", now=10.1))
            self.assertTrue(queue.record_outcome(unknown_work, "", now=10.2))

        confirm.assert_called_once_with(work)
        release.assert_called_once_with(work)
        self.assertEqual(
            queue.snapshot()["counts"],
            {
                "applied": 1,
                "applied_samples": 1,
                "dropped": 1,
                "order_state_write_errors": 1,
                "unknown": 1,
            },
        )


class FastPublicationRequeueMutationContracts(unittest.TestCase):
    def test_expired_requeue_releases_claim_and_counts_drop(self) -> None:
        queue = FastPublicationQueue()
        work = publication_work()
        with (
            patch.object(queue._ordering, "release_fast") as release,
            patch(
                "venus_evcharger.ipc.fast_publication.fast_requeue_candidate",
                return_value=None,
            ) as candidate,
        ):
            queue.requeue(work, deferred=True, now=8.0)

        candidate.assert_called_once_with(work, 8.0)
        release.assert_called_once_with(work)
        self.assertEqual(len(queue), 0)
        self.assertEqual(queue.snapshot()["counts"], {"expired": 1})

    def test_deferred_requeue_merges_at_retry_deadline_and_moves_key_to_tail(self) -> None:
        queue = FastPublicationQueue()
        in_flight = publication_work({"mode": 1})
        retained = publication_work({"mode": 1}, retry_at=2.0)
        queued = publication_work({"power": 500.0}, order=11)
        merged = publication_work(
            {"mode": 1, "power": 500.0},
            order=11,
            retry_at=9.25,
            deferred=True,
        )
        queue._commands["evcs:fields"] = queued
        queue._commands["later"] = publication_work(key="later")
        with (
            patch(
                "venus_evcharger.ipc.fast_publication.fast_requeue_candidate",
                return_value=("evcs:fields", retained),
            ),
            patch(
                "venus_evcharger.ipc.fast_publication.merge_fast_work",
                return_value=merged,
            ) as merge,
        ):
            queue.requeue(in_flight, deferred=True, now=9.0)

        merge.assert_called_once_with(
            retained,
            queued,
            retry_at=9.0 + FAST_PUBLICATION_RETRY_SECONDS,
            deferred=True,
        )
        self.assertEqual(tuple(queue._commands), ("later", "evcs:fields"))
        self.assertIs(queue._commands["evcs:fields"], merged)

    def test_immediate_requeue_uses_current_time_without_retry_delay(self) -> None:
        queue = FastPublicationQueue()
        work = publication_work()
        retained = publication_work()
        with (
            patch(
                "venus_evcharger.ipc.fast_publication.fast_requeue_candidate",
                return_value=("evcs:fields", retained),
            ),
            patch(
                "venus_evcharger.ipc.fast_publication.merge_fast_work",
                return_value=retained,
            ) as merge,
        ):
            queue.requeue(work, deferred=False, now=4.5)

        merge.assert_called_once_with(
            retained,
            None,
            retry_at=4.5,
            deferred=False,
        )


if __name__ == "__main__":
    unittest.main()

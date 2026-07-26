# SPDX-License-Identifier: GPL-3.0-or-later
"""Focused mutation contracts for the fast-publication ordering facade."""

from __future__ import annotations

import unittest
from collections.abc import Collection
from unittest.mock import Mock, patch

from venus_evcharger.ipc import fast_publication_ordering
from venus_evcharger.ipc.command_types import CommandPayload
from venus_evcharger.ipc.fast_publication_work import FastPublicationWork
from venus_evcharger.ipc.publication_order import (
    PUBLICATION_FIELD_ORDERS_FIELD,
    PUBLICATION_ORDER_FIELD,
    PublicationFieldClaim,
    PublicationFieldKey,
    PublicationOrderCapacityError,
    PublicationOrderHistory,
    PublicationOrderPendingFastError,
)


def _command(
    *,
    key: object = "  evcs  ",
    mode_order: int = 11,
    power_order: int = 12,
) -> CommandPayload:
    return {
        "kind": "publish_evcs_fields",
        "coalesce_key": key,
        "fields": {"mode": 2, "power": 2300.0},
        PUBLICATION_ORDER_FIELD: 10,
        PUBLICATION_FIELD_ORDERS_FIELD: {
            "mode": mode_order,
            "power": power_order,
        },
    }


def _work(command: CommandPayload | None = None) -> FastPublicationWork:
    return FastPublicationWork(
        command or _command(),
        {"mode": 100.0, "power": 100.0},
    )


class FastPublicationOrderingMutationContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.history = Mock(spec=PublicationOrderHistory)
        self.history.claim_fast.return_value = PublicationFieldClaim(
            "accepted",
            ("mode", "power"),
        )
        self.history.claim_durable.return_value = PublicationFieldClaim(
            "accepted",
            ("mode", "power"),
        )
        self.history.confirm_fast_applied.return_value = True
        self.history.confirm_durable_applied.return_value = True
        self.history.snapshot.return_value = {
            "ordered_keys": 1,
            "ordered_fields": 2,
        }
        with patch.object(
            fast_publication_ordering,
            "PublicationOrderHistory",
            return_value=self.history,
        ) as history_type:
            self.ordering = fast_publication_ordering.FastPublicationOrdering(
                capacity=17,
                retention_seconds=23.5,
                state_path="/run/order-state.json",
            )
        history_type.assert_called_once_with(
            capacity=17,
            retention_seconds=23.5,
            state_path="/run/order-state.json",
        )

    @staticmethod
    def _active_fields() -> Collection[PublicationFieldKey]:
        return {("evcs", "mode"), ("evcs", "power")}

    def test_fast_claim_delegates_exact_key_orders_and_active_fields(self) -> None:
        command = _command()
        active_fields = self._active_fields()

        claim = self.ordering.claim_fast(
            "evcs",
            command,
            active_fields=active_fields,
        )

        self.assertIs(claim, self.history.claim_fast.return_value)
        self.history.claim_fast.assert_called_once_with(
            "evcs",
            {"mode": 11, "power": 12},
            active_fields=active_fields,
        )

    def test_durable_claim_waits_for_equal_or_newer_fast_field(self) -> None:
        durable = _command(mode_order=11, power_order=13)
        queued = _work(_command(mode_order=11, power_order=12))

        with self.assertRaises(PublicationOrderPendingFastError) as raised:
            self.ordering.prepare_durable(
                "evcs",
                durable,
                active_fields=self._active_fields(),
                queued_work=queued,
            )

        self.assertEqual(
            str(raised.exception),
            "publication fallback is waiting for volatile work",
        )
        self.history.claim_durable.assert_not_called()

    def test_durable_claim_delegates_when_fast_work_is_strictly_older(self) -> None:
        durable = _command(mode_order=13, power_order=14)
        queued = _work(_command(mode_order=11, power_order=12))
        active_fields = self._active_fields()

        claim = self.ordering.prepare_durable(
            "evcs",
            durable,
            active_fields=active_fields,
            queued_work=queued,
        )

        self.assertIs(claim, self.history.claim_durable.return_value)
        self.history.claim_durable.assert_called_once_with(
            "evcs",
            {"mode": 13, "power": 14},
            active_fields=active_fields,
        )

    def test_full_durable_claim_has_stable_deferred_error_contract(self) -> None:
        self.history.claim_durable.return_value = PublicationFieldClaim("full")

        with self.assertRaises(PublicationOrderCapacityError) as raised:
            self.ordering.prepare_durable(
                "evcs",
                _command(),
                active_fields=self._active_fields(),
                queued_work=None,
            )

        self.assertEqual(
            str(raised.exception),
            "publication order history is full",
        )

    def test_fast_confirmation_uses_normalized_command_key_and_field_orders(self) -> None:
        command = _command(key="\t  evcs  \n")

        confirmed = self.ordering.confirm_fast_applied(_work(command))

        self.assertIs(confirmed, True)
        self.history.confirm_fast_applied.assert_called_once_with(
            "evcs",
            {"mode": 11, "power": 12},
        )

    def test_missing_or_falsy_command_key_normalizes_to_empty_key(self) -> None:
        for raw_key in (None, "", 0, False):
            with self.subTest(raw_key=raw_key):
                self.history.confirm_fast_applied.reset_mock()

                self.ordering.confirm_fast_applied(_work(_command(key=raw_key)))

                self.history.confirm_fast_applied.assert_called_once_with(
                    "",
                    {"mode": 11, "power": 12},
                )

    def test_durable_confirmation_returns_persistence_result_and_exact_arguments(
        self,
    ) -> None:
        self.history.confirm_durable_applied.return_value = False
        command = _command(key="\t  evcs  \n")
        active_fields = self._active_fields()

        confirmed = self.ordering.confirm_durable_applied(
            command,
            active_fields=active_fields,
        )

        self.assertIs(confirmed, False)
        self.history.confirm_durable_applied.assert_called_once_with(
            "evcs",
            {"mode": 11, "power": 12},
            active_fields=active_fields,
        )

    def test_fast_release_uses_the_same_normalized_key_and_orders_as_claim(self) -> None:
        command = _command(key="\t  evcs  \n")

        self.ordering.release_fast(_work(command))

        self.history.release_fast.assert_called_once_with(
            "evcs",
            {"mode": 11, "power": 12},
        )

    def test_explicit_field_release_forwards_key_and_mapping_unchanged(self) -> None:
        orders = {"mode": 21, "power": 22}

        self.ordering.release_fields("explicit-key", orders)

        self.history.release_fast.assert_called_once_with("explicit-key", orders)

    def test_snapshot_is_the_history_snapshot_without_facade_reinterpretation(
        self,
    ) -> None:
        snapshot = self.ordering.snapshot()

        self.assertIs(snapshot, self.history.snapshot.return_value)
        self.history.snapshot.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()

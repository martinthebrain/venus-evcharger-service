#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for incremental DBus read aggregation state."""

from __future__ import annotations

import unittest

from venus_evcharger.dbus_adapter.read.aggregate import AggregateState, AggregateStore


class AggregateStateContractTests(unittest.TestCase):
    def test_complete_uses_member_count_as_exclusive_work_boundary(self) -> None:
        state = AggregateState(("sum", ()), empty_confidence=0.25, index=1)
        self.assertFalse(state.complete(2))
        state.index = 2
        self.assertTrue(state.complete(2))
        state.index = 3
        self.assertTrue(state.complete(2))

    def test_store_lifecycle_is_keyed_and_idempotent(self) -> None:
        store = AggregateStore()
        self.assertFalse(store.has_pending())
        store.discard("missing")
        self.assertFalse(store.has_pending())

        signature = ("sum", (("svc", "/A"),))
        state = store.state_for("power", signature, 0.4)
        self.assertTrue(store.has_pending())
        self.assertIs(store.state_for("power", signature, 0.9), state)
        self.assertEqual(state.empty_confidence, 0.4)

        store.discard("power")
        self.assertFalse(store.has_pending())
        store.discard("power")
        self.assertFalse(store.has_pending())

    def test_changed_signature_replaces_state_and_resets_progress(self) -> None:
        store = AggregateStore()
        original = store.state_for("power", ("sum", (("svc", "/A"),)), 0.4)
        original.index = 1
        original.total = 12.0
        replacement_signature = ("sum", (("svc", "/B"),))
        replacement = store.state_for("power", replacement_signature, 0.7)

        self.assertIsNot(replacement, original)
        self.assertEqual(replacement.signature, replacement_signature)
        self.assertEqual(replacement.empty_confidence, 0.7)
        self.assertEqual(replacement.index, 0)
        self.assertEqual(replacement.total, 0.0)

    def test_signature_members_require_existing_matching_state(self) -> None:
        store = AggregateStore()
        self.assertIsNone(store.signature_members("missing", "pv-total"))
        store.state_for(
            "pv_power_w",
            ("pv-total", (("pv.ac", "/Ac/Power"), ("system", "/Dc/Pv/Power"))),
            0.2,
        )
        self.assertEqual(
            store.signature_members("pv_power_w", "pv-total"),
            [("pv.ac", "/Ac/Power"), ("system", "/Dc/Pv/Power")],
        )
        self.assertIsNone(store.signature_members("pv_power_w", "sum"))


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: GPL-3.0-or-later
"""Deep-copy and restore contracts for reversible control state."""

from __future__ import annotations

import unittest
from collections import deque
from types import SimpleNamespace
from unittest.mock import call, patch

from venus_evcharger.controllers import write_snapshot as snapshot_module


class TestWriteSnapshotCaptureContracts(unittest.TestCase):
    def test_scalar_deque_and_mapping_capture_preserve_presence_and_copy_depth(self) -> None:
        nested = {"items": [1]}
        svc = SimpleNamespace(
            scalar={"nested": [1]},
            first=deque([1]),
            second=deque([2]),
            ignored="not-a-dict",
            mapping=nested,
            mapping_two={"nested": {"value": 2}},
        )

        attrs = snapshot_module._snapshot_attrs(svc, ("scalar", "missing"))
        deques = snapshot_module._snapshot_deques(svc, ("missing", "first", "second"))
        mappings = snapshot_module._snapshot_mappings(
            svc,
            ("missing", "ignored", "mapping", "mapping_two"),
        )
        svc.scalar["nested"].append(2)
        nested["items"].append(2)
        svc.mapping_two["nested"]["value"] = 3

        self.assertEqual(attrs, {"scalar": {"nested": [1]}})
        self.assertEqual(deques, {"first": deque([1]), "second": deque([2])})
        self.assertIsNot(deques["first"], svc.first)
        self.assertEqual(mappings["mapping"], {"items": [1]})
        self.assertEqual(mappings["mapping_two"], {"nested": {"value": 2}})

    def test_capture_write_state_dispatches_only_declared_semantic_categories(self) -> None:
        svc = SimpleNamespace()
        with (
            patch.object(snapshot_module, "_snapshot_attrs", side_effect=({"a": 1}, {"v": 2})) as attrs,
            patch.object(snapshot_module, "_snapshot_deques", return_value={"d": deque([1])}) as deques,
            patch.object(snapshot_module, "_snapshot_mappings", return_value={"m": {"x": 1}}) as mappings,
        ):
            result = snapshot_module.capture_write_state(
                svc,
                attrs=("a",),
                deque_attrs=("d",),
                value_attrs=("v",),
                mapping_attrs=("m",),
            )

        self.assertEqual(attrs.call_args_list, [call(svc, ("a",)), call(svc, ("v",))])
        deques.assert_called_once_with(svc, ("d",))
        mappings.assert_called_once_with(svc, ("m",))
        self.assertEqual(
            result,
            {
                "attrs": {"a": 1},
                "deques": {"d": deque([1])},
                "values": {"v": 2},
                "mappings": {"m": {"x": 1}},
            },
        )


class TestWriteSnapshotRestoreContracts(unittest.TestCase):
    def test_deque_and_mapping_restore_updates_existing_and_creates_missing_values(self) -> None:
        svc = SimpleNamespace(
            existing_deque=deque([0]),
            existing_mapping={"old": 0},
        )
        existing_deque = svc.existing_deque
        existing_mapping = svc.existing_mapping
        saved_mapping = {"nested": [1]}

        snapshot_module._restore_deques(
            svc,
            {"existing_deque": deque([1]), "new_deque": deque([2])},
        )
        snapshot_module._restore_mappings(
            svc,
            {"existing_mapping": {"new": 1}, "new_mapping": saved_mapping},
        )
        saved_mapping["nested"].append(2)

        self.assertIs(svc.existing_deque, existing_deque)
        self.assertIs(svc.existing_mapping, existing_mapping)
        self.assertEqual(svc.existing_deque, deque([1]))
        self.assertEqual(svc.new_deque, deque([2]))
        self.assertEqual(svc.existing_mapping, {"new": 1})
        self.assertEqual(svc.new_mapping, {"nested": [1]})

    def test_restore_write_state_restores_every_semantic_group(self) -> None:
        svc = SimpleNamespace(
            existing_deque=deque([0]),
            existing_mapping={"old": 0},
        )
        snapshot = {
            "attrs": {"mode": 1},
            "values": {"smoothed_power": 2.5},
            "deques": {"existing_deque": deque([3])},
            "mappings": {"existing_mapping": {"new": 4}},
        }

        snapshot_module.restore_write_state(svc, snapshot)

        self.assertEqual(svc.mode, 1)
        self.assertEqual(svc.smoothed_power, 2.5)
        self.assertEqual(svc.existing_deque, deque([3]))
        self.assertEqual(svc.existing_mapping, {"new": 4})


if __name__ == "__main__":
    unittest.main()

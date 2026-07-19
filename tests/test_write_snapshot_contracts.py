# SPDX-License-Identifier: GPL-3.0-or-later
"""Deep-copy and fallback contracts for DBus write snapshots."""

from __future__ import annotations

import unittest
from collections import deque
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from venus_evcharger.controllers import write_snapshot as snapshot_module


class _ReadBus:
    def __init__(self, values: dict[str, object], failing: set[str] | None = None) -> None:
        self.values = values
        self.failing = failing or set()

    def __getitem__(self, path: str) -> object:
        if path in self.failing:
            raise KeyError(path)
        return self.values[path]


class _WriteBus(dict[str, object]):
    def __init__(self, failing: set[str]) -> None:
        super().__init__()
        self.failing = failing

    def __setitem__(self, path: str, value: object) -> None:
        if path in self.failing:
            raise KeyError(path)
        super().__setitem__(path, value)


class TestWriteSnapshotCaptureContracts(unittest.TestCase):
    def test_scalar_deque_and_mapping_capture_preserve_presence_and_copy_depth(self) -> None:
        nested = {"items": [1]}
        svc = SimpleNamespace(
            scalar=1,
            first=deque([1]),
            second=deque([2]),
            ignored="not-a-dict",
            mapping=nested,
            mapping_two={"nested": {"value": 2}},
        )
        self.assertEqual(snapshot_module._snapshot_attrs(svc, ("scalar", "missing")), {"scalar": 1})
        deques = snapshot_module._snapshot_deques(svc, ("missing", "first", "second"))
        self.assertEqual(deques, {"first": deque([1]), "second": deque([2])})
        self.assertIsNot(deques["first"], svc.first)

        mappings = snapshot_module._snapshot_mappings(
            svc,
            ("missing", "ignored", "mapping", "mapping_two"),
        )
        nested["items"].append(2)
        svc.mapping_two["nested"]["value"] = 3
        self.assertEqual(mappings["mapping"], {"items": [1]})
        self.assertEqual(mappings["mapping_two"], {"nested": {"value": 2}})

    def test_publish_state_capture_skips_invalid_entries_and_deep_copies_values(self) -> None:
        nested = {"items": [1]}
        svc = SimpleNamespace(
            _dbus_publish_state={
                "/Invalid": "value",
                "/MissingValue": {},
                "/A": {"value": nested},
                "/B": {"value": 2},
            }
        )
        captured = snapshot_module._snapshot_publish_state_paths(
            svc,
            ("/Invalid", "/MissingValue", "/A", "/B"),
        )
        nested["items"].append(2)
        self.assertEqual(captured, {"/A": {"items": [1]}, "/B": 2})

    def test_direct_snapshot_permission_obeys_callable_guard(self) -> None:
        self.assertTrue(snapshot_module._direct_dbus_snapshot_allowed(SimpleNamespace()))
        allowed = MagicMock(return_value=True)
        denied = MagicMock(return_value=False)
        self.assertTrue(
            snapshot_module._direct_dbus_snapshot_allowed(
                SimpleNamespace(_dbus_publish_direct_allowed=allowed)
            )
        )
        self.assertFalse(
            snapshot_module._direct_dbus_snapshot_allowed(
                SimpleNamespace(_dbus_publish_direct_allowed=denied)
            )
        )
        allowed.assert_called_once_with()
        denied.assert_called_once_with()

    def test_combined_snapshot_uses_cache_first_and_direct_reads_only_when_allowed(self) -> None:
        svc = SimpleNamespace()
        direct_inputs: list[tuple[object, tuple[str, ...], dict[str, object]]] = []

        def capture_direct(
            direct_svc: object,
            paths: tuple[str, ...],
            captured: dict[str, object],
        ) -> dict[str, object]:
            direct_inputs.append((direct_svc, paths, dict(captured)))
            return {"/B": 2}

        with (
            patch.object(snapshot_module, "_snapshot_publish_state_paths", return_value={"/A": 1}) as cached,
            patch.object(snapshot_module, "_direct_dbus_snapshot_allowed", return_value=True) as allowed,
            patch.object(snapshot_module, "_snapshot_direct_dbus_paths", side_effect=capture_direct),
        ):
            self.assertEqual(snapshot_module._snapshot_dbus_paths(svc, ("/A", "/B")), {"/A": 1, "/B": 2})
        cached.assert_called_once_with(svc, ("/A", "/B"))
        allowed.assert_called_once_with(svc)
        self.assertEqual(direct_inputs, [(svc, ("/A", "/B"), {"/A": 1})])

        with (
            patch.object(snapshot_module, "_snapshot_publish_state_paths", return_value={"/A": 1}),
            patch.object(snapshot_module, "_direct_dbus_snapshot_allowed", return_value=False),
            patch.object(snapshot_module, "_snapshot_direct_dbus_paths") as direct,
        ):
            self.assertEqual(snapshot_module._snapshot_dbus_paths(svc, ("/A",)), {"/A": 1})
        direct.assert_not_called()

    def test_direct_snapshot_skips_cached_and_failed_paths_but_continues(self) -> None:
        bus = _ReadBus({"/A": 1, "/C": 3}, failing={"/B"})
        svc = SimpleNamespace(_dbusservice=bus)
        self.assertEqual(
            snapshot_module._snapshot_direct_dbus_paths(
                svc,
                ("/Cached", "/B", "/A", "/C"),
                {"/Cached": 9},
            ),
            {"/A": 1, "/C": 3},
        )

    def test_capture_write_state_dispatches_all_categories_with_exact_arguments(self) -> None:
        svc = SimpleNamespace()
        with (
            patch.object(snapshot_module, "_snapshot_attrs", side_effect=({"a": 1}, {"v": 2})) as attrs,
            patch.object(snapshot_module, "_snapshot_deques", return_value={"d": deque([1])}) as deques,
            patch.object(snapshot_module, "_snapshot_mappings", return_value={"m": {"x": 1}}) as mappings,
            patch.object(snapshot_module, "_snapshot_dbus_paths", return_value={"/A": 1}) as paths,
        ):
            result = snapshot_module.capture_write_state(
                svc,
                attrs=("a",),
                deque_attrs=("d",),
                value_attrs=("v",),
                mapping_attrs=("m",),
                dbus_paths=("/A",),
            )
        self.assertEqual(attrs.call_args_list, [call(svc, ("a",)), call(svc, ("v",))])
        deques.assert_called_once_with(svc, ("d",))
        mappings.assert_called_once_with(svc, ("m",))
        paths.assert_called_once_with(svc, ("/A",))
        self.assertEqual(
            result,
            {
                "attrs": {"a": 1},
                "deques": {"d": deque([1])},
                "values": {"v": 2},
                "mappings": {"m": {"x": 1}},
                "dbus_paths": {"/A": 1},
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

    def test_queue_restore_uses_service_time_and_reports_success(self) -> None:
        enqueue = MagicMock()
        now = MagicMock(return_value=25.0)
        svc = SimpleNamespace(_enqueue_dbus_publish_values=enqueue, time_now=now)
        self.assertTrue(snapshot_module._restore_dbus_paths_via_queue(svc, {"/A": 1, "/B": 2}))
        now.assert_called_once_with()
        enqueue.assert_called_once_with([("/A", 1), ("/B", 2)], 25.0)

        with patch.object(snapshot_module.time, "time", return_value=30.0):
            fallback = SimpleNamespace(_enqueue_dbus_publish_values=enqueue)
            self.assertTrue(snapshot_module._restore_dbus_paths_via_queue(fallback, {"/C": 3}))
        enqueue.assert_called_with([("/C", 3)], 30.0)

    def test_direct_restore_continues_after_failed_path(self) -> None:
        snapshot_module._restore_dbus_paths_direct(SimpleNamespace(), {"/A": 1})
        bus = _WriteBus(failing={"/B"})
        snapshot_module._restore_dbus_paths_direct(
            SimpleNamespace(_dbusservice=bus),
            {"/A": 1, "/B": 2, "/C": 3},
        )
        self.assertEqual(bus, {"/A": 1, "/C": 3})

    def test_restore_write_state_dispatches_empty_dbus_default_and_all_saved_groups(self) -> None:
        svc = SimpleNamespace()
        snapshot = {
            "attrs": {"a": 1},
            "values": {"v": 2},
            "deques": {"d": deque([3])},
            "mappings": {"m": {"x": 4}},
        }
        with (
            patch.object(snapshot_module, "_restore_deques") as deques,
            patch.object(snapshot_module, "_restore_mappings") as mappings,
            patch.object(snapshot_module, "_restore_dbus_paths") as paths,
        ):
            snapshot_module.restore_write_state(svc, snapshot)
        self.assertEqual((svc.a, svc.v), (1, 2))
        deques.assert_called_once_with(svc, snapshot["deques"])
        mappings.assert_called_once_with(svc, snapshot["mappings"])
        paths.assert_called_once_with(svc, {})


if __name__ == "__main__":
    unittest.main()

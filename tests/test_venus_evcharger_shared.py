# SPDX-License-Identifier: GPL-3.0-or-later
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from venus_evcharger.core.shared import (
    _coerce_scalar_numeric,
    _iter_numeric_container_items,
    coerce_dbus_numeric,
    compact_json,
    discovery_cache_valid,
    first_matching_prefixed_service,
    prefixed_service_names,
    sum_dbus_numeric,
    write_text_atomically,
)


class TestShellyWallboxShared(unittest.TestCase):
    def test_numeric_helpers_cover_scalar_container_and_invalid_values(self):
        class _BadIterable:
            def __iter__(self):
                raise TypeError("boom")

        self.assertIsNone(_iter_numeric_container_items("abc"))
        self.assertEqual(_iter_numeric_container_items((1, 2)), [1, 2])
        self.assertIsNone(_iter_numeric_container_items(_BadIterable()))
        self.assertIsNone(_coerce_scalar_numeric(None))
        self.assertIsNone(_coerce_scalar_numeric(True))
        self.assertEqual(_coerce_scalar_numeric("4.5"), 4.5)
        self.assertIsNone(_coerce_scalar_numeric("bad"))

        self.assertEqual(coerce_dbus_numeric(5), 5.0)
        self.assertIsNone(coerce_dbus_numeric(True))
        self.assertIsNone(coerce_dbus_numeric([]))
        self.assertEqual(coerce_dbus_numeric([7]), 7.0)
        self.assertEqual(coerce_dbus_numeric([True]), [True])
        self.assertEqual(coerce_dbus_numeric([1, 2]), [1, 2])
        self.assertEqual(coerce_dbus_numeric(["x"]), ["x"])

        self.assertEqual(sum_dbus_numeric(5), 5.0)
        self.assertIsNone(sum_dbus_numeric(True))
        self.assertEqual(sum_dbus_numeric([1, 2, 3]), 6.0)
        self.assertIsNone(sum_dbus_numeric([True]))
        self.assertEqual(sum_dbus_numeric([[1, 2], [3]]), 6.0)
        self.assertEqual(sum_dbus_numeric(["bad", None]), None)

    def test_discovery_and_grid_helpers_cover_branches(self):
        self.assertTrue(discovery_cache_valid(["svc"], 100.0, 60.0, 120.0))
        self.assertFalse(discovery_cache_valid([], 100.0, 60.0, 120.0))
        self.assertEqual(prefixed_service_names(["a.1", "b.2", "a.3"], "a.", max_services=1, sort_names=True), ["a.1"])
        self.assertEqual(prefixed_service_names(["a.2", "a.1"], "a."), ["a.2", "a.1"])
        self.assertEqual(first_matching_prefixed_service(["x", "a.1", "a.2"], "a.", lambda s: s.endswith("2")), "a.2")
        self.assertIsNone(first_matching_prefixed_service(["x"], "a.", lambda _s: True))

    def test_compact_json_and_write_text_atomically_cover_success_and_cleanup(self):
        self.assertEqual(compact_json({"b": 1, "a": 2}), '{"a":2,"b":1}')

        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "nested", "state.json")
            write_text_atomically(path, "payload")
            with open(path, "r", encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "payload")

            failing_path = os.path.join(temp_dir, "broken", "state.json")
            with patch("os.replace", side_effect=RuntimeError("boom")):
                with self.assertRaises(RuntimeError):
                    write_text_atomically(failing_path, "payload")
            self.assertEqual(list(Path(failing_path).parent.glob("state.json.tmp.*")), [])

            with patch("os.replace", side_effect=RuntimeError("boom")):
                with patch("os.path.exists", return_value=True):
                    with patch("os.unlink", side_effect=OSError("still locked")):
                        with self.assertRaises(RuntimeError):
                            write_text_atomically(failing_path, "payload")

            old_cwd = os.getcwd()
            try:
                os.chdir(temp_dir)
                write_text_atomically("flat.json", "flat")
                with open("flat.json", "r", encoding="utf-8") as handle:
                    self.assertEqual(handle.read(), "flat")

                with patch("os.replace", side_effect=RuntimeError("boom")):
                    with patch("os.path.exists", return_value=False):
                        with self.assertRaises(RuntimeError):
                            write_text_atomically("flat.json", "payload")
            finally:
                os.chdir(old_cwd)

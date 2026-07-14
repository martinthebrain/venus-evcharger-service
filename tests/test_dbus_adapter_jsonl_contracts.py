#!/usr/bin/env python3
"""Behavioral contracts for bounded RAM-backed JSONL diagnostics."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import mock_open, patch

import venus_evcharger.dbus_adapter_jsonl as jsonl


class DbusAdapterJsonlContractTests(unittest.TestCase):
    def test_ensure_parent_dir_creates_nested_directory_and_allows_plain_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            nested = Path(temp_dir) / "one" / "two" / "events.jsonl"
            jsonl.ensure_parent_dir(str(nested))
            self.assertTrue(nested.parent.is_dir())
        with patch.object(jsonl.os, "makedirs") as makedirs:
            jsonl.ensure_parent_dir("events.jsonl")
        makedirs.assert_not_called()

    def test_append_jsonl_writes_compact_complete_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "nested" / "events.jsonl"
            jsonl.append_jsonl(str(path), {"idx": 1, "text": "first"}, max_bytes=0)
            jsonl.append_jsonl(str(path), {"idx": 2, "text": "second"}, max_bytes=0)
            raw = path.read_text(encoding="utf-8")
            self.assertTrue(raw.endswith("\n"))
            self.assertNotIn(": ", raw)
            self.assertEqual([json.loads(line) for line in raw.splitlines()], [
                {"idx": 1, "text": "first"},
                {"idx": 2, "text": "second"},
            ])

    def test_append_jsonl_uses_explicit_utf8_append_mode_and_retains(self) -> None:
        handle = mock_open()
        with (
            patch("builtins.open", handle),
            patch.object(jsonl, "ensure_parent_dir") as ensure_parent_dir,
            patch.object(jsonl, "retain_jsonl_tail") as retain_jsonl_tail,
        ):
            jsonl.append_jsonl("events.jsonl", {"idx": 1}, max_bytes=123)
        ensure_parent_dir.assert_called_once_with("events.jsonl")
        handle.assert_called_once_with("events.jsonl", "a", encoding="utf-8")
        handle().write.assert_called_once_with('{"idx":1}\n')
        retain_jsonl_tail.assert_called_once_with("events.jsonl", max_bytes=123)

    def test_trim_target_is_three_quarters_with_minimum_one_byte(self) -> None:
        self.assertEqual(jsonl.trim_target_bytes(4), 3)
        self.assertEqual(jsonl.trim_target_bytes(5), 3)
        self.assertEqual(jsonl.trim_target_bytes(1), 1)
        self.assertEqual(jsonl.trim_target_bytes(0), 1)

    def test_drop_partial_line_retains_complete_or_newline_free_data(self) -> None:
        self.assertEqual(jsonl.drop_partial_first_jsonl_line(b"partial"), b"partial")
        self.assertEqual(jsonl.drop_partial_first_jsonl_line(b"half\nwhole\n"), b"whole\n")
        self.assertEqual(jsonl.drop_partial_first_jsonl_line(b"\nwhole\n"), b"whole\n")
        self.assertEqual(jsonl.drop_partial_first_jsonl_line(b""), b"")

    def test_rewrite_tail_preserves_whole_file_when_target_exceeds_size(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "events.jsonl"
            path.write_bytes(b"one\ntwo\n")
            jsonl.rewrite_jsonl_tail(str(path), target_bytes=99, size=8)
            self.assertEqual(path.read_bytes(), b"one\ntwo\n")
            jsonl.rewrite_jsonl_tail(str(path), target_bytes=8, size=8)
            self.assertEqual(path.read_bytes(), b"one\ntwo\n")

    def test_rewrite_tail_drops_partial_first_line_after_seek(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "events.jsonl"
            path.write_bytes(b"first-line\nsecond-line\nthird-line\n")
            size = path.stat().st_size
            jsonl.rewrite_jsonl_tail(str(path), target_bytes=20, size=size)
            self.assertEqual(path.read_bytes(), b"third-line\n")

    def test_retention_ignores_disabled_missing_and_within_limit_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "missing.jsonl"
            jsonl.retain_jsonl_tail(str(missing), max_bytes=1)

            path = Path(temp_dir) / "events.jsonl"
            path.write_bytes(b"one\n")
            for limit in (0, -1, 4, 5):
                with self.subTest(limit=limit):
                    jsonl.retain_jsonl_tail(str(path), max_bytes=limit)
                    self.assertEqual(path.read_bytes(), b"one\n")

            jsonl.retain_jsonl_tail(str(path), max_bytes=1)
            self.assertEqual(path.read_bytes(), b"")

    def test_retention_bounds_file_and_keeps_latest_complete_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "events.jsonl"
            for idx in range(10):
                jsonl.append_jsonl(str(path), {"idx": idx, "payload": "x" * 20}, max_bytes=160)
            lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertLessEqual(path.stat().st_size, 160)
            self.assertEqual(lines[-1]["idx"], 9)
            self.assertGreater(lines[0]["idx"], 0)


if __name__ == "__main__":
    unittest.main()

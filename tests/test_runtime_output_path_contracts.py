#!/usr/bin/env python3
"""Contracts for runtime output paths."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from venus_evcharger.dbus_adapter.jsonl import append_jsonl
from venus_evcharger.runtime.output_path import validated_output_file_path


class _BytesPath(os.PathLike[bytes]):
    def __fspath__(self) -> bytes:
        return b"/tmp/events.jsonl"


class RuntimeOutputPathContractTests(unittest.TestCase):
    def test_absolute_file_path_with_expected_suffix_is_normalized(self) -> None:
        self.assertEqual(
            validated_output_file_path(" /run/venus-evcharger/events.JSONL ", label="Events", suffix=".jsonl"),
            "/run/venus-evcharger/events.JSONL",
        )
        self.assertEqual(
            validated_output_file_path(Path("/tmp/snapshot.json"), label="Snapshot", suffix=".json"),
            "/tmp/snapshot.json",
        )

    def test_implicit_or_non_file_paths_are_rejected(self) -> None:
        for value in (
            None,
            True,
            _BytesPath(),
            "",
            " ",
            "None",
            ".",
            "events.jsonl",
            "/tmp/None",
            "/tmp/events.json",
            "bad\x00.jsonl",
            "/tmp/bad\x00.jsonl",
        ):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError,
                r"Events must be an absolute \.jsonl file path",
            ):
                validated_output_file_path(value, label="Events", suffix=".jsonl")

    def test_invalid_writer_path_cannot_create_artifacts_in_working_directory(self) -> None:
        with tempfile.TemporaryDirectory(dir=".") as temp_dir:
            working_dir = Path(temp_dir)
            relative_working_dir = working_dir.relative_to(Path.cwd())
            with self.assertRaises(ValueError):
                append_jsonl(str(relative_working_dir / "dbus-health-history.jsonl"), {"state": "ok"}, max_bytes=100)
            with self.assertRaises(ValueError):
                append_jsonl(str(relative_working_dir / "None"), {"state": "ok"}, max_bytes=100)
            self.assertEqual(list(working_dir.iterdir()), [])


if __name__ == "__main__":
    unittest.main()

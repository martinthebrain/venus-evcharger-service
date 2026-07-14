# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact JSON persistence boundary contracts."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from venus_evcharger.controllers.state_json import json_object_payload, read_json_object_file


class TestStateJsonContracts(unittest.TestCase):
    def test_object_payload_accepts_only_dicts_with_string_keys_and_copies_them(self) -> None:
        source = {"a": 1, "nested": {"value": True}}
        payload = json_object_payload(source)
        self.assertEqual(payload, source)
        self.assertIsNot(payload, source)
        self.assertIsNone(json_object_payload([]))
        self.assertIsNone(json_object_payload("{}"))
        self.assertIsNone(json_object_payload({1: "invalid"}))
        self.assertEqual(json_object_payload({}), {})

    def test_read_returns_exact_object_and_missing_file_is_silent(self) -> None:
        with patch.object(Path, "read_text", return_value="{}") as read_text:
            self.assertEqual(read_json_object_file("/state.json"), {})
        read_text.assert_called_once_with(encoding="utf-8")

        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text('{"mode": 2, "enabled": true}', encoding="utf-8")
            self.assertEqual(read_json_object_file(str(path)), {"mode": 2, "enabled": True})
            missing = Path(directory) / "missing.json"
            with patch("venus_evcharger.controllers.state_json.logging.warning") as warning:
                self.assertIsNone(read_json_object_file(str(missing)))
            warning.assert_not_called()

    def test_read_warns_exactly_for_invalid_json_encoding_io_and_non_objects(self) -> None:
        with TemporaryDirectory() as directory:
            directory_path = Path(directory)
            invalid_json = directory_path / "invalid.json"
            invalid_json.write_text("{", encoding="utf-8")
            with patch("venus_evcharger.controllers.state_json.logging.warning") as warning:
                self.assertIsNone(read_json_object_file(str(invalid_json)))
            self.assertEqual(warning.call_args.args[:2], ("Unable to read runtime state from %s: %s", str(invalid_json)))
            self.assertIsInstance(warning.call_args.args[2], json.JSONDecodeError)

            invalid_encoding = directory_path / "encoding.json"
            invalid_encoding.write_bytes(b"\xff")
            with patch("venus_evcharger.controllers.state_json.logging.warning") as warning:
                self.assertIsNone(read_json_object_file(str(invalid_encoding)))
            self.assertIsInstance(warning.call_args.args[2], UnicodeDecodeError)

            with patch.object(Path, "read_text", side_effect=OSError("unreadable")):
                with patch("venus_evcharger.controllers.state_json.logging.warning") as warning:
                    self.assertIsNone(read_json_object_file("/state.json"))
            warning.assert_called_once()
            self.assertIsInstance(warning.call_args.args[2], OSError)

            non_object = directory_path / "array.json"
            non_object.write_text("[1, 2]", encoding="utf-8")
            with patch("venus_evcharger.controllers.state_json.logging.warning") as warning:
                self.assertIsNone(read_json_object_file(str(non_object)))
            warning.assert_called_once_with(
                "Ignoring runtime state from %s: expected JSON object",
                str(non_object),
            )


if __name__ == "__main__":
    unittest.main()

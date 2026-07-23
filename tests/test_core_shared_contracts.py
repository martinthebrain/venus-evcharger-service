# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact contracts for shared numeric, config, and file helpers."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import venus_evcharger.core.shared as shared
from venus_evcharger.core.shared import (
    _coerce_scalar_numeric,
    _sum_numeric_items,
    coerce_dbus_numeric,
    config_get_float,
    discovery_cache_valid,
    parse_config_bool,
    write_bytes_atomically,
    write_text_atomically,
)


class IntOnlyValue:
    def __float__(self) -> float:
        raise TypeError("not a float")

    def __int__(self) -> int:
        return 7


class TestCoreSharedContracts(unittest.TestCase):
    def test_scalar_and_public_coercion_use_integer_fallback(self) -> None:
        value = IntOnlyValue()
        self.assertEqual(_coerce_scalar_numeric(value), 7)
        self.assertEqual(coerce_dbus_numeric(value), 7)

    def test_recursive_sum_skips_invalid_items_without_stopping(self) -> None:
        self.assertIsNone(_sum_numeric_items(["bad", None]))
        self.assertEqual(_sum_numeric_items(["bad", 2, [3]]), 5.0)
        self.assertEqual(_sum_numeric_items([0]), 0.0)

    def test_discovery_cache_interval_is_exclusive(self) -> None:
        self.assertTrue(discovery_cache_valid(["service"], 100.0, 60.0, 159.999))
        self.assertFalse(discovery_cache_valid(["service"], 100.0, 60.0, 160.0))

    def test_parse_config_bool_accepts_only_documented_truthy_values(self) -> None:
        for value in ("1", " TRUE ", "yes", "On"):
            self.assertIs(parse_config_bool(value), True)
        for value in ("0", "false", "no", "off", "unexpected", ""):
            self.assertIs(parse_config_bool(value), False)
        self.assertIs(parse_config_bool(None), False)
        self.assertIs(parse_config_bool(None, default=True), True)

    def test_config_float_uses_string_default_and_empty_fallback(self) -> None:
        section = MagicMock()
        section.get.return_value = " 12.5 "
        self.assertEqual(config_get_float(section, "Power", 3.5), 12.5)
        section.get.assert_called_once_with("Power", "3.5")
        section.get.reset_mock()
        section.get.return_value = "  "
        self.assertEqual(config_get_float(section, "Power", 3.5), 3.5)
        section.get.assert_called_once_with("Power", "3.5")

    def test_atomic_writer_forwards_explicit_encoding(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state.txt"
            write_text_atomically(path, "Grüße", encoding="utf-16")
            self.assertEqual(path.read_text(encoding="utf-16"), "Grüße")

    def test_atomic_writer_default_encoding_is_explicit(self) -> None:
        mocked_open = mock_open()
        with patch("builtins.open", mocked_open), patch("os.replace"):
            write_text_atomically("state.txt", "payload")
        _path, mode = mocked_open.call_args.args
        self.assertEqual(mode, "w")
        self.assertEqual(mocked_open.call_args.kwargs, {"encoding": "utf-8"})

    def test_atomic_binary_writer_uses_binary_mode(self) -> None:
        mocked_open = mock_open()
        with (
            patch("builtins.open", mocked_open),
            patch("os.getpid", return_value=12),
            patch.object(threading, "get_ident", return_value=34),
            patch("os.replace") as replace,
        ):
            write_bytes_atomically("state.bin", b"\x00payload")
        self.assertEqual(mocked_open.call_args.args, ("state.bin.tmp.12.34", "wb"))
        self.assertEqual(mocked_open.call_args.kwargs, {})
        mocked_open().write.assert_called_once_with(b"\x00payload")
        replace.assert_called_once_with("state.bin.tmp.12.34", "state.bin")

    def test_atomic_binary_writer_creates_parent_and_replaces_complete_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "nested" / "state.bin"
            write_bytes_atomically(path, b"\x00\xffpayload")
            self.assertEqual(path.read_bytes(), b"\x00\xffpayload")
            self.assertEqual(list(path.parent.glob("state.bin.tmp.*")), [])

    def test_atomic_binary_writer_accepts_an_existing_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state.bin"
            write_bytes_atomically(path, b"payload")
            self.assertEqual(path.read_bytes(), b"payload")

    def test_atomic_binary_writer_cleans_up_every_supported_failure(self) -> None:
        for error in (OSError("io"), RuntimeError("runtime"), TypeError("type")):
            with self.subTest(error=type(error).__name__):
                mocked_open = mock_open()
                with (
                    patch("builtins.open", mocked_open),
                    patch("os.getpid", return_value=12),
                    patch.object(threading, "get_ident", return_value=34),
                    patch("os.replace", side_effect=error),
                    patch.object(shared, "_remove_temporary_file") as cleanup,
                ):
                    with self.assertRaises(type(error)):
                        write_bytes_atomically("state.bin", b"payload")
                cleanup.assert_called_once_with("state.bin.tmp.12.34")


if __name__ == "__main__":
    unittest.main()

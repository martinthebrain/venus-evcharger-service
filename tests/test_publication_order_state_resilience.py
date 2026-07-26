#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Resilience contracts for the publication-order checkpoint journal."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import venus_evcharger.ipc.publication_order_state as state_module
from venus_evcharger.ipc.publication_order_state import PublicationOrderMark


class PublicationOrderStateResilienceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.marks = {("evcs", "mode"): PublicationOrderMark(10, "durable", 10.0)}

    def test_failed_preappend_compaction_keeps_oversized_journal_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = str(Path(temp_dir) / "orders.json")
            journal_path = Path(f"{state_path}.journal")
            journal_path.write_bytes(b"x" * (state_module._STATE_JOURNAL_COMPACT_BYTES + 1))
            original_size = journal_path.stat().st_size

            with patch.object(
                state_module,
                "persist_publication_order_marks",
                return_value=False,
            ) as persist:
                self.assertFalse(
                    state_module.checkpoint_publication_order_marks(
                        state_path,
                        self.marks,
                        self.marks,
                    )
                )
                self.assertFalse(
                    state_module.checkpoint_publication_order_marks(
                        state_path,
                        self.marks,
                        self.marks,
                    )
                )

            self.assertEqual(journal_path.stat().st_size, original_size)
            self.assertEqual(persist.call_count, 2)

    def test_existing_unstatable_journal_defers_before_append(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = str(Path(temp_dir) / "orders.json")
            Path(f"{state_path}.journal").touch()
            with (
                patch.object(state_module, "_journal_size", return_value=None),
                patch.object(state_module, "_append_state_delta") as append,
            ):
                self.assertFalse(
                    state_module.checkpoint_publication_order_marks(
                        state_path,
                        self.marks,
                        self.marks,
                    )
                )
        append.assert_not_called()

    def test_exact_threshold_appends_without_precompaction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = str(Path(temp_dir) / "orders.json")
            Path(f"{state_path}.journal").touch()
            with (
                patch.object(
                    state_module,
                    "_journal_size",
                    return_value=state_module._STATE_JOURNAL_COMPACT_BYTES,
                ),
                patch.object(state_module, "_append_state_delta", return_value=True) as append,
                patch.object(state_module, "_finish_checkpoint", return_value=True),
                patch.object(state_module, "_compact_state", return_value=True) as compact,
            ):
                self.assertTrue(
                    state_module.checkpoint_publication_order_marks(
                        state_path,
                        self.marks,
                        self.marks,
                    )
                )
        append.assert_called_once_with(state_path, self.marks)
        compact.assert_not_called()

    def test_preappend_and_postappend_compaction_preserve_exact_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = str(Path(temp_dir) / "orders.json")
            journal_path = f"{state_path}.journal"
            Path(journal_path).touch()
            with (
                patch.object(
                    state_module,
                    "_journal_size",
                    return_value=state_module._STATE_JOURNAL_COMPACT_BYTES + 1,
                ),
                patch.object(state_module, "_compact_state", return_value=True) as compact,
                patch.object(state_module, "_append_state_delta", return_value=True),
                patch.object(state_module, "_finish_checkpoint", return_value=True),
            ):
                self.assertTrue(
                    state_module.checkpoint_publication_order_marks(
                        state_path,
                        self.marks,
                        self.marks,
                    )
                )
            compact.assert_called_once_with(state_path, journal_path, self.marks)

            with (
                patch.object(
                    state_module,
                    "_journal_size",
                    return_value=state_module._STATE_JOURNAL_COMPACT_BYTES + 1,
                ),
                patch.object(state_module, "_compact_state", return_value=True) as compact,
            ):
                self.assertTrue(state_module._finish_checkpoint(state_path, self.marks))
            compact.assert_called_once_with(state_path, journal_path, self.marks)


if __name__ == "__main__":
    unittest.main()

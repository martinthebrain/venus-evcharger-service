# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import fcntl
import os
import tempfile
import unittest
from pathlib import Path

from venus_evcharger.ops.removable_storage_coordination import (
    DEFAULT_REMOVABLE_STORAGE_MAINTENANCE_LOCK_PATH,
    removable_storage_write_lease,
)


class RemovableStorageCoordinationTests(unittest.TestCase):
    def test_contract_uses_system_wide_runtime_lock(self) -> None:
        self.assertEqual(
            DEFAULT_REMOVABLE_STORAGE_MAINTENANCE_LOCK_PATH,
            "/run/lock/removable-storage-maintenance.lock",
        )
        updater = (
            Path(__file__).resolve().parents[1]
            / "deploy/venus/bootstrap_updater.sh"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "${VENUS_REMOVABLE_STORAGE_MAINTENANCE_LOCK_PATH:-"
            f"{DEFAULT_REMOVABLE_STORAGE_MAINTENANCE_LOCK_PATH}}}",
            updater,
        )

    def test_shared_lease_blocks_exclusive_maintenance_until_release(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = str(Path(temp_dir) / "maintenance.lock")
            competing_descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                with removable_storage_write_lease(lock_path) as acquired:
                    self.assertTrue(acquired)
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(
                            competing_descriptor,
                            fcntl.LOCK_EX | fcntl.LOCK_NB,
                        )
                fcntl.flock(
                    competing_descriptor,
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
                fcntl.flock(competing_descriptor, fcntl.LOCK_UN)
            finally:
                os.close(competing_descriptor)

    def test_exclusive_maintenance_makes_shared_lease_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = str(Path(temp_dir) / "maintenance.lock")
            descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                with removable_storage_write_lease(lock_path) as acquired:
                    self.assertFalse(acquired)
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

    def test_relative_lock_path_needs_no_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            previous_directory = os.getcwd()
            try:
                os.chdir(temp_dir)
                with removable_storage_write_lease("maintenance.lock") as acquired:
                    self.assertTrue(acquired)
            finally:
                os.chdir(previous_directory)


if __name__ == "__main__":
    unittest.main()

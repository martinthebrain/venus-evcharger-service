# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for RAM-only software-update runtime initialization."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from venus_evcharger.runtime.software_update_setup import (
    _default_version_source,
    _software_update_env,
    _software_update_repo_path,
    initialize_software_update_runtime_state,
)


class TestRuntimeSoftwareUpdateSetup(unittest.TestCase):
    def clean_env(self, values: dict[str, str] | None = None) -> dict[str, str]:
        env = dict(values or {})
        if "MUTANT_UNDER_TEST" in os.environ:
            env["MUTANT_UNDER_TEST"] = os.environ["MUTANT_UNDER_TEST"]
        return env

    def assert_runtime_state_keys(self, service: SimpleNamespace) -> None:
        self.assertEqual(
            set(vars(service)),
            {
                "started_at",
                "software_update_repo_root",
                "software_update_install_script",
                "software_update_no_update_file",
                "software_update_log_path",
                "software_update_repo_slug",
                "software_update_channel",
                "software_update_manifest_source",
                "software_update_version_source",
                "_software_update_current_version",
                "_software_update_available_version",
                "_software_update_available",
                "_software_update_state",
                "_software_update_detail",
                "_software_update_last_check_at",
                "_software_update_last_run_at",
                "_software_update_last_result",
                "_software_update_process",
                "_software_update_process_log_handle",
                "_software_update_run_requested_at",
                "_software_update_no_update_active",
                "_software_update_next_check_at",
                "_software_update_boot_auto_due_at",
            },
        )

    def test_path_and_env_helpers_keep_explicit_defaults(self) -> None:
        self.assertEqual(_software_update_repo_path("", "install.sh"), "")
        self.assertEqual(_software_update_repo_path("/repo", "deploy", "restart.sh"), "/repo/deploy/restart.sh")
        self.assertEqual(
            _default_version_source("martinthebrain/venus-evcharger-service", "main"),
            "https://raw.githubusercontent.com/martinthebrain/venus-evcharger-service/main/version.txt",
        )
        with patch.dict(os.environ, self.clean_env(), clear=True):
            self.assertEqual(_software_update_env("VENUS_EVCHARGER_CHANNEL", "main"), "main")
        with patch.dict(os.environ, self.clean_env({"VENUS_EVCHARGER_CHANNEL": ""}), clear=True):
            self.assertEqual(_software_update_env("VENUS_EVCHARGER_CHANNEL", "main"), "")
        with patch.dict(os.environ, self.clean_env({"VENUS_EVCHARGER_CHANNEL": "testing"}), clear=True):
            self.assertEqual(_software_update_env("VENUS_EVCHARGER_CHANNEL", "main"), "testing")

    def test_defaults_initialize_ram_only_state_without_repo_root(self) -> None:
        service = SimpleNamespace()

        with patch.dict(os.environ, self.clean_env(), clear=True):
            initialize_software_update_runtime_state(
                service,
                repo_root="",
                started_at=200.0,
                current_version="",
                boot_auto_due_at=None,
            )

        self.assertEqual(service.started_at, 200.0)
        self.assert_runtime_state_keys(service)
        self.assertEqual(service.software_update_repo_root, "")
        self.assertEqual(service.software_update_install_script, "")
        self.assertEqual(service.software_update_no_update_file, "")
        self.assertEqual(
            service.software_update_log_path,
            "/var/volatile/log/dbus-venus-evcharger/software-update.log",
        )
        self.assertEqual(service.software_update_repo_slug, "martinthebrain/venus-evcharger-service")
        self.assertEqual(service.software_update_channel, "main")
        self.assertEqual(service.software_update_manifest_source, "")
        self.assertEqual(
            service.software_update_version_source,
            "https://raw.githubusercontent.com/martinthebrain/venus-evcharger-service/main/version.txt",
        )
        self.assertEqual(service._software_update_current_version, "")
        self.assertEqual(service._software_update_available_version, "")
        self.assertIs(service._software_update_available, False)
        self.assertEqual(service._software_update_state, "idle")
        self.assertEqual(service._software_update_detail, "")
        self.assertIsNone(service._software_update_last_check_at)
        self.assertIsNone(service._software_update_last_run_at)
        self.assertEqual(service._software_update_last_result, "")
        self.assertIsNone(service._software_update_process)
        self.assertIsNone(service._software_update_process_log_handle)
        self.assertIsNone(service._software_update_run_requested_at)
        self.assertEqual(service._software_update_no_update_active, 0)
        self.assertEqual(service._software_update_next_check_at, 500.0)
        self.assertIsNone(service._software_update_boot_auto_due_at)

    def test_custom_sources_and_no_update_marker_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            (repo_root / "noUpdate").write_text("", encoding="utf-8")
            service = SimpleNamespace()

            with patch.dict(
                os.environ,
                self.clean_env(
                    {
                        "VENUS_EVCHARGER_REPO_SLUG": "owner/project",
                        "VENUS_EVCHARGER_CHANNEL": "stable",
                        "VENUS_EVCHARGER_MANIFEST_SOURCE": "https://example.invalid/manifest.json",
                        "VENUS_EVCHARGER_VERSION_SOURCE": "https://example.invalid/version.txt",
                    }
                ),
                clear=True,
            ):
                initialize_software_update_runtime_state(
                    service,
                    repo_root=str(repo_root),
                    started_at=123.0,
                    current_version="4.5.6",
                    boot_auto_due_at=456.0,
                )

        self.assertEqual(service.software_update_repo_root, str(repo_root))
        self.assert_runtime_state_keys(service)
        self.assertEqual(service.software_update_install_script, str(repo_root / "install.sh"))
        self.assertEqual(service.software_update_no_update_file, str(repo_root / "noUpdate"))
        self.assertEqual(service.software_update_repo_slug, "owner/project")
        self.assertEqual(service.software_update_channel, "stable")
        self.assertEqual(service.software_update_manifest_source, "https://example.invalid/manifest.json")
        self.assertEqual(service.software_update_version_source, "https://example.invalid/version.txt")
        self.assertEqual(service._software_update_current_version, "4.5.6")
        self.assertEqual(service._software_update_no_update_active, 1)
        self.assertEqual(service._software_update_next_check_at, 423.0)
        self.assertEqual(service._software_update_boot_auto_due_at, 456.0)

    def test_no_update_marker_must_be_a_file_not_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            (repo_root / "noUpdate").mkdir()
            service = SimpleNamespace()

            initialize_software_update_runtime_state(
                service,
                repo_root=str(repo_root),
                started_at=10.0,
                current_version="1.2.3",
                boot_auto_due_at=None,
            )

        self.assert_runtime_state_keys(service)
        self.assertEqual(service.software_update_no_update_file, str(repo_root / "noUpdate"))
        self.assertEqual(service._software_update_no_update_active, 0)
        self.assertEqual(service._software_update_next_check_at, 310.0)


if __name__ == "__main__":
    unittest.main()

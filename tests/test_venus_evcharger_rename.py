# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest
from pathlib import Path


class TestVenusEvchargerRename(unittest.TestCase):
    @staticmethod
    def _repo_root() -> Path:
        return Path(__file__).resolve().parents[1]

    @staticmethod
    def _project_files(repo_root: Path) -> list[Path]:
        return [path for path in repo_root.rglob("*") if TestVenusEvchargerRename._included_project_file(path)]

    @staticmethod
    def _included_project_file(path: Path) -> bool:
        return path.is_file() and not TestVenusEvchargerRename._ignored_project_path(path)

    @staticmethod
    def _ignored_project_path(path: Path) -> bool:
        ignored_parts = {".git", "__pycache__", ".mypy_cache", ".pytest_cache", ".coverage"}
        return any(part in ignored_parts for part in path.parts)

    @staticmethod
    def _forbidden_tokens() -> tuple[str, ...]:
        return (
            "_".join(("shelly", "wallbox")),
            "_".join(("dbus", "shelly", "wallbox")),
            "-".join(("dbus", "shelly", "evcharger")),
        )

    def test_new_project_names_exist(self) -> None:
        repo_root = self._repo_root()
        required_paths = (
            repo_root / "venus_evcharger",
            repo_root / "venus_evcharger_service.py",
            repo_root / "venus_evcharger_auto_input_helper.py",
            repo_root / "deploy" / "venus" / "config.venus_evcharger.ini",
            repo_root / "deploy" / "venus" / "install_venus_evcharger_service.sh",
            repo_root / "deploy" / "venus" / "configure_venus_evcharger_service.sh",
            repo_root / "deploy" / "venus" / "service_venus_evcharger" / "run",
        )
        for path in required_paths:
            self.assertTrue(path.exists(), str(path))

    def test_project_paths_do_not_use_legacy_project_identifiers(self) -> None:
        repo_root = self._repo_root()
        forbidden = self._forbidden_tokens()

        for path in self._project_files(repo_root):
            relative = path.relative_to(repo_root).as_posix()
            for token in forbidden:
                self.assertNotIn(token, relative, relative)
            self.assertNotIn("tests/wallbox_", relative, relative)

    def test_project_file_contents_do_not_use_legacy_project_identifiers(self) -> None:
        repo_root = self._repo_root()
        forbidden = self._forbidden_tokens()
        allowed_shelly_context = {
            repo_root / "README.md",
            repo_root / "CONFIGURATION.md",
            repo_root / "CHARGER_BACKENDS.md",
            repo_root / "SHELLY_PROFILES.md",
            repo_root / "venus_evcharger" / "backend" / "shelly_io.py",
            repo_root / "venus_evcharger" / "backend" / "shelly_profiles.py",
        }

        for path in self._project_files(repo_root):
            if path in allowed_shelly_context:
                continue
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(token, text, str(path))


if __name__ == "__main__":
    unittest.main()

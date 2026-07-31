# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest
from pathlib import Path


_REPOSITORY = Path(__file__).resolve().parents[1]
_RETIRED_CONFIG_KEYS = (
    "AutoBatteryPowerPath",
    "AutoBatteryAcPowerPath",
    "AutoBatteryPvPowerPath",
    "AutoBatteryGridInteractionPath",
    "AutoBatteryOperatingModePath",
    "AutoScheduledFallbackDelaySeconds",
)


def _published_markdown() -> list[Path]:
    return sorted(_REPOSITORY.glob("*.md")) + sorted((_REPOSITORY / "docs").glob("*.md"))


class TestDocumentationConsistency(unittest.TestCase):
    def test_published_docs_do_not_contain_host_specific_paths(self) -> None:
        home_prefix = "/" + "home/"
        for path in _published_markdown():
            with self.subTest(path=path):
                self.assertNotIn(home_prefix, path.read_text(encoding="utf-8"))

    def test_configuration_reference_uses_runtime_config_keys(self) -> None:
        reference = (_REPOSITORY / "CONFIGURATION.md").read_text(encoding="utf-8")
        for retired_key in _RETIRED_CONFIG_KEYS:
            with self.subTest(key=retired_key):
                self.assertNotIn(f"`{retired_key}`", reference)
        self.assertIn("`AutoScheduledNightStartDelaySeconds`", reference)
        self.assertIn("`AutoEnergySource.<id>.BatteryPowerPath`", reference)

    def test_operator_docs_use_the_canonical_bootstrap_directory(self) -> None:
        for relative_path in ("INSTALL.md", "TROUBLESHOOTING.md", "UPDATE_FLOW.md"):
            document = (_REPOSITORY / relative_path).read_text(encoding="utf-8")
            with self.subTest(path=relative_path):
                self.assertNotIn("/data/shellyWB", document)
                self.assertNotIn("/data/bootstrap-venus-evcharger", document)
                self.assertIn("/data/venus-evcharger", document)

    def test_retired_parallel_references_are_absent(self) -> None:
        for name in (
            "backend-adapter-minimal-design.md",
            "dbus-introspection-worker.md",
            "MUTATION_HARDENING_QUEUE.md",
        ):
            with self.subTest(path=name):
                self.assertFalse((_REPOSITORY / "docs" / name).exists())


if __name__ == "__main__":
    unittest.main()

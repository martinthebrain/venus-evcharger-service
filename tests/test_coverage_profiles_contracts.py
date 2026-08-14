# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for the split unit and platform coverage gates."""

from __future__ import annotations

import configparser
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_EXCLUSIONS = {
    r"class .*\(.*Protocol.*\):",
    r"^\s*Protocol,?$",
}


def _coverage_config(name: str) -> configparser.RawConfigParser:
    parser = configparser.RawConfigParser()
    loaded = parser.read(REPOSITORY_ROOT / name, encoding="utf-8")
    if not loaded:
        raise AssertionError(f"Unable to load coverage profile: {name}")
    return parser


def _lines(parser: configparser.RawConfigParser, section: str, option: str) -> set[str]:
    return {line.strip() for line in parser.get(section, option).splitlines() if line.strip()}


class CoverageProfilesContractTests(unittest.TestCase):
    def test_runtime_measurement_enables_branch_coverage_for_explicit_sources(self) -> None:
        runtime = _coverage_config(".coveragerc")
        self.assertTrue(runtime.getboolean("run", "branch"))
        self.assertEqual(
            _lines(runtime, "run", "source"),
            {
                "venus_evcharger",
                "venus_evcharger_auto_input_helper",
                "venus_evcharger_dbus_adapter",
                "venus_evcharger_service",
                "venus_evchargerctl",
            },
        )

    def test_unit_and_platform_profiles_both_enforce_full_coverage(self) -> None:
        unit = _coverage_config(".coveragerc.unit")
        platform = _coverage_config(".coveragerc.platform")
        self.assertEqual(unit.getint("report", "fail_under"), 100)
        self.assertEqual(platform.getint("report", "fail_under"), 100)
        self.assertEqual(_lines(unit, "report", "omit"), _lines(platform, "report", "include"))

    def test_protocol_exclusions_are_narrow_and_shared_by_every_profile(self) -> None:
        for name in (".coveragerc", ".coveragerc.unit", ".coveragerc.platform"):
            with self.subTest(profile=name):
                parser = _coverage_config(name)
                exclusions = _lines(parser, "report", "exclude_lines")
                self.assertTrue(PROTOCOL_EXCLUSIONS.issubset(exclusions))

    def test_runner_reports_both_profiles(self) -> None:
        runner = (REPOSITORY_ROOT / "scripts/dev/run_coverage.sh").read_text(encoding="utf-8")
        self.assertIn('report --rcfile="$REPO_DIR/.coveragerc.unit"', runner)
        self.assertIn('report --rcfile="$REPO_DIR/.coveragerc.platform"', runner)


if __name__ == "__main__":
    unittest.main()

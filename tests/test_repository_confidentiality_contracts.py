#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for excluding private field information from Git."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.dev import check_repository_confidentiality as confidentiality


class RepositoryConfidentialityContractTests(unittest.TestCase):
    def test_disallowed_address_distinguishes_examples_from_private_targets(self) -> None:
        parse = confidentiality.ipaddress.IPv4Address
        cgnat_address = ".".join(("100", "64", "0", "1"))
        private_address = ".".join(("192", "168", "1", "20"))
        self.assertTrue(confidentiality.disallowed_address(parse(cgnat_address), sensitive_surface=False))
        self.assertTrue(confidentiality.disallowed_address(parse(private_address), sensitive_surface=True))
        self.assertFalse(confidentiality.disallowed_address(parse(private_address), sensitive_surface=False))
        self.assertFalse(confidentiality.disallowed_address(parse("192.0.2.20"), sensitive_surface=True))
        self.assertFalse(confidentiality.disallowed_address(parse("127.0.0.1"), sensitive_surface=True))
        self.assertFalse(confidentiality.disallowed_address(parse("0.0.0.0"), sensitive_surface=True))
        self.assertFalse(confidentiality.disallowed_address(parse("192.168.8.1"), sensitive_surface=True))

    def test_text_issues_cover_home_network_and_private_local_patterns(self) -> None:
        issues = confidentiality.text_issues(
            Path("docs/report.md"),
            "/" + "home/operator/work\nhost=" + ".".join(("192", "168", "10", "4")) + "\nProject Orion\n",
            ("Project Orion",),
        )
        self.assertEqual(
            issues,
            [
                "docs/report.md: host-specific home directory",
                "docs/report.md: locally classified confidential literal",
                "docs/report.md: non-public network address",
            ],
        )

    def test_repository_check_rejects_internal_paths_and_incident_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report = Path("reports/incident-20260731-example/trace.txt")
            (root / report).parent.mkdir(parents=True)
            (root / report).write_text("sanitized\n", encoding="utf-8")
            issues = confidentiality.confidentiality_issues(
                root,
                (Path("docs/KNOWN_VENUS_OS_ISSUES.md"), report),
            )
        self.assertEqual(
            issues,
            [
                "docs/KNOWN_VENUS_OS_ISSUES.md: internal document must not be tracked",
                "reports/incident-20260731-example/trace.txt: field incident artifact must not be tracked",
            ],
        )

    def test_local_patterns_are_optional_private_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.assertEqual(confidentiality.local_confidential_patterns(root), ())
            path = root / confidentiality.LOCAL_PATTERNS_PATH
            path.parent.mkdir(parents=True)
            path.write_text("# local only\nAlpha Site\n\n", encoding="utf-8")
            self.assertEqual(confidentiality.local_confidential_patterns(root), ("Alpha Site",))

    def test_tracked_paths_decodes_null_separated_git_output(self) -> None:
        completed = confidentiality.subprocess.CompletedProcess(
            args=["git", "ls-files", "-z"],
            returncode=0,
            stdout=b"README.md\0docs/guide.md\0",
        )
        with patch.object(confidentiality.subprocess, "run", return_value=completed) as run:
            self.assertEqual(
                confidentiality.tracked_paths(Path("/repo")),
                (Path("README.md"), Path("docs/guide.md")),
            )
        run.assert_called_once_with(
            ["git", "ls-files", "-z"],
            cwd=Path("/repo"),
            check=True,
            stdout=confidentiality.subprocess.PIPE,
        )


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Contracts for non-duplicated Python test execution in CI."""

from __future__ import annotations

import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class CiTestExecutionContractTests(unittest.TestCase):
    def test_ci_defers_python_tests_to_coverage_without_delaying_failures(self) -> None:
        workflow = (REPOSITORY_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        static_check = "bash ./scripts/dev/check_all.sh --skip-python-tests"
        coverage_check = "bash ./scripts/dev/run_coverage.sh"
        artifact_check = "Verify pinned ARMv7 observer artifact"

        self.assertEqual(workflow.count(static_check), 1)
        self.assertEqual(workflow.count(coverage_check), 1)
        self.assertLess(workflow.index(static_check), workflow.index(coverage_check))
        self.assertLess(workflow.index(coverage_check), workflow.index(artifact_check))

    def test_local_and_release_checks_keep_the_normal_python_test_run(self) -> None:
        makefile = (REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8")
        release_gate = (REPOSITORY_ROOT / "scripts/dev/run_release_candidate_gate.sh").read_text(encoding="utf-8")

        self.assertIn("\t./scripts/dev/check_all.sh\n", makefile)
        self.assertIn("bash scripts/dev/check_all.sh\n", release_gate)
        self.assertNotIn("--skip-python-tests", makefile)
        self.assertNotIn("--skip-python-tests", release_gate)


if __name__ == "__main__":
    unittest.main()

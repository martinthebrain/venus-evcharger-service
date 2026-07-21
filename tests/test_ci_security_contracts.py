#!/usr/bin/env python3
"""Repository contracts for CI permissions and immutable actions."""

from __future__ import annotations

import hashlib
import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / ".github/workflows"
ACTION_REF_PATTERN = re.compile(r"^\s*uses:\s*[^#\s]+@([0-9a-f]{40})(?:\s+#.*)?$", re.MULTILINE)
USES_LINE_PATTERN = re.compile(r"^\s*uses:\s*.+$", re.MULTILINE)


class CiSecurityContractTests(unittest.TestCase):
    def test_ci_uses_minimal_permissions_and_immutable_actions(self) -> None:
        workflows = {path.name: path.read_text(encoding="utf-8") for path in sorted(WORKFLOW_DIR.glob("*.yml"))}
        self.assertTrue(workflows)
        for name, workflow in workflows.items():
            with self.subTest(workflow=name):
                uses_lines = USES_LINE_PATTERN.findall(workflow)
                self.assertEqual(len(ACTION_REF_PATTERN.findall(workflow)), len(uses_lines))
                self.assertNotIn("id-token: write", workflow)
                if "actions/checkout@" in workflow:
                    self.assertIn("persist-credentials: false", workflow)
        self.assertIn("permissions:\n  contents: read", workflows["ci.yml"])
        release_workflow = workflows["release.yml"]
        self.assertIn("grep -Eq '^[0-9a-f]{40}$'", release_workflow)
        self.assertIn("git rev-parse \"$RELEASE_TAG^{commit}\"", release_workflow)

    def test_hardware_release_gate_requires_a_dedicated_testbed_marker(self) -> None:
        gate = (REPO_ROOT / "scripts/dev/run_release_candidate_gate.sh").read_text(encoding="utf-8")
        marker_check = 'ssh -F "$SSH_CONFIG" -o BatchMode=yes "$PI_TARGET" test -f "$TESTBED_MARKER"'
        self.assertIn(marker_check, gate)
        self.assertLess(gate.index(marker_check), gate.index("bash scripts/dev/check_all.sh"))
        self.assertLess(gate.index(marker_check), gate.index("python3 scripts/dev/pi_gateway_release_gate.py"))

    def test_security_policy_documents_private_reporting_and_runtime_boundaries(self) -> None:
        policy = (REPO_ROOT / "SECURITY.md").read_text(encoding="utf-8")
        self.assertIn("security/advisories/new", policy)
        self.assertIn("non-loopback TCP listener requires explicit authentication tokens", policy)
        self.assertIn("Only the DBus gateway adapter may access the Victron DBus", policy)

    def test_production_guides_pin_the_shipped_bootstrap_public_key(self) -> None:
        public_key = (REPO_ROOT / "deploy/venus/bootstrap_manifest.pub").read_bytes()
        fingerprint = hashlib.sha256(public_key).hexdigest()
        for guide_name in ("README.md", "INSTALL.md"):
            with self.subTest(guide=guide_name):
                guide = (REPO_ROOT / guide_name).read_text(encoding="utf-8")
                self.assertIn(f"BOOTSTRAP_PUBKEY_SHA256={fingerprint}", guide)
                self.assertIn("sha256sum -c -", guide)


if __name__ == "__main__":
    unittest.main()

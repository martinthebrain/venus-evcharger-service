# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.ops.verify_venus_evcharger_deployment import (
    _batches,
    _memory_available_percent,
    _normalized_hashes,
    _resource_gate_active,
    create_full_manifest,
    main,
    verify_full_manifest,
    verify_receipt,
)


class TestVerifyVenusEvchargerDeployment(unittest.TestCase):
    def _receipt(self, root: Path, critical_file: Path) -> Path:
        relative_path = critical_file.relative_to(root).as_posix()
        receipt_path = root / ".bootstrap-state/deployment_receipt.json"
        receipt_path.parent.mkdir(parents=True)
        receipt_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "active_root": str(root),
                    "source_commit": "a" * 40,
                    "bundle_sha256": "b" * 64,
                    "critical_files": {relative_path: hashlib.sha256(critical_file.read_bytes()).hexdigest()},
                }
            ),
            encoding="utf-8",
        )
        return receipt_path

    def test_quick_receipt_detects_success_missing_and_changed_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            critical_file = root / "venus_evcharger_service.py"
            critical_file.write_text("service\n", encoding="utf-8")
            receipt_path = self._receipt(root, critical_file)

            result = verify_receipt(root, receipt_path)
            self.assertTrue(result["ok"])
            self.assertEqual(result["checked_file_count"], 1)

            critical_file.write_text("changed\n", encoding="utf-8")
            self.assertEqual(verify_receipt(root, receipt_path)["mismatches"], [{"path": "venus_evcharger_service.py", "reason": "hash-mismatch"}])
            critical_file.unlink()
            self.assertEqual(verify_receipt(root, receipt_path)["mismatches"], [{"path": "venus_evcharger_service.py", "reason": "missing"}])

    def test_receipt_contract_rejects_invalid_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            receipt_path = root / "receipt.json"
            invalid_payloads = (
                [],
                {"schema_version": 2, "critical_files": {"a": "0" * 64}},
                {"schema_version": 1, "source_commit": "bad", "critical_files": {"a": "0" * 64}},
                {"schema_version": 1, "bundle_sha256": "bad", "critical_files": {"a": "0" * 64}},
                {"schema_version": 1, "critical_files": {}},
            )
            for payload in invalid_payloads:
                with self.subTest(payload=payload):
                    receipt_path.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        verify_receipt(root, receipt_path)

            for hashes in ({"../escape": "0" * 64}, {"safe": "bad"}, []):
                with self.subTest(hashes=hashes), self.assertRaises(ValueError):
                    _normalized_hashes(hashes, label="test")

    def test_receipt_reports_files_missing_at_install(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            critical_file = root / "service.py"
            critical_file.write_text("service\n", encoding="utf-8")
            receipt_path = self._receipt(root, critical_file)
            payload = json.loads(receipt_path.read_text(encoding="utf-8"))
            payload["missing_critical_files"] = ["required.py"]
            receipt_path.write_text(json.dumps(payload), encoding="utf-8")

            result = verify_receipt(root, receipt_path)

            self.assertFalse(result["ok"])
            self.assertEqual(result["mismatches"], [{"path": "required.py", "reason": "missing-at-install"}])

    def test_receipt_must_describe_current_active_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir)
            active_root = target / "releases/2.0.0"
            active_root.mkdir(parents=True)
            (target / "current").symlink_to(active_root)
            critical_file = active_root / "service.py"
            critical_file.write_text("service\n", encoding="utf-8")
            receipt_path = self._receipt(active_root, critical_file)
            payload = json.loads(receipt_path.read_text(encoding="utf-8"))
            payload["active_root"] = str(target)
            receipt_path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "active release root"):
                verify_receipt(target, receipt_path)

    def test_resource_helpers_and_batches(self) -> None:
        meminfo = "MemTotal: 1000 kB\nMemAvailable: 250 kB\n"
        with patch.object(Path, "read_text", return_value=meminfo):
            self.assertEqual(_memory_available_percent(), 25.0)
        with patch.object(Path, "read_text", side_effect=OSError):
            self.assertEqual(_memory_available_percent(), 100.0)
        with patch("os.getloadavg", return_value=(3.0, 0.0, 0.0)), patch("os.cpu_count", return_value=2):
            self.assertTrue(_resource_gate_active(1.0, 10.0))
        with patch("os.getloadavg", side_effect=OSError), patch("os.cpu_count", return_value=None), patch(
            "scripts.ops.verify_venus_evcharger_deployment._memory_available_percent", return_value=5.0
        ):
            self.assertTrue(_resource_gate_active(1.0, 10.0))
        self.assertEqual(list(_batches([("a", "1"), ("b", "2"), ("c", "3")], 2)), [[("a", "1"), ("b", "2")], [("c", "3")]])

    def test_full_manifest_is_batched_and_resource_gated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "first.py"
            second = root / "second.py"
            first.write_text("first\n", encoding="utf-8")
            second.write_text("second\n", encoding="utf-8")
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "files": {
                            "first.py": hashlib.sha256(first.read_bytes()).hexdigest(),
                            "second.py": hashlib.sha256(second.read_bytes()).hexdigest(),
                        }
                    }
                ),
                encoding="utf-8",
            )
            with patch("scripts.ops.verify_venus_evcharger_deployment._resource_gate_active", side_effect=[True, False, False]), patch(
                "time.sleep"
            ) as sleep_mock:
                result = verify_full_manifest(
                    root,
                    manifest_path,
                    batch_size=1,
                    pause_seconds=0.01,
                    max_load_per_cpu=1.0,
                    min_memory_percent=10.0,
                    max_deferrals=2,
                )
            self.assertTrue(result["ok"])
            self.assertEqual(result["resource_deferrals"], 1)
            self.assertEqual(sleep_mock.call_count, 2)

            with patch("scripts.ops.verify_venus_evcharger_deployment._resource_gate_active", return_value=True), patch("time.sleep"):
                with self.assertRaises(RuntimeError):
                    verify_full_manifest(
                        root,
                        manifest_path,
                        batch_size=1,
                        pause_seconds=0.01,
                        max_load_per_cpu=1.0,
                        min_memory_percent=10.0,
                        max_deferrals=0,
                    )

    def test_manifest_creation_excludes_local_config_and_cache_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "venus_evcharger").mkdir()
            (root / "venus_evcharger/module.py").write_text("module\n", encoding="utf-8")
            (root / "venus_evcharger/__pycache__").mkdir()
            (root / "venus_evcharger/__pycache__/module.pyc").write_bytes(b"cache")
            (root / "deploy/venus").mkdir(parents=True)
            (root / "deploy/venus/config.venus_evcharger.ini").write_text("local\n", encoding="utf-8")
            (root / "deploy/venus/config.venus_evcharger.ini.bak-1").write_text("backup\n", encoding="utf-8")
            output_path = root / "manifest.json"
            result = create_full_manifest(root, output_path)
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(result["file_count"], 1)
            self.assertEqual(list(payload["files"]), ["venus_evcharger/module.py"])

    def test_main_reports_quick_success_and_argument_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            critical_file = root / "service.py"
            critical_file.write_text("service\n", encoding="utf-8")
            self._receipt(root, critical_file)
            self.assertEqual(main([str(root)]), 0)
            with self.assertRaises(SystemExit):
                main([str(root), "--batch-size", "0"])
            with self.assertRaises(SystemExit):
                main([str(root), "--create-manifest", str(root)])


if __name__ == "__main__":
    unittest.main()

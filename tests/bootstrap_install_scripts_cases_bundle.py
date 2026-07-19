# SPDX-License-Identifier: GPL-3.0-or-later
from tests.bootstrap_install_scripts_cases_common import REPO_ROOT, Path, _BootstrapInstallScriptsBase, hashlib, json, os, subprocess, tarfile, tempfile, UPDATER_SCRIPT


BUILD_BUNDLE_SCRIPT = REPO_ROOT / "deploy/venus/build_bootstrap_bundle.sh"


class _BootstrapInstallScriptsBundleCases(_BootstrapInstallScriptsBase):
    def test_bootstrap_bundle_contains_all_runtime_entrypoints_and_services(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            subprocess.run(["bash", str(BUILD_BUNDLE_SCRIPT), str(output_dir), str(REPO_ROOT)], check=True)

            with tarfile.open(output_dir / "wallbox-bundle.tar.gz", "r:gz") as bundle:
                bundle_names = set(bundle.getnames())
            manifest = json.loads((output_dir / "bootstrap_manifest.json").read_text(encoding="utf-8"))
            expected_commit = subprocess.run(
                ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
            ).stdout.strip()
            self.assertEqual(manifest["source_repo"], "martinthebrain/venus-evcharger-service")
            self.assertEqual(manifest["source_commit"], expected_commit)

            for rel_path in (
                "./venus_evcharger_service.py",
                "./venus_evcharger_dbus_adapter.py",
                "./venus_evcharger_observer.py",
                "./venus_evcharger_auto_input_helper.py",
                "./venus_evchargerctl.py",
                "./deploy/venus/service_venus_evcharger/run",
                "./deploy/venus/service_venus_evcharger_dbus_adapter/run",
                "./deploy/venus/service_venus_evcharger_observer/run",
                "./deploy/venus/service_lifecycle.sh",
            ):
                self.assertIn(rel_path, bundle_names)

    def test_archive_override_records_commit_bundle_hash_and_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output_dir = root / "bundle"
            target_dir = root / "target"
            subprocess.run(["bash", str(BUILD_BUNDLE_SCRIPT), str(output_dir), str(REPO_ROOT)], check=True)
            flat_bundle_path = output_dir / "wallbox-bundle.tar.gz"
            wrapped_source = root / "wrapped/repository-main"
            wrapped_source.mkdir(parents=True)
            with tarfile.open(flat_bundle_path, "r:gz") as flat_bundle:
                flat_bundle.extractall(wrapped_source, filter="data")
            bundle_path = root / "repository-main.tar.gz"
            with tarfile.open(bundle_path, "w:gz") as wrapped_bundle:
                wrapped_bundle.add(wrapped_source, arcname="repository-main")
            source_commit = "c" * 40

            subprocess.run(
                ["bash", str(UPDATER_SCRIPT), str(target_dir)],
                check=True,
                env={
                    **os.environ,
                    "VENUS_EVCHARGER_ARCHIVE_URL": str(bundle_path),
                    "VENUS_EVCHARGER_SOURCE_COMMIT": source_commit,
                },
            )

            expected_bundle_hash = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
            status = json.loads((target_dir / ".bootstrap-state/update_status.json").read_text(encoding="utf-8"))
            receipt = json.loads((target_dir / ".bootstrap-state/deployment_receipt.json").read_text(encoding="utf-8"))
            self.assertEqual(status["new_source_commit"], source_commit)
            self.assertEqual(status["new_bundle_sha256"], expected_bundle_hash)
            self.assertEqual(receipt["source_commit"], source_commit)
            self.assertEqual(receipt["bundle_sha256"], expected_bundle_hash)
            self.assertEqual((target_dir / ".bootstrap-state/installed_source_commit").read_text(encoding="utf-8"), f"{source_commit}\n")


__all__ = [name for name in globals() if not name.startswith("__")]

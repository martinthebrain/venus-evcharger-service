# SPDX-License-Identifier: GPL-3.0-or-later
from tests.bootstrap_install_scripts_cases_common import (
    Path,
    _BootstrapInstallScriptsBase,
    hashlib,
    json,
    os,
    subprocess,
    tarfile,
    tempfile,
    UPDATER_SCRIPT,
)


class _BootstrapInstallScriptsManifestCases(_BootstrapInstallScriptsBase):
    def test_bootstrap_updater_keeps_current_release_when_staged_manifest_config_fails_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "source"
            target_dir = root / "target"
            release_dir = root / "release"
            current_release = target_dir / "releases/1.0.0"

            (source_dir / "deploy/venus/service_venus_evcharger/log").mkdir(parents=True, exist_ok=True)
            (source_dir / "venus_evcharger").mkdir(parents=True, exist_ok=True)
            (source_dir / "scripts/ops").mkdir(parents=True, exist_ok=True)

            for rel_path, content in (
                ("install.sh", "#!/bin/bash\n"),
                ("LICENSE", "license\n"),
                ("README.md", "readme\n"),
                ("SHELLY_PROFILES.md", "profiles\n"),
                ("version.txt", "9.9.9\n"),
                ("venus_evcharger_service.py", "#!/usr/bin/env python3\n"),
                ("venus_evcharger_auto_input_helper.py", "#!/usr/bin/env python3\n"),
                ("deploy/venus/install_venus_evcharger_service.sh", "#!/bin/bash\n"),
                ("deploy/venus/boot_venus_evcharger_service.sh", "#!/bin/bash\n"),
                ("deploy/venus/restart_venus_evcharger_service.sh", "#!/bin/bash\n"),
                ("deploy/venus/uninstall_venus_evcharger_service.sh", "#!/bin/bash\n"),
                ("deploy/venus/service_venus_evcharger/run", "#!/bin/sh\n"),
                ("deploy/venus/service_venus_evcharger/log/run", "#!/bin/sh\n"),
                ("deploy/venus/config.venus_evcharger.ini", "[DEFAULT]\nHost=template-host\nNewToggle=1\n"),
                ("venus_evcharger/__init__.py", "# pkg\n"),
                ("scripts/ops/example.sh", "#!/bin/bash\n"),
            ):
                path = source_dir / rel_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")

            release_dir.mkdir()
            bundle_path = release_dir / "wallbox-bundle.tar.gz"
            with tarfile.open(bundle_path, "w:gz") as tar:
                for rel_path in (
                    "install.sh",
                    "LICENSE",
                    "README.md",
                    "SHELLY_PROFILES.md",
                    "version.txt",
                    "venus_evcharger_service.py",
                    "venus_evcharger_auto_input_helper.py",
                    "deploy/venus",
                    "venus_evcharger",
                    "scripts/ops",
                ):
                    tar.add(source_dir / rel_path, arcname=rel_path)

            bundle_hash = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
            signing_key, public_key = self._generate_signing_keypair(root)
            manifest = {"format": 1, "channel": "stable", "version": "9.9.9", "bundle_url": str(bundle_path), "bundle_sha256": bundle_hash}
            manifest_path = release_dir / "bootstrap_manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            manifest_sig_path = release_dir / "bootstrap_manifest.json.sig"
            subprocess.run(
                ["openssl", "dgst", "-sha256", "-sign", str(signing_key), "-out", str(manifest_sig_path), str(manifest_path)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            (current_release / "deploy/venus").mkdir(parents=True, exist_ok=True)
            preserved_text = "this is not a valid ini file\nwithout = separators\n"
            (current_release / "deploy/venus/config.venus_evcharger.ini").write_text(preserved_text, encoding="utf-8")
            (target_dir / "current").parent.mkdir(parents=True, exist_ok=True)
            (target_dir / "current").symlink_to(current_release)

            with self.assertRaises(subprocess.CalledProcessError):
                subprocess.run(
                    ["bash", str(UPDATER_SCRIPT), str(target_dir)],
                    check=True,
                    env={
                        **os.environ,
                        "VENUS_EVCHARGER_MANIFEST_SOURCE": str(manifest_path),
                        "VENUS_EVCHARGER_MANIFEST_SIG_SOURCE": str(manifest_sig_path),
                        "VENUS_EVCHARGER_BOOTSTRAP_PUBKEY": str(public_key),
                        "VENUS_EVCHARGER_REQUIRE_SIGNED_MANIFEST": "1",
                    },
                )

            self.assertTrue((target_dir / "current").is_symlink())
            self.assertEqual(os.readlink(target_dir / "current"), str(current_release))
            self.assertFalse((target_dir / "releases/9.9.9").exists())
            status = self._read_normalized_status(target_dir)
            self.assertEqual(status["result"], "failed")
            self.assertEqual(status["promotion_aborted_reason"], "config-validation-failed")
            self.assertTrue(status["current_preserved"])
            self.assertEqual(self._read_normalized_latest_audit(target_dir), status)

    def test_bootstrap_updater_uses_manifest_bundle_and_skips_when_current(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "source"
            target_dir = root / "target"
            release_dir = root / "release"

            (source_dir / "deploy/venus/service_venus_evcharger/log").mkdir(parents=True, exist_ok=True)
            (source_dir / "venus_evcharger").mkdir(parents=True, exist_ok=True)
            (source_dir / "scripts/ops").mkdir(parents=True, exist_ok=True)

            for rel_path, content in (
                ("install.sh", "#!/bin/bash\n"),
                ("LICENSE", "license\n"),
                ("README.md", "readme\n"),
                ("SHELLY_PROFILES.md", "profiles\n"),
                ("version.txt", "9.9.9\n"),
                ("venus_evcharger_service.py", "#!/usr/bin/env python3\n"),
                ("venus_evcharger_auto_input_helper.py", "#!/usr/bin/env python3\n"),
                ("deploy/venus/install_venus_evcharger_service.sh", "#!/bin/bash\n"),
                ("deploy/venus/boot_venus_evcharger_service.sh", "#!/bin/bash\n"),
                ("deploy/venus/restart_venus_evcharger_service.sh", "#!/bin/bash\n"),
                ("deploy/venus/uninstall_venus_evcharger_service.sh", "#!/bin/bash\n"),
                ("deploy/venus/service_venus_evcharger/run", "#!/bin/sh\n"),
                ("deploy/venus/service_venus_evcharger/log/run", "#!/bin/sh\n"),
                ("deploy/venus/config.venus_evcharger.ini", "[DEFAULT]\nHost=template-host\n"),
                ("venus_evcharger/__init__.py", "# pkg\n"),
                ("scripts/ops/example.sh", "#!/bin/bash\n"),
            ):
                path = source_dir / rel_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")

            release_dir.mkdir()
            bundle_path = release_dir / "wallbox-bundle.tar.gz"
            with tarfile.open(bundle_path, "w:gz") as tar:
                for rel_path in (
                    "install.sh",
                    "LICENSE",
                    "README.md",
                    "SHELLY_PROFILES.md",
                    "version.txt",
                    "venus_evcharger_service.py",
                    "venus_evcharger_auto_input_helper.py",
                    "deploy/venus",
                    "venus_evcharger",
                    "scripts/ops",
                ):
                    tar.add(source_dir / rel_path, arcname=rel_path)

            bundle_hash = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
            signing_key, public_key = self._generate_signing_keypair(root)
            manifest = {"format": 1, "channel": "stable", "version": "9.9.9", "bundle_url": str(bundle_path), "bundle_sha256": bundle_hash}
            manifest_path = release_dir / "bootstrap_manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            manifest_sig_path = release_dir / "bootstrap_manifest.json.sig"
            subprocess.run(
                ["openssl", "dgst", "-sha256", "-sign", str(signing_key), "-out", str(manifest_sig_path), str(manifest_path)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            (target_dir / "noUpdate").parent.mkdir(parents=True, exist_ok=True)
            (target_dir / "noUpdate").write_text("", encoding="utf-8")

            subprocess.run(
                ["bash", str(UPDATER_SCRIPT), str(target_dir)],
                check=True,
                env={
                    **os.environ,
                    "VENUS_EVCHARGER_MANIFEST_SOURCE": str(manifest_path),
                    "VENUS_EVCHARGER_MANIFEST_SIG_SOURCE": str(manifest_sig_path),
                    "VENUS_EVCHARGER_BOOTSTRAP_PUBKEY": str(public_key),
                    "VENUS_EVCHARGER_REQUIRE_SIGNED_MANIFEST": "1",
                },
            )

            self.assertTrue((target_dir / "current").is_symlink())
            self.assertTrue((target_dir / "current/venus_evcharger_service.py").is_file())
            self.assertTrue((target_dir / "releases/9.9.9/deploy/venus/install_venus_evcharger_service.sh").is_file())
            self.assertTrue((target_dir / "current/deploy/venus/install_venus_evcharger_service.sh").is_file())
            self.assertEqual((target_dir / ".bootstrap-state/installed_bundle_sha256").read_text(encoding="utf-8"), f"{bundle_hash}\n")
            self.assertEqual((target_dir / ".bootstrap-state/installed_version").read_text(encoding="utf-8"), "9.9.9\n")
            self.assertTrue((target_dir / "noUpdate").is_file())

            sentinel = target_dir / "sentinel.txt"
            sentinel.write_text("keep\n", encoding="utf-8")

            subprocess.run(
                ["bash", str(UPDATER_SCRIPT), str(target_dir)],
                check=True,
                env={
                    **os.environ,
                    "VENUS_EVCHARGER_MANIFEST_SOURCE": str(manifest_path),
                    "VENUS_EVCHARGER_MANIFEST_SIG_SOURCE": str(manifest_sig_path),
                    "VENUS_EVCHARGER_BOOTSTRAP_PUBKEY": str(public_key),
                    "VENUS_EVCHARGER_REQUIRE_SIGNED_MANIFEST": "1",
                },
            )

            self.assertTrue(sentinel.is_file())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep\n")

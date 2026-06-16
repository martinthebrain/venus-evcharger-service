# SPDX-License-Identifier: GPL-3.0-or-later
from tests.bootstrap_install_scripts_cases_common import (
    BOOTSTRAP_SCRIPT,
    Path,
    _BootstrapInstallScriptsBase,
    hashlib,
    json,
    os,
    subprocess,
    tempfile,
    UPDATER_HASH,
    UPDATER_LIB_DIR,
    UPDATER_SCRIPT,
)


class _BootstrapInstallScriptsInstallerCases(_BootstrapInstallScriptsBase):
    def _assert_cached_updater_libs(self, bootstrap_dir: Path) -> None:
        cached_lib_dir = bootstrap_dir / ".venus-evcharger-bootstrap/bootstrap_updater.d"
        expected_names = sorted(path.name for path in UPDATER_LIB_DIR.glob("*.sh"))
        self.assertEqual(sorted(path.name for path in cached_lib_dir.glob("*.sh")), expected_names)

    def test_bootstrap_checked_in_updater_hash_matches_updater_script(self) -> None:
        expected_hash = hashlib.sha256(UPDATER_SCRIPT.read_bytes()).hexdigest()
        self.assertEqual(UPDATER_HASH.read_text(encoding="utf-8").split()[0], expected_hash)

    def test_bootstrap_checked_in_reset_template_matches_live_default_config(self) -> None:
        config_template = (UPDATER_SCRIPT.parent / "config.venus_evcharger.ini").read_text(encoding="utf-8")
        reset_template = (UPDATER_SCRIPT.parent / "config.venus_evcharger.default.ini").read_text(encoding="utf-8")
        self.assertEqual(reset_template, config_template)

    def test_bootstrap_installer_defaults_to_hash_validated_updater_when_manifest_is_unset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bootstrap_dir = root / "bootstrap"
            bootstrap_dir.mkdir()
            target_dir = root / "target"
            source_dir = root / "source"
            updater_dir = root / "updater"
            updater_dir.mkdir()

            (source_dir / "deploy/venus/service_venus_evcharger/log").mkdir(parents=True, exist_ok=True)
            (source_dir / "venus_evcharger").mkdir(parents=True, exist_ok=True)
            (source_dir / "scripts/ops").mkdir(parents=True, exist_ok=True)

            for rel_path, content in (
                ("install.sh", "#!/bin/bash\n"),
                ("LICENSE", "license\n"),
                ("README.md", "readme\n"),
                ("SHELLY_PROFILES.md", "profiles\n"),
                ("version.txt", "1.2.3\n"),
                ("venus_evcharger_service.py", "#!/usr/bin/env python3\n"),
                ("venus_evcharger_auto_input_helper.py", "#!/usr/bin/env python3\n"),
                ("deploy/venus/boot_venus_evcharger_service.sh", "#!/bin/bash\n"),
                ("deploy/venus/restart_venus_evcharger_service.sh", "#!/bin/bash\n"),
                ("deploy/venus/uninstall_venus_evcharger_service.sh", "#!/bin/bash\n"),
                ("deploy/venus/service_venus_evcharger/run", "#!/bin/sh\n"),
                ("deploy/venus/service_venus_evcharger/log/run", "#!/bin/sh\n"),
                ("deploy/venus/config.venus_evcharger.ini", "[DEFAULT]\nHost=template-host\n"),
                ("venus_evcharger/__init__.py", "# pkg\n"),
                ("scripts/ops/example.sh", "#!/bin/bash\n"),
                (
                    "deploy/venus/install_venus_evcharger_service.sh",
                    "#!/bin/bash\nprintf 'installed-without-manifest\\n' > \"$(dirname \"$0\")/../../installed.txt\"\n",
                ),
            ):
                path = source_dir / rel_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")

            bootstrap_copy = bootstrap_dir / "install.sh"
            bootstrap_copy.write_text(BOOTSTRAP_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
            bootstrap_copy.chmod(0o755)

            updater_copy = updater_dir / "bootstrap_updater.sh"
            updater_copy.write_text(UPDATER_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
            updater_copy.chmod(0o755)
            self._copy_updater_libs(updater_dir)
            updater_hash = hashlib.sha256(updater_copy.read_bytes()).hexdigest()
            (updater_dir / "bootstrap_updater.sh.sha256").write_text(
                f"{updater_hash}  bootstrap_updater.sh\n",
                encoding="utf-8",
            )

            subprocess.run(
                ["bash", str(bootstrap_copy)],
                check=True,
                env={
                    **os.environ,
                    "VENUS_EVCHARGER_TARGET_DIR": str(target_dir),
                    "VENUS_EVCHARGER_SOURCE_DIR": str(source_dir),
                    "VENUS_EVCHARGER_UPDATER_SOURCE": str(updater_copy),
                },
            )

            cached_updater = bootstrap_dir / ".venus-evcharger-bootstrap/bootstrap_updater.sh"
            self.assertTrue(cached_updater.is_file())
            self.assertEqual(hashlib.sha256(cached_updater.read_bytes()).hexdigest(), updater_hash)
            self._assert_cached_updater_libs(bootstrap_dir)
            self.assertTrue((target_dir / "installed.txt").is_file())
            self.assertEqual((target_dir / "installed.txt").read_text(encoding="utf-8"), "installed-without-manifest\n")

    def test_bootstrap_installer_refreshes_local_updater_and_runs_target_installer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bootstrap_dir = root / "bootstrap"
            bootstrap_dir.mkdir()
            target_dir = root / "target"
            source_dir = root / "source"

            (source_dir / "deploy/venus/service_venus_evcharger/log").mkdir(parents=True, exist_ok=True)
            (source_dir / "venus_evcharger").mkdir(parents=True, exist_ok=True)
            (source_dir / "scripts/ops").mkdir(parents=True, exist_ok=True)

            for rel_path, content in (
                ("install.sh", "#!/bin/bash\n"),
                ("LICENSE", "license\n"),
                ("README.md", "readme\n"),
                ("SHELLY_PROFILES.md", "profiles\n"),
                ("version.txt", "1.2.3\n"),
                ("venus_evcharger_service.py", "#!/usr/bin/env python3\n"),
                ("venus_evcharger_auto_input_helper.py", "#!/usr/bin/env python3\n"),
                ("deploy/venus/boot_venus_evcharger_service.sh", "#!/bin/bash\n"),
                ("deploy/venus/restart_venus_evcharger_service.sh", "#!/bin/bash\n"),
                ("deploy/venus/uninstall_venus_evcharger_service.sh", "#!/bin/bash\n"),
                ("deploy/venus/service_venus_evcharger/run", "#!/bin/sh\n"),
                ("deploy/venus/service_venus_evcharger/log/run", "#!/bin/sh\n"),
                ("deploy/venus/config.venus_evcharger.ini", "[DEFAULT]\nHost=template-host\n"),
                ("venus_evcharger/__init__.py", "# pkg\n"),
                ("scripts/ops/example.sh", "#!/bin/bash\n"),
                (
                    "deploy/venus/install_venus_evcharger_service.sh",
                    "#!/bin/bash\nprintf 'installed\\n' > \"$(dirname \"$0\")/../../installed.txt\"\n",
                ),
            ):
                path = source_dir / rel_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")

            bootstrap_copy = bootstrap_dir / "install.sh"
            bootstrap_copy.write_text(BOOTSTRAP_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
            bootstrap_copy.chmod(0o755)

            signing_key, public_key = self._generate_signing_keypair(root)
            expected_hash = hashlib.sha256(UPDATER_SCRIPT.read_bytes()).hexdigest()
            manifest = {"format": 1, "channel": "stable", "version": "1.0.0", "updater_url": str(UPDATER_SCRIPT), "updater_sha256": expected_hash}
            manifest_path = root / "bootstrap_manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            manifest_sig_path = root / "bootstrap_manifest.json.sig"
            subprocess.run(
                ["openssl", "dgst", "-sha256", "-sign", str(signing_key), "-out", str(manifest_sig_path), str(manifest_path)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            subprocess.run(
                ["bash", str(bootstrap_copy)],
                check=True,
                env={
                    **os.environ,
                    "VENUS_EVCHARGER_TARGET_DIR": str(target_dir),
                    "VENUS_EVCHARGER_SOURCE_DIR": str(source_dir),
                    "VENUS_EVCHARGER_MANIFEST_SOURCE": str(manifest_path),
                    "VENUS_EVCHARGER_MANIFEST_SIG_SOURCE": str(manifest_sig_path),
                    "VENUS_EVCHARGER_BOOTSTRAP_PUBKEY": str(public_key),
                    "VENUS_EVCHARGER_REQUIRE_SIGNED_MANIFEST": "1",
                },
            )

            self.assertTrue((bootstrap_dir / ".venus-evcharger-bootstrap/bootstrap_updater.sh").is_file())
            self._assert_cached_updater_libs(bootstrap_dir)
            self.assertTrue((target_dir / "installed.txt").is_file())
            self.assertEqual((target_dir / "installed.txt").read_text(encoding="utf-8"), "installed\n")
            self.assertTrue((target_dir / "deploy/venus/install_venus_evcharger_service.sh").is_file())

    def test_bootstrap_rolls_back_to_previous_release_when_current_installer_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bootstrap_dir = root / "bootstrap"
            bootstrap_dir.mkdir()
            target_dir = root / "target"
            current_dir = target_dir / "releases/2.0.0"
            previous_dir = target_dir / "releases/1.0.0"
            (current_dir / "deploy/venus").mkdir(parents=True, exist_ok=True)
            (previous_dir / "deploy/venus").mkdir(parents=True, exist_ok=True)

            (current_dir / "deploy/venus/install_venus_evcharger_service.sh").write_text("#!/bin/bash\nexit 1\n", encoding="utf-8")
            (previous_dir / "deploy/venus/install_venus_evcharger_service.sh").write_text(
                "#!/bin/bash\nprintf 'rolled-back\\n' > \"$(dirname \"$0\")/../../installed.txt\"\n",
                encoding="utf-8",
            )
            (current_dir / "deploy/venus/install_venus_evcharger_service.sh").chmod(0o755)
            (previous_dir / "deploy/venus/install_venus_evcharger_service.sh").chmod(0o755)

            (target_dir / "current").parent.mkdir(parents=True, exist_ok=True)
            (target_dir / "current").symlink_to(current_dir)
            (target_dir / "previous").symlink_to(previous_dir)

            bootstrap_copy = bootstrap_dir / "install.sh"
            bootstrap_copy.write_text(BOOTSTRAP_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
            bootstrap_copy.chmod(0o755)
            (bootstrap_dir / "noUpdate").write_text("", encoding="utf-8")

            subprocess.run(
                ["bash", str(bootstrap_copy)],
                check=True,
                env={**os.environ, "VENUS_EVCHARGER_TARGET_DIR": str(target_dir)},
            )

            self.assertEqual((previous_dir / "installed.txt").read_text(encoding="utf-8"), "rolled-back\n")
            self.assertTrue((target_dir / "current").is_symlink())
            self.assertEqual(os.readlink(target_dir / "current"), str(previous_dir))

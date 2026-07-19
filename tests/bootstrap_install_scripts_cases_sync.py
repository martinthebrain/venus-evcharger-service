# SPDX-License-Identifier: GPL-3.0-or-later
import json

from tests.bootstrap_install_scripts_cases_common import Path, _BootstrapInstallScriptsBase, hashlib, os, subprocess, tempfile, UPDATER_SCRIPT
from venus_evcharger.core.contracts import normalized_bootstrap_update_status_fields


class _BootstrapInstallScriptsSyncCases(_BootstrapInstallScriptsBase):
    def test_bootstrap_updater_syncs_local_source_and_preserves_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "source"
            target_dir = root / "target"
            outer_bootstrap = root / "install.sh"
            outer_bootstrap.write_text("#!/bin/bash\n# Minimal GX bootstrap installer\n# old\n", encoding="utf-8")
            fake_bin = root / "bin"
            fake_bin.mkdir()
            nice_log = root / "nice.log"
            fake_nice = fake_bin / "nice"
            fake_nice.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' \"$*\" >> \"$NICE_LOG\"\n"
                "if [ \"$1\" = '-n' ]; then shift 2; fi\n"
                "exec \"$@\"\n",
                encoding="utf-8",
            )
            fake_nice.chmod(0o755)

            (source_dir / "deploy/venus/service_venus_evcharger/log").mkdir(parents=True, exist_ok=True)
            (source_dir / "venus_evcharger").mkdir(parents=True, exist_ok=True)
            (source_dir / "scripts/ops").mkdir(parents=True, exist_ok=True)
            (source_dir / "tests").mkdir(parents=True, exist_ok=True)
            (source_dir / "docs").mkdir(parents=True, exist_ok=True)

            (source_dir / "install.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "LICENSE").write_text("license\n", encoding="utf-8")
            (source_dir / "README.md").write_text("readme\n", encoding="utf-8")
            (source_dir / "SHELLY_PROFILES.md").write_text("profiles\n", encoding="utf-8")
            (source_dir / "version.txt").write_text("Version: 1.2.3\n", encoding="utf-8")
            (source_dir / "venus_evcharger_service.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            (source_dir / "venus_evcharger_auto_input_helper.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            (source_dir / "deploy/venus/install_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_lifecycle.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/boot_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/restart_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/uninstall_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_venus_evcharger/run").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_venus_evcharger/log/run").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_venus_evcharger_dbus_adapter").mkdir(parents=True)
            (source_dir / "deploy/venus/service_venus_evcharger_dbus_adapter/run").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_venus_evcharger_observer").mkdir(parents=True)
            (source_dir / "deploy/venus/service_venus_evcharger_observer/run").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/config.venus_evcharger.ini").write_text(
                "[DEFAULT]\n"
                "ConfigSchemaVersion=1\n"
                "Host=template-host\n"
                "Mode=0\n"
                "NewToggle=1\n"
                "\n"
                "[Backend]\n"
                "Type=shelly\n"
                "ExtraSetting=42\n",
                encoding="utf-8",
            )
            (source_dir / "venus_evcharger/__init__.py").write_text("# pkg\n", encoding="utf-8")
            (source_dir / "scripts/ops/example.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "tests/should_not_ship.txt").write_text("omit\n", encoding="utf-8")
            (source_dir / "docs/should_not_ship.txt").write_text("omit\n", encoding="utf-8")

            (target_dir / "deploy/venus").mkdir(parents=True, exist_ok=True)
            live_service_dir = target_dir / "deploy/venus/service_venus_evcharger"
            (live_service_dir / "log").mkdir(parents=True)
            (live_service_dir / "run").write_text("stale run\n", encoding="utf-8")
            (live_service_dir / "stale.txt").write_text("remove\n", encoding="utf-8")
            service_inode = live_service_dir.stat().st_ino
            original_config = (
                "[DEFAULT]\n"
                "# keep this local host comment\n"
                "Host=keep-me\n"
                "Mode=2\n"
                "\n"
                "[Backend]\n"
                "# keep this local backend comment\n"
                "Type=custom\n"
            )
            (target_dir / "deploy/venus/config.venus_evcharger.ini").write_text(original_config, encoding="utf-8")
            (target_dir / "deploy/venus/config.venus_evcharger.ini.wizard-inventory.ini").write_text(
                "[Profile:custom-device]\n"
                "Label=Custom Device\n",
                encoding="utf-8",
            )
            (target_dir / "deploy/venus/config.venus_evcharger.ini.wizard-topology.txt").write_text(
                "Measurement -> device-a\n",
                encoding="utf-8",
            )
            (target_dir / "deploy/venus/wizard-meter.ini").write_text(
                "[Adapter]\n"
                "Type=template_meter\n",
                encoding="utf-8",
            )
            (target_dir / "tests").mkdir(parents=True, exist_ok=True)
            (target_dir / "tests/stale.txt").write_text("stale\n", encoding="utf-8")
            (target_dir / "venus_evcharger").mkdir(parents=True, exist_ok=True)
            retired_adapter_modules = (
                "dbus_adapter_process.py",
                "dbus_adapter_read.py",
                "dbus_adapter_write.py",
            )
            for retired_module in retired_adapter_modules:
                (target_dir / "venus_evcharger" / retired_module).write_text("retired\n", encoding="utf-8")
            for retired_name in (
                "DBUS_INTROSPECTION_WORKER.md",
                "dbus_adapter_write.py",
                "venus_evcharger_dbus_introspection_worker.py",
            ):
                (target_dir / retired_name).write_text("retired\n", encoding="utf-8")

            subprocess.run(
                ["bash", str(UPDATER_SCRIPT), str(target_dir)],
                check=True,
                env={
                    **os.environ,
                    "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                    "NICE_LOG": str(nice_log),
                    "VENUS_EVCHARGER_RESOURCE_PRIORITY_APPLIED": "0",
                    "VENUS_EVCHARGER_SOURCE_DIR": str(source_dir),
                },
            )

            self.assertTrue((target_dir / "venus_evcharger_service.py").is_file())
            self.assertEqual(outer_bootstrap.read_text(encoding="utf-8"), "#!/bin/bash\n")
            self.assertEqual(nice_log.read_text(encoding="utf-8").splitlines(), ["-n 10 " + str(UPDATER_SCRIPT) + " " + str(target_dir)])
            self.assertTrue((target_dir / "deploy/venus/install_venus_evcharger_service.sh").is_file())
            self.assertEqual(live_service_dir.stat().st_ino, service_inode)
            self.assertEqual((live_service_dir / "run").read_text(encoding="utf-8"), "#!/bin/sh\n")
            self.assertFalse((live_service_dir / "stale.txt").exists())
            self.assertTrue((target_dir / "venus_evcharger/__init__.py").is_file())
            merged_config = (target_dir / "deploy/venus/config.venus_evcharger.ini").read_text(encoding="utf-8")
            self.assertIn("# keep this local host comment\n", merged_config)
            self.assertIn("Host=keep-me\n", merged_config)
            self.assertIn("Mode=2\n", merged_config)
            self.assertIn("ConfigSchemaVersion=1\n", merged_config)
            self.assertIn("NewToggle=1\n", merged_config)
            self.assertIn("[Backend]\n", merged_config)
            self.assertIn("# keep this local backend comment\n", merged_config)
            self.assertIn("Type=custom\n", merged_config)
            self.assertIn("ExtraSetting=42\n", merged_config)
            self.assertEqual(
                (target_dir / "deploy/venus/config.venus_evcharger.ini.wizard-inventory.ini").read_text(encoding="utf-8"),
                "[Profile:custom-device]\nLabel=Custom Device\n",
            )
            self.assertEqual(
                (target_dir / "deploy/venus/config.venus_evcharger.ini.wizard-topology.txt").read_text(encoding="utf-8"),
                "Measurement -> device-a\n",
            )
            self.assertEqual(
                (target_dir / "deploy/venus/wizard-meter.ini").read_text(encoding="utf-8"),
                "[Adapter]\nType=template_meter\n",
            )
            self.assertFalse((target_dir / "tests").exists())
            self.assertFalse((target_dir / "docs").exists())
            for retired_name in (
                "DBUS_INTROSPECTION_WORKER.md",
                "dbus_adapter_write.py",
                "venus_evcharger_dbus_introspection_worker.py",
            ):
                self.assertFalse((target_dir / retired_name).exists())
            for retired_module in retired_adapter_modules:
                self.assertFalse((target_dir / "venus_evcharger" / retired_module).exists())
            backup_candidates = sorted((target_dir / "deploy/venus").glob("config.venus_evcharger.ini.bak-*"))
            self.assertEqual(len(backup_candidates), 1)
            self.assertEqual(backup_candidates[0].read_text(encoding="utf-8"), original_config)
            status = self._read_normalized_status(target_dir)
            self.assertEqual(status["result"], "success")
            self.assertTrue(status["config_merge_changed"])
            self.assertTrue(status["config_merge_comment_preserved"])
            self.assertTrue(status["config_validation_passed"])
            self.assertEqual(status["config_schema_before"], "0")
            self.assertEqual(status["config_schema_target"], "1")
            self.assertEqual(status["new_version"], "1.2.3")
            self.assertTrue(status["bootstrap_refreshed"])
            self.assertEqual(status["bootstrap_entrypoint_path"], str(outer_bootstrap))
            self.assertIn("DEFAULT.ConfigSchemaVersion", status["config_merge_added_keys"])
            self.assertIn("DEFAULT.NewToggle", status["config_merge_added_keys"])
            self.assertIn("Backend.ExtraSetting", status["config_merge_added_keys"])
            self.assertEqual(status["config_merge_backup_path"], str(backup_candidates[0]))
            self.assertTrue((target_dir / ".bootstrap-state/update_audit.log").is_file())
            self.assertEqual(self._read_normalized_latest_audit(target_dir), status)
            self.assertEqual((target_dir / ".bootstrap-state/installed_version").read_text(encoding="utf-8"), "1.2.3\n")
            receipt = json.loads((target_dir / ".bootstrap-state/deployment_receipt.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["schema_version"], 1)
            self.assertEqual(receipt["version"], "1.2.3")
            self.assertEqual(receipt["critical_files"]["venus_evcharger_service.py"], hashlib.sha256(b"#!/usr/bin/env python3\n").hexdigest())
            self.assertIn("venus_evcharger_dbus_adapter.py", receipt["missing_critical_files"])

    def test_bootstrap_updater_rejects_invalid_preserved_config_when_validation_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "source"
            target_dir = root / "target"

            (source_dir / "deploy/venus/service_venus_evcharger/log").mkdir(parents=True, exist_ok=True)
            (source_dir / "venus_evcharger").mkdir(parents=True, exist_ok=True)
            (source_dir / "scripts/ops").mkdir(parents=True, exist_ok=True)

            (source_dir / "install.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "LICENSE").write_text("license\n", encoding="utf-8")
            (source_dir / "README.md").write_text("readme\n", encoding="utf-8")
            (source_dir / "SHELLY_PROFILES.md").write_text("profiles\n", encoding="utf-8")
            (source_dir / "version.txt").write_text("1.2.3\n", encoding="utf-8")
            (source_dir / "venus_evcharger_service.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            (source_dir / "venus_evcharger_auto_input_helper.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            (source_dir / "deploy/venus/install_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_lifecycle.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/boot_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/restart_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/uninstall_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_venus_evcharger/run").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_venus_evcharger/log/run").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_venus_evcharger_dbus_adapter").mkdir(parents=True)
            (source_dir / "deploy/venus/service_venus_evcharger_dbus_adapter/run").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_venus_evcharger_observer").mkdir(parents=True)
            (source_dir / "deploy/venus/service_venus_evcharger_observer/run").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/config.venus_evcharger.ini").write_text(
                "[DEFAULT]\nHost=template-host\nNewToggle=1\n",
                encoding="utf-8",
            )
            (source_dir / "venus_evcharger/__init__.py").write_text("# pkg\n", encoding="utf-8")
            (source_dir / "scripts/ops/example.sh").write_text("#!/bin/bash\n", encoding="utf-8")

            (target_dir / "deploy/venus").mkdir(parents=True, exist_ok=True)
            preserved_text = "this is not a valid ini file\nwithout = separators\n"
            (target_dir / "deploy/venus/config.venus_evcharger.ini").write_text(preserved_text, encoding="utf-8")

            with self.assertRaises(subprocess.CalledProcessError):
                subprocess.run(
                    ["bash", str(UPDATER_SCRIPT), str(target_dir)],
                    check=True,
                    env={**os.environ, "VENUS_EVCHARGER_SOURCE_DIR": str(source_dir)},
                )

            self.assertEqual((target_dir / "deploy/venus/config.venus_evcharger.ini").read_text(encoding="utf-8"), preserved_text)
            self.assertFalse((target_dir / "venus_evcharger_service.py").exists())
            status = self._read_normalized_status(target_dir)
            self.assertEqual(status["result"], "failed")
            self.assertEqual(status["failure_reason"], "config-validation-failed")
            self.assertEqual(status["config_merge_skipped_reason"], "malformed-local-config")
            self.assertFalse(status["config_validation_passed"])
            self.assertEqual(self._read_normalized_latest_audit(target_dir), status)

    def test_bootstrap_updater_dry_run_reports_preview_without_modifying_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "source"
            target_dir = root / "target"

            (source_dir / "deploy/venus/service_venus_evcharger/log").mkdir(parents=True, exist_ok=True)
            (source_dir / "venus_evcharger").mkdir(parents=True, exist_ok=True)
            (source_dir / "scripts/ops").mkdir(parents=True, exist_ok=True)

            (source_dir / "install.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "LICENSE").write_text("license\n", encoding="utf-8")
            (source_dir / "README.md").write_text("readme\n", encoding="utf-8")
            (source_dir / "SHELLY_PROFILES.md").write_text("profiles\n", encoding="utf-8")
            (source_dir / "version.txt").write_text("Version: 2.0.0\n", encoding="utf-8")
            (source_dir / "venus_evcharger_service.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            (source_dir / "venus_evcharger_auto_input_helper.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            (source_dir / "deploy/venus/install_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_lifecycle.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/boot_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/restart_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/uninstall_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_venus_evcharger/run").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_venus_evcharger/log/run").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_venus_evcharger_dbus_adapter").mkdir(parents=True)
            (source_dir / "deploy/venus/service_venus_evcharger_dbus_adapter/run").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_venus_evcharger_observer").mkdir(parents=True)
            (source_dir / "deploy/venus/service_venus_evcharger_observer/run").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/config.venus_evcharger.ini").write_text(
                "[DEFAULT]\nConfigSchemaVersion=1\nHost=template-host\nNewToggle=1\n",
                encoding="utf-8",
            )
            (source_dir / "venus_evcharger/__init__.py").write_text("# pkg\n", encoding="utf-8")
            (source_dir / "scripts/ops/example.sh").write_text("#!/bin/bash\n", encoding="utf-8")

            (target_dir / "deploy/venus").mkdir(parents=True, exist_ok=True)
            original_config = "[DEFAULT]\nHost=keep-me\n"
            (target_dir / "deploy/venus/config.venus_evcharger.ini").write_text(original_config, encoding="utf-8")

            completed = subprocess.run(
                ["bash", str(UPDATER_SCRIPT), "--dry-run", str(target_dir)],
                check=True,
                capture_output=True,
                text=True,
                env={**os.environ, "VENUS_EVCHARGER_SOURCE_DIR": str(source_dir)},
            )

            preview = normalized_bootstrap_update_status_fields(json.loads(completed.stdout.strip()))
            self.assertEqual(preview["mode"], "dry-run")
            self.assertEqual(preview["result"], "preview")
            self.assertEqual(preview["new_version"], "2.0.0")
            self.assertTrue(preview["config_merge_changed"])
            self.assertTrue(preview["config_merge_backup_required"])
            self.assertTrue(preview["config_validation_passed"])
            self.assertIn("DEFAULT.ConfigSchemaVersion", preview["config_merge_added_keys"])
            self.assertIn("DEFAULT.NewToggle", preview["config_merge_added_keys"])
            self.assertEqual((target_dir / "deploy/venus/config.venus_evcharger.ini").read_text(encoding="utf-8"), original_config)
            self.assertFalse((target_dir / "venus_evcharger_service.py").exists())
            self.assertFalse((target_dir / ".bootstrap-state/update_status.json").exists())

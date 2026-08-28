# SPDX-License-Identifier: GPL-3.0-or-later
import fcntl
import json

from tests.bootstrap_install_scripts_cases_common import Path, _BootstrapInstallScriptsBase, hashlib, os, subprocess, tempfile, UPDATER_SCRIPT
from venus_evcharger.core.contracts import normalized_bootstrap_update_status_fields


class _BootstrapInstallScriptsSyncCases(_BootstrapInstallScriptsBase):
    def test_bootstrap_updater_selects_ram_sd_and_data_workspaces_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ram_base = root / "ram"
            sd_mount = root / "sd"
            ram_base.mkdir()
            sd_mount.mkdir()
            ram_mountpoint = subprocess.run(
                ["df", "-Pk", str(ram_base)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.splitlines()[1].split()[-1]

            cases = (
                (
                    "ram",
                    "MemAvailable:      524288 kB\n",
                    f"tmpfs {ram_mountpoint} tmpfs rw 0 0\n/dev/mmcblk0p1 {sd_mount} ext4 rw 0 0\n",
                    ram_base / "venus-evcharger-updater-work",
                ),
                (
                    "sd",
                    "MemAvailable:      131072 kB\n",
                    f"tmpfs {ram_mountpoint} tmpfs rw 0 0\n/dev/mmcblk0p1 {sd_mount} ext4 rw 0 0\n",
                    sd_mount / ".venus-evcharger-updater-work",
                ),
                (
                    "data",
                    "MemAvailable:      131072 kB\n",
                    f"overlay {ram_mountpoint} ext4 rw 0 0\n",
                    None,
                ),
            )

            for index, (expected_storage, meminfo, mounts, expected_root) in enumerate(cases):
                with self.subTest(storage=expected_storage):
                    target_dir = root / f"target-{index}"
                    mounts_path = root / f"mounts-{index}"
                    meminfo_path = root / f"meminfo-{index}"
                    mounts_path.write_text(mounts, encoding="utf-8")
                    meminfo_path.write_text(meminfo, encoding="utf-8")
                    if expected_root is None:
                        expected_root = target_dir / ".bootstrap-state/work"

                    completed = subprocess.run(
                        ["bash", str(UPDATER_SCRIPT), str(target_dir)],
                        check=False,
                        capture_output=True,
                        text=True,
                        env={
                            **os.environ,
                            "VENUS_EVCHARGER_SOURCE_DIR": str(root / "missing-source"),
                            "VENUS_EVCHARGER_UPDATER_RAM_WORK_BASE": str(ram_base),
                            "VENUS_EVCHARGER_UPDATER_RAM_MIN_MEM_AVAILABLE_KB": "393216",
                            "VENUS_EVCHARGER_UPDATER_RAM_MIN_FILESYSTEM_AVAILABLE_KB": "1",
                            "VENUS_EVCHARGER_UPDATER_MEMINFO_PATH": str(meminfo_path),
                            "VENUS_EVCHARGER_UPDATER_MOUNTS_PATH": str(mounts_path),
                            "VENUS_EVCHARGER_UPDATER_RESOURCE_GUARD": "0",
                            "VENUS_REMOVABLE_STORAGE_MAINTENANCE_LOCK_PATH": str(
                                root / f"storage-maintenance-{index}.lock"
                            ),
                            **(
                                {"VENUS_EVCHARGER_UPDATER_SD_WORK_ROOT": str(expected_root)}
                                if expected_storage == "sd"
                                else {}
                            ),
                        },
                    )

                    self.assertEqual(completed.returncode, 1)
                    self.assertIn(f"Using {expected_storage} updater workspace", completed.stderr)
                    status = self._read_normalized_status(target_dir)
                    self.assertEqual(status["work_storage"], expected_storage)
                    self.assertEqual(status["work_root"], str(expected_root))
                    self.assertEqual(status["failure_reason"], "incomplete-local-source")
                    self.assertEqual(list(expected_root.iterdir()), [])

    def test_bootstrap_updater_avoids_sd_during_external_maintenance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sd_mount = root / "sd"
            sd_mount.mkdir()
            mounts_path = root / "mounts"
            mounts_path.write_text(
                f"/dev/mmcblk0p1 {sd_mount} vfat rw 0 0\n",
                encoding="utf-8",
            )
            meminfo_path = root / "meminfo"
            meminfo_path.write_text(
                "MemAvailable:      131072 kB\n",
                encoding="utf-8",
            )
            lock_path = root / "storage-maintenance.lock"
            descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                target_dir = root / "target"
                completed = subprocess.run(
                    ["bash", str(UPDATER_SCRIPT), str(target_dir)],
                    check=False,
                    capture_output=True,
                    text=True,
                    env={
                        **os.environ,
                        "VENUS_EVCHARGER_SOURCE_DIR": str(
                            root / "missing-source"
                        ),
                        "VENUS_EVCHARGER_UPDATER_RAM_WORK_BASE": str(
                            root / "missing-ram"
                        ),
                        "VENUS_EVCHARGER_UPDATER_MEMINFO_PATH": str(
                            meminfo_path
                        ),
                        "VENUS_EVCHARGER_UPDATER_MOUNTS_PATH": str(mounts_path),
                        "VENUS_EVCHARGER_UPDATER_SD_WORK_ROOT": str(
                            sd_mount / ".venus-evcharger-updater-work"
                        ),
                        "VENUS_EVCHARGER_UPDATER_RESOURCE_GUARD": "0",
                        "VENUS_EVCHARGER_UPDATER_STORAGE_MAINTENANCE_WAIT_SECONDS": "0",
                        "VENUS_REMOVABLE_STORAGE_MAINTENANCE_LOCK_PATH": str(
                            lock_path
                        ),
                    },
                )
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

            self.assertEqual(completed.returncode, 1)
            self.assertIn(
                "Removable-storage maintenance remained active; skipping SD workspace",
                completed.stderr,
            )
            self.assertIn("Using data updater workspace", completed.stderr)
            status = self._read_normalized_status(target_dir)
            self.assertEqual(status["work_storage"], "data")
            self.assertFalse((sd_mount / ".venus-evcharger-updater-work").exists())

    def test_bootstrap_updater_rejects_a_concurrent_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target_dir = root / "target"
            lock_dir = target_dir / ".bootstrap-state/update.lock"
            lock_dir.mkdir(parents=True)
            (lock_dir / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")

            completed = subprocess.run(
                ["bash", str(UPDATER_SCRIPT), str(target_dir)],
                check=False,
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "VENUS_EVCHARGER_SOURCE_DIR": str(root / "missing-source"),
                    "VENUS_EVCHARGER_UPDATER_RESOURCE_GUARD": "0",
                },
            )

            self.assertEqual(completed.returncode, 73)
            self.assertIn("Another updater is already running", completed.stderr)
            self.assertEqual((lock_dir / "pid").read_text(encoding="utf-8"), f"{os.getpid()}\n")
            status = self._read_normalized_status(target_dir)
            self.assertEqual(status["failure_reason"], "update-already-running")

    def test_bootstrap_updater_aborts_before_source_work_under_resource_pressure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target_dir = root / "target"
            work_root = root / "persistent-work"
            loadavg_path = root / "loadavg"
            meminfo_path = root / "meminfo"
            loadavg_path.write_text("12.5 8.0 4.0 1/100 1\n", encoding="utf-8")
            meminfo_path.write_text("MemAvailable:      262144 kB\n", encoding="utf-8")

            completed = subprocess.run(
                ["bash", str(UPDATER_SCRIPT), str(target_dir)],
                check=False,
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "VENUS_EVCHARGER_SOURCE_DIR": str(root / "missing-source"),
                    "VENUS_EVCHARGER_UPDATER_WORK_ROOT": str(work_root),
                    "VENUS_EVCHARGER_UPDATER_LOADAVG_PATH": str(loadavg_path),
                    "VENUS_EVCHARGER_UPDATER_MEMINFO_PATH": str(meminfo_path),
                    "VENUS_EVCHARGER_UPDATER_RESOURCE_WAIT_SECONDS": "0",
                },
            )

            self.assertEqual(completed.returncode, 75)
            self.assertIn("Resource pressure persisted during update-start", completed.stderr)
            status = self._read_normalized_status(target_dir)
            self.assertEqual(status["result"], "failed")
            self.assertEqual(status["failure_reason"], "resource-pressure:update-start:load1=12.5>8.0")
            self.assertEqual(status["work_storage"], "override")
            self.assertEqual(status["work_root"], str(work_root))
            self.assertFalse((target_dir / ".bootstrap-state/update.lock").exists())
            self.assertEqual(list(work_root.iterdir()), [])

    def test_bootstrap_updater_uses_lower_memory_floor_for_persistent_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ram_base = root / "ram"
            sd_mount = root / "sd"
            ram_base.mkdir()
            sd_mount.mkdir()
            ram_mountpoint = subprocess.run(
                ["df", "-Pk", str(ram_base)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.splitlines()[1].split()[-1]
            mounts_path = root / "mounts"
            mounts_path.write_text(
                f"tmpfs {ram_mountpoint} tmpfs rw 0 0\n/dev/mmcblk0p1 {sd_mount} ext4 rw 0 0\n",
                encoding="utf-8",
            )
            meminfo_path = root / "meminfo"
            meminfo_path.write_text("MemAvailable:       39012 kB\n", encoding="utf-8")
            target_dir = root / "target"

            completed = subprocess.run(
                ["bash", str(UPDATER_SCRIPT), str(target_dir)],
                check=False,
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "VENUS_EVCHARGER_SOURCE_DIR": str(root / "missing-source"),
                    "VENUS_EVCHARGER_UPDATER_RAM_WORK_BASE": str(ram_base),
                    "VENUS_EVCHARGER_UPDATER_RAM_MIN_MEM_AVAILABLE_KB": "393216",
                    "VENUS_EVCHARGER_UPDATER_RAM_MIN_FILESYSTEM_AVAILABLE_KB": "1",
                    "VENUS_EVCHARGER_UPDATER_MEMINFO_PATH": str(meminfo_path),
                    "VENUS_EVCHARGER_UPDATER_MOUNTS_PATH": str(mounts_path),
                    "VENUS_EVCHARGER_UPDATER_SD_WORK_ROOT": str(sd_mount / ".venus-evcharger-updater-work"),
                    "VENUS_EVCHARGER_UPDATER_MIN_MEM_AVAILABLE_KB": "65536",
                    "VENUS_EVCHARGER_UPDATER_PERSISTENT_MIN_MEM_AVAILABLE_KB": "32768",
                    "VENUS_EVCHARGER_UPDATER_RESOURCE_WAIT_SECONDS": "0",
                    "VENUS_REMOVABLE_STORAGE_MAINTENANCE_LOCK_PATH": str(
                        root / "storage-maintenance.lock"
                    ),
                },
            )

            self.assertEqual(completed.returncode, 1)
            self.assertIn("Using sd updater workspace", completed.stderr)
            self.assertNotIn("Resource pressure", completed.stderr)
            status = self._read_normalized_status(target_dir)
            self.assertEqual(status["failure_reason"], "incomplete-local-source")
            self.assertEqual(status["work_storage"], "sd")

    def test_bootstrap_updater_keeps_memory_guard_for_persistent_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            work_root = root / "sd-work"
            work_root.mkdir()
            meminfo_path = root / "meminfo"
            meminfo_path.write_text("MemAvailable:       30000 kB\n", encoding="utf-8")

            completed = subprocess.run(
                ["bash", str(UPDATER_SCRIPT), str(root / "target")],
                check=False,
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "VENUS_EVCHARGER_SOURCE_DIR": str(root / "missing-source"),
                    "VENUS_EVCHARGER_UPDATER_SD_WORK_ROOT": str(work_root),
                    "VENUS_EVCHARGER_UPDATER_RAM_WORK_BASE": str(root / "missing-ram"),
                    "VENUS_EVCHARGER_UPDATER_MEMINFO_PATH": str(meminfo_path),
                    "VENUS_EVCHARGER_UPDATER_PERSISTENT_MIN_MEM_AVAILABLE_KB": "32768",
                    "VENUS_EVCHARGER_UPDATER_RESOURCE_WAIT_SECONDS": "0",
                    "VENUS_REMOVABLE_STORAGE_MAINTENANCE_LOCK_PATH": str(
                        root / "storage-maintenance.lock"
                    ),
                },
            )

            self.assertEqual(completed.returncode, 75)
            self.assertIn("mem_available_kb=30000<32768", completed.stderr)
            status = self._read_normalized_status(root / "target")
            self.assertEqual(
                status["failure_reason"],
                "resource-pressure:update-start:mem_available_kb=30000<32768",
            )
            self.assertEqual(status["work_storage"], "sd")

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
            (source_dir / "deploy/venus/install_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_lifecycle.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/boot_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/restart_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/uninstall_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            service_run_contents = {
                "service_venus_evcharger": "#!/bin/sh\n# core service\n",
                "service_venus_evcharger_dbus_adapter": "#!/bin/sh\n# adapter service\n",
                "service_venus_evcharger_observer": "#!/bin/sh\n# observer service\n",
            }
            (source_dir / "deploy/venus/service_venus_evcharger/run").write_text(
                service_run_contents["service_venus_evcharger"], encoding="utf-8"
            )
            (source_dir / "deploy/venus/service_venus_evcharger/log/run").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_venus_evcharger_dbus_adapter").mkdir(parents=True)
            (source_dir / "deploy/venus/service_venus_evcharger_dbus_adapter/run").write_text(
                service_run_contents["service_venus_evcharger_dbus_adapter"], encoding="utf-8"
            )
            (source_dir / "deploy/venus/bin").mkdir(parents=True)
            (source_dir / "deploy/venus/bin/venus-evcharger-auto-input-helper").write_text(
                "auto-input-helper-binary\n",
                encoding="utf-8",
            )
            (source_dir / "deploy/venus/bin/venus-evcharger-dbus-adapter").write_text(
                "dbus-adapter-binary\n",
                encoding="utf-8",
            )
            (source_dir / "deploy/venus/bin/venus-evcharger-forensic-observer").write_text(
                "observer-binary\n",
                encoding="utf-8",
            )
            (source_dir / "deploy/venus/service_venus_evcharger_observer/log").mkdir(parents=True)
            (source_dir / "deploy/venus/service_venus_evcharger_observer/run").write_text(
                service_run_contents["service_venus_evcharger_observer"], encoding="utf-8"
            )
            (source_dir / "deploy/venus/service_venus_evcharger_observer/log/run").write_text(
                "#!/bin/sh\n",
                encoding="utf-8",
            )
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
            live_service_dirs: dict[str, Path] = {}
            service_inodes: dict[str, int] = {}
            for service_name in service_run_contents:
                live_service_dir = target_dir / "deploy/venus" / service_name
                (live_service_dir / "log").mkdir(parents=True)
                (live_service_dir / "run").write_text("stale run\n", encoding="utf-8")
                (live_service_dir / "stale.txt").write_text("remove\n", encoding="utf-8")
                live_service_dirs[service_name] = live_service_dir
                service_inodes[service_name] = live_service_dir.stat().st_ino
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
            self.assertEqual(nice_log.read_text(encoding="utf-8").splitlines(), ["-n 15 " + str(UPDATER_SCRIPT) + " " + str(target_dir)])
            self.assertTrue((target_dir / "deploy/venus/install_venus_evcharger_service.sh").is_file())
            for service_name, expected_run in service_run_contents.items():
                live_service_dir = live_service_dirs[service_name]
                self.assertEqual(live_service_dir.stat().st_ino, service_inodes[service_name])
                self.assertEqual((live_service_dir / "run").read_text(encoding="utf-8"), expected_run)
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
            self.assertEqual(
                receipt["critical_files"]["deploy/venus/bin/venus-evcharger-auto-input-helper"],
                hashlib.sha256(b"auto-input-helper-binary\n").hexdigest(),
            )
            self.assertEqual(
                receipt["critical_files"]["deploy/venus/bin/venus-evcharger-forensic-observer"],
                hashlib.sha256(b"observer-binary\n").hexdigest(),
            )
            self.assertEqual(
                receipt["critical_files"]["deploy/venus/bin/venus-evcharger-dbus-adapter"],
                hashlib.sha256(b"dbus-adapter-binary\n").hexdigest(),
            )
            for service_name, expected_run in service_run_contents.items():
                relative_run = f"deploy/venus/{service_name}/run"
                self.assertEqual(
                    receipt["critical_files"][relative_run],
                    hashlib.sha256(expected_run.encode()).hexdigest(),
                )
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
            (source_dir / "deploy/venus/install_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_lifecycle.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/boot_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/restart_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/uninstall_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_venus_evcharger/run").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_venus_evcharger/log/run").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_venus_evcharger_dbus_adapter").mkdir(parents=True)
            (source_dir / "deploy/venus/service_venus_evcharger_dbus_adapter/run").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/bin").mkdir(parents=True)
            (source_dir / "deploy/venus/bin/venus-evcharger-auto-input-helper").write_text(
                "auto-input-helper-binary\n",
                encoding="utf-8",
            )
            (source_dir / "deploy/venus/bin/venus-evcharger-dbus-adapter").write_text(
                "dbus-adapter-binary\n",
                encoding="utf-8",
            )
            (source_dir / "deploy/venus/bin/venus-evcharger-forensic-observer").write_text(
                "observer-binary\n",
                encoding="utf-8",
            )
            (source_dir / "deploy/venus/service_venus_evcharger_observer/log").mkdir(parents=True)
            (source_dir / "deploy/venus/service_venus_evcharger_observer/run").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_venus_evcharger_observer/log/run").write_text(
                "#!/bin/sh\n",
                encoding="utf-8",
            )
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
            (source_dir / "deploy/venus/install_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_lifecycle.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/boot_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/restart_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/uninstall_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_venus_evcharger/run").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_venus_evcharger/log/run").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_venus_evcharger_dbus_adapter").mkdir(parents=True)
            (source_dir / "deploy/venus/service_venus_evcharger_dbus_adapter/run").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/bin").mkdir(parents=True)
            (source_dir / "deploy/venus/bin/venus-evcharger-auto-input-helper").write_text(
                "auto-input-helper-binary\n",
                encoding="utf-8",
            )
            (source_dir / "deploy/venus/bin/venus-evcharger-dbus-adapter").write_text(
                "dbus-adapter-binary\n",
                encoding="utf-8",
            )
            (source_dir / "deploy/venus/bin/venus-evcharger-forensic-observer").write_text(
                "observer-binary\n",
                encoding="utf-8",
            )
            (source_dir / "deploy/venus/service_venus_evcharger_observer/log").mkdir(parents=True)
            (source_dir / "deploy/venus/service_venus_evcharger_observer/run").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_venus_evcharger_observer/log/run").write_text(
                "#!/bin/sh\n",
                encoding="utf-8",
            )
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

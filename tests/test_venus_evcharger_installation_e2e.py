# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import time
import unittest

from tests.venus_evcharger_control_test_support import started_control_api_server

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestVenusEvchargerInstallationEndToEnd(unittest.TestCase):
    """Exercise the user-facing install flow in a hermetic temp installation.

    This is intentionally not a live GX/DBus system test. Instead it verifies
    the contract we control:
    - the wizard shell entrypoint can generate a usable config
    - the installer shell entrypoint can register the service and boot hook
    - the boot helper can re-establish service registration and kick the
      optional one-shot helper
    - the runit run script can launch the configured service entrypoint from
      the installed tree

    That gives us a stable end-to-end test for the install UX without requiring
    a real Venus OS runtime, DBus daemon, or runit supervisor.
    """

    def _copy_installation_tree(self, destination: Path) -> Path:
        repo_copy = destination / "repo"
        repo_copy.mkdir()
        for rel_path in (
            "deploy/venus",
            "venus_evcharger",
            "scripts/ops",
            "venus_evcharger_service.py",
            "venus_evcharger_auto_input_helper.py",
            "venus_evchargerctl.py",
        ):
            source = REPO_ROOT / rel_path
            target = repo_copy / rel_path
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        return repo_copy

    @staticmethod
    def _make_executable(path: Path) -> None:
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def _rewrite_shell_paths(self, repo_copy: Path, service_root: Path, rc_local_path: Path) -> None:
        replacements = (
            ('"/service/$SERVICE_NAME"', f'"{service_root}/$SERVICE_NAME"'),
            ("RC_LOCAL_FILE=/data/rc.local", f'RC_LOCAL_FILE="{rc_local_path}"'),
        )
        for rel_path in (
            "deploy/venus/install_venus_evcharger_service.sh",
            "deploy/venus/boot_venus_evcharger_service.sh",
        ):
            script_path = repo_copy / rel_path
            text = script_path.read_text(encoding="utf-8")
            for old, new in replacements:
                text = text.replace(old, new)
            script_path.write_text(text, encoding="utf-8")

    def _stub_service_entrypoint(self, repo_copy: Path, started_marker: Path) -> None:
        entrypoint_path = repo_copy / "venus_evcharger_service.py"
        entrypoint_path.write_text(
            "#!/usr/bin/env python3\n"
            "from pathlib import Path\n"
            f"Path({str(started_marker)!r}).write_text('started\\n', encoding='utf-8')\n",
            encoding="utf-8",
        )
        self._make_executable(entrypoint_path)

    def _stub_disable_helper(self, repo_copy: Path, helper_marker: Path) -> None:
        helper_path = repo_copy / "venus_evcharger/ops/disable_generic_shelly_once.py"
        helper_path.write_text(
            "#!/usr/bin/env python3\n"
            "from pathlib import Path\n"
            f"Path({str(helper_marker)!r}).write_text('helper-ran\\n', encoding='utf-8')\n",
            encoding="utf-8",
        )
        self._make_executable(helper_path)

    def _wait_for_file(self, path: Path, timeout_seconds: float = 2.0) -> None:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if path.exists():
                return
            time.sleep(0.05)
        self.fail(f"Timed out waiting for {path}")

    def _run_installation_flow(
        self,
        *,
        wizard_args: list[str],
        expected_config_fragments: tuple[str, ...],
        expected_generated_files: tuple[str, ...] = (),
        generated_file_fragments: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo_copy = self._copy_installation_tree(root)
            service_root = root / "service"
            rc_local_path = root / "data" / "rc.local"
            started_marker = root / "service-started.txt"
            helper_marker = root / "helper-ran.txt"
            config_path = repo_copy / "deploy/venus/config.venus_evcharger.ini"
            service_link = service_root / "dbus-venus-evcharger"

            service_root.mkdir(parents=True, exist_ok=True)
            rc_local_path.parent.mkdir(parents=True, exist_ok=True)
            self._rewrite_shell_paths(repo_copy, service_root, rc_local_path)
            self._stub_service_entrypoint(repo_copy, started_marker)
            self._stub_disable_helper(repo_copy, helper_marker)
            target_cli_path = repo_copy / "deploy/venus/venus_evchargerctl.sh"
            gx_smoke_path = repo_copy / "deploy/venus/gx_api_smoke_test_skeleton.sh"
            reset_helper_path = repo_copy / "deploy/venus/reset_venus_evcharger_config.sh"

            subprocess.run(
                ["bash", str(repo_copy / "deploy/venus/configure_venus_evcharger_service.sh"), *wizard_args],
                cwd=repo_copy,
                check=True,
                env={**os.environ, "PYTHONPATH": str(repo_copy)},
            )

            self.assertTrue(config_path.is_file())
            config_text = config_path.read_text(encoding="utf-8")
            for fragment in expected_config_fragments:
                self.assertIn(fragment, config_text)
            self.assertTrue((config_path.parent / "config.venus_evcharger.ini.wizard-result.json").is_file())
            self.assertTrue((config_path.parent / "config.venus_evcharger.ini.wizard-audit.jsonl").is_file())
            self.assertTrue((config_path.parent / "config.venus_evcharger.ini.wizard-topology.txt").is_file())
            for file_name in expected_generated_files:
                generated_path = config_path.parent / file_name
                self.assertTrue(generated_path.is_file(), msg=f"Expected generated file {generated_path}")
                for fragment in (generated_file_fragments or {}).get(file_name, ()):
                    self.assertIn(fragment, generated_path.read_text(encoding="utf-8"))

            subprocess.run(
                ["bash", str(repo_copy / "deploy/venus/install_venus_evcharger_service.sh")],
                cwd=repo_copy,
                check=True,
            )

            self.assertTrue(service_link.is_symlink())
            self.assertEqual(service_link.resolve(), (repo_copy / "deploy/venus/service_venus_evcharger").resolve())
            self.assertTrue(target_cli_path.is_file())
            self.assertTrue(os.access(target_cli_path, os.X_OK))
            self.assertTrue(gx_smoke_path.is_file())
            self.assertTrue(os.access(gx_smoke_path, os.X_OK))
            self.assertTrue(reset_helper_path.is_file())
            self.assertTrue(os.access(reset_helper_path, os.X_OK))
            rc_local_text = rc_local_path.read_text(encoding="utf-8")
            self.assertIn(str(repo_copy / "deploy/venus/boot_venus_evcharger_service.sh"), rc_local_text)

            subprocess.run(
                ["bash", str(repo_copy / "deploy/venus/boot_venus_evcharger_service.sh")],
                cwd=repo_copy,
                check=True,
            )
            self.assertTrue(service_link.is_symlink())
            self._wait_for_file(helper_marker)

            subprocess.run(
                ["sh", str(service_link / "run")],
                cwd=repo_copy,
                check=True,
                env={**os.environ, "PYTHONPATH": str(repo_copy)},
            )
            self.assertEqual(started_marker.read_text(encoding="utf-8"), "started\n")

    def test_wizard_install_boot_and_service_run_flow_matrix(self) -> None:
        scenarios = (
            {
                "name": "simple relay",
                "wizard_args": [
                    "--non-interactive",
                    "--force",
                    "--profile",
                    "simple_relay",
                    "--host",
                    "192.168.1.44",
                    "--device-instance",
                    "61",
                    "--policy-mode",
                    "manual",
                ],
                "expected_config_fragments": (
                    "Host=192.168.1.44\n",
                    "DeviceInstance=61\n",
                ),
            },
            {
                "name": "native go-e charger",
                "wizard_args": [
                    "--non-interactive",
                    "--force",
                    "--profile",
                    "native_device",
                    "--host",
                    "goe.local",
                    "--charger-host",
                    "charger.local",
                    "--device-instance",
                    "62",
                    "--phase",
                    "3P",
                    "--policy-mode",
                    "auto",
                    "--charger-backend",
                    "goe_charger",
                ],
                "expected_config_fragments": (
                    "ChargerType=goe_charger\n",
                    "AutoStart=1\n",
                ),
                "expected_generated_files": ("wizard-charger.ini",),
                "generated_file_fragments": {
                    "wizard-charger.ini": (
                        "Type=goe_charger\n",
                        "BaseUrl=http://charger.local\n",
                    ),
                },
            },
            {
                "name": "native modbus charger",
                "wizard_args": [
                    "--non-interactive",
                    "--force",
                    "--profile",
                    "native_device",
                    "--host",
                    "192.168.1.90",
                    "--device-instance",
                    "64",
                    "--phase",
                    "L1",
                    "--policy-mode",
                    "manual",
                    "--charger-backend",
                    "modbus_charger",
                    "--transport",
                    "tcp",
                    "--transport-host",
                    "192.168.1.91",
                    "--transport-unit-id",
                    "7",
                ],
                "expected_config_fragments": (
                    "ChargerType=modbus_charger\n",
                    "Phase=L1\n",
                ),
                "expected_generated_files": ("wizard-charger.ini",),
                "generated_file_fragments": {
                    "wizard-charger.ini": (
                        "Type=modbus_charger\n",
                        "Transport=tcp\n",
                        "Host=192.168.1.91\n",
                        "UnitId=7\n",
                    ),
                },
            },
            {
                "name": "split topology shelly meter plus go-e",
                "wizard_args": [
                    "--non-interactive",
                    "--force",
                    "--profile",
                    "multi_adapter_topology",
                    "--topology-preset",
                    "shelly-meter-goe",
                    "--host",
                    "goe.local",
                    "--meter-host",
                    "meter.local",
                    "--charger-host",
                    "charger.local",
                    "--device-instance",
                    "66",
                    "--phase",
                    "3P",
                    "--policy-mode",
                    "auto",
                    "--charger-backend",
                    "goe_charger",
                ],
                "expected_config_fragments": (
                    "MeterType=shelly_meter\n",
                    "SwitchType=none\n",
                    "ChargerType=goe_charger\n",
                ),
                "expected_generated_files": ("wizard-meter.ini", "wizard-charger.ini"),
                "generated_file_fragments": {
                    "wizard-meter.ini": (
                        "Type=shelly_meter\n",
                        "Host=meter.local\n",
                    ),
                    "wizard-charger.ini": (
                        "Type=goe_charger\n",
                        "BaseUrl=http://charger.local\n",
                    ),
                },
            },
            {
                "name": "split topology go-e plus switch group",
                "wizard_args": [
                    "--non-interactive",
                    "--force",
                    "--profile",
                    "multi_adapter_topology",
                    "--topology-preset",
                    "goe-external-switch-group",
                    "--host",
                    "goe.local",
                    "--switch-host",
                    "switch.local",
                    "--charger-host",
                    "charger.local",
                    "--device-instance",
                    "67",
                    "--phase",
                    "3P",
                    "--policy-mode",
                    "manual",
                    "--charger-backend",
                    "goe_charger",
                ],
                "expected_config_fragments": (
                    "MeterType=none\n",
                    "SwitchType=switch_group\n",
                    "ChargerType=goe_charger\n",
                ),
                "expected_generated_files": (
                    "wizard-switch-group.ini",
                    "wizard-phase1-switch.ini",
                    "wizard-charger.ini",
                ),
                "generated_file_fragments": {
                    "wizard-switch-group.ini": (
                        "SupportedPhaseSelections=P1,P1_P2,P1_P2_P3\n",
                    ),
                    "wizard-phase1-switch.ini": (
                        "BaseUrl=http://switch.local\n",
                    ),
                    "wizard-charger.ini": (
                        "Type=goe_charger\n",
                        "BaseUrl=http://charger.local\n",
                    ),
                },
            },
        )
        for scenario in scenarios:
            with self.subTest(scenario=scenario["name"]):
                self._run_installation_flow(
                    wizard_args=scenario["wizard_args"],
                    expected_config_fragments=scenario["expected_config_fragments"],
                    expected_generated_files=scenario.get("expected_generated_files", ()),
                    generated_file_fragments=scenario.get("generated_file_fragments"),
                )

    def test_installed_target_cli_wrapper_can_query_live_local_api(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo_copy = self._copy_installation_tree(root)
            service_root = root / "service"
            rc_local_path = root / "data" / "rc.local"
            wrapper_path = repo_copy / "deploy/venus/venus_evchargerctl.sh"
            service_root.mkdir(parents=True, exist_ok=True)
            rc_local_path.parent.mkdir(parents=True, exist_ok=True)
            self._rewrite_shell_paths(repo_copy, service_root, rc_local_path)

            subprocess.run(
                ["bash", str(repo_copy / "deploy/venus/install_venus_evcharger_service.sh")],
                cwd=repo_copy,
                check=True,
            )

            with started_control_api_server() as (_service, server):
                environment = {**os.environ, "PYTHONPATH": str(repo_copy)}

                health_result = subprocess.run(
                    [
                        "sh",
                        str(wrapper_path),
                        "--url",
                        f"http://{server.bound_host}:{server.bound_port}",
                        "health",
                    ],
                    cwd=repo_copy,
                    check=True,
                    capture_output=True,
                    text=True,
                    env=environment,
                )
                self.assertTrue(Path(wrapper_path).is_file())
                self.assertIn('"ok": true', health_result.stdout)

                state_result = subprocess.run(
                    [
                        "sh",
                        str(wrapper_path),
                        "--url",
                        f"http://{server.bound_host}:{server.bound_port}",
                        "--token",
                        "read-token",
                        "state",
                        "summary",
                    ],
                    cwd=repo_copy,
                    check=True,
                    capture_output=True,
                    text=True,
                    env=environment,
                )
                self.assertIn('"kind": "summary"', state_result.stdout)


if __name__ == "__main__":
    unittest.main()

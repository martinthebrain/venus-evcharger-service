# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]


class TestVenusResetConfigScript(unittest.TestCase):
    def _copy_installation_tree(self, destination: Path) -> Path:
        repo_copy = destination / "repo"
        repo_copy.mkdir()
        for rel_path in (
            "deploy/venus",
            "venus_evcharger",
            "venus_evcharger_service.py",
            "venus_evcharger_auto_input_helper.py",
        ):
            source = REPO_ROOT / rel_path
            target = repo_copy / rel_path
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        return repo_copy

    def test_reset_helper_restores_unconfigured_default_and_removes_generated_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo_copy = self._copy_installation_tree(root)
            deploy_dir = repo_copy / "deploy/venus"
            config_path = deploy_dir / "config.venus_evcharger.ini"
            default_config_path = deploy_dir / "config.venus_evcharger.default.ini"
            reset_script = deploy_dir / "reset_venus_evcharger_config.sh"
            runtime_state_path = root / "runtime-state.json"
            runtime_overrides_path = root / "runtime-overrides.ini"

            config_path.write_text(
                "[DEFAULT]\n"
                "Host=192.168.1.50\n"
                f"RuntimeStatePath={runtime_state_path}\n"
                f"RuntimeOverridesPath={runtime_overrides_path}\n"
                "DeviceInstance=77\n",
                encoding="utf-8",
            )
            runtime_state_path.write_text("state\n", encoding="utf-8")
            runtime_overrides_path.write_text("overrides\n", encoding="utf-8")
            (deploy_dir / "wizard-charger.ini").write_text("charger\n", encoding="utf-8")
            (deploy_dir / "wizard-energy-alpha.ini").write_text("alpha\n", encoding="utf-8")
            (deploy_dir / "config.venus_evcharger.ini.wizard-result.json").write_text("{}\n", encoding="utf-8")
            (deploy_dir / "config.venus_evcharger.ini.wizard-audit.jsonl").write_text("{}\n", encoding="utf-8")
            (deploy_dir / "config.venus_evcharger.ini.wizard-topology.txt").write_text("topology\n", encoding="utf-8")
            (deploy_dir / "config.venus_evcharger.ini.wizard-inventory.ini").write_text("[Profile:test]\nLabel=Test\n", encoding="utf-8")

            reset_script.chmod(reset_script.stat().st_mode | stat.S_IXUSR)

            subprocess.run(["bash", str(reset_script)], cwd=repo_copy, check=True, env={**os.environ, "PATH": os.environ.get("PATH", "")})

            self.assertEqual(config_path.read_text(encoding="utf-8"), default_config_path.read_text(encoding="utf-8"))
            backup_candidates = sorted(deploy_dir.glob("config.venus_evcharger.ini.reset-backup-*"))
            self.assertEqual(len(backup_candidates), 1)
            self.assertIn("Host=192.168.1.50\n", backup_candidates[0].read_text(encoding="utf-8"))
            self.assertFalse(runtime_state_path.exists())
            self.assertFalse(runtime_overrides_path.exists())
            self.assertFalse((deploy_dir / "wizard-charger.ini").exists())
            self.assertFalse((deploy_dir / "wizard-energy-alpha.ini").exists())
            self.assertFalse((deploy_dir / "config.venus_evcharger.ini.wizard-result.json").exists())
            self.assertFalse((deploy_dir / "config.venus_evcharger.ini.wizard-audit.jsonl").exists())
            self.assertFalse((deploy_dir / "config.venus_evcharger.ini.wizard-topology.txt").exists())
            self.assertFalse((deploy_dir / "config.venus_evcharger.ini.wizard-inventory.ini").exists())

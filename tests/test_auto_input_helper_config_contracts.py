# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from venus_evcharger.inputs.helper.config_runtime import load_auto_input_helper_settings


class AutoInputHelperConfigContracts(unittest.TestCase):
    def test_missing_config_fails_fast(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unable to read config file"):
            load_auto_input_helper_settings("/missing/config.ini", None, None, None, None)

    def test_settings_parse_identity_polling_and_gateway_paths(self) -> None:
        config = """[DEFAULT]
AutoInputSnapshotPath=/run/custom.json
DbusGatewayRunDir=/run/gateway
AutoInputPollIntervalMs=500
AutoPvPollIntervalMs=200
AutoGridPollIntervalMs=700
AutoBatteryPollIntervalMs=900
EnergyTopologyRefreshSeconds=20
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "config.ini"
            path.write_text(config, encoding="utf-8")
            settings = load_auto_input_helper_settings(str(path), None, "12", "-2", " runtime ")
        self.assertEqual(settings.snapshot_path, "/run/custom.json")
        self.assertEqual(settings.gateway_cache_path, "/run/gateway/dbus-cache.json")
        self.assertEqual(settings.parent_pid, 12)
        self.assertEqual(settings.helper_generation, 0)
        self.assertEqual(settings.runtime_instance_id, "runtime")
        self.assertEqual(settings.poll_interval_seconds, 0.2)
        self.assertEqual(settings.topology_refresh_seconds, 20.0)

    def test_runtime_identity_is_generated_when_absent(self) -> None:
        config = "[DEFAULT]\n"
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "config.ini"
            path.write_text(config, encoding="utf-8")
            with patch("venus_evcharger.inputs.helper.config_runtime.uuid.uuid4") as uuid4:
                uuid4.return_value.hex = "generated"
                settings = load_auto_input_helper_settings(str(path), None, True, True, None)
        self.assertEqual(settings.parent_pid, 1)
        self.assertEqual(settings.helper_generation, 1)
        self.assertEqual(settings.runtime_instance_id, "generated")

    def test_zero_topology_refresh_interval_keeps_safe_floor(self) -> None:
        config = "[DEFAULT]\nEnergyTopologyRefreshSeconds=0\n"
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "config.ini"
            path.write_text(config, encoding="utf-8")
            settings = load_auto_input_helper_settings(str(path), None, None, None, "instance")
        self.assertEqual(settings.topology_refresh_seconds, 5.0)

    def test_grid_fusion_freshness_must_cover_battery_polling(self) -> None:
        config = """[DEFAULT]
AutoGridFusionEnabled=1
AutoGridFusionPrimarySource=huawei
AutoGridFusionPrimaryMaxAgeSeconds=1
AutoBatteryPollIntervalMs=2000
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "config.ini"
            path.write_text(config, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must cover"):
                load_auto_input_helper_settings(str(path), None, None, None, None)

    def test_disabled_grid_fusion_uses_gateway_freshness_for_the_victron_source(self) -> None:
        config = """[DEFAULT]
DbusGatewayMaxAgeSeconds=10
AutoGridFusionEnabled=0
AutoGridFusionBackupMaxAgeSeconds=6
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "config.ini"
            path.write_text(config, encoding="utf-8")
            settings = load_auto_input_helper_settings(str(path), None, None, None, None)

        self.assertEqual(settings.gateway_max_age_seconds, 10.0)
        self.assertEqual(settings.grid_fusion_config.backup_max_age_seconds, 10.0)

    def test_enabled_grid_fusion_keeps_its_explicit_backup_freshness(self) -> None:
        config = """[DEFAULT]
DbusGatewayMaxAgeSeconds=10
AutoGridFusionEnabled=1
AutoGridFusionPrimarySource=huawei
AutoGridFusionBackupMaxAgeSeconds=6
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "config.ini"
            path.write_text(config, encoding="utf-8")
            settings = load_auto_input_helper_settings(str(path), None, None, None, None)

        self.assertEqual(settings.grid_fusion_config.backup_max_age_seconds, 6.0)


if __name__ == "__main__":
    unittest.main()

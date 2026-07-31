# SPDX-License-Identifier: GPL-3.0-or-later
import json
import tempfile
from pathlib import Path

from venus_evcharger.bootstrap.wizard import WizardAnswers, configure_wallbox, default_template_path
from venus_evcharger.inventory import parse_device_inventory_config


class _WizardSetupConfigCases:
    def test_configure_wallbox_generates_simple_relay_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            result = configure_wallbox(
                WizardAnswers(
                    profile="simple_relay",
                    host_input="192.0.2.44",
                    meter_host_input=None,
                    switch_host_input=None,
                    charger_host_input=None,
                    device_instance=61,
                    phase="L1",
                    policy_mode="manual",
                    digest_auth=False,
                    username="",
                    password="",
                    topology_preset=None,
                    charger_backend=None,
                    transport_kind="serial_rtu",
                    transport_host="192.0.2.44",
                    transport_port=502,
                    transport_device="/dev/ttyUSB0",
                    transport_unit_id=1,
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
            )

            import configparser

            parser = configparser.ConfigParser()
            parser.read(config_path, encoding="utf-8")
            topology_text = (config_path.parent / "config.ini.wizard-topology.txt").read_text(encoding="utf-8")
            self.assertEqual(parser["DEFAULT"]["Host"], "192.0.2.44")
            self.assertEqual(parser["DEFAULT"]["DeviceInstance"], "61")
            self.assertFalse(parser.has_section("Backends"))
            self.assertTrue((config_path.parent / "config.ini.wizard-result.json").exists())
            self.assertTrue((config_path.parent / "config.ini.wizard-audit.jsonl").exists())
            self.assertTrue((config_path.parent / "config.ini.wizard-topology.txt").exists())
            self.assertTrue((config_path.parent / "config.ini.wizard-inventory.ini").exists())
            self.assertIn("setup: simple_relay\n", topology_text)
            self.assertIn("role_endpoints:\n  - meter: 192.0.2.44\n  - switch: 192.0.2.44\n", topology_text)
            self.assertEqual(result.role_hosts, {"meter": "192.0.2.44", "switch": "192.0.2.44"})
            self.assertEqual(result.validation["resolved_roles"], {"meter": True, "switch": True, "charger": False})
            self.assertEqual(result.topology_config["topology"]["type"], "simple_relay")
            self.assertEqual(result.topology_config["actuator"]["type"], "shelly_contactor_switch")
            self.assertEqual(result.topology_config["measurement"]["type"], "actuator_native")
            self.assertTrue(str(result.inventory_path).endswith(".wizard-inventory.ini"))
            inventory_parser = configparser.ConfigParser()
            inventory_parser.read(config_path.parent / "config.ini.wizard-inventory.ini", encoding="utf-8")
            inventory = parse_device_inventory_config(inventory_parser)
            self.assertEqual(len(inventory.devices), 1)
            self.assertEqual(len(inventory.bindings), 2)

    def test_configure_wallbox_generates_native_goe_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            result = configure_wallbox(
                WizardAnswers(
                    profile="native_device",
                    host_input="goe.local",
                    meter_host_input=None,
                    switch_host_input=None,
                    charger_host_input="goe.local",
                    device_instance=62,
                    phase="3P",
                    policy_mode="auto",
                    digest_auth=False,
                    username="",
                    password="",
                    topology_preset=None,
                    charger_backend="goe_charger",
                    transport_kind="serial_rtu",
                    transport_host="goe.local",
                    transport_port=502,
                    transport_device="/dev/ttyUSB0",
                    transport_unit_id=1,
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
            )

            config_text = config_path.read_text(encoding="utf-8")
            charger_text = (config_path.parent / "wizard-charger.ini").read_text(encoding="utf-8")
            self.assertIn("[Backends]\nMode=split\nMeterType=none\nSwitchType=none\n", config_text)
            self.assertIn("ChargerType=goe_charger\n", config_text)
            self.assertIn("Type=goe_charger\nBaseUrl=http://goe.local\n", charger_text)
            self.assertEqual(result.role_hosts, {"charger": "goe.local"})
            self.assertEqual(result.validation["resolved_roles"], {"meter": False, "switch": False, "charger": True})
            self.assertEqual(result.topology_config["topology"]["type"], "native_device")
            self.assertEqual(result.topology_config["measurement"]["type"], "charger_native")
            self.assertEqual(result.topology_config["charger"]["type"], "goe_charger")

    def test_configure_wallbox_can_include_huawei_recommendation_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            bundle_prefix = temp_path / "huawei-rec"
            Path(str(bundle_prefix) + ".ini").write_text(
                "AutoEnergySource.huawei.Profile=huawei_mb_sdongle\n"
                "AutoEnergySource.huawei.ConfigPath=/data/etc/huawei-mb-modbus.ini\n",
                encoding="utf-8",
            )
            Path(str(bundle_prefix) + ".wizard.txt").write_text(
                "Huawei recommendation\n- profile: huawei_mb_sdongle\n",
                encoding="utf-8",
            )
            Path(str(bundle_prefix) + ".summary.txt").write_text(
                "Use profile huawei_mb_sdongle\n",
                encoding="utf-8",
            )
            config_path = temp_path / "config.ini"
            result = configure_wallbox(
                WizardAnswers(
                    profile="simple_relay",
                    host_input="192.0.2.44",
                    meter_host_input=None,
                    switch_host_input=None,
                    charger_host_input=None,
                    device_instance=61,
                    phase="L1",
                    policy_mode="manual",
                    digest_auth=False,
                    username="",
                    password="",
                    topology_preset=None,
                    charger_backend=None,
                    transport_kind="serial_rtu",
                    transport_host="192.0.2.44",
                    transport_port=502,
                    transport_device="/dev/ttyUSB0",
                    transport_unit_id=1,
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
                energy_recommendation_prefix=str(bundle_prefix),
            )

            self.assertIn("wizard-huawei-energy.ini", result.generated_files)
            self.assertIn("wizard-auto-energy-merge.ini", result.generated_files)
            self.assertIn("External energy source integration", result.manual_review)
            self.assertIn("Set usable battery capacity for weighted combined SOC", result.manual_review)
            self.assertIn("External energy source", result.suggested_blocks)
            self.assertEqual(len(result.suggested_energy_sources), 1)
            self.assertEqual(result.suggested_energy_sources[0]["source_id"], "huawei")
            self.assertEqual(result.suggested_energy_sources[0]["profile"], "huawei_mb_sdongle")
            self.assertEqual(result.suggested_energy_sources[0]["configPath"], "/data/etc/huawei-mb-modbus.ini")
            self.assertEqual(
                result.suggested_energy_sources[0]["capacityConfigKey"],
                "AutoEnergySource.huawei.UsableCapacityWh",
            )
            self.assertEqual(result.suggested_energy_merge["merged_source_ids"], ["huawei"])
            self.assertEqual(result.suggested_energy_merge["helper_file"], "wizard-auto-energy-merge.ini")
            self.assertEqual(
                result.suggested_energy_merge["capacity_follow_up"][0]["config_key"],
                "AutoEnergySource.huawei.UsableCapacityWh",
            )
            self.assertIn(
                "AutoEnergySource.huawei.Profile=huawei_mb_sdongle",
                (config_path.parent / "wizard-huawei-energy.ini").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "AutoEnergySources=huawei",
                (config_path.parent / "wizard-auto-energy-merge.ini").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "# AutoEnergySource.huawei.UsableCapacityWh=<set-me>",
                (config_path.parent / "wizard-auto-energy-merge.ini").read_text(encoding="utf-8"),
            )

    def test_configure_wallbox_can_apply_huawei_capacity_to_suggested_energy_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            bundle_prefix = temp_path / "huawei-rec"
            Path(str(bundle_prefix) + ".ini").write_text(
                "AutoEnergySource.huawei.Profile=huawei_mb_sdongle\n"
                "AutoEnergySource.huawei.ConfigPath=/data/etc/huawei-mb-modbus.ini\n",
                encoding="utf-8",
            )
            Path(str(bundle_prefix) + ".wizard.txt").write_text("Huawei recommendation\n", encoding="utf-8")
            Path(str(bundle_prefix) + ".summary.txt").write_text("Use profile huawei_mb_sdongle\n", encoding="utf-8")
            config_path = temp_path / "config.ini"

            result = configure_wallbox(
                WizardAnswers(
                    profile="simple_relay",
                    host_input="192.0.2.44",
                    meter_host_input=None,
                    switch_host_input=None,
                    charger_host_input=None,
                    device_instance=61,
                    phase="L1",
                    policy_mode="manual",
                    digest_auth=False,
                    username="",
                    password="",
                    topology_preset=None,
                    charger_backend=None,
                    transport_kind="serial_rtu",
                    transport_host="192.0.2.44",
                    transport_port=502,
                    transport_device="/dev/ttyUSB0",
                    transport_unit_id=1,
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
                energy_recommendation_prefix=str(bundle_prefix),
                suggested_energy_capacity_wh=15360.0,
            )

            self.assertEqual(result.suggested_energy_sources[0]["usableCapacityWh"], 15360.0)
            self.assertEqual(result.suggested_energy_merge["capacity_follow_up"][0]["placeholder"], "15360")
            self.assertTrue(result.suggested_energy_merge["capacity_follow_up"][0]["configured"])
            self.assertIn(
                "AutoEnergySource.huawei.UsableCapacityWh=15360",
                (config_path.parent / "wizard-auto-energy-merge.ini").read_text(encoding="utf-8"),
            )

    def test_configure_wallbox_reads_custom_source_id_from_huawei_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            bundle_prefix = temp_path / "huawei-rec"
            Path(str(bundle_prefix) + ".ini").write_text(
                "AutoEnergySource.hybrid_ext.Profile=huawei_mb_sdongle\n"
                "AutoEnergySource.hybrid_ext.ConfigPath=/data/etc/huawei-mb-modbus.ini\n",
                encoding="utf-8",
            )
            Path(str(bundle_prefix) + ".wizard.txt").write_text("Huawei recommendation\n", encoding="utf-8")
            Path(str(bundle_prefix) + ".summary.txt").write_text("Use profile huawei_mb_sdongle\n", encoding="utf-8")
            config_path = temp_path / "config.ini"

            result = configure_wallbox(
                WizardAnswers(
                    profile="simple_relay",
                    host_input="192.0.2.44",
                    meter_host_input=None,
                    switch_host_input=None,
                    charger_host_input=None,
                    device_instance=61,
                    phase="L1",
                    policy_mode="manual",
                    digest_auth=False,
                    username="",
                    password="",
                    topology_preset=None,
                    charger_backend=None,
                    transport_kind="serial_rtu",
                    transport_host="192.0.2.44",
                    transport_port=502,
                    transport_device="/dev/ttyUSB0",
                    transport_unit_id=1,
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
                energy_recommendation_prefix=str(bundle_prefix),
            )

            self.assertEqual(result.suggested_energy_sources[0]["source_id"], "hybrid_ext")
            self.assertEqual(
                result.suggested_energy_sources[0]["capacityConfigKey"],
                "AutoEnergySource.hybrid_ext.UsableCapacityWh",
            )
            self.assertIn(
                "AutoEnergySources=hybrid_ext",
                (config_path.parent / "wizard-auto-energy-merge.ini").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "# AutoEnergySource.hybrid_ext.UsableCapacityWh=<set-me>",
                (config_path.parent / "wizard-auto-energy-merge.ini").read_text(encoding="utf-8"),
            )

    def test_configure_wallbox_can_read_manifest_backed_recommendation_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            bundle_prefix = temp_path / "energy-rec"
            config_file = temp_path / "bundle-source.ini"
            wizard_file = temp_path / "bundle-source.wizard.txt"
            summary_file = temp_path / "bundle-source.summary.txt"
            manifest_file = temp_path / "energy-rec.manifest.json"
            config_file.write_text(
                "AutoEnergySource.hybrid_ext.Profile=huawei_mb_sdongle\n"
                "AutoEnergySource.hybrid_ext.ConfigPath=/data/etc/huawei-mb-modbus.ini\n",
                encoding="utf-8",
            )
            wizard_file.write_text("External energy recommendation\n", encoding="utf-8")
            summary_file.write_text("Use profile huawei_mb_sdongle\n", encoding="utf-8")
            manifest_file.write_text(
                json.dumps(
                    {
                        "schema_type": "energy-recommendation-bundle",
                        "schema_version": 1,
                        "source_id": "hybrid_ext",
                        "profile": "huawei_mb_sdongle",
                        "config_path": "/data/etc/huawei-mb-modbus.ini",
                        "files": {
                            "config_snippet": str(config_file),
                            "wizard_hint": str(wizard_file),
                            "summary": str(summary_file),
                        },
                    }
                ),
                encoding="utf-8",
            )
            config_path = temp_path / "config.ini"

            result = configure_wallbox(
                WizardAnswers(
                    profile="simple_relay",
                    host_input="192.0.2.44",
                    meter_host_input=None,
                    switch_host_input=None,
                    charger_host_input=None,
                    device_instance=61,
                    phase="L1",
                    policy_mode="manual",
                    digest_auth=False,
                    username="",
                    password="",
                    topology_preset=None,
                    charger_backend=None,
                    transport_kind="serial_rtu",
                    transport_host="192.0.2.44",
                    transport_port=502,
                    transport_device="/dev/ttyUSB0",
                    transport_unit_id=1,
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
                energy_recommendation_prefix=str(bundle_prefix),
            )

            self.assertEqual(result.suggested_energy_sources[0]["source_id"], "hybrid_ext")
            self.assertIn("wizard-energy-hybrid_ext.ini", result.generated_files)

    def test_configure_wallbox_can_apply_suggested_energy_merge_to_main_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            bundle_prefix = temp_path / "huawei-rec"
            Path(str(bundle_prefix) + ".ini").write_text(
                "AutoEnergySource.huawei.Profile=huawei_mb_sdongle\n"
                "AutoEnergySource.huawei.ConfigPath=/data/etc/huawei-mb-modbus.ini\n",
                encoding="utf-8",
            )
            Path(str(bundle_prefix) + ".wizard.txt").write_text("Huawei recommendation\n", encoding="utf-8")
            Path(str(bundle_prefix) + ".summary.txt").write_text("Use profile huawei_mb_sdongle\n", encoding="utf-8")
            config_path = temp_path / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                "AutoEnergySources=victron\n"
                "AutoEnergySource.victron.Profile=dbus-battery\n"
                "AutoEnergySource.victron.ConfigPath=/data/etc/victron-battery.ini\n",
                encoding="utf-8",
            )

            result = configure_wallbox(
                WizardAnswers(
                    profile="simple_relay",
                    host_input="192.0.2.44",
                    meter_host_input=None,
                    switch_host_input=None,
                    charger_host_input=None,
                    device_instance=61,
                    phase="L1",
                    policy_mode="manual",
                    digest_auth=False,
                    username="",
                    password="",
                    topology_preset=None,
                    charger_backend=None,
                    transport_kind="serial_rtu",
                    transport_host="192.0.2.44",
                    transport_port=502,
                    transport_device="/dev/ttyUSB0",
                    transport_unit_id=1,
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
                energy_recommendation_prefix=str(bundle_prefix),
                apply_suggested_energy_merge=True,
                suggested_energy_capacity_wh=10240.0,
            )

            config_text = config_path.read_text(encoding="utf-8")
            self.assertIn("AutoUseCombinedBatterySoc=1\n", config_text)
            self.assertIn("AutoEnergySources=victron,huawei\n", config_text)
            self.assertIn("AutoEnergySource.victron.Profile=dbus-battery\n", config_text)
            self.assertIn("AutoEnergySource.huawei.Profile=huawei_mb_sdongle\n", config_text)
            self.assertIn("AutoEnergySource.huawei.UsableCapacityWh=10240\n", config_text)
            self.assertTrue(result.suggested_energy_merge["applied_to_config"])

    def test_configure_wallbox_can_merge_multiple_huawei_recommendation_bundles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            first_prefix = temp_path / "huawei-a"
            second_prefix = temp_path / "huawei-b"
            Path(str(first_prefix) + ".ini").write_text(
                "AutoEnergySource.huawei_a.Profile=huawei_mb_unit1\n"
                "AutoEnergySource.huawei_a.ConfigPath=/data/etc/huawei-mb-unit1.ini\n",
                encoding="utf-8",
            )
            Path(str(first_prefix) + ".wizard.txt").write_text("Huawei recommendation A\n", encoding="utf-8")
            Path(str(first_prefix) + ".summary.txt").write_text("Use source huawei_a\n", encoding="utf-8")
            Path(str(second_prefix) + ".ini").write_text(
                "AutoEnergySource.huawei_b.Profile=huawei_mb_unit2\n"
                "AutoEnergySource.huawei_b.ConfigPath=/data/etc/huawei-mb-unit2.ini\n",
                encoding="utf-8",
            )
            Path(str(second_prefix) + ".wizard.txt").write_text("Huawei recommendation B\n", encoding="utf-8")
            Path(str(second_prefix) + ".summary.txt").write_text("Use source huawei_b\n", encoding="utf-8")
            config_path = temp_path / "config.ini"

            result = configure_wallbox(
                WizardAnswers(
                    profile="simple_relay",
                    host_input="192.0.2.44",
                    meter_host_input=None,
                    switch_host_input=None,
                    charger_host_input=None,
                    device_instance=61,
                    phase="L1",
                    policy_mode="manual",
                    digest_auth=False,
                    username="",
                    password="",
                    topology_preset=None,
                    charger_backend=None,
                    transport_kind="serial_rtu",
                    transport_host="192.0.2.44",
                    transport_port=502,
                    transport_device="/dev/ttyUSB0",
                    transport_unit_id=1,
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
                energy_recommendation_prefix=(str(first_prefix), str(second_prefix)),
                suggested_energy_capacity_overrides={"huawei_a": 15360.0, "huawei_b": 7680.0},
            )

            self.assertEqual(
                [source["source_id"] for source in result.suggested_energy_sources],
                ["huawei_a", "huawei_b"],
            )
            self.assertEqual(result.suggested_energy_sources[0]["usableCapacityWh"], 15360.0)
            self.assertEqual(result.suggested_energy_sources[1]["usableCapacityWh"], 7680.0)
            self.assertIn("wizard-energy-huawei_a.ini", result.generated_files)
            self.assertIn("wizard-energy-huawei_b.ini", result.generated_files)
            self.assertEqual(result.suggested_energy_merge["merged_source_ids"], ["huawei_a", "huawei_b"])
            merge_text = (config_path.parent / "wizard-auto-energy-merge.ini").read_text(encoding="utf-8")
            self.assertIn("AutoEnergySources=huawei_a,huawei_b", merge_text)
            self.assertIn("AutoEnergySource.huawei_a.UsableCapacityWh=15360", merge_text)
            self.assertIn("AutoEnergySource.huawei_b.UsableCapacityWh=7680", merge_text)

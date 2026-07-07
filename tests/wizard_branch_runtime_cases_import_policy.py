# SPDX-License-Identifier: GPL-3.0-or-later
from tests.wizard_branch_runtime_cases_common import (
    Path,
    _adapter_path,
    _as_bool,
    _as_float,
    _as_int,
    _imported_defaults,
    _load_from_result_json,
    _namespace,
    _policy_mode,
    _profile_defaults_from_types,
    _switch_group_member_host,
    _topology_choice_labels,
    datetime,
    json,
    load_imported_defaults,
    patch,
    preset_specific_defaults,
    prompt_policy_defaults,
    prompt_topology_preset,
    resolved_primary_host,
    scheduled_mode_snapshot,
    tempfile,
    wizard_cli,
    wizard_main,
    WizardResult,
    _topology_summary_text,
)


class _WizardBranchRuntimeImportPolicyCases:
    def test_branch_helpers_cover_remaining_prompt_and_import_edges(self) -> None:
        self.assertEqual(prompt_topology_preset(lambda *_args: "template-stack", "template-stack"), "template-stack")
        self.assertIn("responsibilities separate", _topology_choice_labels()["template-stack"])
        self.assertEqual(resolved_primary_host(_namespace(), _imported_defaults(), None, None, None), "192.168.1.50")

        result_text = wizard_cli.result_text(
            WizardResult(
                created_at="2026-04-20T02:53:57",
                config_path="/tmp/config.ini",
                imported_from=None,
                profile="simple_relay",
                policy_mode="manual",
                topology_preset=None,
                charger_backend=None,
                charger_preset=None,
                transport_kind=None,
                role_hosts={},
                validation={"resolved_roles": {"meter": False}},
                live_check={"ok": True, "roles": {}},
                generated_files=("config.ini",),
                backup_files=tuple(),
                result_path=None,
                audit_path=None,
                topology_summary_path=None,
                manual_review=("Auth",),
                dry_run=False,
                warnings=tuple(),
                answer_defaults={},
            )
        )
        self.assertNotIn("  - meter:", result_text)
        self.assertIn("Selected setup: Recommended: Shelly PM/PM1 Gen4 measures and switches", result_text)
        self.assertIn("Responsibilities: one Shelly-compatible device provides both metering and switching", result_text)

        hinted_result_text = wizard_cli.result_text(
            WizardResult(
                created_at="2026-04-20T02:53:57",
                config_path="/tmp/config.ini",
                imported_from=None,
                profile="simple_relay",
                policy_mode="manual",
                topology_preset=None,
                charger_backend=None,
                charger_preset=None,
                transport_kind=None,
                role_hosts={},
                validation={"resolved_roles": {"meter": False}},
                live_check=None,
                generated_files=("config.ini", "wizard-huawei-energy.ini"),
                backup_files=tuple(),
                result_path=None,
                audit_path=None,
                topology_summary_path=None,
                manual_review=("Auth", "External energy source integration"),
                dry_run=False,
                warnings=tuple(),
                answer_defaults={},
                suggested_blocks={
                    "External energy source": "AutoEnergySource.huawei.Profile=huawei_mb_sdongle\n"
                },
                suggested_energy_sources=(
                    {
                        "source_id": "huawei",
                        "profile": "huawei_mb_sdongle",
                        "configPath": "/data/etc/huawei-mb-modbus.ini",
                        "host": "192.168.8.1",
                        "port": 502,
                        "unitId": 1,
                        "usableCapacityWh": 15360.0,
                        "capacityConfigKey": "AutoEnergySource.huawei.UsableCapacityWh",
                    },
                ),
                suggested_energy_merge={
                    "merged_source_ids": ["victron", "huawei"],
                    "helper_file": "wizard-auto-energy-merge.ini",
                    "applied_to_config": True,
                    "capacity_follow_up": [
                        {
                            "source_id": "huawei",
                            "config_key": "AutoEnergySource.huawei.UsableCapacityWh",
                            "placeholder": "15360",
                            "configured": True,
                        }
                    ],
                    "merge_block": (
                        "AutoEnergySources=victron,huawei\n"
                        "AutoEnergySource.huawei.Profile=huawei_mb_sdongle\n"
                        "AutoEnergySource.huawei.UsableCapacityWh=15360\n"
                    ),
                },
            )
        )
        self.assertIn("Suggested energy sources:", hinted_result_text)
        self.assertIn("profile=huawei_mb_sdongle", hinted_result_text)
        self.assertIn("capacity follow-up: AutoEnergySource.huawei.UsableCapacityWh=<set-me>", hinted_result_text)
        self.assertIn("Suggested AutoEnergy merge:", hinted_result_text)
        self.assertIn("merged source ids: victron,huawei", hinted_result_text)
        self.assertIn("wizard-auto-energy-merge.ini", hinted_result_text)
        self.assertIn("applied to main config: yes", hinted_result_text)
        self.assertIn("AutoEnergySource.huawei.UsableCapacityWh=15360", hinted_result_text)
        self.assertIn("Suggested config blocks:", hinted_result_text)
        self.assertIn("AutoEnergySource.huawei.Profile=huawei_mb_sdongle", hinted_result_text)

    def test_resolved_energy_capacity_wh_prompts_only_for_single_energy_recommendation(self) -> None:
        self.assertIsNone(wizard_main.resolved_energy_capacity_wh(_namespace(non_interactive=True), tuple()))
        self.assertEqual(
            wizard_main.resolved_energy_capacity_wh(
                _namespace(non_interactive=True, energy_default_usable_capacity_wh=12000.0),
                ("/tmp/huawei-rec",),
            ),
            12000.0,
        )
        with (
            patch("venus_evcharger.bootstrap.wizard_main.prompt_yes_no", return_value=True),
            patch("builtins.input", return_value="15360"),
        ):
            self.assertEqual(
                wizard_main.resolved_energy_capacity_wh(
                    _namespace(energy_recommendation_prefix=["/tmp/huawei-rec"]),
                    ("/tmp/huawei-rec",),
                ),
                15360.0,
            )
        self.assertEqual(
            wizard_main.resolved_energy_capacity_overrides(
                _namespace(energy_usable_capacity_wh=["hybrid_a=15360", "hybrid_b=7680"])
            ),
            {"hybrid_a": 15360.0, "hybrid_b": 7680.0},
        )
        with self.assertRaisesRegex(ValueError, "source_id=Wh"):
            wizard_main.resolved_energy_capacity_overrides(_namespace(energy_usable_capacity_wh=["broken"]))
        with self.assertRaisesRegex(ValueError, "source_id=Wh"):
            wizard_main.resolved_energy_capacity_overrides(_namespace(energy_usable_capacity_wh=["hybrid_a=0"]))

        self.assertIsNone(_as_bool(None))
        self.assertIsNone(_as_int(" "))
        self.assertIsNone(_as_float(" "))
        self.assertEqual(_policy_mode("1"), "auto")
        self.assertEqual(_policy_mode("2"), "scheduled")
        self.assertEqual(_policy_mode("0"), "manual")
        self.assertIsNone(_policy_mode("other"))
        self.assertEqual(_profile_defaults_from_types("none", "none", "goe_charger"), ("native_device", None, "goe_charger"))

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            group_path = temp_path / "switch-group.ini"
            group_path.write_text("[Members]\nP1=missing.ini\n", encoding="utf-8")
            config_path = temp_path / "config.ini"
            config_path.write_text("[Backends]\n", encoding="utf-8")
            self.assertIsNone(_adapter_path(config_path, {}, "SwitchConfigPath"))
            self.assertIsNone(_switch_group_member_host(group_path, None))
            self.assertIsNone(_switch_group_member_host(group_path, "missing.ini"))
            phase_path = temp_path / "phase1.ini"
            phase_path.write_text("[Adapter]\nHost=phase1.local\n", encoding="utf-8")
            self.assertEqual(_switch_group_member_host(group_path, str(phase_path)), "phase1.local")

            bad_result_path = temp_path / "bad.wizard-result.json"
            bad_result_path.write_text("[]\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "JSON object"):
                _load_from_result_json(bad_result_path)

            bad_defaults_path = temp_path / "bad-defaults.wizard-result.json"
            bad_defaults_path.write_text(json.dumps({"answer_defaults": []}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "missing answer_defaults"):
                _load_from_result_json(bad_defaults_path)

            minimal_result_path = temp_path / "minimal.wizard-result.json"
            minimal_result_path.write_text(json.dumps({"answer_defaults": {}}), encoding="utf-8")
            self.assertIsNone(_load_from_result_json(minimal_result_path).profile)

            valid_result_path = temp_path / "valid.wizard-result.json"
            valid_result_path.write_text(
                json.dumps(
                    {
                        "inventory_path": "inventory.ini",
                        "answer_defaults": {
                            "profile": "native_device",
                            "host_input": "charger.local",
                            "meter_host_input": "meter.local",
                            "switch_host_input": "switch.local",
                            "charger_host_input": "charger.local",
                            "device_instance": 42,
                            "phase": "L1",
                            "policy_mode": "auto",
                            "digest_auth": True,
                            "username": "user",
                            "topology_preset": "shelly-meter-goe",
                            "charger_backend": "goe_charger",
                            "charger_preset": "go-e",
                            "request_timeout_seconds": 2,
                            "switch_group_supported_phase_selections": "P1,P1_P2",
                            "auto_start_surplus_watts": 1800,
                            "auto_stop_surplus_watts": 1500.5,
                            "auto_min_soc": 30,
                            "auto_resume_soc": 35.5,
                            "scheduled_enabled_days": "Mon",
                            "scheduled_latest_end_time": "06:30",
                            "scheduled_night_current_amps": 6,
                            "transport_kind": "tcp",
                            "transport_host": "192.168.1.90",
                            "transport_port": 502,
                            "transport_device": "/dev/ttyUSB0",
                            "transport_unit_id": 1,
                        },
                    }
                ),
                encoding="utf-8",
            )
            valid_defaults = _load_from_result_json(valid_result_path)
            self.assertEqual(valid_defaults.profile, "native_device")
            self.assertEqual(valid_defaults.request_timeout_seconds, 2.0)
            self.assertEqual(valid_defaults.inventory_path, "inventory.ini")

            invalid_result_fields = (
                ("bad-string", {"host_input": 1}, "string or null"),
                ("bad-bool", {"digest_auth": "yes"}, "boolean or null"),
                ("bad-int", {"device_instance": True}, "integer or null"),
                ("bad-float", {"auto_start_surplus_watts": "1800"}, "numeric or null"),
                ("bad-transport", {"transport_kind": "udp"}, "Unsupported transport"),
            )
            for name, defaults_payload, error_pattern in invalid_result_fields:
                invalid_path = temp_path / f"{name}.wizard-result.json"
                invalid_path.write_text(json.dumps({"answer_defaults": defaults_payload}), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, error_pattern):
                    _load_from_result_json(invalid_path)

            with self.assertRaisesRegex(ValueError, "Import config does not exist"):
                load_imported_defaults(temp_path / "missing.ini")

        summary = _topology_summary_text({"validation": "invalid"})
        self.assertNotIn("resolved_roles:", summary)

        prompted = prompt_policy_defaults(
            "scheduled",
            _imported_defaults(
                scheduled_enabled_days="Mon",
                scheduled_latest_end_time="06:30",
                scheduled_night_current_amps=5.0,
            ),
            _namespace(
                auto_start_surplus_watts=1800.0,
                scheduled_enabled_days="Mon",
                scheduled_latest_end_time="06:30",
                scheduled_night_current_amps=5.0,
            ),
            prompt_text=lambda _label, default: default,
        )
        self.assertEqual(prompted[0], 1800.0)
        self.assertEqual(prompted[4:], ("Mon", "06:30", 5.0))

        timeout, _ = preset_specific_defaults(
            _namespace(request_timeout_seconds=7.5),
            _imported_defaults(),
            backend="goe_charger",
            topology_preset=None,
            charger_preset=None,
        )
        self.assertEqual(timeout, 7.5)

    def test_scheduled_mode_snapshot_covers_daytime_window(self) -> None:
        snapshot = scheduled_mode_snapshot(
            datetime(2026, 4, 20, 10, 30),
            {4: ((7, 30), (19, 30))},
            "Mon,Tue,Wed,Thu,Fri",
            delay_seconds=3600.0,
            latest_end_time="06:30",
        )
        self.assertEqual(snapshot.state, "auto-window")
        self.assertEqual(snapshot.reason, "daytime-auto")

# SPDX-License-Identifier: GPL-3.0-or-later
from tests.wizard_branch_coverage_cases_common import (
    Path,
    _adapter_path,
    _imported_defaults,
    _namespace,
    _native_profile_defaults,
    _profile_defaults,
    _profile_defaults_from_types,
    _request_timeout_seconds,
    _switch_group_host_value,
    _topology_summary_text,
    policy_defaults,
    prompt_policy_defaults,
    render_charger_preset_config,
    split_topology_files,
    tempfile,
    wizard_adapters,
)
from venus_evcharger.bootstrap.wizard_policy_guidance import _format_prompt_default


class _WizardBranchCoverageImportCases:
    def test_policy_guidance_covers_manual_and_scheduled_branches(self) -> None:
        namespace = _namespace(auto_start_surplus_watts=2000.0)
        manual_defaults = policy_defaults("manual", _imported_defaults(), namespace)
        self.assertEqual(manual_defaults, (2000.0, None, None, None, None, None, None))

        scheduled_defaults = policy_defaults(
            "scheduled",
            _imported_defaults(
                auto_start_surplus_watts=1900.0,
                auto_stop_surplus_watts=1400.0,
                auto_min_soc=31.0,
                auto_resume_soc=34.0,
                scheduled_enabled_days="Sat,Sun",
                scheduled_latest_end_time="08:00",
                scheduled_night_current_amps=6.0,
            ),
            _namespace(),
        )
        self.assertEqual(scheduled_defaults, (1900.0, 1400.0, 31.0, 34.0, "Sat,Sun", "08:00", 6.0))

        prompted_defaults = prompt_policy_defaults(
            "scheduled",
            _imported_defaults(),
            _namespace(),
            prompt_text=lambda label, default: {
                "Auto start surplus watts": "2100",
                "Auto stop surplus watts": "1500",
                "Battery minimum SOC for Auto": "35",
                "Battery resume SOC for Auto": "39",
                "Scheduled weekdays": "Mon,Wed",
                "Scheduled latest end time (HH:MM)": "07:45",
                "Scheduled fallback night current amps": "8",
            }[label],
        )
        self.assertEqual(prompted_defaults, (2100.0, 1500.0, 35.0, 39.0, "Mon,Wed", "07:45", 8.0))

        with self.assertRaisesRegex(ValueError, "Policy prompt default must be resolved"):
            _format_prompt_default(None)

    def test_import_helpers_cover_adapter_paths_profiles_and_timeouts(self) -> None:
        self.assertEqual(_profile_defaults(None), ("simple_relay", None, None))
        self.assertEqual(_profile_defaults_from_types("", "", ""), (None, None, None))
        self.assertEqual(_profile_defaults_from_types("template_meter", "custom_switch", ""), ("advanced_manual", None, None))
        self.assertEqual(_native_profile_defaults("none", "none", "goe_charger", "goe_charger"), ("native_device", None, "goe_charger"))
        self.assertEqual(
            _native_profile_defaults("none", "switch_group", "simpleevse_charger", "simpleevse_charger"),
            ("hybrid_topology", None, "simpleevse_charger"),
        )
        self.assertIsNone(_native_profile_defaults("shelly_meter", "none", "goe_charger", "goe_charger"))

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config_path = temp_path / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter_path = temp_path / "switch-group.ini"
            adapter_path.write_text("[DEFAULT]\nBaseUrl=http://fallback.local\n", encoding="utf-8")
            self.assertIsNone(_adapter_path(config_path, None, "SwitchConfigPath"))
            self.assertIsNone(_adapter_path(config_path, {"SwitchConfigPath": "missing.ini"}, "SwitchConfigPath"))
            self.assertEqual(_switch_group_host_value(adapter_path), "http://fallback.local")

            charger_path = temp_path / "charger.ini"
            charger_path.write_text("[Adapter]\nType=goe_charger\nRequestTimeoutSeconds=4.25\n", encoding="utf-8")
            backends = __import__("types").SimpleNamespace(get=lambda key, default=None: "charger.ini" if key == "ChargerConfigPath" else default)
            self.assertEqual(_request_timeout_seconds(config_path, backends, "goe_charger"), 4.25)
            self.assertIsNone(_request_timeout_seconds(config_path, backends, "template_charger"))

    def test_persistence_and_split_layout_helpers_cover_optional_paths(self) -> None:
        summary = _topology_summary_text(
            {
                "config_path": "/tmp/config.ini",
                "profile": "simple_relay",
                "topology_preset": None,
                "charger_backend": None,
                "charger_preset": None,
                "policy_mode": "manual",
                "transport_kind": None,
                "role_hosts": {},
                "validation": {"resolved_roles": {"meter": False}},
                "live_check": {"ok": True},
                "warnings": ["careful"],
            }
        )
        self.assertIn("resolved_roles: {'meter': False}", summary)
        self.assertIn("live_check_ok: True", summary)
        self.assertIn("warnings:\n  - careful", summary)

        backend_lines, files, role_hosts = split_topology_files(
            topology_preset="shelly-io-template-charger",
            role_hosts={"meter": "meter.local", "switch": "switch.local", "charger": "charger.local"},
            meter_base_url="http://meter.local",
            switch_base_url="http://switch.local",
            charger_base_url="http://charger.local",
            charger_preset=None,
            request_timeout_seconds=None,
            switch_group_supported_phase_selections="P1,P1_P2,P1_P2_P3",
            transport_kind="serial_rtu",
            transport_host="charger.local",
            transport_port=502,
            transport_device="/dev/ttyUSB0",
            transport_unit_id=1,
        )
        self.assertIn("ChargerType=template_charger", backend_lines)
        self.assertIn("Type=template_charger", files["wizard-charger.ini"])
        self.assertEqual(role_hosts["meter"], "meter.local")
        self.assertIn("Type=template_charger", wizard_adapters.native_charger_config(
            "template_charger",
            "http://charger.local",
            charger_preset=None,
            request_timeout_seconds=None,
            transport_kind="serial_rtu",
            transport_host="charger.local",
            transport_port=502,
            transport_device="/dev/ttyUSB0",
            transport_unit_id=1,
        ))
        self.assertIn("Transport=serial_rtu", wizard_adapters.native_charger_config(
            "simpleevse_charger",
            "http://charger.local",
            charger_preset=None,
            request_timeout_seconds=None,
            transport_kind="serial_rtu",
            transport_host="charger.local",
            transport_port=502,
            transport_device="/dev/ttyUSB0",
            transport_unit_id=1,
        ))
        abb_config = render_charger_preset_config(
            "abb-terra-ac-modbus",
            transport_kind="tcp",
            transport_host="abb.local",
            transport_port=502,
            transport_device="/dev/ttyUSB0",
            transport_unit_id=1,
        )
        self.assertIn("Preset=abb-terra-ac-modbus", abb_config)
        self.assertIn("Address=16645", abb_config)
        cfos_config = render_charger_preset_config(
            "cfos-power-brain-modbus",
            transport_kind="tcp",
            transport_host="cfos.local",
            transport_port=4701,
            transport_device="/dev/ttyUSB0",
            transport_unit_id=1,
        )
        self.assertIn("Preset=cfos-power-brain-modbus", cfos_config)
        self.assertIn("Map=P1:1,P1_P2_P3:0", cfos_config)
        openwb_config = render_charger_preset_config(
            "openwb-modbus-secondary",
            transport_kind="tcp",
            transport_host="openwb.local",
            transport_port=1502,
            transport_device="/dev/ttyUSB0",
            transport_unit_id=1,
        )
        self.assertIn("Preset=openwb-modbus-secondary", openwb_config)
        self.assertIn("EnableUsesCurrentWrite=1", openwb_config)
        self.assertIn("Address=10171", openwb_config)
        self.assertIn(
            "Device=/dev/ttyUSB7",
            render_charger_preset_config(
                "openwb-modbus-secondary",
                transport_kind="serial_rtu",
                transport_host="unused.local",
                transport_port=1502,
                transport_device="/dev/ttyUSB7",
                transport_unit_id=3,
            ),
        )
        with self.assertRaisesRegex(ValueError, "Unsupported charger preset"):
            render_charger_preset_config(
                "invalid-preset",
                transport_kind="tcp",
                transport_host="invalid.local",
                transport_port=1234,
                transport_device="/dev/ttyUSB0",
                transport_unit_id=1,
            )
        self.assertIn("Type=modbus_charger", wizard_adapters.native_charger_config(
            "modbus_charger",
            "",
            charger_preset=None,
            request_timeout_seconds=None,
            transport_kind="tcp",
            transport_host="modbus.local",
            transport_port=502,
            transport_device="/dev/ttyUSB0",
            transport_unit_id=1,
        ))
        self.assertIn("RequestTimeoutSeconds=3.5", wizard_adapters.native_charger_config(
            "smart_custom",
            "http://charger.local",
            charger_preset=None,
            request_timeout_seconds=3.5,
            transport_kind="serial_rtu",
            transport_host="charger.local",
            transport_port=502,
            transport_device="/dev/ttyUSB0",
            transport_unit_id=1,
        ))

        modbus_backend_lines, modbus_files, _ = split_topology_files(
            topology_preset="shelly-meter-modbus-charger",
            role_hosts={"meter": "meter.local"},
            meter_base_url="http://meter.local",
            switch_base_url="http://switch.local",
            charger_base_url="http://charger.local",
            charger_preset=None,
            request_timeout_seconds=None,
            switch_group_supported_phase_selections="P1,P1_P2,P1_P2_P3",
            transport_kind="tcp",
            transport_host="modbus.local",
            transport_port=502,
            transport_device="/dev/ttyUSB0",
            transport_unit_id=1,
        )
        self.assertIn("ChargerType=modbus_charger", modbus_backend_lines)
        self.assertIn("Profile=generic", modbus_files["wizard-charger.ini"])

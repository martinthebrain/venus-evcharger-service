# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from venus_evcharger.bootstrap import wizard_import as imported


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


class BootstrapWizardImportContracts(unittest.TestCase):
    def test_scalar_and_profile_contracts(self) -> None:
        self.assertIsNone(imported._as_bool(None))
        self.assertTrue(imported._as_bool("1"))
        self.assertTrue(imported._as_bool("true"))
        self.assertTrue(imported._as_bool(" YES "))
        self.assertTrue(imported._as_bool("on"))
        self.assertFalse(imported._as_bool("off"))
        self.assertIsNone(imported._as_int(None))
        self.assertIsNone(imported._as_int(" "))
        self.assertEqual(imported._as_int("42"), 42)
        self.assertIsNone(imported._as_float(None))
        self.assertIsNone(imported._as_float(" "))
        self.assertEqual(imported._as_float("4.25"), 4.25)
        self.assertEqual(imported._policy_mode("0"), "manual")
        self.assertEqual(imported._policy_mode("1"), "auto")
        self.assertEqual(imported._policy_mode("2"), "scheduled")
        self.assertIsNone(imported._policy_mode("3"))
        self.assertIsNone(imported._policy_mode(None))

        self.assertEqual(imported._profile_defaults(None), ("simple_relay", None, None))
        self.assertEqual(imported._profile_defaults_from_types("", "", ""), (None, None, None))
        self.assertEqual(imported._profile_defaults_from_types("none", "none", "goe_charger"), ("native_device", None, "goe_charger"))
        self.assertEqual(
            imported._profile_defaults_from_types("none", "switch_group", "simpleevse_charger"),
            ("hybrid_topology", None, "simpleevse_charger"),
        )
        self.assertEqual(
            imported._profile_defaults_from_types("shelly_meter", "shelly_switch", "template_charger"),
            ("multi_adapter_topology", "shelly-io-template-charger", "template_charger"),
        )
        self.assertEqual(imported._profile_defaults_from_types("template_meter", "custom_switch", ""), ("advanced_manual", None, None))

    def test_adapter_paths_hosts_and_switch_group_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = _write(root / "config.ini", "[DEFAULT]\n")
            adapter_path = _write(root / "adapter.ini", "[Adapter]\nHost=adapter.local\n")
            default_adapter = _write(root / "default-adapter.ini", "[DEFAULT]\nBaseUrl=http://default.local\n")
            missing_path = root / "missing.ini"

            self.assertIsNone(imported._adapter_path(config_path, None, "MeterConfigPath"))
            self.assertIsNone(imported._adapter_path(config_path, {"MeterConfigPath": ""}, "MeterConfigPath"))
            self.assertIsNone(imported._adapter_path(config_path, {"MeterConfigPath": str(missing_path)}, "MeterConfigPath"))
            self.assertEqual(imported._adapter_path(config_path, {"MeterConfigPath": "adapter.ini"}, "MeterConfigPath"), adapter_path)
            self.assertEqual(imported._adapter_path(config_path, {"MeterConfigPath": str(default_adapter)}, "MeterConfigPath"), default_adapter)
            self.assertEqual(imported._adapter_host_value(adapter_path), "adapter.local")
            self.assertEqual(imported._adapter_host_value(default_adapter), "http://default.local")
            self.assertIsNone(imported._adapter_host_value(None))

            phase_path = _write(root / "phase1.ini", "[Adapter]\nBaseUrl=http://phase1.local\n")
            group_path = _write(root / "switch-group.ini", "[Members]\nP1=phase1.ini\n")
            fallback_group_path = _write(root / "fallback-switch.ini", "[DEFAULT]\nHost=fallback-switch.local\n")
            self.assertEqual(imported._switch_group_member_host(group_path, "phase1.ini"), "http://phase1.local")
            self.assertEqual(imported._switch_group_member_host(group_path, str(phase_path)), "http://phase1.local")
            self.assertIsNone(imported._switch_group_member_host(group_path, "missing.ini"))
            self.assertIsNone(imported._switch_group_member_host(group_path, None))
            self.assertEqual(imported._switch_group_host_value(group_path), "http://phase1.local")
            self.assertEqual(imported._switch_group_host_value(fallback_group_path), "fallback-switch.local")
            self.assertIsNone(imported._switch_group_host_value(None))

    def test_transport_preset_timeout_phase_and_full_config_import_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write(root / "meter.ini", "[Adapter]\nHost=meter.local\n")
            _write(root / "switch-phase.ini", "[Adapter]\nHost=switch.local\n")
            _write(
                root / "switch-group.ini",
                "[Members]\nP1=switch-phase.ini\n[Capabilities]\nSupportedPhaseSelections=P1,P1_P2\n",
            )
            _write(
                root / "charger.ini",
                "[Adapter]\n"
                "Type=modbus_charger\n"
                "Host=charger.local\n"
                "Transport=tcp\n"
                "Preset=openwb-modbus-secondary\n"
                "RequestTimeoutSeconds=5.5\n"
                "[Transport]\n"
                "Host=modbus.local\n"
                "Port=1502\n"
                "Device=/dev/ttyUSB9\n"
                "UnitId=7\n",
            )
            config_path = _write(
                root / "config.ini",
                "[DEFAULT]\n"
                "Host=primary.local\n"
                "DeviceInstance=42\n"
                "Phase=L1\n"
                "Mode=2\n"
                "DigestAuth=yes\n"
                "Username=user\n"
                "Password=secret\n"
                "AutoStartSurplusWatts=1800\n"
                "AutoStopSurplusWatts=1500.5\n"
                "AutoMinSoc=30\n"
                "AutoResumeSoc=35.5\n"
                "AutoScheduledEnabledDays=Mon,Tue\n"
                "AutoScheduledLatestEndTime=06:30\n"
                "AutoScheduledNightCurrentAmps=6\n"
                "[Backends]\n"
                "MeterType=shelly_meter\n"
                "SwitchType=switch_group\n"
                "ChargerType=modbus_charger\n"
                "MeterConfigPath=meter.ini\n"
                "SwitchConfigPath=switch-group.ini\n"
                "ChargerConfigPath=charger.ini\n",
            )
            _write(root / "config.ini.wizard-inventory.ini", "[inventory]\n")
            parser = imported._config_parser(config_path)
            backends = parser["Backends"]

            self.assertEqual(imported._backend_types(backends), ("shelly_meter", "switch_group", "modbus_charger"))
            self.assertEqual(
                imported._transport_defaults(config_path, backends, "modbus_charger"),
                ("tcp", "modbus.local", 1502, "/dev/ttyUSB9", 7),
            )
            self.assertEqual(imported._charger_preset(config_path, backends), "openwb-modbus-secondary")
            self.assertIsNone(imported._request_timeout_seconds(config_path, backends, "modbus_charger"))
            self.assertEqual(imported._switch_group_phase_layout(config_path, backends), "P1,P1_P2")
            self.assertIsNone(imported._charger_adapter_path(config_path, backends, None))

            defaults = imported.load_imported_defaults(config_path)
        self.assertEqual(defaults.imported_from, str(config_path))
        self.assertEqual(defaults.profile, "multi_adapter_topology")
        self.assertEqual(defaults.topology_preset, "shelly-meter-modbus-switch-group")
        self.assertEqual(defaults.charger_backend, "modbus_charger")
        self.assertEqual(defaults.host_input, "primary.local")
        self.assertEqual(defaults.meter_host_input, "meter.local")
        self.assertEqual(defaults.switch_host_input, "switch.local")
        self.assertEqual(defaults.charger_host_input, "charger.local")
        self.assertEqual(defaults.device_instance, 42)
        self.assertEqual(defaults.phase, "L1")
        self.assertEqual(defaults.policy_mode, "scheduled")
        self.assertTrue(defaults.digest_auth)
        self.assertEqual(defaults.username, "user")
        self.assertEqual(defaults.password, "secret")
        self.assertEqual(defaults.charger_preset, "openwb-modbus-secondary")
        self.assertIsNone(defaults.request_timeout_seconds)
        self.assertEqual(defaults.switch_group_phase_layout, "P1,P1_P2")
        self.assertEqual(defaults.auto_start_surplus_watts, 1800.0)
        self.assertEqual(defaults.auto_stop_surplus_watts, 1500.5)
        self.assertEqual(defaults.auto_min_soc, 30.0)
        self.assertEqual(defaults.auto_resume_soc, 35.5)
        self.assertEqual(defaults.scheduled_enabled_days, "Mon,Tue")
        self.assertEqual(defaults.scheduled_latest_end_time, "06:30")
        self.assertEqual(defaults.scheduled_night_current_amps, 6.0)
        self.assertEqual(defaults.transport_kind, "tcp")
        self.assertEqual(defaults.transport_host, "modbus.local")
        self.assertEqual(defaults.transport_port, 1502)
        self.assertEqual(defaults.transport_device, "/dev/ttyUSB9")
        self.assertEqual(defaults.transport_unit_id, 7)
        self.assertEqual(defaults.inventory_path, str(Path(temp_dir) / "config.ini.wizard-inventory.ini"))

    def test_goe_timeout_and_transport_absence_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write(root / "charger.ini", "[Adapter]\nType=goe_charger\nRequestTimeoutSeconds=4.25\n")
            config_path = _write(
                root / "config.ini",
                "[Backends]\nMeterType=none\nSwitchType=none\nChargerType=goe_charger\nChargerConfigPath=charger.ini\n",
            )
            parser = imported._config_parser(config_path)
            backends = parser["Backends"]
            self.assertEqual(imported._request_timeout_seconds(config_path, backends, "goe_charger"), 4.25)
            self.assertEqual(imported._transport_defaults(config_path, backends, "goe_charger"), (None, None, None, None, None))
            self.assertIsNone(imported._switch_group_phase_layout(config_path, backends))
            defaults = imported.load_imported_defaults(config_path)
        self.assertEqual(defaults.profile, "native_device")
        self.assertEqual(defaults.charger_backend, "goe_charger")
        self.assertEqual(defaults.request_timeout_seconds, 4.25)

    def test_result_json_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.assertIsNone(imported._json_str(None))
            self.assertEqual(imported._json_str("value"), "value")
            self.assertIsNone(imported._json_bool(None))
            self.assertTrue(imported._json_bool(True))
            self.assertIsNone(imported._json_int(None))
            self.assertEqual(imported._json_int(7), 7)
            self.assertIsNone(imported._json_float(None))
            self.assertEqual(imported._json_float(7), 7.0)
            self.assertEqual(imported._json_float(7.5), 7.5)
            invalid_fields = (
                ("bad-profile", {"profile": "bad"}, "Unsupported profile"),
                ("bad-string", {"host_input": 1}, "string or null"),
                ("bad-bool", {"digest_auth": "yes"}, "boolean or null"),
                ("bad-int", {"device_instance": True}, "integer or null"),
                ("bad-float", {"auto_start_surplus_watts": "1800"}, "numeric or null"),
                ("bad-charger", {"charger_backend": "bad"}, "Unsupported charger backend"),
                ("bad-policy", {"policy_mode": "bad"}, "Unsupported policy mode"),
                ("bad-transport", {"transport_kind": "udp"}, "Unsupported transport"),
            )
            for name, defaults_payload, error_pattern in invalid_fields:
                path = root / f"{name}.wizard-result.json"
                path.write_text(json.dumps({"answer_defaults": defaults_payload}), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, error_pattern):
                    imported._load_from_result_json(path)

            list_path = root / "list.wizard-result.json"
            list_path.write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "JSON object"):
                imported._load_from_result_json(list_path)
            missing_defaults_path = root / "missing-defaults.wizard-result.json"
            missing_defaults_path.write_text(json.dumps({"answer_defaults": []}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "missing answer_defaults"):
                imported._load_from_result_json(missing_defaults_path)

            result_path = root / "valid.wizard-result.json"
            sibling_inventory = root / "valid.wizard-result.json.wizard-inventory.ini"
            sibling_inventory.write_text("[inventory]\n", encoding="utf-8")
            result_path.write_text(
                json.dumps(
                    {
                        "inventory_path": "explicit-inventory.ini",
                        "answer_defaults": {
                            "profile": "native_device",
                            "host_input": "charger.local",
                            "meter_host_input": "meter.local",
                            "switch_host_input": "switch.local",
                            "charger_host_input": "charger.local",
                            "device_instance": 21,
                            "phase": "L1",
                            "policy_mode": "auto",
                            "digest_auth": False,
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
                            "transport_host": "192.0.2.90",
                            "transport_port": 502,
                            "transport_device": "/dev/ttyUSB0",
                            "transport_unit_id": 1,
                        }
                    }
                ),
                encoding="utf-8",
            )
            defaults = imported.load_imported_defaults(result_path)
            fallback_result_path = root / "fallback.wizard-result.json"
            fallback_inventory = root / "fallback.wizard-result.json.wizard-inventory.ini"
            fallback_inventory.write_text("[inventory]\n", encoding="utf-8")
            fallback_result_path.write_text(json.dumps({"answer_defaults": {}}), encoding="utf-8")
            fallback_defaults = imported.load_imported_defaults(fallback_result_path)
            missing_path = root / "missing.ini"
            with self.assertRaises(ValueError) as missing_error:
                imported.load_imported_defaults(missing_path)
        self.assertEqual(defaults.imported_from, str(result_path))
        self.assertEqual(defaults.profile, "native_device")
        self.assertEqual(defaults.host_input, "charger.local")
        self.assertEqual(defaults.meter_host_input, "meter.local")
        self.assertEqual(defaults.switch_host_input, "switch.local")
        self.assertEqual(defaults.charger_host_input, "charger.local")
        self.assertEqual(defaults.device_instance, 21)
        self.assertEqual(defaults.phase, "L1")
        self.assertEqual(defaults.policy_mode, "auto")
        self.assertFalse(defaults.digest_auth)
        self.assertEqual(defaults.username, "user")
        self.assertIsNone(defaults.password)
        self.assertEqual(defaults.topology_preset, "shelly-meter-goe")
        self.assertEqual(defaults.charger_backend, "goe_charger")
        self.assertEqual(defaults.charger_preset, "go-e")
        self.assertEqual(defaults.request_timeout_seconds, 2.0)
        self.assertEqual(defaults.switch_group_phase_layout, "P1,P1_P2")
        self.assertEqual(defaults.auto_start_surplus_watts, 1800.0)
        self.assertEqual(defaults.auto_stop_surplus_watts, 1500.5)
        self.assertEqual(defaults.auto_min_soc, 30.0)
        self.assertEqual(defaults.auto_resume_soc, 35.5)
        self.assertEqual(defaults.scheduled_enabled_days, "Mon")
        self.assertEqual(defaults.scheduled_latest_end_time, "06:30")
        self.assertEqual(defaults.scheduled_night_current_amps, 6.0)
        self.assertEqual(defaults.transport_kind, "tcp")
        self.assertEqual(defaults.transport_host, "192.0.2.90")
        self.assertEqual(defaults.transport_port, 502)
        self.assertEqual(defaults.transport_device, "/dev/ttyUSB0")
        self.assertEqual(defaults.transport_unit_id, 1)
        self.assertEqual(defaults.inventory_path, "explicit-inventory.ini")
        self.assertEqual(fallback_defaults.inventory_path, str(fallback_inventory))
        self.assertEqual(str(missing_error.exception), f"Import config does not exist: {missing_path}")


if __name__ == "__main__":
    unittest.main()

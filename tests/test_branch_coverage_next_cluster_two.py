# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.modules["vedbus"] = MagicMock()

from tests.venus_evcharger_test_fixtures import make_runtime_state_service
from venus_evcharger.backend.modbus_transport_types import ModbusTransportSettings
from venus_evcharger.controllers import state_json as state_json_mod
from venus_evcharger.controllers import state_runtime_snapshot as runtime_snapshot_mod
from venus_evcharger.controllers import state_runtime_snapshot_victron as snapshot_victron
from venus_evcharger.controllers.state_runtime_normalize import RuntimeStateNormalizer
from venus_evcharger.energy import probe_core as probe_core_mod

class BranchCoverageNextClusterTwoStateRuntimeSnapshotCases(unittest.TestCase):
    def test_runtime_snapshot_helpers_cover_profile_payloads_and_state_loading(self) -> None:
        service = make_runtime_state_service(
            virtual_mode=0,
            auto_energy_sources=(SimpleNamespace(source_id="hybrid"), SimpleNamespace(source_id="victron"), SimpleNamespace(source_id=""),),
            auto_battery_discharge_balance_victron_bias_source_id=" victron ",
            auto_battery_discharge_balance_victron_bias_kp=0.2,
            auto_battery_discharge_balance_victron_bias_ki=0.02,
            auto_battery_discharge_balance_victron_bias_kd=0.01,
            auto_battery_discharge_balance_victron_bias_deadband_watts=100.0,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=500.0,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=50.0,
            auto_battery_discharge_balance_victron_bias_activation_mode="EXPORT_ONLY",
            _victron_ess_balance_auto_apply_generation=2,
            _victron_ess_balance_auto_apply_observe_until=123.0,
            _victron_ess_balance_auto_apply_last_applied_param="kp",
            _victron_ess_balance_auto_apply_last_applied_at=120.0,
            _victron_ess_balance_auto_apply_suspend_until=130.0,
            _victron_ess_balance_auto_apply_suspend_reason="cooldown",
            _victron_ess_balance_oscillation_lockout_until=140.0,
            _victron_ess_balance_oscillation_lockout_reason="oscillation",
            _victron_ess_balance_overshoot_cooldown_until=150.0,
            _victron_ess_balance_overshoot_cooldown_reason="overshoot",
            _victron_ess_balance_last_stable_tuning={"kp": 0.18},
            _victron_ess_balance_last_stable_at=160.0,
            _victron_ess_balance_last_stable_profile_key="profile-a",
            _victron_ess_balance_conservative_tuning={"kp": 0.1},
            _victron_ess_balance_safe_state_active=True,
            _victron_ess_balance_safe_state_reason="rollback",
            _victron_ess_balance_learning_profiles={
                "profile-a": {
                    "delay_samples": 3,
                    "gain_samples": 2,
                    "overshoot_count": 1,
                    "settled_count": 4,
                    "response_delay_seconds": 5.0,
                    "estimated_gain": 2.5,
                    "response_delay_mad_seconds": 1.0,
                    "gain_mad": 0.5,
                    "stability_score": 0.8,
                    "typical_response_delay_seconds": 6.0,
                    "effective_gain": 2.7,
                    "regime_consistency_score": 0.9,
                    "response_variance_score": 0.7,
                    "reproducibility_score": 0.6,
                    "safe_ramp_rate_watts_per_second": 40.0,
                    "preferred_bias_limit_watts": 450.0,
                    "action_direction": "more_export",
                },
                "profile-b": "bad",
            },
            _get_worker_snapshot=lambda: {"battery_combined_soc": 62.0},
        )
        snapshot = runtime_snapshot_mod.RuntimeStateSnapshotBuilder(service, RuntimeStateNormalizer())

        explicit_profile = {"sample_count": 5}
        self.assertEqual(snapshot._victron_ess_balance_runtime_profile_sample_count(explicit_profile), 5)
        self.assertEqual(snapshot._victron_ess_balance_runtime_profile_sample_count({"delay_samples": 2, "settled_count": 3}), 3)
        self.assertEqual(snapshot._victron_ess_balance_runtime_profile_metric({"a": 1.5}, "a"), 1.5)
        self.assertEqual(snapshot._victron_ess_balance_runtime_profile_metric({"b": 2.5}, "a", "b"), 2.5)
        self.assertEqual(snapshot._victron_ess_balance_runtime_adaptive_scalar_payload(service)["activation_mode"], "export_only")
        self.assertIn("/energy=hybrid,victron", snapshot._victron_ess_balance_runtime_topology_key(service, "victron"))

        learning_state = snapshot._victron_ess_balance_runtime_learning_state(service)
        self.assertEqual(learning_state["schema_version"], 2)
        self.assertIn("profile-a", learning_state["profiles"])
        self.assertEqual(learning_state["profiles"]["profile-b"]["key"], "profile-b")
        self.assertEqual(
            snapshot._victron_ess_balance_runtime_adaptive_tuning_state(service)["source_id"],
            "victron",
        )

        current_state = snapshot.build()
        self.assertEqual(current_state["mode"], 0)
        self.assertIn("victron_ess_balance_learning_state", current_state)
        self.assertIn("victron_ess_balance_adaptive_tuning_state", current_state)
        self.assertEqual(
            runtime_snapshot_mod.RuntimeStateSnapshotBuilder._energy_runtime_state(service)["combined_battery_soc"],
            62.0,
        )
        non_mapping_service = SimpleNamespace(_get_worker_snapshot=lambda: "bad")
        self.assertIsNone(
            runtime_snapshot_mod.RuntimeStateSnapshotBuilder._energy_runtime_state(non_mapping_service)[
                "combined_battery_soc"
            ]
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            good_path = Path(tmpdir) / "runtime.json"
            bad_path = Path(tmpdir) / "bad.json"
            list_path = Path(tmpdir) / "list.json"
            good_path.write_text(json.dumps({"ok": True}), encoding="utf-8")
            bad_path.write_text("{broken", encoding="utf-8")
            list_path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
            self.assertEqual(snapshot._read_runtime_state_payload(str(good_path)), {"ok": True})
            self.assertIsNone(snapshot._read_runtime_state_payload(str(Path(tmpdir) / "missing.json")))
            self.assertIsNone(snapshot._read_runtime_state_payload(str(bad_path)))
            self.assertIsNone(snapshot._read_runtime_state_payload(str(list_path)))
            self.assertIsNone(state_json_mod.json_object_payload({1: "bad-key"}))

        self.assertEqual(snapshot_victron._victron_ess_balance_energy_ids(service), ["hybrid", "victron"])
        self.assertEqual(snapshot_victron._victron_ess_balance_runtime_non_negative_int(-5), 0)
        self.assertEqual(
            snapshot_victron._victron_ess_balance_runtime_attr_text(service, "auto_battery_discharge_balance_victron_bias_activation_mode", normalize_lower=True),
            "export_only",
        )
        self.assertEqual(snapshot_victron._victron_ess_balance_runtime_profile_text({}, "missing", fallback="x"), "x")
        self.assertEqual(snapshot_victron._victron_ess_balance_runtime_profile_counts({"delay_samples": 1})["sample_count"], 1)
        self.assertEqual(snapshot_victron._victron_ess_balance_runtime_profile_response_metrics({"response_delay_seconds": 4.0})["response_delay_seconds"], 4.0)
        self.assertEqual(snapshot_victron._victron_ess_balance_runtime_profile_learning_metrics({"estimated_gain": 2.0})["effective_gain"], 2.0)
        self.assertEqual(snapshot_victron._victron_ess_balance_runtime_profile_limit_metrics({"preferred_bias_limit_watts": 300.0})["preferred_bias_limit_watts"], 300.0)


class BranchCoverageNextClusterTwoProbeCoreCases(unittest.TestCase):
    def test_probe_core_helpers_cover_parser_fields_validation_and_candidates(self) -> None:
        parser = configparser.ConfigParser()
        parser["DEFAULT"] = {"Host": "default.local"}
        parser["Transport"] = {"Host": "transport.local"}
        parser["SocRead"] = {
            "Address": "10",
            "RegisterType": "INPUT",
            "DataType": "INT16",
            "WordOrder": "LITTLE",
            "Scale": "2",
        }
        parser["BatteryPowerRead"] = {"Address": "not-an-int"}

        self.assertEqual(probe_core_mod._config_transport_section(parser)["Host"], "transport.local")
        del parser["Transport"]
        self.assertEqual(probe_core_mod._config_transport_section(parser)["Host"], "default.local")

        self.assertEqual(
            probe_core_mod._field_settings(parser, "SocRead"),
            {
                "section": "SocRead",
                "register_type": "input",
                "address": 10,
                "data_type": "int16",
                "word_order": "little",
                "scale": 2.0,
            },
        )
        self.assertIsNone(probe_core_mod._field_settings(parser, "BatteryPowerRead"))
        self.assertIsNone(probe_core_mod._field_settings(parser, "Missing"))
        self.assertEqual(
            probe_core_mod._probe_field(parser, probe_core_mod._field_settings)["section"],
            "SocRead",
        )

        empty_parser = configparser.ConfigParser()
        with self.assertRaisesRegex(ValueError, "at least one Modbus read section"):
            probe_core_mod._probe_field(empty_parser, probe_core_mod._field_settings)

        base_transport = ModbusTransportSettings(
            transport_kind="tcp",
            unit_id=1,
            timeout_seconds=1.0,
            host="base.local",
            port=502,
            device=None,
            baudrate=9600,
            bytesize=8,
            parity="N",
            stopbits=1,
            serial_port_owner="none",
            serial_port_owner_stop_command=None,
            serial_port_owner_start_command=None,
            serial_retry_count=0,
            serial_retry_delay_seconds=0.0,
        )
        huawei_results = probe_core_mod._validate_fields(
            base_transport,
            parser,
            "huawei_inverter",
            probe_core_mod._field_settings,
            lambda _transport, field: {"ok": True, "address": field["address"]},
        )
        self.assertGreater(len(huawei_results), 1)
        self.assertTrue(any(result["required"] is False for result in huawei_results))
        plain_results = probe_core_mod._validate_fields(
            base_transport,
            parser,
            "generic_profile",
            probe_core_mod._field_settings,
            lambda _transport, field: {"ok": True, "address": field["address"]},
        )
        self.assertEqual(len(plain_results), 1)
        self.assertTrue(all(result["required"] is True for result in plain_results))

        serial_transport = ModbusTransportSettings(
            transport_kind="serial_rtu",
            unit_id=3,
            timeout_seconds=1.0,
            host="",
            port=None,
            device="/dev/ttyUSB0",
            baudrate=9600,
            bytesize=8,
            parity="N",
            stopbits=1,
            serial_port_owner="none",
            serial_port_owner_stop_command=None,
            serial_port_owner_start_command=None,
            serial_retry_count=0,
            serial_retry_delay_seconds=0.0,
        )
        tcp_candidates = probe_core_mod._probe_candidates(
            base_transport,
            {"host": ["a.local", "b.local"], "port_candidates": ["502", "503"], "unit_id_candidates": ["1", "2"]},
        )
        self.assertEqual(len(tcp_candidates), 8)
        serial_candidates = probe_core_mod._probe_candidates(serial_transport, {})
        self.assertEqual(serial_candidates, (serial_transport,))

        with self.assertRaisesRegex(ValueError, "host candidate"):
            probe_core_mod._validate_probe_host_candidates([])

        self.assertEqual(probe_core_mod._text_candidates((" a ", "", None)), ["a"])
        self.assertEqual(probe_core_mod._int_candidates(["x"]), [])
        self.assertEqual(probe_core_mod._probe_int_values(["1", "x", None]), [1, None, None])
        self.assertIsNone(probe_core_mod._probe_int_value(True))
        self.assertIsNone(probe_core_mod._probe_int_value(" "))
        self.assertIsNone(probe_core_mod._probe_int_value("x"))
        self.assertEqual(probe_core_mod._probe_int_value("5"), 5)
        self.assertIsNone(probe_core_mod._optional_probe_text(" "))
        self.assertEqual(probe_core_mod._optional_probe_text(" host "), "host")
        self.assertEqual(probe_core_mod._normalized_probe_text(" HoLdIng ", "fallback"), "holding")
        self.assertEqual(probe_core_mod._normalized_probe_text(None, "fallback"), "fallback")

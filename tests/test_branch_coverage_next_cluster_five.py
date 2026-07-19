import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from tests.venus_evcharger_test_fixtures import make_state_validation_service
from venus_evcharger.bootstrap import wizard_persistence as wizard_persistence_mod
from venus_evcharger.controllers.state import ServiceStateController
from venus_evcharger.controllers.state_validation import RuntimeConfigValidator
from venus_evcharger.energy import probe_cli as probe_cli_mod


def _normalize_mode(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float, str)):
        return int(value)
    return 0


class BranchCoverageNextClusterFiveStateValidationTests(unittest.TestCase):
    def test_validate_runtime_config_clamps_victron_balance_modes_and_auto_apply_fields(self) -> None:
        service = make_state_validation_service(
            auto_battery_discharge_balance_coordination_support_mode="bad-mode",
            auto_battery_discharge_balance_victron_bias_support_mode="bad-mode",
            auto_battery_discharge_balance_victron_bias_activation_mode="bad-mode",
            auto_battery_discharge_balance_victron_bias_auto_apply_min_confidence=-1.0,
            auto_battery_discharge_balance_victron_bias_auto_apply_min_stability_score=-1.0,
            auto_battery_discharge_balance_victron_bias_auto_apply_blend=-1.0,
            auto_battery_discharge_balance_victron_bias_auto_apply_min_profile_samples=-1,
            auto_battery_discharge_balance_victron_bias_observation_window_seconds=-1.0,
            auto_battery_discharge_balance_victron_bias_oscillation_lockout_window_seconds=-1.0,
            auto_battery_discharge_balance_victron_bias_oscillation_lockout_min_direction_changes=-1,
            auto_battery_discharge_balance_victron_bias_oscillation_lockout_duration_seconds=-1.0,
            auto_battery_discharge_balance_victron_bias_rollback_min_stability_score=-1.0,
        )

        controller = ServiceStateController(service, _normalize_mode)
        controller.validate_runtime_config()

        self.assertEqual(service.auto_battery_discharge_balance_coordination_support_mode, "supported_only")
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_support_mode, "allow_experimental")
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_activation_mode, "always")
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_auto_apply_min_confidence, 0.85)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_auto_apply_min_stability_score, 0.75)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_auto_apply_blend, 0.25)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_auto_apply_min_profile_samples, 0)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_observation_window_seconds, 0.0)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_oscillation_lockout_window_seconds, 0.0)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_oscillation_lockout_min_direction_changes, 0)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_oscillation_lockout_duration_seconds, 0.0)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_rollback_min_stability_score, 0.45)

    def test_state_validation_helpers_cover_missing_and_valid_optional_modes(self) -> None:
        controller = RuntimeConfigValidator(make_state_validation_service())

        bare_service = object()
        controller._validate_scheduled_runtime_config(bare_service)
        controller._normalize_discharge_balance_bias_mode(bare_service)
        controller._normalize_discharge_balance_coordination_support_mode(bare_service)

        valid_service = make_state_validation_service(
            auto_battery_discharge_balance_victron_bias_support_mode="supported_only",
            auto_battery_discharge_balance_victron_bias_activation_mode="export_only",
        )
        controller._normalize_victron_balance_support_mode(valid_service)
        controller._normalize_victron_balance_activation_mode(valid_service)
        self.assertEqual(valid_service.auto_battery_discharge_balance_victron_bias_support_mode, "supported_only")
        self.assertEqual(valid_service.auto_battery_discharge_balance_victron_bias_activation_mode, "export_only")


class BranchCoverageNextClusterFiveProbeCliTests(unittest.TestCase):
    def test_render_payload_falls_back_to_json_for_missing_recommendation_and_unknown_emit(self) -> None:
        args = Namespace(command="validate-huawei-energy", emit="ini")
        payload = {"ok": True}
        rendered = probe_cli_mod._render_payload(args, payload)
        self.assertEqual(json.loads(rendered), payload)

        args.emit = "unknown"
        payload = {"recommendation": {"summary": "x"}}
        rendered = probe_cli_mod._render_payload(args, payload)
        self.assertEqual(json.loads(rendered), payload)

    def test_render_recommendation_field_and_payload_with_written_files_cover_fallbacks(self) -> None:
        payload = {"recommendation": {"summary": ""}, "ok": True}
        rendered = probe_cli_mod._render_payload(Namespace(command="validate-huawei-energy", emit="summary"), payload)
        self.assertEqual(json.loads(rendered), payload)

        args = Namespace(command="validate-huawei-energy", write_recommendation_prefix="/tmp/out")
        enriched = probe_cli_mod._payload_with_written_files(args, {"recommendation": "bad"})
        self.assertEqual(enriched, {"recommendation": "bad"})

    def test_bundle_source_id_defaults_to_huawei_without_config_line(self) -> None:
        self.assertEqual(probe_cli_mod._bundle_source_id_from_recommendation({"config_snippet": "Profile=x"}), "huawei")


class BranchCoverageNextClusterFiveWizardPersistenceTests(unittest.TestCase):
    def test_merge_and_source_helpers_cover_empty_and_non_dict_cases(self) -> None:
        self.assertEqual(wizard_persistence_mod._suggested_energy_merge_lines({"suggested_energy_merge": "bad"}), [])
        self.assertEqual(wizard_persistence_mod._suggested_energy_merge_lines({"suggested_energy_merge": {}}), [])
        self.assertEqual(wizard_persistence_mod._merged_source_ids({"merged_source_ids": "bad"}), [])
        self.assertIsNone(wizard_persistence_mod._suggested_energy_merge_applied_line({}))
        self.assertEqual(wizard_persistence_mod._inventory_lines({"device_inventory": {}}), [])
        self.assertEqual(
            wizard_persistence_mod._inventory_lines(
                {
                    "device_inventory": {"profiles": [], "devices": [], "bindings": []},
                    "inventory_path": "",
                }
            ),
            ["inventory_counts: profiles=0 devices=0 bindings=0"],
        )
        self.assertEqual(
            wizard_persistence_mod._suggested_energy_source_ids([{"source_id": "alpha"}, "bad", {"source_id": "beta"}]),
            ["alpha", "beta"],
        )

    def test_persist_wizard_state_writes_summary_for_sparse_merge_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            payload = {
                "config_path": str(config_path),
                "profile": "simple_relay",
                "charger_backend": "none",
                "charger_preset": None,
                "policy_mode": "manual",
                "transport_kind": None,
                "role_hosts": {},
                "suggested_energy_merge": {"merged_source_ids": ["hybrid"]},
            }

            result_path, audit_path, topology_path = wizard_persistence_mod.persist_wizard_state(config_path, payload)

            self.assertTrue(Path(result_path).exists())
            self.assertTrue(Path(audit_path).exists())
            topology_text = Path(topology_path).read_text(encoding="utf-8")
            self.assertIn("suggested_energy_merge: hybrid", topology_text)

    def test_topology_summary_text_covers_optional_sections(self) -> None:
        payload = {
            "config_path": "/tmp/config.ini",
            "profile": "multi_adapter_topology",
            "topology_preset": "preset-a",
            "charger_backend": "goe_charger",
            "charger_preset": "goe",
            "policy_mode": "auto",
            "transport_kind": "tcp",
            "role_hosts": {"charger": "charger.local"},
            "validation": {"resolved_roles": {"charger": True}},
            "live_check": {"ok": True},
            "suggested_energy_sources": [{"source_id": "hybrid"}],
            "suggested_energy_merge": {"merged_source_ids": ["hybrid"], "applied_to_config": False},
            "suggested_blocks": {"AutoEnergySource.hybrid": "[AutoEnergySource.hybrid]"},
            "warnings": ["manual review"],
        }

        summary = wizard_persistence_mod._topology_summary_text(payload)

        self.assertIn("role_endpoints:\n  - charger: charger.local", summary)
        self.assertIn("resolved_roles:", summary)
        self.assertIn("live_check_ok: True", summary)
        self.assertIn("suggested_energy_sources: hybrid", summary)
        self.assertIn("suggested_energy_merge: hybrid", summary)
        self.assertIn("suggested_energy_merge_applied: False", summary)
        self.assertIn("suggested_blocks: AutoEnergySource.hybrid", summary)
        self.assertIn("warnings:\n  - manual review", summary)


if __name__ == "__main__":
    unittest.main()

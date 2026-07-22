import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.venus_evcharger_test_fixtures import make_runtime_state_service
from tests.wizard_branch_runtime_cases_common import _imported_defaults, _namespace
from venus_evcharger.bootstrap import wizard_cli_imports, wizard_cli_non_interactive
from venus_evcharger.controllers.state_restore import RuntimeStateRestorer
from venus_evcharger.controllers.state_restore_victron_ess import (
    VictronEssRuntimeRestorer,
    _victron_ess_balance_energy_ids,
)
from venus_evcharger.controllers.state_runtime_normalize import RuntimeStateNormalizer


class BranchCoverageNextClusterSevenTests(unittest.TestCase):
    def test_wizard_cli_wrappers_cover_direct_non_interactive_paths(self) -> None:
        imported = _imported_defaults(
            imported_from="/tmp/import.json",
            policy_mode="scheduled",
            topology_preset="template-stack",
            charger_backend="goe_charger",
            device_instance=77,
            phase="L1",
            host_input="imported.local",
        )
        namespace = _namespace(
            import_config="/tmp/import.json",
            policy_mode="manual",
            charger_backend="modbus_charger",
            device_instance=81,
            phase="L2",
            host="cli.local",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            result_path = Path(f"{config_path}.wizard-result.json")
            result_path.write_text("{}", encoding="utf-8")

            self.assertEqual(wizard_cli_imports.resume_import_path(_namespace(resume_last=True, config_path=str(config_path))), result_path)
            self.assertEqual(wizard_cli_imports.clone_import_path(_namespace(clone_current=True, config_path=str(config_path))), config_path)

        self.assertEqual(wizard_cli_imports.resolve_import_path(namespace), Path("/tmp/import.json"))
        self.assertEqual(wizard_cli_non_interactive.non_interactive_policy_mode(namespace, imported), "manual")
        self.assertEqual(
            wizard_cli_non_interactive.non_interactive_topology_preset(_namespace(), imported, "multi_adapter_topology"),
            "template-stack",
        )
        self.assertEqual(
            wizard_cli_non_interactive.non_interactive_backend(namespace, imported, "native_device"),
            "modbus_charger",
        )
        self.assertEqual(wizard_cli_non_interactive.non_interactive_device_instance(namespace, imported), 81)
        self.assertEqual(wizard_cli_non_interactive.non_interactive_phase(namespace, imported), "L2")
        self.assertEqual(wizard_cli_non_interactive.non_interactive_string("cli.local", "imported.local"), "cli.local")

    def test_state_restore_helpers_cover_invalid_payloads_and_empty_energy_ids(self) -> None:
        service = make_runtime_state_service(
            auto_battery_discharge_balance_victron_bias_source_id="hybrid",
            auto_battery_discharge_balance_victron_bias_activation_mode="always",
            auto_energy_sources=[SimpleNamespace(source_id=""), SimpleNamespace(source_id="hybrid")],
            _victron_ess_balance_learning_profiles={"keep": {"sample_count": 1}},
        )
        restorer = VictronEssRuntimeRestorer()

        self.assertIsNone(restorer._victron_ess_balance_activation_mode({"activation_mode": "bad"}, service))
        self.assertEqual(_victron_ess_balance_energy_ids(service), ["hybrid"])

        learning_payload = {
            "schema_version": 1,
            "source_id": "hybrid",
            "topology_key": restorer._victron_ess_balance_runtime_topology_key(service, "hybrid"),
            "profiles": {"": {}, "bad": "x", "ok": {"delay_samples": 2}},
        }
        normalized = restorer._normalized_victron_ess_balance_learning_profiles(learning_payload["profiles"])
        self.assertEqual(set(normalized), {"ok"})

        restorer._restore_victron_ess_balance_learning_state_payload(service, {"schema_version": 9})
        self.assertIn("keep", service._victron_ess_balance_learning_profiles)

        restorer._restore_victron_ess_balance_learning_state_payload(
            service,
            {"schema_version": 1, "topology_key": "mismatch", "profiles": {"ok": {}}},
        )
        self.assertIn("keep", service._victron_ess_balance_learning_profiles)

        restorer._restore_victron_ess_balance_learning_state_payload(
            service,
            {
                "schema_version": 1,
                "source_id": "hybrid",
                "topology_key": restorer._victron_ess_balance_runtime_topology_key(service, "hybrid"),
                "profiles": "bad",
            },
        )
        self.assertIn("keep", service._victron_ess_balance_learning_profiles)

        restorer._restore_victron_ess_balance_adaptive_tuning_payload(service, {"schema_version": 9})
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_activation_mode, "always")

        restorer._restore_victron_ess_balance_adaptive_tuning_payload(
            service,
            {"schema_version": 1, "topology_key": "mismatch"},
        )
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_activation_mode, "always")

        restorer._restore_victron_ess_balance_adaptive_tuning_payload(
            service,
            {
                "schema_version": 1,
                "source_id": "hybrid",
                "topology_key": restorer._victron_ess_balance_runtime_topology_key(service, "hybrid"),
                "activation_mode": "bad",
            },
        )
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_activation_mode, "always")

    def test_state_restore_helpers_cover_non_dict_counts_and_empty_runtime_paths(self) -> None:
        service = make_runtime_state_service(runtime_state_path="")
        controller = RuntimeStateRestorer(
            service,
            int,
            RuntimeStateNormalizer(),
            VictronEssRuntimeRestorer(),
        )

        self.assertEqual(controller._normalized_phase_switch_mismatch_counts("bad", "P1"), {})
        self.assertEqual(RuntimeStateRestorer._normalized_contactor_fault_counts("bad"), {})
        self.assertEqual(
            RuntimeStateRestorer._normalized_contactor_fault_counts(
                {"bad": 2, "contactor-suspected-open": 3}
            ),
            {"contactor-suspected-open": 3},
        )


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from tests.venus_evcharger_auto_controller_support import AutoDecisionControllerTestCase
from tests.wizard_branch_coverage_cases_common import _result
from venus_evcharger.auto import logic_gates_battery_balance_support as battery_balance_support_mod
from venus_evcharger.bootstrap import wizard_cli_output
from venus_evcharger.energy import EnergySourceDefinition, EnergySourceSnapshot
from venus_evcharger.energy import aggregate as aggregate_mod


class BranchCoverageNextWizardCliOutputCases(unittest.TestCase):
    def test_suggested_energy_source_summary_and_merge_helpers_cover_sparse_inputs(self) -> None:
        self.assertEqual(
            wizard_cli_output._result_live_check_lines(
                _result(live_check={"roles": {}})
            ),
            ["Live connectivity by role:"],
        )
        self.assertEqual(
            wizard_cli_output._result_suggested_block_lines(
                _result(suggested_blocks={"energy": "Line one\n\nLine three"})
            ),
            [
                "Suggested config blocks:",
                "  - energy:",
                "    Line one",
                "",
                "    Line three",
            ],
        )
        self.assertEqual(
            wizard_cli_output._suggested_energy_source_summary(
                {
                    "source_id": "hybrid",
                    "profile": "huawei",
                }
            ),
            "  - hybrid: profile=huawei",
        )
        self.assertEqual(
            wizard_cli_output._suggested_energy_source_summary(
                {
                    "source_id": "hybrid",
                    "profile": "huawei",
                    "configPath": "/tmp/source.ini",
                    "host": "inverter.local",
                    "port": 502,
                    "unitId": 7,
                }
            ),
            "  - hybrid: profile=huawei, config=/tmp/source.ini, host=inverter.local, port=502, unit_id=7",
        )
        self.assertEqual(
            wizard_cli_output._result_suggested_energy_source_lines(
                _result(
                    suggested_energy_sources=(
                        {"source_id": "hybrid", "profile": "huawei"},
                        {
                            "source_id": "victron",
                            "profile": "dbus",
                            "capacityConfigKey": "AutoEnergySource.victron.UsableCapacityWh",
                        },
                    )
                )
            ),
            [
                "Suggested energy sources:",
                "  - hybrid: profile=huawei",
                "  - victron: profile=dbus",
                "    capacity follow-up: AutoEnergySource.victron.UsableCapacityWh=<set-me>",
            ],
        )
        self.assertEqual(
            wizard_cli_output._suggested_energy_merge_capacity_lines(
                {
                    "capacity_follow_up": [
                        None,
                        {},
                        {"config_key": "   "},
                        {"config_key": "AutoEnergySource.hybrid.UsableCapacityWh"},
                    ]
                }
            ),
            [
                "  - capacity follow-up:",
                "    AutoEnergySource.hybrid.UsableCapacityWh=<set-me>",
            ],
        )
        self.assertEqual(wizard_cli_output._suggested_energy_merge_capacity_lines({}), [])
        self.assertIsNone(wizard_cli_output._merged_source_ids_line({"merged_source_ids": []}))
        self.assertEqual(
            wizard_cli_output._merged_source_ids_line({"merged_source_ids": ["hybrid", "victron"]}),
            "  - merged source ids: hybrid,victron",
        )
        self.assertIsNone(wizard_cli_output._helper_file_line({"helper_file": ""}))
        self.assertEqual(
            wizard_cli_output._helper_file_line({"helper_file": "wizard-energy.ini"}),
            "  - helper file: wizard-energy.ini",
        )
        self.assertEqual(wizard_cli_output._suggested_energy_merge_block_lines(" \n "), [])
        self.assertEqual(
            wizard_cli_output._suggested_energy_merge_block_lines("[section]\nkey=value"),
            [
                "  - merge block:",
                "    [section]",
                "    key=value",
            ],
        )
        self.assertEqual(
            wizard_cli_output._suggested_energy_merge_header_lines({"applied_to_config": False}),
            [
                "Suggested AutoEnergy merge:",
                "  - applied to main config: no",
            ],
        )
        self.assertEqual(
            wizard_cli_output._suggested_energy_merge_header_lines(
                {
                    "applied_to_config": True,
                    "merged_source_ids": ["hybrid"],
                    "helper_file": "wizard-energy.ini",
                }
            ),
            [
                "Suggested AutoEnergy merge:",
                "  - merged source ids: hybrid",
                "  - helper file: wizard-energy.ini",
                "  - applied to main config: yes",
            ],
        )
        self.assertEqual(wizard_cli_output._result_suggested_energy_merge_lines(_result()), [])
        self.assertEqual(
            wizard_cli_output._result_suggested_energy_merge_lines(
                _result(
                    suggested_energy_merge={
                        "applied_to_config": True,
                        "merged_source_ids": ["hybrid"],
                        "helper_file": "wizard-energy.ini",
                        "capacity_follow_up": [{"config_key": "AutoEnergySource.hybrid.UsableCapacityWh"}],
                        "merge_block": "[AutoEnergy]\nEnabled=1",
                    }
                )
            ),
            [
                "Suggested AutoEnergy merge:",
                "  - merged source ids: hybrid",
                "  - helper file: wizard-energy.ini",
                "  - applied to main config: yes",
                "  - capacity follow-up:",
                "    AutoEnergySource.hybrid.UsableCapacityWh=<set-me>",
                "  - merge block:",
                "    [AutoEnergy]",
                "    Enabled=1",
            ],
        )


class BranchCoverageNextAggregateCases(unittest.TestCase):
    def test_aggregate_helpers_cover_weight_fallbacks_and_empty_metrics(self) -> None:
        weighted_source = EnergySourceSnapshot(
            source_id="hybrid",
            role="battery",
            service_name="svc.hybrid",
            usable_capacity_wh=8000.0,
            online=True,
        )
        fallback_source = EnergySourceSnapshot(
            source_id="victron",
            role="battery",
            service_name="svc.victron",
            online=True,
        )

        self.assertEqual(
            aggregate_mod._discharge_balance_weight(weighted_source, reserve_floor_soc=50.0),
            (8000.0, None, "usable_capacity_fallback"),
        )
        self.assertEqual(
            aggregate_mod._discharge_balance_weight(fallback_source, reserve_floor_soc=50.0),
            (1.0, None, "uniform_fallback"),
        )
        eligible_sources = [{"weight": 2.5}, {"weight": 1.5}]
        self.assertEqual(aggregate_mod._discharge_balance_total_weight(eligible_sources), 4.0)
        self.assertEqual(aggregate_mod._empty_discharge_balance_metrics()["eligible_source_count"], 0)

        empty_metrics = aggregate_mod.derive_discharge_balance_metrics(
            (
                EnergySourceSnapshot(
                    source_id="grid",
                    role="grid",
                    service_name="svc.grid",
                    online=True,
                ),
            ),
            {},
        )
        self.assertEqual(empty_metrics["eligible_source_count"], 0)
        self.assertEqual(empty_metrics["active_source_count"], 0)

        definition = EnergySourceDefinition(source_id="hybrid", profile_name="dbus", role="battery", connector_type="dbus")
        self.assertEqual(
            aggregate_mod._discharge_control_source_context(weighted_source, None),
            ("", "battery", "battery"),
        )
        self.assertEqual(
            aggregate_mod._defined_discharge_control_source_context(weighted_source, definition),
            ("dbus", "dbus", "battery"),
        )
        self.assertEqual(aggregate_mod._discharge_control_reason(True, "supported"), "profile_write_supported")


class BranchCoverageNextBatteryBalanceCases(AutoDecisionControllerTestCase):
    def test_battery_balance_helpers_cover_invalid_modes_and_penalty_edges(self) -> None:
        controller, service = self._make_controller()
        service.auto_battery_discharge_balance_bias_mode = "unexpected"
        service.auto_battery_discharge_balance_coordination_support_mode = "unexpected"

        self.assertEqual(controller.battery_balance_policy._discharge_balance_bias_mode(), "always")
        self.assertEqual(controller.battery_balance_policy._discharge_balance_coordination_support_mode(), "supported_only")

        service._warning_throttled = None
        controller.battery_balance._combined_battery_warning_throttled("balance", "message %s", "ignored")

        self.assertEqual(
            battery_balance_support_mod._battery_discharge_balance_penalty_value(5.0, 0.0, 150.0),
            150.0,
        )
        self.assertEqual(
            battery_balance_support_mod._battery_discharge_balance_penalty_value(0.0, 0.0, 150.0),
            0.0,
        )
        self.assertEqual(
            battery_balance_support_mod._battery_discharge_balance_penalty_value(400.0, 500.0, 150.0),
            0.0,
        )

    def test_battery_balance_coordination_helpers_cover_remaining_advisory_paths(self) -> None:
        self.assertIsNone(
            battery_balance_support_mod._battery_discharge_balance_coordination_experimental(
                {
                    "control_ready_count": 2,
                    "supported_count": 0,
                    "experimental_count": 1,
                },
                warning_active=True,
            )
        )
        self.assertIsNone(
            battery_balance_support_mod._battery_discharge_balance_coordination_experimental(
                {
                    "control_ready_count": 2,
                    "supported_count": 2,
                    "experimental_count": 0,
                },
                warning_active=True,
            )
        )
        self.assertIsNone(
            battery_balance_support_mod._battery_discharge_balance_coordination_blocked_by_availability(
                {
                    "control_candidate_count": 2,
                    "control_ready_count": 2,
                },
                warning_active=True,
            )
        )
        self.assertEqual(
            battery_balance_support_mod._battery_discharge_balance_coordination_partial(
                {
                    "control_candidate_count": 1,
                },
                warning_active=True,
            ),
            ("partial", True, "only_some_sources_offer_a_write_path"),
        )

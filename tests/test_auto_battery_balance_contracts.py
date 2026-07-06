# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from venus_evcharger.auto.logic_gates_battery_balance import _AutoDecisionBatteryBalance
from venus_evcharger.auto.logic_gates_battery_balance_support import (
    _AutoDecisionBatteryBalanceSupport,
    _battery_discharge_balance_allowed_feasibilities,
    _battery_discharge_balance_cluster_count,
    _battery_discharge_balance_coordination_blocked_by_availability,
    _battery_discharge_balance_coordination_counts,
    _battery_discharge_balance_coordination_experimental,
    _battery_discharge_balance_coordination_not_needed,
    _battery_discharge_balance_coordination_partial,
    _battery_discharge_balance_coordination_penalty_w,
    _battery_discharge_balance_coordination_supported,
    _battery_discharge_balance_penalty_inputs_valid,
    _battery_discharge_balance_penalty_scale,
    _battery_discharge_balance_penalty_value,
    _battery_discharge_balance_zero_start_penalty,
    _discharge_balance_bias_mode_active,
)


class _BatteryBalanceHarness(_AutoDecisionBatteryBalance):
    def __init__(self) -> None:
        self.service = SimpleNamespace(
            auto_battery_scan_interval_seconds=60.0,
            auto_battery_discharge_balance_policy_enabled=True,
            _warning_throttled=MagicMock(),
        )


class _RecordingBatteryBalanceHarness(_BatteryBalanceHarness):
    def __init__(self) -> None:
        super().__init__()
        self.policy_args: tuple[object, ...] | None = None
        self.coordination_args: tuple[object, ...] | None = None
        self.coordination_policy_args: tuple[object, ...] | None = None
        self.multiplier_kwargs: dict[str, object] | None = None

    def _battery_discharge_balance_policy_context(
        self,
        cluster: object,
        *,
        expected_export_w: float | None,
        reserve_floor_soc: float | None,
    ) -> tuple[bool, float, float, str, bool, float, float]:
        self.policy_args = (cluster, expected_export_w, reserve_floor_soc)
        return True, 12.5, 7.5, "export_only", True, 4.0, 9.0

    def _battery_discharge_balance_coordination_advisory(
        self,
        cluster: object,
        *,
        warning_active: bool,
    ) -> tuple[str, bool, str]:
        self.coordination_args = (cluster, warning_active)
        return "experimental", True, "coordination_depends_on_experimental_write_paths"

    def _battery_discharge_balance_coordination_policy_context(
        self,
        cluster: object,
        *,
        feasibility: str,
    ) -> tuple[bool, str, bool, float, float]:
        self.coordination_policy_args = (cluster, feasibility)
        return True, "allow_experimental", True, 11.0, 22.0

    def _battery_penalty_multiplier(self, **kwargs: object) -> float:
        self.multiplier_kwargs = dict(kwargs)
        return 1.75


class _ActivityContextHarness(_RecordingBatteryBalanceHarness):
    def _battery_activity_inputs(self) -> tuple[dict[str, object], list[dict[str, object]], dict[str, object]]:
        return (
            {"battery_headroom_charge_w": 300.0},
            [{"source_id": "battery-a"}],
            {"battery-a": {"observed_max_charge_power_w": 4000.0}},
        )

    def _combined_battery_learning_summary(self, profiles: dict[str, object]) -> dict[str, float | int | None]:
        self.learning_profiles = profiles
        return {"profile_count": 1}

    def _combined_battery_penalties(
        self,
        cluster: dict[str, object],
        sources: list[dict[str, object]],
        profiles: dict[str, object],
        learning_summary: dict[str, float | int | None],
    ) -> tuple[float, float, float | None, float | None]:
        self.penalty_inputs = (cluster, sources, profiles, learning_summary)
        return 10.0, 20.0, 0.42, 0.66

    def _battery_learning_behavior(
        self,
        learning_summary: dict[str, float | int | None],
    ) -> dict[str, float | None]:
        self.behavior_summary = learning_summary
        return {
            "response_delay_seconds": 1.0,
            "support_bias": None,
            "day_support_bias": None,
            "night_support_bias": None,
            "import_support_bias": None,
            "export_bias": None,
            "battery_first_export_bias": None,
            "power_smoothing_ratio": None,
            "reserve_band_floor_soc": None,
            "reserve_band_ceiling_soc": None,
            "reserve_band_width_soc": None,
        }

    def _combined_battery_scaled_penalties(
        self,
        charge_penalty: float,
        discharge_penalty: float,
        behavior: object,
    ) -> tuple[float, float]:
        self.scaled_inputs = (charge_penalty, discharge_penalty, behavior)
        return 11.0, 22.0


class _BatteryBalanceSupportHarness(_AutoDecisionBatteryBalanceSupport):
    def __init__(self) -> None:
        self.service = SimpleNamespace(
            auto_battery_discharge_balance_policy_enabled=True,
            auto_battery_discharge_balance_warn_error_watts=100.0,
            auto_battery_discharge_balance_bias_start_error_watts=50.0,
            auto_battery_discharge_balance_bias_max_penalty_watts=200.0,
            auto_battery_discharge_balance_bias_reserve_margin_soc=5.0,
            auto_battery_discharge_balance_bias_mode="export_and_above_reserve_band",
            auto_battery_discharge_balance_coordination_enabled=True,
            auto_battery_discharge_balance_coordination_start_error_watts=60.0,
            auto_battery_discharge_balance_coordination_max_penalty_watts=180.0,
            auto_battery_discharge_balance_coordination_support_mode="allow_experimental",
        )

    @staticmethod
    def _non_negative_optional_float(value: object) -> float | None:
        if not isinstance(value, (int, float)):
            return None
        numeric_value = float(value)
        return numeric_value if numeric_value >= 0.0 else None


class TestAutoBatteryBalanceContracts(unittest.TestCase):
    def test_combined_battery_activity_payload_is_exact_semantic_contract(self) -> None:
        controller = _BatteryBalanceHarness()

        payload = controller._combined_battery_activity_payload(
            cluster={"battery_headroom_charge_w": 300.0},
            learning_summary={
                "profile_count": 2,
                "observed_max_charge_power_w": 5000.0,
                "observed_max_discharge_power_w": 2500.0,
            },
            behavior={
                "response_delay_seconds": 12.0,
                "support_bias": 0.3,
                "day_support_bias": 0.4,
                "night_support_bias": 0.2,
                "import_support_bias": 0.5,
                "export_bias": 0.6,
                "battery_first_export_bias": 0.7,
                "power_smoothing_ratio": 0.8,
                "reserve_band_floor_soc": 40.0,
                "reserve_band_ceiling_soc": 90.0,
                "reserve_band_width_soc": 50.0,
            },
            forecast={
                "battery_headroom_charge_w": 999.0,
                "battery_headroom_discharge_w": 450.0,
                "expected_near_term_export_w": 210.0,
                "expected_near_term_import_w": 120.0,
            },
            charge_penalty=120.0,
            discharge_penalty=80.0,
            max_charge_ratio=0.45,
            max_discharge_ratio=0.35,
            effective_penalty_w=55.0,
            bias_context={
                "warning_active": True,
                "error_w": 70.0,
                "warn_threshold_w": 40.0,
                "bias_mode": "export_only",
                "bias_gate_active": True,
                "bias_start_error_w": 30.0,
                "bias_penalty_w": 55.0,
            },
            coordination_context={
                "feasibility": "experimental",
                "advisory_active": True,
                "advisory_reason": "coordination_depends_on_experimental_write_paths",
            },
            coordination_policy_context={
                "enabled": True,
                "support_mode": "allow_experimental",
                "gate_active": True,
                "start_error_w": 25.0,
                "penalty_w": 35.0,
            },
        )

        self.assertEqual(
            payload,
            {
                "surplus_penalty_w": 255.0,
                "charge_power_w": 120.0,
                "discharge_power_w": 80.0,
                "charge_activity_ratio": 0.45,
                "discharge_activity_ratio": 0.35,
                "mode": "mixed",
                "learning_profile_count": 2,
                "observed_max_charge_power_w": 5000.0,
                "observed_max_discharge_power_w": 2500.0,
                "typical_response_delay_seconds": 12.0,
                "support_bias": 0.3,
                "day_support_bias": 0.4,
                "night_support_bias": 0.2,
                "import_support_bias": 0.5,
                "export_bias": 0.6,
                "battery_first_export_bias": 0.7,
                "power_smoothing_ratio": 0.8,
                "reserve_band_floor_soc": 40.0,
                "reserve_band_ceiling_soc": 90.0,
                "reserve_band_width_soc": 50.0,
                "battery_headroom_charge_w": 300.0,
                "battery_headroom_discharge_w": 450.0,
                "expected_near_term_export_w": 210.0,
                "expected_near_term_import_w": 120.0,
                "discharge_balance_policy_enabled": 1,
                "discharge_balance_warning_active": 1,
                "discharge_balance_warning_error_w": 70.0,
                "discharge_balance_warn_threshold_w": 40.0,
                "discharge_balance_bias_mode": "export_only",
                "discharge_balance_bias_gate_active": 1,
                "discharge_balance_bias_start_error_w": 30.0,
                "discharge_balance_bias_penalty_w": 55.0,
                "discharge_balance_coordination_policy_enabled": 1,
                "discharge_balance_coordination_support_mode": "allow_experimental",
                "discharge_balance_coordination_feasibility": "experimental",
                "discharge_balance_coordination_gate_active": 1,
                "discharge_balance_coordination_start_error_w": 25.0,
                "discharge_balance_coordination_penalty_w": 35.0,
                "discharge_balance_coordination_advisory_active": 1,
                "discharge_balance_coordination_advisory_reason": "coordination_depends_on_experimental_write_paths",
            },
        )

    def test_power_learning_bias_and_coordination_payload_edge_contracts(self) -> None:
        controller = _BatteryBalanceHarness()
        controller.service.auto_battery_discharge_balance_policy_enabled = False

        self.assertEqual(
            controller._combined_battery_power_payload(0.5, 0.5, 0.1, 0.2),
            {
                "charge_power_w": 0.5,
                "discharge_power_w": 0.5,
                "charge_activity_ratio": 0.1,
                "discharge_activity_ratio": 0.2,
                "mode": "mixed",
            },
        )
        self.assertEqual(
            controller._combined_battery_power_payload(0.0, 0.0, None, None),
            {
                "charge_power_w": None,
                "discharge_power_w": None,
                "charge_activity_ratio": None,
                "discharge_activity_ratio": None,
                "mode": "idle",
            },
        )
        self.assertEqual(
            controller._combined_battery_power_payload(0.0, 25.0, None, 0.2),
            {
                "charge_power_w": None,
                "discharge_power_w": 25.0,
                "charge_activity_ratio": None,
                "discharge_activity_ratio": 0.2,
                "mode": "discharging",
            },
        )
        self.assertEqual(
            controller._combined_battery_learning_payload(
                {
                    "profile_count": 3,
                    "observed_max_charge_power_w": -1.0,
                    "observed_max_discharge_power_w": 250.5,
                }
            ),
            {
                "learning_profile_count": 3,
                "observed_max_charge_power_w": None,
                "observed_max_discharge_power_w": 250.5,
            },
        )
        self.assertEqual(
            controller._combined_battery_learning_payload({}),
            {
                "learning_profile_count": 0,
                "observed_max_charge_power_w": None,
                "observed_max_discharge_power_w": None,
            },
        )
        self.assertEqual(
            controller._combined_battery_learning_payload({"profile_count": 0}),
            {
                "learning_profile_count": 0,
                "observed_max_charge_power_w": None,
                "observed_max_discharge_power_w": None,
            },
        )
        self.assertEqual(controller._combined_battery_effective_penalty_w("8.5", 9), 9.0)
        self.assertIsNone(controller._combined_battery_warning_error_w({"warning_active": False, "error_w": 12.0}))
        self.assertEqual(
            controller._combined_battery_bias_payload(
                {
                    "warning_active": False,
                    "error_w": 12.0,
                    "warn_threshold_w": 4.0,
                    "bias_mode": "always",
                    "bias_gate_active": False,
                    "bias_start_error_w": 2.0,
                    "bias_penalty_w": 0.0,
                }
            ),
            {
                "discharge_balance_policy_enabled": 0,
                "discharge_balance_warning_active": 0,
                "discharge_balance_warning_error_w": None,
                "discharge_balance_warn_threshold_w": 4.0,
                "discharge_balance_bias_mode": "always",
                "discharge_balance_bias_gate_active": 0,
                "discharge_balance_bias_start_error_w": 2.0,
                "discharge_balance_bias_penalty_w": 0.0,
            },
        )
        self.assertEqual(
            controller._combined_battery_coordination_payload(
                {
                    "feasibility": "partial",
                    "advisory_active": False,
                    "advisory_reason": "only_some_sources_offer_a_write_path",
                },
                {
                    "enabled": False,
                    "support_mode": "supported_only",
                    "gate_active": False,
                    "start_error_w": 10.0,
                    "penalty_w": 0.0,
                },
            ),
            {
                "discharge_balance_coordination_policy_enabled": 0,
                "discharge_balance_coordination_support_mode": "supported_only",
                "discharge_balance_coordination_feasibility": "partial",
                "discharge_balance_coordination_gate_active": 0,
                "discharge_balance_coordination_start_error_w": 10.0,
                "discharge_balance_coordination_penalty_w": 0.0,
                "discharge_balance_coordination_advisory_active": 0,
                "discharge_balance_coordination_advisory_reason": "only_some_sources_offer_a_write_path",
            },
        )

    def test_context_helpers_delegate_with_exact_inputs(self) -> None:
        controller = _RecordingBatteryBalanceHarness()
        cluster = {"battery_combined_soc": 88.0}

        bias_context = controller._combined_battery_discharge_balance_context(
            cluster,
            {"expected_near_term_export_w": 123.0},
            {"reserve_band_floor_soc": 75.0},
        )
        self.assertEqual(controller.policy_args, (cluster, 123.0, 75.0))
        self.assertEqual(
            bias_context,
            {
                "warning_active": True,
                "error_w": 12.5,
                "warn_threshold_w": 7.5,
                "bias_mode": "export_only",
                "bias_gate_active": True,
                "bias_start_error_w": 4.0,
                "bias_penalty_w": 9.0,
            },
        )

        coordination_context = controller._combined_battery_coordination_context(cluster, "truthy")
        self.assertEqual(controller.coordination_args, (cluster, True))
        self.assertEqual(
            coordination_context,
            {
                "feasibility": "experimental",
                "advisory_active": True,
                "advisory_reason": "coordination_depends_on_experimental_write_paths",
            },
        )

        policy_context = controller._combined_battery_coordination_policy_context(cluster, "experimental")
        self.assertEqual(controller.coordination_policy_args, (cluster, "experimental"))
        self.assertEqual(
            policy_context,
            {
                "enabled": True,
                "support_mode": "allow_experimental",
                "gate_active": True,
                "start_error_w": 11.0,
                "penalty_w": 22.0,
            },
        )

        behavior = {
            "response_delay_seconds": 5.0,
            "support_bias": 0.1,
            "import_support_bias": 0.2,
            "export_bias": 0.3,
        }
        self.assertEqual(controller._combined_battery_penalty_multiplier("charge", behavior), 1.75)
        self.assertEqual(
            controller.multiplier_kwargs,
            {
                "direction": "charge",
                "response_delay_seconds": 5.0,
                "support_bias": 0.1,
                "import_support_bias": 0.2,
                "export_bias": 0.3,
            },
        )

    def test_activity_context_preserves_activity_ratios_from_penalty_stage(self) -> None:
        controller = _ActivityContextHarness()

        payload = controller._combined_battery_activity_context()

        self.assertEqual(payload["charge_power_w"], 11.0)
        self.assertEqual(payload["discharge_power_w"], 22.0)
        self.assertEqual(payload["charge_activity_ratio"], 0.42)
        self.assertEqual(payload["discharge_activity_ratio"], 0.66)
        self.assertEqual(payload["surplus_penalty_w"], 55.0)
        self.assertEqual(controller.learning_profiles, {"battery-a": {"observed_max_charge_power_w": 4000.0}})
        self.assertEqual(controller.behavior_summary, {"profile_count": 1})
        self.assertEqual(controller.coordination_args, ({"battery_headroom_charge_w": 300.0}, True))
        self.assertEqual(controller.coordination_policy_args, ({"battery_headroom_charge_w": 300.0}, "experimental"))

    def test_warning_helpers_are_exact_and_throttled(self) -> None:
        controller = _BatteryBalanceHarness()
        cluster = {
            "battery_discharge_balance_active_source_count": 2,
            "battery_discharge_balance_eligible_source_count": 3,
        }

        controller._emit_combined_battery_discharge_warning(cluster, {"warning_active": False})
        controller.service._warning_throttled.assert_not_called()

        controller._emit_combined_battery_discharge_warning(
            cluster,
            {"warning_active": True, "error_w": 12.34},
        )
        controller.service._warning_throttled.assert_called_once_with(
            "battery-discharge-balance-warning",
            60.0,
            "Auto mode observed battery discharge imbalance: error=%s W active=%s eligible=%s",
            12.3,
            2,
            3,
        )

        controller.service._warning_throttled.reset_mock()
        controller._emit_combined_battery_discharge_warning({}, {"warning_active": True, "error_w": 0.04})
        controller.service._warning_throttled.assert_called_once_with(
            "battery-discharge-balance-warning",
            60.0,
            "Auto mode observed battery discharge imbalance: error=%s W active=%s eligible=%s",
            0.0,
            0,
            0,
        )

        controller.service._warning_throttled.reset_mock()
        controller._emit_combined_battery_coordination_warning({"advisory_active": False})
        controller.service._warning_throttled.assert_not_called()

        controller._emit_combined_battery_coordination_warning(
            {
                "advisory_active": True,
                "advisory_reason": "candidate_sources_not_ready",
            }
        )
        controller.service._warning_throttled.assert_called_once_with(
            "battery-discharge-balance-coordination-advisory",
            60.0,
            "Auto mode observed ESS imbalance but coordination feasibility is limited: %s",
            "candidate_sources_not_ready",
        )

        controller.service._warning_throttled.reset_mock()
        controller.service.auto_battery_scan_interval_seconds = 5.0
        controller._combined_battery_warning_throttled("custom", "value=%s", "x")
        controller.service._warning_throttled.assert_called_once_with("custom", 30.0, "value=%s", "x")

        controller.service._warning_throttled.reset_mock()
        controller.service.auto_battery_scan_interval_seconds = 0.0
        controller._combined_battery_warning_throttled("default", "message")
        controller.service._warning_throttled.assert_called_once_with("default", 60.0, "message")

        delattr(controller.service, "auto_battery_scan_interval_seconds")
        controller.service._warning_throttled.reset_mock()
        controller._combined_battery_warning_throttled("missing", "message")
        controller.service._warning_throttled.assert_called_once_with("missing", 60.0, "message")

        controller.service._warning_throttled = None
        controller._combined_battery_warning_throttled("ignored", "message")


class TestAutoBatteryBalanceSupportContracts(unittest.TestCase):
    def test_policy_context_counts_thresholds_and_modes_are_explicit(self) -> None:
        controller = _BatteryBalanceSupportHarness()
        cluster = {
            "battery_discharge_balance_error_w": 150.0,
            "battery_discharge_balance_eligible_source_count": 2,
            "battery_discharge_balance_active_source_count": 1,
            "battery_combined_soc": 80.0,
        }

        self.assertEqual(controller._battery_discharge_balance_policy_counts(cluster), (150.0, 2, 1))
        self.assertEqual(controller._battery_discharge_balance_policy_counts({}), (0.0, 0, 0))
        self.assertEqual(
            controller._battery_discharge_balance_policy_counts(
                {
                    "battery_discharge_balance_eligible_source_count": 0,
                    "battery_discharge_balance_active_source_count": 0,
                }
            ),
            (0.0, 0, 0),
        )
        self.assertEqual(controller._battery_discharge_balance_policy_thresholds(), (100.0, 50.0, 200.0))
        self.assertEqual(controller._battery_discharge_balance_reserve_margin_soc(), 5.0)
        self.assertEqual(controller._discharge_balance_bias_mode(), "export_and_above_reserve_band")
        self.assertEqual(
            controller._battery_discharge_balance_policy_context(
                cluster,
                expected_export_w=1.0,
                reserve_floor_soc=70.0,
            ),
            (True, 150.0, 100.0, "export_and_above_reserve_band", True, 50.0, 200.0),
        )

        controller.service.auto_battery_discharge_balance_policy_enabled = False
        self.assertEqual(
            controller._battery_discharge_balance_policy_context(
                cluster,
                expected_export_w=1.0,
                reserve_floor_soc=70.0,
            ),
            (False, 0.0, 0.0, "always", False, 0.0, 0.0),
        )

        controller.service.auto_battery_discharge_balance_policy_enabled = True
        controller.service.auto_battery_discharge_balance_warn_error_watts = -1.0
        controller.service.auto_battery_discharge_balance_bias_start_error_watts = None
        controller.service.auto_battery_discharge_balance_bias_max_penalty_watts = -2.0
        controller.service.auto_battery_discharge_balance_bias_reserve_margin_soc = -3.0
        controller.service.auto_battery_discharge_balance_bias_mode = "bad-mode"
        self.assertEqual(controller._battery_discharge_balance_policy_thresholds(), (0.0, 0.0, 0.0))
        self.assertEqual(controller._battery_discharge_balance_reserve_margin_soc(), 0.0)
        self.assertEqual(controller._discharge_balance_bias_mode(), "always")
        controller.service.auto_battery_discharge_balance_bias_mode = ""
        self.assertEqual(controller._discharge_balance_bias_mode(), "always")

    def test_policy_and_coordination_defaults_are_safe_when_service_config_is_absent(self) -> None:
        controller = _BatteryBalanceSupportHarness()
        controller.service = SimpleNamespace()
        cluster = {
            "battery_discharge_balance_error_w": 150.0,
            "battery_discharge_balance_eligible_source_count": 2,
            "battery_discharge_balance_active_source_count": 1,
            "battery_discharge_balance_control_ready_count": 2,
            "battery_combined_soc": 80.0,
        }

        self.assertFalse(controller._battery_discharge_balance_policy_enabled())
        self.assertEqual(controller._battery_discharge_balance_policy_counts(cluster), (150.0, 2, 1))
        self.assertEqual(controller._battery_discharge_balance_policy_thresholds(), (0.0, 0.0, 0.0))
        self.assertEqual(controller._battery_discharge_balance_reserve_margin_soc(), 0.0)
        self.assertEqual(controller._discharge_balance_bias_mode(), "always")
        self.assertEqual(
            controller._battery_discharge_balance_policy_context(
                cluster,
                expected_export_w=1.0,
                reserve_floor_soc=70.0,
            ),
            (False, 0.0, 0.0, "always", False, 0.0, 0.0),
        )
        self.assertFalse(controller._battery_discharge_balance_coordination_enabled())
        self.assertEqual(controller._battery_discharge_balance_coordination_counts(cluster), (150.0, 2))
        self.assertEqual(controller._battery_discharge_balance_coordination_thresholds(), (0.0, 0.0))
        self.assertEqual(controller._discharge_balance_coordination_support_mode(), "supported_only")
        self.assertEqual(
            controller._battery_discharge_balance_coordination_policy_context(
                cluster,
                feasibility="supported",
            ),
            (False, "supported_only", False, 0.0, 0.0),
        )

    def test_policy_formula_boundaries_are_contracts(self) -> None:
        self.assertFalse(_BatteryBalanceSupportHarness._battery_discharge_balance_warning_active(1, 1, 100.0, 100.0))
        self.assertFalse(_BatteryBalanceSupportHarness._battery_discharge_balance_warning_active(2, 0, 100.0, 100.0))
        self.assertFalse(_BatteryBalanceSupportHarness._battery_discharge_balance_warning_active(2, 1, 0.0, 0.0))
        self.assertFalse(_BatteryBalanceSupportHarness._battery_discharge_balance_warning_active(2, 1, 99.9, 100.0))
        self.assertTrue(_BatteryBalanceSupportHarness._battery_discharge_balance_warning_active(2, 1, 0.5, 0.5))
        self.assertTrue(_BatteryBalanceSupportHarness._battery_discharge_balance_warning_active(2, 1, 100.0, 100.0))

        self.assertFalse(_battery_discharge_balance_penalty_inputs_valid(1, 1, True, 1.0))
        self.assertFalse(_battery_discharge_balance_penalty_inputs_valid(2, 0, True, 1.0))
        self.assertFalse(_battery_discharge_balance_penalty_inputs_valid(2, 1, False, 1.0))
        self.assertFalse(_battery_discharge_balance_penalty_inputs_valid(2, 1, True, 0.0))
        self.assertTrue(_battery_discharge_balance_penalty_inputs_valid(2, 1, True, 1.0))

        self.assertEqual(_battery_discharge_balance_penalty_scale(75.0, 50.0), 0.5)
        self.assertEqual(_battery_discharge_balance_penalty_scale(500.0, 50.0), 1.0)
        self.assertEqual(_battery_discharge_balance_penalty_scale(0.5, 1.0), 0.0)
        self.assertEqual(_battery_discharge_balance_penalty_scale(1.5, 1.0), 0.5)
        self.assertEqual(_battery_discharge_balance_penalty_scale(2.0, 1.0), 1.0)
        self.assertEqual(_battery_discharge_balance_zero_start_penalty(0.0, 200.0), 0.0)
        self.assertEqual(_battery_discharge_balance_zero_start_penalty(0.1, 200.0), 200.0)
        self.assertEqual(_battery_discharge_balance_penalty_value(0.1, 0.0, 200.0), 200.0)
        self.assertEqual(_battery_discharge_balance_penalty_value(1.0, 1.0, 200.0), 0.0)
        self.assertAlmostEqual(_battery_discharge_balance_penalty_value(1.1, 1.0, 200.0), 20.0)
        self.assertEqual(_battery_discharge_balance_penalty_value(50.0, 50.0, 200.0), 0.0)
        self.assertEqual(_battery_discharge_balance_penalty_value(75.0, 50.0, 200.0), 100.0)
        self.assertEqual(_battery_discharge_balance_coordination_penalty_w(
            gate_active=False,
            error_w=75.0,
            start_error_w=50.0,
            max_penalty_w=200.0,
        ), 0.0)
        self.assertEqual(_battery_discharge_balance_coordination_penalty_w(
            gate_active=True,
            error_w=75.0,
            start_error_w=50.0,
            max_penalty_w=0.0,
        ), 0.0)
        self.assertEqual(_battery_discharge_balance_coordination_penalty_w(
            gate_active=True,
            error_w=75.0,
            start_error_w=50.0,
            max_penalty_w=-10.0,
        ), 0.0)
        self.assertEqual(_battery_discharge_balance_coordination_penalty_w(
            gate_active=True,
            error_w=75.0,
            start_error_w=50.0,
            max_penalty_w=1.0,
        ), 0.5)
        self.assertEqual(_battery_discharge_balance_coordination_penalty_w(
            gate_active=True,
            error_w=75.0,
            start_error_w=50.0,
            max_penalty_w=200.0,
        ), 100.0)
        self.assertEqual(_BatteryBalanceSupportHarness._battery_discharge_balance_penalty_w(
            eligible_source_count=2,
            active_source_count=1,
            gate_active=True,
            error_w=75.0,
            start_error_w=50.0,
            max_penalty_w=200.0,
        ), 100.0)
        self.assertEqual(_BatteryBalanceSupportHarness._battery_discharge_balance_penalty_w(
            eligible_source_count=1,
            active_source_count=1,
            gate_active=True,
            error_w=75.0,
            start_error_w=50.0,
            max_penalty_w=200.0,
        ), 0.0)

    def test_coordination_counts_feasibility_and_policy_context_are_contracts(self) -> None:
        controller = _BatteryBalanceSupportHarness()
        cluster = {
            "battery_discharge_balance_error_w": 150.0,
            "battery_discharge_balance_eligible_source_count": 2,
            "battery_discharge_balance_control_candidate_count": 2,
            "battery_discharge_balance_control_ready_count": 2,
            "battery_discharge_balance_supported_control_source_count": 1,
            "battery_discharge_balance_experimental_control_source_count": 1,
        }

        self.assertEqual(_battery_discharge_balance_cluster_count(cluster, "missing"), 0)
        self.assertEqual(_battery_discharge_balance_cluster_count({"count": 3}, "count"), 3)
        self.assertEqual(_battery_discharge_balance_cluster_count({"count": 0}, "count"), 0)
        self.assertEqual(
            _battery_discharge_balance_coordination_counts(cluster),
            {
                "eligible_source_count": 2,
                "control_candidate_count": 2,
                "control_ready_count": 2,
                "supported_count": 1,
                "experimental_count": 1,
            },
        )
        self.assertEqual(controller._battery_discharge_balance_coordination_counts(cluster), (150.0, 2))
        self.assertEqual(
            controller._battery_discharge_balance_coordination_counts(
                {"battery_discharge_balance_control_ready_count": 2}
            ),
            (0.0, 2),
        )
        self.assertEqual(controller._battery_discharge_balance_coordination_thresholds(), (60.0, 180.0))
        self.assertEqual(controller._discharge_balance_coordination_support_mode(), "allow_experimental")
        self.assertEqual(
            controller._battery_discharge_balance_coordination_policy_context(cluster, feasibility="experimental"),
            (True, "allow_experimental", True, 60.0, 180.0),
        )

        controller.service.auto_battery_discharge_balance_coordination_enabled = False
        self.assertEqual(
            controller._battery_discharge_balance_coordination_policy_context(cluster, feasibility="experimental"),
            (False, "allow_experimental", False, 0.0, 0.0),
        )
        controller.service.auto_battery_discharge_balance_coordination_enabled = True
        controller.service.auto_battery_discharge_balance_coordination_start_error_watts = -1.0
        controller.service.auto_battery_discharge_balance_coordination_max_penalty_watts = None
        controller.service.auto_battery_discharge_balance_coordination_support_mode = "bad-mode"
        self.assertEqual(controller._battery_discharge_balance_coordination_thresholds(), (0.0, 0.0))
        self.assertEqual(controller._discharge_balance_coordination_support_mode(), "supported_only")
        controller.service.auto_battery_discharge_balance_coordination_support_mode = ""
        self.assertEqual(controller._discharge_balance_coordination_support_mode(), "supported_only")
        self.assertFalse(controller._battery_discharge_balance_coordination_gate_active(
            support_mode="supported_only",
            feasibility="experimental",
            control_ready_count=2,
        ))
        self.assertTrue(controller._battery_discharge_balance_coordination_gate_active(
            support_mode="allow_experimental",
            feasibility="experimental",
            control_ready_count=2,
        ))

    def test_coordination_advisory_evaluators_are_ordered_contracts(self) -> None:
        self.assertEqual(
            _battery_discharge_balance_coordination_not_needed({"eligible_source_count": 1}, True),
            ("not_needed", False, "single_source_or_insufficient_sources"),
        )
        self.assertIsNone(_battery_discharge_balance_coordination_not_needed({"eligible_source_count": 2}, True))
        self.assertEqual(
            _battery_discharge_balance_coordination_supported(
                {"supported_count": 2, "control_ready_count": 2},
                True,
            ),
            ("supported", False, "multiple_supported_control_sources_ready"),
        )
        self.assertIsNone(_battery_discharge_balance_coordination_supported({"supported_count": 1, "control_ready_count": 2}, True))
        self.assertEqual(
            _battery_discharge_balance_coordination_experimental(
                {"control_ready_count": 2, "supported_count": 1, "experimental_count": 1},
                True,
            ),
            ("experimental", True, "coordination_depends_on_experimental_write_paths"),
        )
        self.assertIsNone(_battery_discharge_balance_coordination_experimental({"control_ready_count": 1}, True))
        self.assertIsNone(
            _battery_discharge_balance_coordination_experimental(
                {"control_ready_count": 2, "supported_count": 2, "experimental_count": 0},
                True,
            )
        )
        self.assertEqual(
            _battery_discharge_balance_coordination_blocked_by_availability(
                {"control_candidate_count": 2, "control_ready_count": 1},
                True,
            ),
            ("blocked_by_source_availability", True, "candidate_sources_not_ready"),
        )
        self.assertIsNone(_battery_discharge_balance_coordination_blocked_by_availability(
            {"control_candidate_count": 2, "control_ready_count": 2},
            True,
        ))
        self.assertEqual(
            _battery_discharge_balance_coordination_partial({"control_candidate_count": 1}, False),
            ("partial", False, "only_some_sources_offer_a_write_path"),
        )
        self.assertEqual(
            _battery_discharge_balance_coordination_partial({"control_candidate_count": 1}, True),
            ("partial", True, "only_some_sources_offer_a_write_path"),
        )
        self.assertIsNone(_battery_discharge_balance_coordination_partial({"control_candidate_count": 0}, True))
        self.assertEqual(_battery_discharge_balance_allowed_feasibilities("allow_experimental"), {"supported", "experimental"})
        self.assertEqual(_battery_discharge_balance_allowed_feasibilities("supported_only"), {"supported"})
        self.assertEqual(
            _BatteryBalanceSupportHarness._battery_discharge_balance_coordination_advisory(
                {
                    "battery_discharge_balance_eligible_source_count": 2,
                    "battery_discharge_balance_control_candidate_count": 0,
                    "battery_discharge_balance_control_ready_count": 0,
                    "battery_discharge_balance_supported_control_source_count": 0,
                    "battery_discharge_balance_experimental_control_source_count": 0,
                },
                warning_active=True,
            ),
            ("observe_only", True, "no_configured_source_offers_a_write_path"),
        )
        self.assertEqual(
            _BatteryBalanceSupportHarness._battery_discharge_balance_coordination_advisory(
                {
                    "battery_discharge_balance_eligible_source_count": 2,
                    "battery_discharge_balance_control_candidate_count": 2,
                    "battery_discharge_balance_control_ready_count": 2,
                    "battery_discharge_balance_supported_control_source_count": 1,
                    "battery_discharge_balance_experimental_control_source_count": 1,
                },
                warning_active=True,
            ),
            ("experimental", True, "coordination_depends_on_experimental_write_paths"),
        )

    def test_bias_gates_export_and_reserve_contracts(self) -> None:
        controller = _BatteryBalanceSupportHarness()

        self.assertFalse(controller._discharge_balance_export_active(None))
        self.assertFalse(controller._discharge_balance_export_active(0.0))
        self.assertTrue(controller._discharge_balance_export_active(0.1))
        self.assertFalse(controller._discharge_balance_reserve_gate_active({}, 50.0, 5.0))
        self.assertFalse(controller._discharge_balance_reserve_gate_active({"battery_combined_soc": 54.9}, 50.0, 5.0))
        self.assertTrue(controller._discharge_balance_reserve_gate_active({"battery_combined_soc": 55.0}, 50.0, 5.0))

        for mode, expected in (
            ("always", True),
            ("export_only", True),
            ("above_reserve_band", True),
            ("export_and_above_reserve_band", True),
            ("unknown", True),
        ):
            self.assertIs(_discharge_balance_bias_mode_active(mode, True, True), expected)
        self.assertTrue(_discharge_balance_bias_mode_active("always", False, False))
        self.assertFalse(_discharge_balance_bias_mode_active("export_only", False, True))
        self.assertTrue(_discharge_balance_bias_mode_active("export_only", True, False))
        self.assertFalse(_discharge_balance_bias_mode_active("above_reserve_band", True, False))
        self.assertTrue(_discharge_balance_bias_mode_active("above_reserve_band", False, True))
        self.assertFalse(_discharge_balance_bias_mode_active("export_and_above_reserve_band", False, True))
        self.assertFalse(_discharge_balance_bias_mode_active("export_and_above_reserve_band", True, False))
        self.assertTrue(_discharge_balance_bias_mode_active("export_and_above_reserve_band", True, True))
        self.assertTrue(controller._discharge_balance_bias_gate_active(
            bias_mode="export_and_above_reserve_band",
            cluster={"battery_combined_soc": 80.0},
            expected_export_w=1.0,
            reserve_floor_soc=70.0,
            reserve_margin_soc=5.0,
        ))
        self.assertFalse(controller._discharge_balance_bias_gate_active(
            bias_mode="export_and_above_reserve_band",
            cluster={"battery_combined_soc": 74.9},
            expected_export_w=1.0,
            reserve_floor_soc=70.0,
            reserve_margin_soc=5.0,
        ))


if __name__ == "__main__":
    unittest.main()

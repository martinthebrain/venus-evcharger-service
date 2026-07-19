# SPDX-License-Identifier: GPL-3.0-or-later
"""Behavioral contracts for operational and Victron service state."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from venus_evcharger.service import control_state_operational as operational_module
from venus_evcharger.service import control_state_operational_support as operational_support
from venus_evcharger.service import control_state_victron as victron_module
from venus_evcharger.service.control_state_operational import ControlStateOperational
from venus_evcharger.service.control_state_victron import ControlStateVictron


class TestControlStateOperationalContracts(unittest.TestCase):
    def test_operational_pipeline_passes_normalized_inputs_exactly(self) -> None:
        owner = SimpleNamespace(
            _last_auto_state="charging",
            _last_auto_state_code=2,
            _last_auto_metrics={"surplus": 1800.0},
            _last_health_reason="shelly-offline",
            _software_update_state="available",
            _software_update_available=True,
            _software_update_no_update_active=False,
        )
        worker_snapshot = {"battery_combined_soc": 72.5}
        learning_summary = {"profile_count": 1}
        operational_state = {"mode": 2}
        with (
            patch.object(operational_module, "_worker_snapshot", return_value=worker_snapshot) as worker,
            patch.object(
                operational_module,
                "_worker_learning_summary",
                return_value=learning_summary,
            ) as learning,
            patch.object(operational_module, "evse_fault_reason", return_value="fault-input") as fault_reason,
            patch.object(
                operational_module,
                "normalized_fault_state",
                return_value=("fault-normalized", 1),
            ) as normalize_fault,
            patch.object(
                operational_module,
                "normalized_software_update_state_fields",
                return_value=("available", 2, 1, 0),
            ) as normalize_update,
            patch.object(
                operational_module,
                "_state_api_operational_state",
                return_value=operational_state,
            ) as build_state,
        ):
            payload = operational_module._state_api_operational_payload(owner)

        self.assertEqual(
            payload,
            {
                "ok": True,
                "api_version": "v1",
                "kind": "operational",
                "state": operational_state,
            },
        )
        worker.assert_called_once_with(owner)
        learning.assert_called_once_with(worker_snapshot)
        fault_reason.assert_called_once_with("shelly-offline")
        normalize_fault.assert_called_once_with("fault-input")
        normalize_update.assert_called_once_with("available", True, False)
        build_state.assert_called_once_with(
            owner,
            worker_snapshot,
            {"surplus": 1800.0},
            "charging",
            2,
            "fault-normalized",
            1,
            ("available", 2, 1, 0),
            learning_summary,
        )

    def test_operational_pipeline_preserves_missing_state_code_and_active_no_update_defaults(self) -> None:
        owner = SimpleNamespace(_software_update_no_update_active=True)
        with (
            patch.object(operational_module, "_worker_snapshot", return_value={}),
            patch.object(operational_module, "_worker_learning_summary", return_value={}),
            patch.object(operational_module, "evse_fault_reason", return_value=""),
            patch.object(operational_module, "normalized_fault_state", return_value=("", 0)),
            patch.object(
                operational_module,
                "normalized_software_update_state_fields",
                return_value=("idle", 0, 0, 1),
            ) as normalize_update,
            patch.object(
                operational_module,
                "_state_api_operational_state",
                return_value={},
            ) as build_state,
        ):
            payload = operational_module._state_api_operational_payload(owner)

        self.assertEqual(payload["state"], {})
        normalize_update.assert_called_once_with("idle", False, True)
        build_state.assert_called_once_with(owner, {}, {}, "idle", 0, "", 0, ("idle", 0, 0, 1), {})

    def test_operational_state_merges_each_provider_exactly(self) -> None:
        owner = object()
        worker_snapshot = {"battery_combined_soc": 72.5}
        metrics = {"surplus": 1800.0}
        learning_summary = {"profile_count": 1}
        core = {"mode": 2}
        auto = {"auto_decision": {"reason": "ready"}}
        energy = {"combined_battery_soc": 72.5}
        balance = {"battery_discharge_balance_mode": "balanced"}
        bias = {"battery_discharge_balance_victron_bias_active": True}
        with (
            patch.object(operational_module, "_state_api_operational_core_state", return_value=core) as core_builder,
            patch.object(
                operational_module,
                "_state_api_operational_auto_decision_state",
                return_value=auto,
            ) as auto_builder,
            patch.object(
                operational_module,
                "_state_api_operational_energy_state",
                return_value=energy,
            ) as energy_builder,
            patch.object(
                operational_module,
                "_state_api_operational_balance_state",
                return_value=balance,
            ) as balance_builder,
            patch.object(
                operational_module,
                "_state_api_operational_victron_bias_state",
                return_value=bias,
            ) as bias_builder,
        ):
            state = operational_module._state_api_operational_state(
                owner,
                worker_snapshot,
                metrics,
                "charging",
                2,
                "fault",
                1,
                ("available", 3, 1, 0),
                learning_summary,
            )

        self.assertEqual(state, {**core, **auto, **energy, **balance, **bias})
        core_builder.assert_called_once_with(owner, "charging", 2, "fault", 1, "available", 3, 1, 0)
        auto_builder.assert_called_once_with(owner, metrics, "charging", 2)
        energy_builder.assert_called_once_with(worker_snapshot, learning_summary)
        balance_builder.assert_called_once_with(owner, worker_snapshot, metrics)
        bias_builder.assert_called_once_with(metrics)

    def test_operational_core_state_preserves_configured_values_and_defaults(self) -> None:
        owner = SimpleNamespace(
            virtual_mode=2,
            virtual_enable=1,
            virtual_startstop=1,
            virtual_autostart=1,
            active_phase_selection="P1_P2_P3",
            requested_phase_selection="P1_P2",
            _runtime_overrides_active=True,
            runtime_overrides_path="/run/overrides.json",
        )
        backend_mode = MagicMock(return_value="split")
        backend_type = MagicMock(side_effect=lambda _owner, role, _default: f"{role}-backend")
        with (
            patch.object(operational_module, "backend_mode_for_service", backend_mode),
            patch.object(operational_module, "backend_type_for_service", backend_type),
        ):
            configured = operational_module._state_api_operational_core_state(
                owner,
                "charging",
                2,
                "fault",
                1,
                "available",
                3,
                1,
                0,
            )
        self.assertEqual(
            configured,
            {
                "mode": 2,
                "enable": 1,
                "startstop": 1,
                "autostart": 1,
                "active_phase_selection": "P1_P2_P3",
                "requested_phase_selection": "P1_P2",
                "backend_mode": "split",
                "meter_backend": "meter-backend",
                "switch_backend": "switch-backend",
                "charger_backend": "charger-backend",
                "auto_state": "charging",
                "auto_state_code": 2,
                "fault_active": 1,
                "fault_reason": "fault",
                "software_update_state": "available",
                "software_update_state_code": 3,
                "software_update_available": 1,
                "software_update_no_update_active": 0,
                "runtime_overrides_active": True,
                "runtime_overrides_path": "/run/overrides.json",
            },
        )
        backend_mode.assert_called_once_with(owner, "combined")
        self.assertEqual(
            backend_type.call_args_list,
            [
                call(owner, "meter", "na"),
                call(owner, "switch", "na"),
                call(owner, "charger", "na"),
            ],
        )

        with (
            patch.object(operational_module, "backend_mode_for_service", return_value="combined"),
            patch.object(operational_module, "backend_type_for_service", return_value="na"),
        ):
            defaults = operational_module._state_api_operational_core_state(
                SimpleNamespace(),
                "idle",
                0,
                "",
                0,
                "idle",
                0,
                0,
                0,
            )
        self.assertEqual(
            defaults,
            {
                "mode": 0,
                "enable": 0,
                "startstop": 0,
                "autostart": 0,
                "active_phase_selection": "P1",
                "requested_phase_selection": "P1",
                "backend_mode": "combined",
                "meter_backend": "na",
                "switch_backend": "na",
                "charger_backend": "na",
                "auto_state": "idle",
                "auto_state_code": 0,
                "fault_active": 0,
                "fault_reason": "",
                "software_update_state": "idle",
                "software_update_state_code": 0,
                "software_update_available": 0,
                "software_update_no_update_active": 0,
                "runtime_overrides_active": False,
                "runtime_overrides_path": "",
            },
        )

    def test_operational_scalar_source_defaults_are_exact(self) -> None:
        self.assertEqual(operational_module._last_health_reason(SimpleNamespace()), "")
        self.assertEqual(operational_module._last_health_reason(SimpleNamespace(_last_health_reason=None)), "")
        self.assertEqual(operational_module._last_health_reason(SimpleNamespace(_last_health_reason="ok")), "ok")
        self.assertEqual(operational_module._software_update_state(SimpleNamespace()), "idle")
        self.assertEqual(
            operational_module._software_update_state(SimpleNamespace(_software_update_state="available")),
            "available",
        )

    def test_operational_payload_composes_runtime_worker_learning_and_balance_state(self) -> None:
        worker_snapshot = {
            "battery_combined_soc": 72.5,
            "battery_source_count": 2,
            "battery_online_source_count": 1,
            "battery_discharge_balance_mode": "balanced",
            "battery_discharge_balance_eligible_source_count": 2,
            "battery_learning_profiles": {},
        }
        owner = SimpleNamespace(
            _get_worker_snapshot=lambda: worker_snapshot,
            _last_auto_state="charging",
            _last_auto_state_code=2,
            _last_health_reason="ok",
            _last_auto_metrics={
                "relay_intent": True,
                "surplus": 1800.0,
                "grid": -100.0,
                "soc": 72.5,
                "start_threshold": 1500.0,
                "stop_threshold": 800.0,
                "profile": "normal",
                "threshold_mode": "dynamic",
                "battery_discharge_balance_warning_active": 1,
                "battery_discharge_balance_victron_bias_active": 1,
            },
            _software_update_state="available",
            _software_update_available=True,
            _software_update_no_update_active=False,
            virtual_mode=1,
            virtual_enable=1,
            virtual_startstop=1,
            virtual_autostart=1,
            active_phase_selection="P1",
            requested_phase_selection="P1_P2_P3",
            auto_battery_discharge_balance_policy_enabled=True,
        )
        with (
            patch.object(operational_module, "backend_mode_for_service", return_value="split"),
            patch.object(
                operational_module, "backend_type_for_service", side_effect=lambda _owner, role, _default: role
            ),
        ):
            payload = ControlStateOperational(owner).payload()
        state = payload["state"]
        self.assertEqual(state["auto_state"], "charging")
        self.assertEqual(state["auto_decision"]["relay_intent"], 1)
        self.assertEqual(state["combined_battery_soc"], 72.5)
        self.assertEqual(state["combined_battery_source_count"], 2)
        self.assertTrue(state["battery_discharge_balance_policy_enabled"])
        self.assertTrue(state["battery_discharge_balance_warning_active"])
        self.assertTrue(state["battery_discharge_balance_victron_bias_active"])

    def test_operational_defaults_and_support_helpers_reject_malformed_runtime_values(self) -> None:
        with (
            patch.object(operational_module, "backend_mode_for_service", return_value="combined"),
            patch.object(operational_module, "backend_type_for_service", return_value="na"),
        ):
            state = ControlStateOperational(SimpleNamespace()).payload()["state"]
        self.assertEqual(state["mode"], 0)
        self.assertEqual(state["auto_state"], "idle")
        self.assertEqual(state["auto_decision"]["relay_intent"], -1)

        self.assertEqual(operational_module._last_health_reason(SimpleNamespace(_last_health_reason=None)), "")
        self.assertEqual(operational_module._last_auto_metrics(SimpleNamespace(_last_auto_metrics=[])), {})
        self.assertFalse(operational_module._owner_bool_or_false(SimpleNamespace(), "missing"))
        self.assertTrue(operational_module._owner_bool_or_false(SimpleNamespace(flag=1), "flag"))
        self.assertEqual(operational_support._worker_snapshot(SimpleNamespace(_get_worker_snapshot=[])), {})
        self.assertEqual(operational_support._worker_snapshot(SimpleNamespace(_get_worker_snapshot=lambda: [])), {})
        self.assertEqual(
            operational_support._worker_learning_summary({"battery_learning_profiles": []})["profile_count"], 0
        )
        self.assertEqual(operational_support._relay_intent_value(None), -1)
        self.assertEqual(operational_support._relay_intent_value(False), 0)
        self.assertEqual(operational_support._optional_metric_text(None), "")
        self.assertEqual(operational_support._optional_metric_text(" ready "), "ready")
        self.assertEqual(operational_support._owner_health_reason(SimpleNamespace()), "na")
        self.assertEqual(operational_support._owner_health_reason(SimpleNamespace(_last_health_reason="")), "na")
        self.assertEqual(operational_support._owner_health_reason(SimpleNamespace(_last_health_reason="ok")), "ok")
        self.assertTrue(operational_support._owner_bool_attr(SimpleNamespace(), "missing", True))
        self.assertFalse(operational_support._owner_bool_attr(SimpleNamespace(flag=0), "flag", True))
        self.assertEqual(operational_support._mapping_value_or_default({}, "missing", 4), 4)
        self.assertEqual(operational_support._mapping_value_or_default({"value": 5}, "value", 4), 5)

    def test_worker_learning_summary_uses_only_the_profiles_mapping(self) -> None:
        profiles = {"battery-main": {"sample_count": 7}}
        summary = {"profile_count": 1, "observed_max_charge_power_w": 3210.0}
        with patch.object(
            operational_support,
            "summarize_energy_learning_profiles",
            return_value=summary,
        ) as summarize:
            result = operational_support._worker_learning_summary(
                {"battery_learning_profiles": profiles, "unrelated": "ignored"}
            )

        self.assertIs(result, summary)
        summarize.assert_called_once_with(profiles)

    def test_operational_energy_state_maps_every_source_field_exactly(self) -> None:
        worker = {
            "battery_combined_soc": 1.0,
            "battery_source_count": 2,
            "battery_online_source_count": 3,
            "battery_combined_charge_power_w": 4.0,
            "battery_combined_discharge_power_w": 5.0,
            "battery_combined_net_power_w": 6.0,
            "battery_combined_ac_power_w": 7.0,
            "battery_combined_pv_input_power_w": 8.0,
            "battery_combined_grid_interaction_w": 9.0,
            "battery_headroom_charge_w": 10.0,
            "battery_headroom_discharge_w": 11.0,
            "expected_near_term_export_w": 12.0,
            "expected_near_term_import_w": 13.0,
            "battery_average_confidence": 14.0,
            "battery_battery_source_count": 15,
            "battery_hybrid_inverter_source_count": 16,
            "battery_inverter_source_count": 17,
        }
        learning = {
            "profile_count": 18,
            "observed_max_charge_power_w": 19.0,
            "observed_max_discharge_power_w": 20.0,
            "observed_max_ac_power_w": 21.0,
            "observed_max_pv_input_power_w": 22.0,
            "observed_max_grid_import_w": 23.0,
            "observed_max_grid_export_w": 24.0,
            "average_active_charge_power_w": 25.0,
            "average_active_discharge_power_w": 26.0,
            "average_active_power_delta_w": 27.0,
            "power_smoothing_ratio": 28.0,
            "typical_response_delay_seconds": 29.0,
            "support_bias": 30.0,
            "day_support_bias": 31.0,
            "night_support_bias": 32.0,
            "import_support_bias": 33.0,
            "export_bias": 34.0,
            "battery_first_export_bias": 35.0,
            "reserve_band_floor_soc": 36.0,
            "reserve_band_ceiling_soc": 37.0,
            "reserve_band_width_soc": 38.0,
            "direction_change_count": 39,
        }

        self.assertEqual(
            operational_support._state_api_operational_energy_state(worker, learning),
            {
                "combined_battery_soc": 1.0,
                "combined_battery_source_count": 2,
                "combined_battery_online_source_count": 3,
                "combined_battery_charge_power_w": 4.0,
                "combined_battery_discharge_power_w": 5.0,
                "combined_battery_net_power_w": 6.0,
                "combined_battery_ac_power_w": 7.0,
                "combined_battery_pv_input_power_w": 8.0,
                "combined_battery_grid_interaction_w": 9.0,
                "combined_battery_headroom_charge_w": 10.0,
                "combined_battery_headroom_discharge_w": 11.0,
                "expected_near_term_export_w": 12.0,
                "expected_near_term_import_w": 13.0,
                "combined_battery_average_confidence": 14.0,
                "combined_battery_battery_source_count": 15,
                "combined_battery_hybrid_inverter_source_count": 16,
                "combined_battery_inverter_source_count": 17,
                "combined_battery_learning_profile_count": 18,
                "combined_battery_observed_max_charge_power_w": 19.0,
                "combined_battery_observed_max_discharge_power_w": 20.0,
                "combined_battery_observed_max_ac_power_w": 21.0,
                "combined_battery_observed_max_pv_input_power_w": 22.0,
                "combined_battery_observed_max_grid_import_w": 23.0,
                "combined_battery_observed_max_grid_export_w": 24.0,
                "combined_battery_average_active_charge_power_w": 25.0,
                "combined_battery_average_active_discharge_power_w": 26.0,
                "combined_battery_average_active_power_delta_w": 27.0,
                "combined_battery_power_smoothing_ratio": 28.0,
                "combined_battery_typical_response_delay_seconds": 29.0,
                "combined_battery_support_bias": 30.0,
                "combined_battery_day_support_bias": 31.0,
                "combined_battery_night_support_bias": 32.0,
                "combined_battery_import_support_bias": 33.0,
                "combined_battery_export_bias": 34.0,
                "combined_battery_battery_first_export_bias": 35.0,
                "combined_battery_reserve_band_floor_soc": 36.0,
                "combined_battery_reserve_band_ceiling_soc": 37.0,
                "combined_battery_reserve_band_width_soc": 38.0,
                "combined_battery_direction_change_count": 39,
                "combined_battery_learning_summary": learning,
            },
        )

        empty = operational_support._state_api_operational_energy_state({}, {})
        self.assertEqual(
            {
                key: empty[key]
                for key in (
                    "combined_battery_source_count",
                    "combined_battery_online_source_count",
                    "combined_battery_battery_source_count",
                    "combined_battery_hybrid_inverter_source_count",
                    "combined_battery_inverter_source_count",
                    "combined_battery_learning_profile_count",
                    "combined_battery_direction_change_count",
                )
            },
            {
                "combined_battery_source_count": 0,
                "combined_battery_online_source_count": 0,
                "combined_battery_battery_source_count": 0,
                "combined_battery_hybrid_inverter_source_count": 0,
                "combined_battery_inverter_source_count": 0,
                "combined_battery_learning_profile_count": 0,
                "combined_battery_direction_change_count": 0,
            },
        )

    def test_auto_decision_state_maps_sanitized_metrics_exactly(self) -> None:
        owner = SimpleNamespace(_last_health_reason="healthy")
        metrics = {
            "relay_intent": True,
            "surplus": 1.0,
            "grid": 2.0,
            "soc": 3.0,
            "start_threshold": 4.0,
            "stop_threshold": 5.0,
            "profile": " profile-a ",
            "threshold_mode": " adaptive ",
        }
        finite = MagicMock(side_effect=lambda value: value)
        with (
            patch.object(operational_support, "sanitized_auto_metrics", return_value=metrics) as sanitize,
            patch.object(operational_support, "finite_float_or_none", finite),
        ):
            state = operational_support._state_api_operational_auto_decision_state(
                owner,
                {"raw": "metrics"},
                "charging",
                2,
            )

        self.assertEqual(
            state,
            {
                "auto_decision": {
                    "reason": "healthy",
                    "state": "charging",
                    "state_code": 2,
                    "relay_intent": 1,
                    "surplus_watts": 1.0,
                    "grid_watts": 2.0,
                    "soc_percent": 3.0,
                    "start_threshold_watts": 4.0,
                    "stop_threshold_watts": 5.0,
                    "profile": "profile-a",
                    "threshold_mode": "adaptive",
                }
            },
        )
        sanitize.assert_called_once_with({"raw": "metrics"})
        self.assertEqual(finite.call_args_list, [call(1.0), call(2.0), call(3.0), call(4.0), call(5.0)])

        self.assertEqual(
            operational_support._state_api_operational_auto_decision_state(SimpleNamespace(), {}, "idle", 0),
            {
                "auto_decision": {
                    "reason": "na",
                    "state": "idle",
                    "state_code": 0,
                    "relay_intent": -1,
                    "surplus_watts": None,
                    "grid_watts": None,
                    "soc_percent": None,
                    "start_threshold_watts": None,
                    "stop_threshold_watts": None,
                    "profile": "",
                    "threshold_mode": "",
                }
            },
        )

    def test_operational_mapping_and_balance_contracts_are_exact(self) -> None:
        self.assertEqual(
            operational_support._mapping_values({"first": 1, "second": 2}, ("second", "missing", "first")),
            {"second": 2, "missing": None, "first": 1},
        )
        self.assertEqual(
            operational_support._mapping_values_with_default(
                {"first": 1, "second": None},
                ("first", "second", "missing"),
                7,
            ),
            {"first": 1, "second": None, "missing": 7},
        )

        worker = {
            "battery_discharge_balance_mode": "mode",
            "battery_discharge_balance_target_distribution_mode": "distribution",
            "battery_discharge_balance_error_w": 1.0,
            "battery_discharge_balance_max_abs_error_w": 2.0,
            "battery_discharge_balance_total_discharge_w": 3.0,
            "battery_discharge_balance_eligible_source_count": 4,
            "battery_discharge_balance_active_source_count": 5,
            "battery_discharge_balance_control_candidate_count": 6,
            "battery_discharge_balance_control_ready_count": 7,
            "battery_discharge_balance_supported_control_source_count": 8,
            "battery_discharge_balance_experimental_control_source_count": 9,
        }
        metrics = {
            "battery_discharge_balance_warning_error_w": 10.0,
            "battery_discharge_balance_warn_threshold_w": 11.0,
            "battery_discharge_balance_bias_mode": "bias",
            "battery_discharge_balance_bias_start_error_w": 12.0,
            "battery_discharge_balance_bias_penalty_w": 13.0,
            "battery_discharge_balance_coordination_support_mode": "support",
            "battery_discharge_balance_coordination_feasibility": "feasible",
            "battery_discharge_balance_coordination_start_error_w": 14.0,
            "battery_discharge_balance_coordination_penalty_w": 15.0,
            "battery_discharge_balance_coordination_advisory_reason": "reason",
            "battery_discharge_balance_warning_active": 1,
            "battery_discharge_balance_bias_gate_active": 0,
            "battery_discharge_balance_coordination_policy_enabled": 1,
            "battery_discharge_balance_coordination_gate_active": 0,
            "battery_discharge_balance_coordination_advisory_active": 1,
        }
        expected = {**worker, **metrics, "battery_discharge_balance_policy_enabled": True}
        self.assertEqual(
            operational_support._state_api_operational_balance_state(
                SimpleNamespace(auto_battery_discharge_balance_policy_enabled=True),
                worker,
                metrics,
            ),
            expected,
        )
        empty_balance = operational_support._state_api_operational_balance_state(SimpleNamespace(), {}, {})
        self.assertIs(empty_balance["battery_discharge_balance_policy_enabled"], False)
        self.assertEqual(
            {
                field: empty_balance[field]
                for field in (
                    "battery_discharge_balance_eligible_source_count",
                    "battery_discharge_balance_active_source_count",
                    "battery_discharge_balance_control_candidate_count",
                    "battery_discharge_balance_control_ready_count",
                    "battery_discharge_balance_supported_control_source_count",
                    "battery_discharge_balance_experimental_control_source_count",
                )
            },
            {
                "battery_discharge_balance_eligible_source_count": 0,
                "battery_discharge_balance_active_source_count": 0,
                "battery_discharge_balance_control_candidate_count": 0,
                "battery_discharge_balance_control_ready_count": 0,
                "battery_discharge_balance_supported_control_source_count": 0,
                "battery_discharge_balance_experimental_control_source_count": 0,
            },
        )


class TestControlStateVictronContracts(unittest.TestCase):
    _CORE_BOOLEAN_FIELDS = (
        "enabled",
        "active",
        "activation_gate_active",
        "telemetry_clean",
        "overshoot_active",
        "overshoot_cooldown_active",
        "settling_active",
        "oscillation_lockout_enabled",
        "oscillation_lockout_active",
        "auto_apply_enabled",
        "auto_apply_active",
        "auto_apply_observation_window_active",
        "auto_apply_suspend_active",
        "rollback_enabled",
        "rollback_active",
        "safe_state_active",
    )
    _CORE_VALUE_FIELDS = (
        "source_id",
        "topology_key",
        "support_mode",
        "activation_mode",
        "recommended_kp",
        "recommended_ki",
        "recommended_kd",
        "recommended_deadband_watts",
        "recommended_max_abs_watts",
        "recommended_ramp_rate_watts_per_second",
        "recommended_activation_mode",
        "recommendation_confidence",
        "recommendation_regime_consistency_score",
        "recommendation_response_variance_score",
        "recommendation_reproducibility_score",
        "recommendation_reason",
        "recommendation_profile_key",
        "recommendation_hint",
        "recommendation_ini_snippet",
        "telemetry_clean_reason",
        "response_delay_seconds",
        "estimated_gain",
        "overshoot_count",
        "overshoot_cooldown_reason",
        "overshoot_cooldown_until",
        "settled_count",
        "stability_score",
        "oscillation_lockout_reason",
        "oscillation_lockout_until",
        "oscillation_direction_change_count",
        "auto_apply_reason",
        "auto_apply_generation",
        "auto_apply_observation_window_until",
        "auto_apply_last_param",
        "auto_apply_suspend_reason",
        "auto_apply_suspend_until",
        "rollback_reason",
        "rollback_stable_profile_key",
        "safe_state_reason",
    )
    _LEARNING_PROFILE_FIELDS = (
        "key",
        "action_direction",
        "site_regime",
        "direction",
        "day_phase",
        "reserve_phase",
        "ev_phase",
        "pv_phase",
        "battery_limit_phase",
        "sample_count",
        "response_delay_seconds",
        "estimated_gain",
        "overshoot_count",
        "settled_count",
        "stability_score",
        "regime_consistency_score",
        "response_variance_score",
        "reproducibility_score",
        "safe_ramp_rate_watts_per_second",
        "preferred_bias_limit_watts",
    )
    _OWNER_CURRENT_FIELDS = {
        "current_kp": ("auto_battery_discharge_balance_victron_bias_kp", 0.11),
        "current_ki": ("auto_battery_discharge_balance_victron_bias_ki", 0.22),
        "current_kd": ("auto_battery_discharge_balance_victron_bias_kd", 0.33),
        "current_deadband_watts": (
            "auto_battery_discharge_balance_victron_bias_deadband_watts",
            44.0,
        ),
        "current_max_abs_watts": (
            "auto_battery_discharge_balance_victron_bias_max_abs_watts",
            55.0,
        ),
        "current_ramp_rate_watts_per_second": (
            "auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second",
            66.0,
        ),
    }

    @staticmethod
    def _metric_value(suffix: str) -> str:
        return f"metric:{suffix}"

    @classmethod
    def _complete_victron_fixture(cls) -> tuple[SimpleNamespace, dict[str, object], dict[str, object]]:
        prefix = "battery_discharge_balance_victron_bias_"
        metrics: dict[str, object] = {f"{prefix}{field}": cls._metric_value(field) for field in cls._CORE_VALUE_FIELDS}
        metrics.update({f"{prefix}{field}": 2 for field in cls._CORE_BOOLEAN_FIELDS})
        metrics[f"{prefix}learning_profile_key"] = cls._metric_value("learning_profile_key")
        metrics[f"{prefix}reason"] = cls._metric_value("reason")
        for field in cls._LEARNING_PROFILE_FIELDS:
            suffix = "learning_profile_key" if field == "key" else f"learning_profile_{field}"
            metrics[f"{prefix}{suffix}"] = cls._metric_value(suffix)

        profiles: dict[str, object] = {"profile-a": {"sample_count": 17, "gain": 0.42}}
        owner_values: dict[str, object] = {attribute: value for attribute, value in cls._OWNER_CURRENT_FIELDS.values()}
        owner_values.update(
            {
                "_last_auto_metrics": metrics,
                "_victron_ess_balance_learning_profiles": profiles,
                "_victron_ess_balance_last_stable_tuning": {"kp": 0.19, "ki": 0.02},
                "_victron_ess_balance_last_stable_at": 1234.5,
                "_victron_ess_balance_last_stable_profile_key": "stable-profile",
                "_victron_ess_balance_conservative_tuning": {"kp": 0.07, "ki": 0.01},
            }
        )
        return SimpleNamespace(**owner_values), metrics, profiles

    @classmethod
    def _expected_core_state(cls, metrics: dict[str, object]) -> dict[str, object]:
        prefix = "battery_discharge_balance_victron_bias_"
        expected = {field: metrics[f"{prefix}{field}"] for field in cls._CORE_VALUE_FIELDS}
        expected.update({field: True for field in cls._CORE_BOOLEAN_FIELDS})
        expected["active_learning_profile_key"] = metrics[f"{prefix}learning_profile_key"]
        expected["controller_reason"] = metrics[f"{prefix}reason"]
        expected.update({field: value for field, (_attribute, value) in cls._OWNER_CURRENT_FIELDS.items()})
        expected["active_learning_profile"] = {
            field: metrics[f"{prefix}{'learning_profile_key' if field == 'key' else f'learning_profile_{field}'}"]
            for field in cls._LEARNING_PROFILE_FIELDS
        }
        return expected

    @classmethod
    def _expected_adaptive_tuning(cls, metrics: dict[str, object]) -> dict[str, object]:
        prefix = "battery_discharge_balance_victron_bias_"
        current = {
            field.removeprefix("current_"): value for field, (_attribute, value) in cls._OWNER_CURRENT_FIELDS.items()
        }
        return {
            "schema_version": 2,
            "topology_key": metrics[f"{prefix}topology_key"],
            "source_id": metrics[f"{prefix}source_id"],
            **current,
            "activation_mode": metrics[f"{prefix}activation_mode"],
            "auto_apply_generation": metrics[f"{prefix}auto_apply_generation"],
            "auto_apply_observe_until": metrics[f"{prefix}auto_apply_observation_window_until"],
            "auto_apply_last_applied_param": metrics[f"{prefix}auto_apply_last_param"],
            "oscillation_lockout_until": metrics[f"{prefix}oscillation_lockout_until"],
            "oscillation_lockout_reason": metrics[f"{prefix}oscillation_lockout_reason"],
            "overshoot_cooldown_until": metrics[f"{prefix}overshoot_cooldown_until"],
            "overshoot_cooldown_reason": metrics[f"{prefix}overshoot_cooldown_reason"],
            "last_stable_tuning": {"kp": 0.19, "ki": 0.02},
            "last_stable_at": 1234.5,
            "last_stable_profile_key": "stable-profile",
            "conservative_tuning": {"kp": 0.07, "ki": 0.01},
            "auto_apply_suspend_until": metrics[f"{prefix}auto_apply_suspend_until"],
            "auto_apply_suspend_reason": metrics[f"{prefix}auto_apply_suspend_reason"],
            "safe_state_active": metrics[f"{prefix}safe_state_active"],
            "safe_state_reason": metrics[f"{prefix}safe_state_reason"],
        }

    def test_complete_victron_payload_preserves_every_contract_field(self) -> None:
        owner, metrics, profiles = self._complete_victron_fixture()

        payload = ControlStateVictron(owner).recommendation_payload()

        expected_state = self._expected_core_state(metrics)
        expected_state.update(
            {
                "learning_state": {
                    "schema_version": 2,
                    "topology_key": metrics["battery_discharge_balance_victron_bias_topology_key"],
                    "source_id": metrics["battery_discharge_balance_victron_bias_source_id"],
                    "profiles": profiles,
                },
                "adaptive_tuning": self._expected_adaptive_tuning(metrics),
                "learning_profiles": profiles,
            }
        )
        self.assertEqual(
            payload,
            {
                "ok": True,
                "api_version": "v1",
                "kind": "victron-bias-recommendation",
                "state": expected_state,
            },
        )

    def test_victron_payload_defaults_are_exact(self) -> None:
        payload = ControlStateVictron(SimpleNamespace()).recommendation_payload()

        expected_core: dict[str, object] = {field: None for field in self._CORE_VALUE_FIELDS}
        expected_core.update({field: False for field in self._CORE_BOOLEAN_FIELDS})
        expected_core.update({field: 0.0 for field in self._OWNER_CURRENT_FIELDS})
        expected_core.update(
            {
                "active_learning_profile_key": None,
                "controller_reason": None,
                "active_learning_profile": {field: None for field in self._LEARNING_PROFILE_FIELDS},
                "learning_state": {
                    "schema_version": 2,
                    "topology_key": None,
                    "source_id": None,
                    "profiles": {},
                },
                "adaptive_tuning": {
                    "schema_version": 2,
                    "topology_key": None,
                    "source_id": None,
                    "kp": 0.0,
                    "ki": 0.0,
                    "kd": 0.0,
                    "deadband_watts": 0.0,
                    "max_abs_watts": 0.0,
                    "ramp_rate_watts_per_second": 0.0,
                    "activation_mode": None,
                    "auto_apply_generation": None,
                    "auto_apply_observe_until": None,
                    "auto_apply_last_applied_param": None,
                    "oscillation_lockout_until": None,
                    "oscillation_lockout_reason": None,
                    "overshoot_cooldown_until": None,
                    "overshoot_cooldown_reason": None,
                    "last_stable_tuning": {},
                    "last_stable_at": None,
                    "last_stable_profile_key": "",
                    "conservative_tuning": {},
                    "auto_apply_suspend_until": None,
                    "auto_apply_suspend_reason": None,
                    "safe_state_active": None,
                    "safe_state_reason": None,
                },
                "learning_profiles": {},
            }
        )
        self.assertEqual(payload["state"], expected_core)

    def test_recommendation_payload_composes_metrics_profiles_and_adaptive_state(self) -> None:
        metrics = {
            "battery_discharge_balance_victron_bias_enabled": 1,
            "battery_discharge_balance_victron_bias_active": 1,
            "battery_discharge_balance_victron_bias_source_id": "battery-main",
            "battery_discharge_balance_victron_bias_topology_key": "topology-1",
            "battery_discharge_balance_victron_bias_learning_profile_key": "profile-1",
            "battery_discharge_balance_victron_bias_learning_profile_sample_count": 12,
            "battery_discharge_balance_victron_bias_recommended_kp": 0.4,
            "battery_discharge_balance_victron_bias_auto_apply_generation": 3,
        }
        profiles = {"profile-1": {"sample_count": 12}}
        owner = SimpleNamespace(
            _last_auto_metrics=metrics,
            _victron_ess_balance_learning_profiles=profiles,
            auto_battery_discharge_balance_victron_bias_kp=0.2,
            auto_battery_discharge_balance_victron_bias_ki=0.03,
            auto_battery_discharge_balance_victron_bias_kd=0.01,
            auto_battery_discharge_balance_victron_bias_deadband_watts=50.0,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=500.0,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=20.0,
            _victron_ess_balance_last_stable_tuning={"kp": 0.18},
            _victron_ess_balance_last_stable_at=100.0,
            _victron_ess_balance_last_stable_profile_key="profile-0",
            _victron_ess_balance_conservative_tuning={"kp": 0.1},
        )
        payload = ControlStateVictron(owner).recommendation_payload()
        state = payload["state"]
        self.assertTrue(state["enabled"])
        self.assertEqual(state["active_learning_profile"]["sample_count"], 12)
        self.assertEqual(state["learning_state"]["profiles"], profiles)
        self.assertEqual(state["adaptive_tuning"]["kp"], 0.2)
        self.assertEqual(state["adaptive_tuning"]["last_stable_tuning"], {"kp": 0.18})
        self.assertEqual(state["learning_profiles"], profiles)

    def test_victron_defaults_reject_malformed_metrics_profiles_and_tuning_maps(self) -> None:
        owner = SimpleNamespace(
            _last_auto_metrics=[],
            _victron_ess_balance_learning_profiles="invalid",
            _victron_ess_balance_last_stable_tuning=[],
            _victron_ess_balance_conservative_tuning=None,
        )
        state = ControlStateVictron(owner).recommendation_payload()["state"]
        self.assertFalse(state["enabled"])
        self.assertEqual(state["learning_profiles"], {})
        self.assertEqual(state["adaptive_tuning"]["last_stable_tuning"], {})
        self.assertEqual(state["adaptive_tuning"]["conservative_tuning"], {})
        self.assertEqual(victron_module._last_auto_metrics(SimpleNamespace()), {})
        self.assertEqual(victron_module._learning_profiles(SimpleNamespace()), {})
        self.assertEqual(victron_module._owner_dict_or_empty(SimpleNamespace(), "missing"), {})
        self.assertEqual(victron_module._owner_dict_or_empty(SimpleNamespace(value={"a": 1}), "value"), {"a": 1})


if __name__ == "__main__":
    unittest.main()

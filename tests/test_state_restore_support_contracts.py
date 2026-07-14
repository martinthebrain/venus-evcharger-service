# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from venus_evcharger.controllers.state import ServiceStateController
from venus_evcharger.controllers.state_restore_support import (
    _victron_ess_balance_energy_ids,
    _victron_ess_balance_runtime_string,
)


def _normalize_mode(value: object) -> int:
    return int(value) if isinstance(value, (int, float, str)) else 0


def _service() -> SimpleNamespace:
    return SimpleNamespace(
        auto_energy_sources=(
            SimpleNamespace(source_id=" battery-b "),
            SimpleNamespace(source_id="battery-a"),
            SimpleNamespace(source_id=""),
        ),
        auto_battery_discharge_balance_victron_bias_source_id="primary",
        auto_battery_discharge_balance_victron_bias_service=" com.victronenergy.settings ",
        auto_battery_discharge_balance_victron_bias_path=" /Settings/Ess/Bias ",
        auto_battery_discharge_balance_victron_bias_activation_mode="export_only",
        _victron_ess_balance_auto_apply_generation=2,
    )


class StateRestoreSupportContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = _service()
        self.controller = ServiceStateController(self.service, _normalize_mode)

    def _topology_key(self, source_id: str = "primary") -> str:
        return self.controller._victron_ess_balance_runtime_topology_key(
            self.service, source_id
        )

    def test_schema_and_topology_contracts(self) -> None:
        for value, expected in (
            (1, True),
            (2, True),
            (0, False),
            (3, False),
            ("bad", False),
            (None, False),
        ):
            with self.subTest(value=value):
                self.assertIs(
                    self.controller._valid_victron_ess_balance_schema_version(
                        {"schema_version": value}
                    ),
                    expected,
                )

        expected_key = (
            "victron-bias-learning/v2/source=primary"
            "/service=com.victronenergy.settings/path=/Settings/Ess/Bias"
            "/energy=battery-a,battery-b"
        )
        self.assertEqual(self._topology_key(), expected_key)
        self.assertTrue(
            self.controller._victron_ess_balance_payload_matches_topology(
                self.service,
                {"source_id": "primary", "topology_key": expected_key},
            )
        )
        self.assertTrue(
            self.controller._victron_ess_balance_payload_matches_topology(
                self.service,
                {"source_id": "primary", "topology_key": expected_key.replace("/v2", "/v1")},
            )
        )
        self.assertFalse(
            self.controller._victron_ess_balance_payload_matches_topology(
                self.service,
                {"source_id": "other", "topology_key": expected_key},
            )
        )
        self.assertTrue(
            self.controller._victron_ess_balance_payload_matches_topology(
                self.service,
                {"topology_key": expected_key},
            )
        )
        self.assertFalse(
            self.controller._victron_ess_balance_payload_matches_topology(
                self.service,
                {"source_id": None, "topology_key": expected_key},
            )
        )
        self.assertFalse(
            self.controller._victron_ess_balance_payload_matches_topology(
                self.service,
                {"source_id": "primary"},
            )
        )
        empty_topology_key = self.controller._victron_ess_balance_runtime_topology_key(
            SimpleNamespace(),
            "",
        )
        self.assertEqual(
            empty_topology_key,
            "victron-bias-learning/v2/source=/service=/path=/energy=",
        )
        self.assertTrue(
            self.controller._victron_ess_balance_payload_matches_topology(
                SimpleNamespace(),
                {"topology_key": empty_topology_key},
            )
        )

    def test_learning_profile_normalizes_complete_surface(self) -> None:
        raw: dict[str, object] = {
            "action_direction": "discharge",
            "site_regime": "export",
            "direction": "outbound",
            "day_phase": "day",
            "reserve_phase": "above",
            "ev_phase": "ev_active",
            "pv_phase": "pv_strong",
            "battery_limit_phase": "upper_band",
            "delay_samples": "2",
            "gain_samples": "3",
            "response_delay_seconds": "4.5",
            "estimated_gain": "0.6",
            "response_delay_mad_seconds": "0.7",
            "gain_mad": "0.08",
            "overshoot_count": "9",
            "settled_count": "10",
            "stability_score": "0.81",
            "regime_consistency_score": "0.82",
            "response_variance_score": "0.83",
            "reproducibility_score": "0.84",
            "safe_ramp_rate_watts_per_second": "85",
            "preferred_bias_limit_watts": "860",
        }

        normalized = self.controller._normalized_victron_ess_balance_learning_profile(
            "profile-a", raw
        )

        self.assertEqual(
            normalized,
            {
                "key": "profile-a",
                "action_direction": "discharge",
                "site_regime": "export",
                "direction": "outbound",
                "day_phase": "day",
                "reserve_phase": "above",
                "ev_phase": "ev_active",
                "pv_phase": "pv_strong",
                "battery_limit_phase": "upper_band",
                "delay_samples": 2,
                "gain_samples": 3,
                "response_delay_seconds": 4.5,
                "estimated_gain": 0.6,
                "response_delay_mad_seconds": 0.7,
                "gain_mad": 0.08,
                "overshoot_count": 9,
                "settled_count": 10,
                "stability_score": 0.81,
                "regime_consistency_score": 0.82,
                "response_variance_score": 0.83,
                "reproducibility_score": 0.84,
                "safe_ramp_rate_watts_per_second": 85.0,
                "preferred_bias_limit_watts": 860.0,
            },
        )

    def test_learning_profile_fallbacks_and_collection_filtering(self) -> None:
        normalized = self.controller._normalized_victron_ess_balance_learning_profile(
            "fallback",
            {
                "direction": "import",
                "typical_response_delay_seconds": 5,
                "effective_gain": 0.4,
            },
        )
        self.assertEqual(normalized["site_regime"], "import")
        self.assertEqual(normalized["direction"], "import")
        self.assertEqual(normalized["ev_phase"], "ev_idle")
        self.assertEqual(normalized["pv_phase"], "pv_weak")
        self.assertEqual(normalized["battery_limit_phase"], "mid_band")
        self.assertEqual(normalized["response_delay_seconds"], 5.0)
        self.assertEqual(normalized["estimated_gain"], 0.4)
        self.assertEqual(
            self.controller._normalized_victron_ess_balance_learning_profile(
                "site-only",
                {"site_regime": "export"},
            )["direction"],
            "export",
        )

        profiles = self.controller._normalized_victron_ess_balance_learning_profiles(
            {"": {}, "wrong": "not-a-map", " valid ": {"gain_samples": 2}}
        )
        self.assertEqual(set(profiles), {"valid"})
        self.assertEqual(profiles["valid"]["key"], "valid")
        self.assertEqual(profiles["valid"]["gain_samples"], 2)

    def test_empty_learning_profile_has_complete_stable_defaults(self) -> None:
        normalized = self.controller._normalized_victron_ess_balance_learning_profile(
            "empty",
            {},
        )

        self.assertEqual(
            normalized,
            {
                "key": "empty",
                "action_direction": "",
                "site_regime": "",
                "direction": "",
                "day_phase": "",
                "reserve_phase": "",
                "ev_phase": "ev_idle",
                "pv_phase": "pv_weak",
                "battery_limit_phase": "mid_band",
                "delay_samples": 0,
                "gain_samples": 0,
                "response_delay_seconds": None,
                "estimated_gain": None,
                "response_delay_mad_seconds": None,
                "gain_mad": None,
                "overshoot_count": 0,
                "settled_count": 0,
                "stability_score": None,
                "regime_consistency_score": None,
                "response_variance_score": None,
                "reproducibility_score": None,
                "safe_ramp_rate_watts_per_second": None,
                "preferred_bias_limit_watts": None,
            },
        )
        self.assertEqual(
            self.controller._normalized_victron_ess_balance_learning_profile(
                "explicit-none",
                {"direction": None, "site_regime": "fallback-must-not-win"},
            )["direction"],
            "",
        )

    def test_activation_mode_contract_covers_payload_and_runtime_fallbacks(self) -> None:
        normalize = self.controller._victron_ess_balance_activation_mode

        self.assertEqual(normalize({}, self.service), "export_only")
        self.assertEqual(normalize({}, SimpleNamespace()), "always")
        self.assertEqual(normalize({"activation_mode": None}, self.service), "always")
        self.assertEqual(normalize({"activation_mode": "  "}, self.service), "always")
        for mode in (
            "always",
            "export_only",
            "above_reserve_band",
            "export_and_above_reserve_band",
        ):
            with self.subTest(mode=mode):
                self.assertEqual(
                    normalize({"activation_mode": f" {mode.upper()} "}, self.service),
                    mode,
                )
        self.assertIsNone(normalize({"activation_mode": "invalid"}, self.service))

    def test_learning_payload_requires_schema_topology_and_profile_mapping(self) -> None:
        original = {"old": {"key": "old"}}
        self.service._victron_ess_balance_learning_profiles = original
        valid: dict[str, object] = {
            "schema_version": 2,
            "source_id": "primary",
            "topology_key": self._topology_key(),
            "profiles": {"profile-a": {"delay_samples": 4}},
        }

        for patch_values in (
            {"schema_version": 9},
            {"topology_key": "wrong"},
            {"profiles": []},
        ):
            payload = dict(valid)
            payload.update(patch_values)
            self.controller._restore_victron_ess_balance_learning_state_payload(
                self.service, payload
            )
            self.assertIs(self.service._victron_ess_balance_learning_profiles, original)

        self.controller._restore_victron_ess_balance_learning_state_payload(
            self.service, valid
        )
        self.assertEqual(
            self.service._victron_ess_balance_learning_profiles["profile-a"]["delay_samples"],
            4,
        )

    def test_pid_suspend_auto_apply_and_stable_tuning_restore_exact_values(self) -> None:
        payload: dict[str, object] = {
            "kp": 1.1,
            "ki": 1.2,
            "kd": 1.3,
            "deadband_watts": 14,
            "max_abs_watts": 150,
            "ramp_rate_watts_per_second": 16,
            "auto_apply_generation": 7,
            "auto_apply_observe_until": 20,
            "auto_apply_last_applied_param": "kp",
            "auto_apply_last_applied_at": 21,
            "oscillation_lockout_until": 22,
            "oscillation_lockout_reason": "oscillation",
            "overshoot_cooldown_until": 23,
            "overshoot_cooldown_reason": "overshoot",
            "last_stable_tuning": {"kp": 0.9},
            "last_stable_at": 24,
            "last_stable_profile_key": "profile-a",
            "conservative_tuning": {"kp": 0.5},
            "auto_apply_suspend_until": 25,
            "auto_apply_suspend_reason": "manual",
            "safe_state_active": 1,
            "safe_state_reason": "guard",
        }

        self.controller._restore_victron_ess_balance_pid_tuning(self.service, payload)
        self.controller._restore_victron_ess_balance_auto_apply_state(self.service, payload)
        self.controller._restore_victron_ess_balance_stable_tuning_state(self.service, payload)

        self.assertEqual(self.service.auto_battery_discharge_balance_victron_bias_kp, 1.1)
        self.assertEqual(self.service.auto_battery_discharge_balance_victron_bias_ki, 1.2)
        self.assertEqual(self.service.auto_battery_discharge_balance_victron_bias_kd, 1.3)
        self.assertEqual(self.service.auto_battery_discharge_balance_victron_bias_deadband_watts, 14.0)
        self.assertEqual(self.service.auto_battery_discharge_balance_victron_bias_max_abs_watts, 150.0)
        self.assertEqual(self.service.auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second, 16.0)
        self.assertEqual(self.service._victron_ess_balance_auto_apply_generation, 7)
        self.assertEqual(self.service._victron_ess_balance_auto_apply_observe_until, 20.0)
        self.assertEqual(self.service._victron_ess_balance_auto_apply_last_applied_param, "kp")
        self.assertEqual(self.service._victron_ess_balance_auto_apply_last_applied_at, 21.0)
        self.assertEqual(self.service._victron_ess_balance_oscillation_lockout_until, 22.0)
        self.assertEqual(self.service._victron_ess_balance_oscillation_lockout_reason, "oscillation")
        self.assertEqual(self.service._victron_ess_balance_overshoot_cooldown_until, 23.0)
        self.assertEqual(self.service._victron_ess_balance_overshoot_cooldown_reason, "overshoot")
        self.assertEqual(self.service._victron_ess_balance_last_stable_tuning, {"kp": 0.9})
        self.assertEqual(self.service._victron_ess_balance_last_stable_at, 24.0)
        self.assertEqual(self.service._victron_ess_balance_last_stable_profile_key, "profile-a")
        self.assertEqual(self.service._victron_ess_balance_conservative_tuning, {"kp": 0.5})
        self.assertEqual(self.service._victron_ess_balance_auto_apply_suspend_until, 25.0)
        self.assertEqual(self.service._victron_ess_balance_auto_apply_suspend_reason, "manual")
        self.assertIs(self.service._victron_ess_balance_safe_state_active, True)
        self.assertEqual(self.service._victron_ess_balance_safe_state_reason, "guard")

    def test_empty_restore_payload_has_explicit_defaults(self) -> None:
        self.controller._restore_victron_ess_balance_pid_value(
            self.service,
            {},
            "missing",
            "pid_value",
        )
        self.controller._restore_victron_ess_balance_auto_apply_state(self.service, {})
        self.controller._restore_victron_ess_balance_stable_tuning_state(self.service, {})

        self.assertEqual(self.service.pid_value, 0.0)
        self.assertEqual(self.service._victron_ess_balance_auto_apply_generation, 2)
        self.assertIsNone(self.service._victron_ess_balance_auto_apply_observe_until)
        self.assertEqual(self.service._victron_ess_balance_auto_apply_last_applied_param, "")
        self.assertIsNone(self.service._victron_ess_balance_auto_apply_last_applied_at)
        self.assertIsNone(self.service._victron_ess_balance_oscillation_lockout_until)
        self.assertEqual(self.service._victron_ess_balance_oscillation_lockout_reason, "")
        self.assertIsNone(self.service._victron_ess_balance_overshoot_cooldown_until)
        self.assertEqual(self.service._victron_ess_balance_overshoot_cooldown_reason, "")
        self.assertEqual(self.service._victron_ess_balance_last_stable_tuning, {})
        self.assertIsNone(self.service._victron_ess_balance_last_stable_at)
        self.assertEqual(self.service._victron_ess_balance_last_stable_profile_key, "")
        self.assertEqual(self.service._victron_ess_balance_conservative_tuning, {})
        self.assertIsNone(self.service._victron_ess_balance_auto_apply_suspend_until)
        self.assertEqual(self.service._victron_ess_balance_auto_apply_suspend_reason, "")
        self.assertIs(self.service._victron_ess_balance_safe_state_active, False)
        self.assertEqual(self.service._victron_ess_balance_safe_state_reason, "")

        minimal_service = SimpleNamespace()
        self.controller._restore_victron_ess_balance_auto_apply_state(
            minimal_service,
            {},
        )
        self.assertEqual(minimal_service._victron_ess_balance_auto_apply_generation, 0)

    def test_adaptive_restore_passes_service_to_each_restore_stage(self) -> None:
        payload: dict[str, object] = {
            "schema_version": 2,
            "source_id": "primary",
            "topology_key": self._topology_key(),
            "activation_mode": "above_reserve_band",
        }

        with (
            patch.object(
                ServiceStateController,
                "_restore_victron_ess_balance_pid_tuning",
            ) as restore_pid,
            patch.object(
                ServiceStateController,
                "_victron_ess_balance_activation_mode",
                return_value="above_reserve_band",
            ) as activation_mode,
            patch.object(
                ServiceStateController,
                "_restore_victron_ess_balance_auto_apply_state",
            ) as restore_auto_apply,
            patch.object(
                ServiceStateController,
                "_restore_victron_ess_balance_stable_tuning_state",
            ) as restore_stable,
        ):
            self.controller._restore_victron_ess_balance_adaptive_tuning_payload(
                self.service,
                payload,
            )

        restore_pid.assert_called_once_with(self.service, payload)
        activation_mode.assert_called_once_with(payload, self.service)
        restore_auto_apply.assert_called_once_with(self.service, payload)
        restore_stable.assert_called_once_with(self.service, payload)
        self.assertEqual(
            self.service.auto_battery_discharge_balance_victron_bias_activation_mode,
            "above_reserve_band",
        )

    def test_runtime_state_dispatches_only_mapping_payloads(self) -> None:
        learning = {"schema_version": 2}
        adaptive = {"schema_version": 2}

        with (
            patch.object(
                ServiceStateController,
                "_restore_victron_ess_balance_learning_state_payload",
            ) as restore_learning,
            patch.object(
                ServiceStateController,
                "_restore_victron_ess_balance_adaptive_tuning_payload",
            ) as restore_adaptive,
        ):
            self.controller._restore_victron_ess_balance_runtime_state(
                self.service,
                {
                    "victron_ess_balance_learning_state": learning,
                    "victron_ess_balance_adaptive_tuning_state": adaptive,
                },
            )

        restore_learning.assert_called_once_with(self.service, learning)
        restore_adaptive.assert_called_once_with(self.service, adaptive)

    def test_energy_id_and_runtime_string_helpers(self) -> None:
        self.assertEqual(
            _victron_ess_balance_energy_ids(self.service),
            ["battery-b", "battery-a"],
        )
        self.assertEqual(
            _victron_ess_balance_runtime_string(
                self.service, "auto_battery_discharge_balance_victron_bias_service"
            ),
            "com.victronenergy.settings",
        )
        self.assertEqual(_victron_ess_balance_runtime_string(SimpleNamespace(), "missing"), "")
        self.assertEqual(
            _victron_ess_balance_energy_ids(
                SimpleNamespace(
                    auto_energy_sources=(SimpleNamespace(), SimpleNamespace(source_id=None))
                )
            ),
            [],
        )
        self.assertEqual(_victron_ess_balance_energy_ids(SimpleNamespace()), [])
        self.assertEqual(
            _victron_ess_balance_runtime_string(SimpleNamespace(value=None), "value"),
            "",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

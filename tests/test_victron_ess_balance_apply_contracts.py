import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from venus_evcharger.runtime.setup_support import default_auto_metrics
from venus_evcharger.update.victron_ess_balance import VictronEssBalanceController
from venus_evcharger.update.victron_ess_balance_apply import VictronEssBalanceExecutor
from tests.support.victron_ess_balance import build_victron_ess_components


def _service(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "auto_battery_discharge_balance_victron_bias_enabled": True,
        "auto_battery_discharge_balance_victron_bias_auto_apply_enabled": True,
        "auto_battery_discharge_balance_victron_bias_base_setpoint_watts": 50.0,
        "auto_battery_discharge_balance_victron_bias_service": "settings-service",
        "auto_battery_discharge_balance_victron_bias_path": "/Setpoint",
        "_last_energy_cluster": {"battery_discharge_balance_eligible_source_count": 2},
        "_victron_ess_balance_safe_state_active": True,
        "_victron_ess_balance_safe_state_reason": "old",
        "_victron_ess_balance_pid_last_output_w": 0.0,
        "_victron_ess_balance_last_write_at": None,
        "_victron_ess_balance_last_setpoint_w": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class ApplyContracts(unittest.TestCase):
    def setUp(self) -> None:
        components = build_victron_ess_components()
        self.subject: VictronEssBalanceExecutor = components.executor
        self.sources = components.sources
        self.pid = components.pid
        self.writer = components.writer
        self.profiles = components.profiles
        self.safety = components.safety
        self.recovery = components.recovery
        self.telemetry = components.telemetry
        self.recommendation = components.recommendation
        self.adaptive = components.adaptive

    def test_default_metrics_are_the_central_victron_contract_with_owned_reason(self) -> None:
        expected = {
            key: value
            for key, value in default_auto_metrics().items()
            if key.startswith("battery_discharge_balance_victron_bias_")
        }
        expected["battery_discharge_balance_victron_bias_reason"] = "contract-reason"
        self.assertEqual(self.subject._victron_ess_balance_default_metrics("contract-reason"), expected)
        self.assertEqual(self.subject._victron_ess_balance_default_metrics()["battery_discharge_balance_victron_bias_reason"], "disabled")

    def test_public_controller_delegates_only_its_three_domain_operations(self) -> None:
        controller = VictronEssBalanceController()
        svc = _service()
        controller.components.executor.apply_victron_ess_balance_bias = MagicMock()
        controller.components.profiles.victron_ess_balance_learning_state_payload = MagicMock(
            return_value={"learning": 1}
        )
        controller.components.profiles.victron_ess_balance_adaptive_tuning_payload = MagicMock(
            return_value={"tuning": 2}
        )
        controller.apply_victron_ess_balance_bias(svc, 3.0, True)
        self.assertEqual(controller.victron_ess_balance_learning_state_payload(svc), {"learning": 1})
        self.assertEqual(controller.victron_ess_balance_adaptive_tuning_payload(svc), {"tuning": 2})
        controller.components.executor.apply_victron_ess_balance_bias.assert_called_once_with(
            svc, 3.0, True
        )

    def test_initialize_metrics_and_enabled_use_exact_service_contract(self) -> None:
        svc = _service()
        metrics: dict[str, Any] = {}
        self.sources._victron_ess_balance_support_mode = MagicMock(return_value="supported")
        self.sources._victron_ess_balance_activation_mode = MagicMock(return_value="export")
        self.subject._initialize_victron_ess_balance_apply_metrics(svc, metrics)
        self.assertEqual(
            metrics,
            {
                "battery_discharge_balance_victron_bias_enabled": 1,
                "battery_discharge_balance_victron_bias_support_mode": "supported",
                "battery_discharge_balance_victron_bias_activation_mode": "export",
                "battery_discharge_balance_victron_bias_auto_apply_enabled": 1,
            },
        )
        self.sources._victron_ess_balance_support_mode.assert_called_once_with(svc)
        self.sources._victron_ess_balance_activation_mode.assert_called_once_with(svc)
        self.assertTrue(self.subject._victron_ess_balance_enabled(svc))
        self.assertFalse(
            self.subject._victron_ess_balance_enabled(
                _service(auto_battery_discharge_balance_victron_bias_enabled=False)
            )
        )
        self.assertFalse(self.subject._victron_ess_balance_enabled(SimpleNamespace()))
        sparse_metrics: dict[str, Any] = {}
        sparse = SimpleNamespace()
        self.subject._initialize_victron_ess_balance_apply_metrics(sparse, sparse_metrics)
        self.assertEqual(sparse_metrics["battery_discharge_balance_victron_bias_auto_apply_enabled"], 0)

    def test_apply_entrypoint_owns_disabled_blocked_and_tracking_paths(self) -> None:
        svc = _service()
        metrics: dict[str, Any] = {"seed": 1}
        self.subject._victron_ess_balance_default_metrics = MagicMock(return_value=metrics)
        self.subject._initialize_victron_ess_balance_apply_metrics = MagicMock()
        self.subject._disable_victron_ess_balance = MagicMock()
        self.subject._prepare_victron_ess_balance_tracking_state = MagicMock()
        self.subject._restore_victron_ess_balance_base_setpoint = MagicMock()
        self.subject._apply_victron_ess_balance_tracking = MagicMock()
        self.subject._victron_ess_balance_enabled = MagicMock(return_value=False)
        self.subject.apply_victron_ess_balance_bias(svc, 10.0, True)
        self.subject._victron_ess_balance_default_metrics.assert_called_once_with("disabled")
        self.subject._initialize_victron_ess_balance_apply_metrics.assert_called_once_with(svc, metrics)
        self.subject._disable_victron_ess_balance.assert_called_once_with(svc, metrics)
        self.subject._prepare_victron_ess_balance_tracking_state.assert_not_called()

        self.subject._victron_ess_balance_enabled.return_value = True
        self.subject._prepare_victron_ess_balance_tracking_state.return_value = ({"c": 1}, None, None, "blocked")
        self.subject.apply_victron_ess_balance_bias(svc, 11.0, False)
        self.subject._restore_victron_ess_balance_base_setpoint.assert_called_once_with(svc, 11.0, metrics, "blocked")
        self.subject._prepare_victron_ess_balance_tracking_state.assert_called_with(svc, 11.0, False, metrics)

        cluster = {"c": 2}
        self.subject._prepare_victron_ess_balance_tracking_state.return_value = (cluster, -25.0, "profile", "")
        self.subject.apply_victron_ess_balance_bias(svc, 12.0, True)
        self.subject._apply_victron_ess_balance_tracking.assert_called_once_with(
            svc, 12.0, cluster, -25.0, "profile", metrics
        )

    def test_pid_component_resets_owned_state(self) -> None:
        svc = _service(
            _victron_ess_balance_pid_last_error_w=4.0,
            _victron_ess_balance_pid_last_at=8.0,
            _victron_ess_balance_pid_integral_output_w=12.0,
            _victron_ess_balance_pid_last_output_w=16.0,
        )
        self.pid.reset_integral(svc)
        self.assertEqual(svc._victron_ess_balance_pid_integral_output_w, 0.0)
        self.assertEqual(svc._victron_ess_balance_pid_last_output_w, 16.0)
        self.pid.reset_integral(svc, aggressive=True)
        self.assertEqual(svc._victron_ess_balance_pid_last_output_w, 0.0)
        self.pid.reset(svc)
        self.assertEqual(svc._victron_ess_balance_pid_last_error_w, 0.0)
        self.assertIsNone(svc._victron_ess_balance_pid_last_at)

    def test_disable_merges_before_reset(self) -> None:
        svc = _service()
        metrics: dict[str, Any] = {}
        events: list[str] = []
        self.sources._merge_victron_ess_balance_metrics = MagicMock(side_effect=lambda *_: events.append("merge"))
        self.pid.reset = MagicMock(side_effect=lambda *_: events.append("reset"))
        self.subject._disable_victron_ess_balance(svc, metrics)
        self.assertEqual(events, ["merge", "reset"])
        self.sources._merge_victron_ess_balance_metrics.assert_called_once_with(svc, metrics)

    def test_cluster_state_short_circuits_and_enforces_two_sources(self) -> None:
        svc = _service()
        self.sources._normalized_mapping = MagicMock(return_value={"battery_discharge_balance_eligible_source_count": 1})
        self.assertEqual(self.subject._victron_ess_balance_cluster_state(svc, False), ({}, "auto-mode-inactive"))
        self.sources._normalized_mapping.assert_not_called()
        self.assertEqual(
            self.subject._victron_ess_balance_cluster_state(svc, True),
            ({"battery_discharge_balance_eligible_source_count": 1}, "insufficient-eligible-sources"),
        )
        self.sources._normalized_mapping.assert_called_once_with(svc._last_energy_cluster)
        self.sources._normalized_mapping.return_value = {}
        self.assertEqual(
            self.subject._victron_ess_balance_cluster_state(svc, True),
            ({}, "insufficient-eligible-sources"),
        )
        self.sources._normalized_mapping.reset_mock()
        self.subject._victron_ess_balance_cluster_state(SimpleNamespace(), True)
        self.sources._normalized_mapping.assert_called_once_with(None)
        cluster = {"battery_discharge_balance_eligible_source_count": 2}
        self.sources._normalized_mapping.return_value = cluster
        self.assertEqual(self.subject._victron_ess_balance_cluster_state(svc, True), (cluster, ""))

    def test_source_state_covers_resolution_health_value_support_and_success(self) -> None:
        svc = _service()
        metrics: dict[str, Any] = {}
        self.sources._victron_ess_balance_source = MagicMock(return_value=(None, "missing"))
        self.assertEqual(self.subject._victron_ess_balance_source_state({}, svc, metrics), (None, None, "missing"))
        self.profiles._victron_ess_balance_current_topology_key = MagicMock(return_value="topology")
        self.sources._victron_ess_balance_source_support_allowed = MagicMock(return_value=True)
        source = {"source_id": " victron ", "online": False, "discharge_balance_error_w": -12.0}
        self.sources._victron_ess_balance_source.return_value = (source, "")
        self.assertEqual(
            self.subject._victron_ess_balance_source_state({}, svc, metrics),
            (None, None, "victron-source-offline"),
        )
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_source_id"], "victron")
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_topology_key"], "topology")
        self.sources._victron_ess_balance_source.assert_called_with({}, svc)
        self.profiles._victron_ess_balance_current_topology_key.assert_called_with(svc, "victron")
        source["online"] = True
        source["discharge_balance_error_w"] = None
        self.assertEqual(
            self.subject._victron_ess_balance_source_state({}, svc, metrics),
            (None, None, "victron-source-error-missing"),
        )
        source["discharge_balance_error_w"] = -12.0
        self.sources._victron_ess_balance_source_support_allowed.return_value = False
        self.assertEqual(
            self.subject._victron_ess_balance_source_state({}, svc, metrics),
            (None, None, "victron-source-support-blocked"),
        )
        self.sources._victron_ess_balance_source_support_allowed.return_value = True
        self.assertEqual(self.subject._victron_ess_balance_source_state({}, svc, metrics), (source, -12.0, ""))
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_source_error_w"], -12.0)
        source.pop("online")
        self.assertEqual(self.subject._victron_ess_balance_source_state({}, svc, metrics), (source, -12.0, ""))

    def test_learning_state_executes_profile_contract_in_order(self) -> None:
        svc = _service()
        metrics: dict[str, Any] = {}
        profile = {"key": "profile", "action_direction": "export"}
        events: list[str] = []
        self.profiles._victron_ess_balance_learning_profile = MagicMock(return_value=profile)
        self.profiles._set_victron_ess_balance_active_profile = MagicMock(side_effect=lambda *_: events.append("set"))
        self.profiles._ensure_victron_ess_balance_learning_profile_state = MagicMock(
            side_effect=lambda *_: events.append("ensure")
        )
        self.profiles._merge_victron_ess_balance_learning_profile_metrics = MagicMock(
            side_effect=lambda *_: events.append("merge")
        )
        self.recovery._victron_ess_balance_refresh_stable_tuning = MagicMock(
            side_effect=lambda *_: events.append("refresh")
        )
        self.safety._victron_ess_balance_note_action_direction = MagicMock(
            side_effect=lambda *_: events.append("direction") or 3
        )
        self.safety._populate_victron_ess_balance_runtime_safety_metrics = MagicMock(
            side_effect=lambda *_: events.append("safety")
        )
        self.assertIs(
            self.subject._prepare_victron_ess_balance_learning_state(svc, 5.0, {"c": 1}, {"s": 1}, -4.0, metrics),
            profile,
        )
        self.assertEqual(events, ["set", "ensure", "merge", "refresh", "direction", "safety"])
        self.profiles._victron_ess_balance_learning_profile.assert_called_once_with(
            svc, {"c": 1}, {"s": 1}, -4.0
        )
        self.profiles._ensure_victron_ess_balance_learning_profile_state.assert_called_once_with(svc, "profile")
        self.profiles._merge_victron_ess_balance_learning_profile_metrics.assert_called_once_with(
            svc, metrics, "profile"
        )
        self.recovery._victron_ess_balance_refresh_stable_tuning.assert_called_once_with(svc, metrics, 5.0)
        self.safety._victron_ess_balance_note_action_direction.assert_called_once_with(svc, "export", 5.0)
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_oscillation_direction_change_count"], 3)

    def test_tracking_profile_blocks_safety_then_activation_and_returns_key(self) -> None:
        svc = _service()
        metrics: dict[str, Any] = {}
        profile = {"key": "profile"}
        self.subject._prepare_victron_ess_balance_learning_state = MagicMock(return_value=profile)
        self.subject._victron_ess_balance_safety_block_reason = MagicMock(return_value="unsafe")
        self.sources._victron_ess_balance_activation_allowed = MagicMock()
        args = (svc, 4.0, {"c": 1}, {"s": 1}, -2.0, metrics)
        self.assertEqual(self.subject._prepare_victron_ess_balance_tracking_profile(*args), (None, "unsafe"))
        self.sources._victron_ess_balance_activation_allowed.assert_not_called()
        self.subject._victron_ess_balance_safety_block_reason.return_value = ""
        self.sources._victron_ess_balance_activation_allowed.return_value = False
        self.assertEqual(
            self.subject._prepare_victron_ess_balance_tracking_profile(*args),
            (None, "activation-mode-blocked"),
        )
        self.sources._victron_ess_balance_activation_allowed.assert_called_with(profile, svc)
        self.sources._victron_ess_balance_activation_allowed.return_value = True
        self.assertEqual(self.subject._prepare_victron_ess_balance_tracking_profile(*args), ("profile", ""))

    def test_safety_blocker_short_circuits_and_clears_stale_safe_state(self) -> None:
        svc = _service()
        metrics: dict[str, Any] = {}
        self.recovery._victron_ess_balance_overshoot_cooldown_active = MagicMock(return_value=True)
        self.safety._victron_ess_balance_oscillation_lockout_active = MagicMock(return_value=True)
        self.recovery._maybe_restore_victron_ess_balance_stable_tuning = MagicMock()
        self.assertEqual(self.subject._victron_ess_balance_safety_block_reason(svc, 8.0, metrics), "overshoot-cooldown-active")
        self.recovery._maybe_restore_victron_ess_balance_stable_tuning.assert_called_once_with(
            svc, metrics, "overshoot_cooldown"
        )
        self.safety._victron_ess_balance_oscillation_lockout_active.assert_not_called()
        self.recovery._victron_ess_balance_overshoot_cooldown_active.return_value = False
        self.assertEqual(self.subject._victron_ess_balance_safety_block_reason(svc, 9.0, metrics), "oscillation-lockout-active")
        self.recovery._maybe_restore_victron_ess_balance_stable_tuning.assert_called_with(
            svc, metrics, "oscillation_lockout"
        )
        self.safety._victron_ess_balance_oscillation_lockout_active.return_value = False
        self.assertEqual(self.subject._victron_ess_balance_safety_block_reason(svc, 10.0, metrics), "")
        self.safety._victron_ess_balance_oscillation_lockout_active.assert_called_with(svc, 10.0)
        self.assertIs(svc._victron_ess_balance_safe_state_active, False)
        self.assertEqual(svc._victron_ess_balance_safe_state_reason, "")

    def test_setpoint_and_telemetry_delegation_publish_exact_values(self) -> None:
        svc = _service(auto_battery_discharge_balance_victron_bias_base_setpoint_watts=40.0)
        metrics: dict[str, Any] = {}
        self.pid._victron_ess_balance_pid_output = MagicMock(return_value=12.5)
        self.assertEqual(self.subject._victron_ess_balance_tracking_setpoint(svc, 7.0, -20.0, metrics), 52.5)
        self.pid._victron_ess_balance_pid_output.assert_called_once_with(svc, -20.0, 7.0)
        self.assertEqual(
            metrics,
            {
                "battery_discharge_balance_victron_bias_activation_gate_active": 1,
                "battery_discharge_balance_victron_bias_pid_output_w": 12.5,
                "battery_discharge_balance_victron_bias_setpoint_w": 52.5,
                "battery_discharge_balance_victron_bias_reason": "tracking",
            },
        )
        self.assertEqual(svc._victron_ess_balance_pid_last_output_w, 12.5)
        self.telemetry._update_victron_ess_balance_telemetry = MagicMock()
        self.subject._victron_ess_balance_update_tracking_telemetry(svc, 8.0, {"c": 1}, -3.0, "p", metrics)
        self.telemetry._update_victron_ess_balance_telemetry.assert_called_once_with(
            svc, 8.0, {"c": 1}, -3.0, metrics, "p"
        )

    def test_write_outcome_and_tracking_write_state_cover_all_branches(self) -> None:
        svc = _service()
        metrics: dict[str, Any] = {}
        self.writer._victron_ess_balance_write_setpoint = MagicMock(return_value=True)
        self.telemetry._record_victron_ess_balance_command = MagicMock()
        self.subject._victron_ess_balance_apply_write_outcome(svc, 9.0, 65.0, -5.0, "p", metrics)
        self.writer._victron_ess_balance_write_setpoint.assert_called_once_with(
            svc, "settings-service", "/Setpoint", 65.0
        )
        self.assertEqual(svc._victron_ess_balance_last_write_at, 9.0)
        self.assertEqual(svc._victron_ess_balance_last_setpoint_w, 65.0)
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_reason"], "applied")
        self.writer._victron_ess_balance_write_setpoint.return_value = False
        self.subject._victron_ess_balance_apply_write_outcome(svc, 10.0, 70.0, -6.0, "p", metrics)
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_reason"], "write-failed")
        self.writer._victron_ess_balance_write_setpoint.reset_mock()
        sparse = SimpleNamespace()
        self.subject._victron_ess_balance_apply_write_outcome(sparse, 10.0, 70.0, -6.0, "p", {})
        self.writer._victron_ess_balance_write_setpoint.assert_called_once_with(sparse, "", "", 70.0)

        self.writer._victron_ess_balance_should_write = MagicMock(return_value=True)
        self.subject._victron_ess_balance_apply_write_outcome = MagicMock()
        self.subject._victron_ess_balance_tracking_write_state(svc, 11.0, 71.0, -7.0, "p", metrics)
        self.subject._victron_ess_balance_apply_write_outcome.assert_called_once_with(
            svc, 11.0, 71.0, -7.0, "p", metrics
        )
        self.writer._victron_ess_balance_should_write.return_value = False
        self.writer._victron_ess_balance_last_setpoint = MagicMock(return_value=65.0)
        metrics.clear()
        self.subject._victron_ess_balance_tracking_write_state(svc, 12.0, 72.0, -8.0, "p", metrics)
        self.writer._victron_ess_balance_should_write.assert_called_with(svc, 12.0, 72.0)
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_active"], 1)
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_reason"], "holding")
        self.writer._victron_ess_balance_last_setpoint.return_value = None
        metrics.clear()
        self.subject._victron_ess_balance_tracking_write_state(svc, 13.0, 73.0, -9.0, "p", metrics)
        self.assertEqual(metrics, {})

    def test_tracking_pipeline_and_finalizer_preserve_order_and_arguments(self) -> None:
        svc = _service()
        metrics: dict[str, Any] = {}
        events: list[str] = []
        self.subject._victron_ess_balance_tracking_setpoint = MagicMock(
            side_effect=lambda *_: events.append("setpoint") or 61.0
        )
        self.subject._victron_ess_balance_update_tracking_telemetry = MagicMock(
            side_effect=lambda *_: events.append("telemetry")
        )
        self.subject._victron_ess_balance_tracking_write_state = MagicMock(
            side_effect=lambda *_: events.append("write")
        )
        self.subject._finalize_victron_ess_balance_metrics = MagicMock(
            side_effect=lambda *_: events.append("finalize")
        )
        self.subject._apply_victron_ess_balance_tracking(svc, 4.0, {"c": 1}, -2.0, "p", metrics)
        self.assertEqual(events, ["setpoint", "telemetry", "write", "finalize"])
        self.subject._victron_ess_balance_tracking_write_state.assert_called_once_with(
            svc, 4.0, 61.0, -2.0, "p", metrics
        )
        self.subject._victron_ess_balance_update_tracking_telemetry.assert_called_once_with(
            svc, 4.0, {"c": 1}, -2.0, "p", metrics
        )
        self.subject._victron_ess_balance_tracking_setpoint.assert_called_once_with(svc, 4.0, -2.0, metrics)


if __name__ == "__main__":
    unittest.main()

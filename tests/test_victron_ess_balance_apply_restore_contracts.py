import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from tests.test_victron_ess_balance_apply_contracts import _service
from venus_evcharger.update.victron_ess_balance_apply import VictronEssBalanceExecutor
from tests.support.victron_ess_balance import build_victron_ess_components


class ApplyPreparationContracts(unittest.TestCase):
    def setUp(self) -> None:
        components = build_victron_ess_components()
        self.subject = components.executor
        self.adaptive = components.adaptive
        self.sources = components.sources
        self.svc = _service()
        self.metrics: dict[str, Any] = {}

    def test_tracking_source_returns_cluster_block_source_block_and_success(self) -> None:
        cluster = {"cluster": 1}
        source = {"source_id": "victron"}
        self.subject._victron_ess_balance_cluster_state = MagicMock(return_value=(cluster, "cluster-block"))
        self.subject._victron_ess_balance_source_state = MagicMock()
        self.assertEqual(
            self.subject._prepare_victron_ess_balance_tracking_source(self.svc, False, self.metrics),
            (cluster, None, None, "cluster-block"),
        )
        self.subject._victron_ess_balance_source_state.assert_not_called()
        self.subject._victron_ess_balance_cluster_state.return_value = (cluster, "")
        self.subject._victron_ess_balance_source_state.return_value = (None, None, "source-block")
        self.assertEqual(
            self.subject._prepare_victron_ess_balance_tracking_source(self.svc, True, self.metrics),
            (cluster, None, None, "source-block"),
        )
        self.subject._victron_ess_balance_source_state.assert_called_with(cluster, self.svc, self.metrics)
        self.subject._victron_ess_balance_source_state.return_value = (source, -15.0, "")
        self.assertEqual(
            self.subject._prepare_victron_ess_balance_tracking_source(self.svc, True, self.metrics),
            (cluster, source, -15.0, ""),
        )

    def test_tracking_state_short_circuits_source_and_profile_failures(self) -> None:
        cluster = {"cluster": 1}
        source = {"source_id": "victron"}
        self.subject._prepare_victron_ess_balance_tracking_source = MagicMock(
            return_value=(cluster, None, None, "source-block")
        )
        self.subject._prepare_victron_ess_balance_tracking_profile = MagicMock()
        self.assertEqual(
            self.subject._prepare_victron_ess_balance_tracking_state(self.svc, 3.0, True, self.metrics),
            (cluster, None, None, "source-block"),
        )
        self.subject._prepare_victron_ess_balance_tracking_profile.assert_not_called()
        self.subject._prepare_victron_ess_balance_tracking_source.return_value = (cluster, source, -20.0, "")
        self.subject._prepare_victron_ess_balance_tracking_profile.return_value = (None, "profile-block")
        self.assertEqual(
            self.subject._prepare_victron_ess_balance_tracking_state(self.svc, 4.0, False, self.metrics),
            (cluster, None, None, "profile-block"),
        )
        self.subject._prepare_victron_ess_balance_tracking_profile.assert_called_with(
            self.svc, 4.0, cluster, source, -20.0, self.metrics
        )
        self.subject._prepare_victron_ess_balance_tracking_profile.return_value = ("profile", "")
        self.assertEqual(
            self.subject._prepare_victron_ess_balance_tracking_state(self.svc, 5.0, True, self.metrics),
            (cluster, -20.0, "profile", ""),
        )

    def test_base_setpoint_normalizes_falsey_value_and_preserves_number(self) -> None:
        self.assertEqual(self.subject._victron_ess_balance_base_setpoint_w(self.svc), 50.0)
        self.svc.auto_battery_discharge_balance_victron_bias_base_setpoint_watts = None
        self.assertEqual(self.subject._victron_ess_balance_base_setpoint_w(self.svc), 0.0)
        self.svc.auto_battery_discharge_balance_victron_bias_base_setpoint_watts = -25.0
        self.assertEqual(self.subject._victron_ess_balance_base_setpoint_w(self.svc), -25.0)
        self.assertEqual(self.subject._victron_ess_balance_base_setpoint_w(SimpleNamespace()), 50.0)

    def test_finalizer_auto_applies_before_merging(self) -> None:
        events: list[str] = []
        self.adaptive._maybe_auto_apply_victron_ess_balance_recommendation = MagicMock(
            side_effect=lambda *_: events.append("auto")
        )
        self.sources._merge_victron_ess_balance_metrics = MagicMock(side_effect=lambda *_: events.append("merge"))
        self.subject._finalize_victron_ess_balance_metrics(self.svc, 6.0, self.metrics)
        self.assertEqual(events, ["auto", "merge"])
        self.adaptive._maybe_auto_apply_victron_ess_balance_recommendation.assert_called_once_with(
            self.svc, self.metrics, 6.0
        )
        self.sources._merge_victron_ess_balance_metrics.assert_called_once_with(self.svc, self.metrics)


class ApplyRestoreContracts(unittest.TestCase):
    def setUp(self) -> None:
        components = build_victron_ess_components()
        self.subject: VictronEssBalanceExecutor = components.executor
        self.pid = components.pid
        self.writer = components.writer
        self.profiles = components.profiles
        self.telemetry = components.telemetry
        self.recommendation = components.recommendation
        self.adaptive = components.adaptive
        self.sources = components.sources
        self.svc = _service(_victron_ess_balance_last_setpoint_w=70.0)
        self.metrics: dict[str, Any] = {}
        self.events: list[str] = []
        self.subject._victron_ess_balance_base_setpoint_w = MagicMock(return_value=50.0)
        self.pid.reset = MagicMock(side_effect=lambda *_: self.events.append("reset"))
        self.telemetry._clear_victron_ess_balance_tracking_episode = MagicMock(
            side_effect=lambda *_: self.events.append("clear-episode")
        )
        self.profiles._clear_victron_ess_balance_active_profile = MagicMock(
            side_effect=lambda *_: self.events.append("clear-profile")
        )
        self.recommendation._populate_victron_ess_balance_telemetry_metrics = MagicMock(
            side_effect=lambda *_: self.events.append("telemetry")
        )
        self.adaptive._maybe_auto_apply_victron_ess_balance_recommendation = MagicMock(
            side_effect=lambda *_: self.events.append("auto")
        )
        self.sources._merge_victron_ess_balance_metrics = MagicMock(
            side_effect=lambda *_: self.events.append("merge")
        )
        self.writer._victron_ess_balance_last_setpoint = MagicMock(return_value=70.0)
        self.writer._victron_ess_balance_should_write = MagicMock(return_value=True)
        self.writer._victron_ess_balance_write_setpoint = MagicMock(return_value=True)

    def _restore(self, reason: str = "blocked") -> None:
        self.subject._restore_victron_ess_balance_base_setpoint(self.svc, 20.0, self.metrics, reason)

    def _assert_common_initialization(self, reason: str = "blocked") -> None:
        self.assertEqual(self.events[:3], ["reset", "clear-episode", "clear-profile"])
        self.subject._victron_ess_balance_base_setpoint_w.assert_called_once_with(self.svc)
        self.pid.reset.assert_called_once_with(self.svc)
        self.telemetry._clear_victron_ess_balance_tracking_episode.assert_called_once_with(self.svc)
        self.profiles._clear_victron_ess_balance_active_profile.assert_called_once_with(self.svc)
        self.assertEqual(self.metrics["battery_discharge_balance_victron_bias_setpoint_w"], 50.0)
        self.assertEqual(self.metrics["battery_discharge_balance_victron_bias_activation_gate_active"], 0)
        self.assertTrue(self.metrics["battery_discharge_balance_victron_bias_reason"].startswith(reason))

    def _assert_common_finalization(self) -> None:
        self.assertEqual(self.events[-3:], ["telemetry", "auto", "merge"])
        self.recommendation._populate_victron_ess_balance_telemetry_metrics.assert_called_once_with(
            self.svc, self.metrics
        )
        self.adaptive._maybe_auto_apply_victron_ess_balance_recommendation.assert_called_once_with(
            self.svc, self.metrics, 20.0
        )
        self.sources._merge_victron_ess_balance_metrics.assert_called_once_with(self.svc, self.metrics)

    def test_no_previous_setpoint_finalizes_without_write_decision(self) -> None:
        self.writer._victron_ess_balance_last_setpoint.return_value = None
        self._restore()
        self._assert_common_initialization()
        self._assert_common_finalization()
        self.assertEqual(self.metrics["battery_discharge_balance_victron_bias_reason"], "blocked")
        self.writer._victron_ess_balance_should_write.assert_not_called()
        self.writer._victron_ess_balance_write_setpoint.assert_not_called()

    def test_rate_limited_restore_holds_active_state(self) -> None:
        self.writer._victron_ess_balance_should_write.return_value = False
        self._restore("safety")
        self._assert_common_initialization("safety")
        self._assert_common_finalization()
        self.writer._victron_ess_balance_should_write.assert_called_once_with(self.svc, 20.0, 50.0)
        self.assertEqual(self.metrics["battery_discharge_balance_victron_bias_active"], 1)
        self.assertEqual(self.metrics["battery_discharge_balance_victron_bias_reason"], "safety-holding")
        self.writer._victron_ess_balance_write_setpoint.assert_not_called()

    def test_successful_restore_clears_last_setpoint_and_records_time(self) -> None:
        self._restore()
        self._assert_common_initialization()
        self._assert_common_finalization()
        self.writer._victron_ess_balance_write_setpoint.assert_called_once_with(
            self.svc, "settings-service", "/Setpoint", 50.0
        )
        self.assertEqual(self.svc._victron_ess_balance_last_write_at, 20.0)
        self.assertIsNone(self.svc._victron_ess_balance_last_setpoint_w)
        self.assertEqual(self.metrics["battery_discharge_balance_victron_bias_reason"], "blocked-restored")

    def test_successful_restore_uses_empty_gateway_target_when_config_is_absent(self) -> None:
        del self.svc.auto_battery_discharge_balance_victron_bias_service
        del self.svc.auto_battery_discharge_balance_victron_bias_path
        self._restore()
        self.writer._victron_ess_balance_write_setpoint.assert_called_once_with(self.svc, "", "", 50.0)

    def test_failed_restore_remains_active_and_reports_exact_reason(self) -> None:
        self.writer._victron_ess_balance_write_setpoint.return_value = False
        self._restore("offline")
        self._assert_common_initialization("offline")
        self._assert_common_finalization()
        self.assertEqual(self.metrics["battery_discharge_balance_victron_bias_active"], 1)
        self.assertEqual(self.metrics["battery_discharge_balance_victron_bias_reason"], "offline-restore-failed")
        self.assertEqual(self.svc._victron_ess_balance_last_setpoint_w, 70.0)


if __name__ == "__main__":
    unittest.main()

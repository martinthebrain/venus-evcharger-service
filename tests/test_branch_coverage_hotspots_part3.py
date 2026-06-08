# SPDX-License-Identifier: GPL-3.0-or-later
from tests.test_branch_coverage_hotspots_support import *  # noqa: F401,F403

class _BranchCoverageVictronApplyCasesPart1:
    def test_victron_apply_helper_branches(self) -> None:
        controller = _controller()
        service = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_min_update_seconds=10.0,
            auto_battery_discharge_balance_victron_bias_support_mode="weird",
            auto_battery_discharge_balance_victron_bias_activation_mode="weird",
            auto_battery_discharge_balance_victron_bias_kp=0.2,
            auto_battery_discharge_balance_victron_bias_ki=0.1,
            auto_battery_discharge_balance_victron_bias_kd=0.0,
            auto_battery_discharge_balance_victron_bias_deadband_watts=100.0,
            auto_battery_discharge_balance_victron_bias_integral_limit_watts=50.0,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=200.0,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=10.0,
            auto_battery_discharge_balance_victron_bias_base_setpoint_watts=50.0,
            auto_battery_discharge_balance_victron_bias_service="",
            auto_battery_discharge_balance_victron_bias_path="",
            dbus_method_timeout_seconds=1.0,
            _warning_throttled=MagicMock(),
            _reset_system_bus=MagicMock(),
            _last_auto_metrics=None,
            _victron_ess_balance_last_write_at=100.0,
            _victron_ess_balance_last_setpoint_w=75.0,
            _victron_ess_balance_pid_last_output_w=0.0,
            _victron_ess_balance_pid_integral_output_w=10.0,
            _victron_ess_balance_pid_last_error_w=15.0,
            _victron_ess_balance_pid_last_at=100.0,
        )
        metrics: dict[str, object] = {}

        self.assertEqual(controller._victron_ess_balance_cluster_state(service, False), ({}, "auto-mode-inactive"))
        service._last_energy_cluster = {"battery_discharge_balance_eligible_source_count": 1}
        self.assertEqual(
            controller._victron_ess_balance_cluster_state(service, True),
            (service._last_energy_cluster, "insufficient-eligible-sources"),
        )

        with patch.object(controller, "_victron_ess_balance_source", return_value=(None, "missing")):
            self.assertEqual(
                controller._victron_ess_balance_source_state({}, service, {}),
                (None, None, "missing"),
            )

        with patch.object(controller, "_victron_ess_balance_source", return_value=({"source_id": "a", "online": False}, "")):
            self.assertEqual(
                controller._victron_ess_balance_source_state({}, service, {}),
                (None, None, "victron-source-offline"),
            )

        with patch.object(
            controller,
            "_victron_ess_balance_source",
            return_value=({"source_id": "a", "online": True, "discharge_balance_error_w": None}, ""),
        ):
            self.assertEqual(
                controller._victron_ess_balance_source_state({}, service, {}),
                (None, None, "victron-source-error-missing"),
            )

        with patch.object(
            controller,
            "_victron_ess_balance_source",
            return_value=({"source_id": "a", "online": True, "discharge_balance_error_w": 10.0}, ""),
        ), patch.object(controller, "_victron_ess_balance_source_support_allowed", return_value=False):
            self.assertEqual(
                controller._victron_ess_balance_source_state({}, service, {}),
                (None, None, "victron-source-support-blocked"),
            )

        self.assertEqual(controller._victron_ess_balance_support_mode(service), "allow_experimental")
        self.assertEqual(controller._victron_ess_balance_activation_mode(service), "always")
        self.assertFalse(
            controller._victron_ess_balance_activation_allowed(
                {"site_regime": "import", "reserve_phase": "reserve_band"},
                SimpleNamespace(auto_battery_discharge_balance_victron_bias_activation_mode="export_and_above_reserve_band"),
            )
        )
        self.assertTrue(controller._victron_ess_balance_activation_site_regime_matches("above_reserve_band", "import"))
        self.assertTrue(controller._victron_ess_balance_activation_reserve_phase_matches("export_only", "reserve_band"))
        self.assertTrue(
            controller._victron_ess_balance_activation_allowed(
                {"site_regime": "import", "reserve_phase": "reserve_band"},
                SimpleNamespace(auto_battery_discharge_balance_victron_bias_activation_mode="always"),
            )
        )

        self.assertFalse(controller._victron_ess_balance_should_write(service, 105.0, 80.0))
        self.assertFalse(controller._victron_ess_balance_write_setpoint(service, "", "", 10.0))
        self.assertEqual(controller._victron_ess_balance_write_target(None, None), ("", ""))
        self.assertEqual(controller._victron_ess_balance_write_payload(object(), 12.5), 12.5)

        with patch.object(controller, "_victron_ess_balance_write_error", return_value=RuntimeError("boom")):
            self.assertFalse(controller._victron_ess_balance_write_setpoint(service, "svc", "/path", 10.0))
            service._warning_throttled.assert_called()

        with patch.object(
            controller,
            "_victron_ess_balance_try_write_setpoint",
            side_effect=[RuntimeError("first"), None],
        ), patch.object(controller, "_victron_ess_balance_log_write_retry") as log_retry:
            self.assertIsNone(controller._victron_ess_balance_write_error(service, "svc", "/path", 10.0))
            log_retry.assert_called_once()
            service._reset_system_bus.assert_called()
        with patch.object(
            controller,
            "_victron_ess_balance_try_write_setpoint",
            side_effect=[RuntimeError("first"), RuntimeError("second")],
        ), patch.object(controller, "_victron_ess_balance_log_write_retry") as log_retry:
            last_error = controller._victron_ess_balance_write_error(service, "svc", "/path", 10.0)
            self.assertEqual(str(last_error), "second")
            log_retry.assert_called_once()

        class _FakeInterface:
            def __init__(self) -> None:
                self.calls: list[tuple[object, float]] = []

            def SetValue(self, value: object, timeout: float) -> None:
                self.calls.append((value, timeout))

        class _FakeBus:
            def __init__(self, interface: _FakeInterface) -> None:
                self.interface = interface

            def get_object(self, normalized_service: str, normalized_path: str, introspect: bool = True) -> object:
                return (normalized_service, normalized_path)

        fake_interface = _FakeInterface()

        class _FakeDbus:
            @staticmethod
            def Double(value: float) -> tuple[str, float]:
                return ("double", value)

            @staticmethod
            def Interface(_obj: object, _name: str) -> _FakeInterface:
                return fake_interface

        service._get_system_bus = MagicMock(return_value=_FakeBus(fake_interface))
        with patch("venus_evcharger.update.victron_ess_balance_apply.dbus", _FakeDbus):
            controller._victron_ess_balance_try_write_setpoint(service, "svc", "/path", 12.0)
        self.assertEqual(fake_interface.calls[0], (("double", 12.0), 1.0))

        with patch("venus_evcharger.update.victron_ess_balance_apply.logging.debug") as debug_log:
            controller._victron_ess_balance_log_write_retry("svc", "/path", RuntimeError("boom"))
            debug_log.assert_called_once()

        self.assertEqual(controller._victron_ess_balance_pid_output(service, 150.0, 101.0), 10.0)
        self.assertEqual(controller._victron_ess_balance_pid_output(service, 0.0, 102.0), 0.0)

        with patch.object(controller, "_victron_ess_balance_should_write", return_value=False):
            controller._victron_ess_balance_tracking_write_state(service, 110.0, 75.0, 25.0, "profile", metrics)
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_reason"], "holding")
        service._victron_ess_balance_last_setpoint_w = None
        metrics = {}
        with patch.object(controller, "_victron_ess_balance_should_write", return_value=False):
            controller._victron_ess_balance_tracking_write_state(service, 110.5, 75.0, 25.0, "profile", metrics)
        self.assertEqual(metrics, {})
        with patch.object(controller, "_victron_ess_balance_should_write", return_value=True), patch.object(
            controller, "_victron_ess_balance_apply_write_outcome"
        ) as apply_outcome:
            controller._victron_ess_balance_tracking_write_state(service, 111.0, 75.0, 25.0, "profile", {})
            apply_outcome.assert_called_once()

        with patch.object(controller, "_populate_victron_ess_balance_telemetry_metrics"), patch.object(
            controller, "_maybe_auto_apply_victron_ess_balance_recommendation"
        ), patch.object(controller, "_merge_victron_ess_balance_metrics"):
            service._victron_ess_balance_last_setpoint_w = None
            metrics = {}
            controller._restore_victron_ess_balance_base_setpoint(service, 120.0, metrics, "blocked")
            self.assertEqual(metrics["battery_discharge_balance_victron_bias_reason"], "blocked")

        with patch.object(controller, "_victron_ess_balance_should_write", return_value=False), patch.object(
            controller, "_populate_victron_ess_balance_telemetry_metrics"
        ), patch.object(controller, "_maybe_auto_apply_victron_ess_balance_recommendation"), patch.object(
            controller, "_merge_victron_ess_balance_metrics"
        ):
            service._victron_ess_balance_last_setpoint_w = 70.0
            metrics = {}
            controller._restore_victron_ess_balance_base_setpoint(service, 121.0, metrics, "blocked")
            self.assertEqual(metrics["battery_discharge_balance_victron_bias_reason"], "blocked-holding")

        with patch.object(controller, "_victron_ess_balance_should_write", return_value=True), patch.object(
            controller, "_victron_ess_balance_write_setpoint", return_value=False
        ), patch.object(controller, "_populate_victron_ess_balance_telemetry_metrics"), patch.object(
            controller, "_maybe_auto_apply_victron_ess_balance_recommendation"
        ), patch.object(controller, "_merge_victron_ess_balance_metrics"):
            metrics = {}
            controller._restore_victron_ess_balance_base_setpoint(service, 122.0, metrics, "blocked")
            self.assertEqual(metrics["battery_discharge_balance_victron_bias_reason"], "blocked-restore-failed")

        self.assertEqual(
            controller._victron_ess_balance_source(
                {"battery_sources": [{"source_id": "x", "discharge_balance_control_connector_type": "dbus"}]},
                SimpleNamespace(auto_battery_discharge_balance_victron_bias_source_id="missing"),
            ),
            (None, "victron-source-not-found"),
        )
        self.assertEqual(
            controller._victron_ess_balance_source({"battery_sources": []}, SimpleNamespace(auto_battery_discharge_balance_victron_bias_source_id="")),
            (None, "victron-source-not-detected"),
        )
        self.assertEqual(
            controller._victron_ess_balance_source(
                {
                    "battery_sources": [
                        {"source_id": "a", "discharge_balance_control_connector_type": "dbus"},
                        {"source_id": "b", "discharge_balance_control_connector_type": "dbus"},
                    ]
                },
                SimpleNamespace(auto_battery_discharge_balance_victron_bias_source_id=""),
            ),
            (None, "victron-source-ambiguous"),
        )

        self.assertFalse(
            controller._victron_ess_balance_source_support_allowed(
                {"discharge_balance_control_support": "experimental"},
                SimpleNamespace(auto_battery_discharge_balance_victron_bias_support_mode="supported_only"),
            )
        )
        self.assertTrue(
            controller._victron_ess_balance_source_support_allowed(
                {"discharge_balance_control_support": "experimental"},
                SimpleNamespace(auto_battery_discharge_balance_victron_bias_support_mode="allow_experimental"),
            )
        )
        self.assertEqual(controller._victron_ess_balance_cluster_state(SimpleNamespace(_last_energy_cluster={"battery_discharge_balance_eligible_source_count": 2}), True)[1], "")
        self.assertEqual(
            controller._victron_ess_balance_matching_source([{"source_id": "a"}], "a"),
            {"source_id": "a"},
        )
        self.assertEqual(
            controller._victron_ess_balance_source(
                {"battery_sources": [{"source_id": "a", "discharge_balance_control_connector_type": "dbus"}]},
                SimpleNamespace(auto_battery_discharge_balance_victron_bias_source_id="a"),
            ),
            ({"source_id": "a", "discharge_balance_control_connector_type": "dbus"}, "configured-source"),
        )
        self.assertEqual(
            controller._victron_ess_balance_source(
                {"battery_sources": [{"source_id": "a", "discharge_balance_control_connector_type": "dbus"}]},
                SimpleNamespace(auto_battery_discharge_balance_victron_bias_source_id=""),
            ),
            ({"source_id": "a", "discharge_balance_control_connector_type": "dbus"}, "auto-detected-dbus-source"),
        )
        controller._reset_victron_ess_balance_pid_integral(service)
        self.assertEqual(service._victron_ess_balance_pid_integral_output_w, 0.0)
        self.assertTrue(
            controller._victron_ess_balance_activation_allowed(
                {"site_regime": "export", "reserve_phase": "above_reserve_band"},
                SimpleNamespace(auto_battery_discharge_balance_victron_bias_activation_mode="export_and_above_reserve_band"),
            )
        )
        self.assertEqual(controller._victron_ess_balance_pid_clamped_output_w(12.0, 0.0), 12.0)
        self.assertEqual(controller._victron_ess_balance_pid_ramped_output_w(1.0, 5.0, 0.0, 10.0), 5.0)
        service._victron_ess_balance_last_setpoint_w = None
        self.assertTrue(controller._victron_ess_balance_should_write(service, 120.0, 80.0))
        service._victron_ess_balance_last_setpoint_w = 75.0
        service._victron_ess_balance_last_write_at = 100.0
        self.assertTrue(controller._victron_ess_balance_should_write(service, 120.0, 90.0))

        metrics = {}
        with patch.object(
            controller,
            "_victron_ess_balance_source",
            return_value=({"source_id": "a", "online": True, "discharge_balance_error_w": 10.0}, ""),
        ), patch.object(controller, "_victron_ess_balance_source_support_allowed", return_value=True):
            source_state = controller._victron_ess_balance_source_state({}, service, metrics)
            self.assertEqual(source_state[2], "")
            self.assertEqual(source_state[1], 10.0)
        with patch.object(controller, "_victron_ess_balance_cluster_state", return_value=({"cluster": 1}, "")), patch.object(
            controller, "_victron_ess_balance_source_state", return_value=(None, None, "blocked-source")
        ):
            self.assertEqual(
                controller._prepare_victron_ess_balance_tracking_source(service, True, {}),
                ({"cluster": 1}, None, None, "blocked-source"),
            )
        with patch.object(controller, "_victron_ess_balance_cluster_state", return_value=({"cluster": 1}, "blocked-cluster")):
            self.assertEqual(
                controller._prepare_victron_ess_balance_tracking_source(service, True, {}),
                ({"cluster": 1}, None, None, "blocked-cluster"),
            )
        with patch.object(controller, "_victron_ess_balance_cluster_state", return_value=({"cluster": 1}, "")), patch.object(
            controller, "_victron_ess_balance_source_state", return_value=({"source_id": "a"}, 12.0, "")
        ):
            self.assertEqual(
                controller._prepare_victron_ess_balance_tracking_source(service, True, {}),
                ({"cluster": 1}, {"source_id": "a"}, 12.0, ""),
            )

        with patch.object(
            controller,
            "_prepare_victron_ess_balance_tracking_source",
            return_value=({"cluster": 1}, None, None, "blocked"),
        ):
            self.assertEqual(
                controller._prepare_victron_ess_balance_tracking_state(service, 122.0, True, {}),
                ({"cluster": 1}, None, None, "blocked"),
            )

        with patch.object(
            controller,
            "_prepare_victron_ess_balance_tracking_source",
            return_value=({"cluster": 1}, {"source_id": "a"}, 12.0, ""),
        ), patch.object(controller, "_prepare_victron_ess_balance_tracking_profile", return_value=("profile", "")):
            self.assertEqual(
                controller._prepare_victron_ess_balance_tracking_state(service, 123.0, True, {}),
                ({"cluster": 1}, 12.0, "profile", ""),
            )
        with patch.object(
            controller,
            "_prepare_victron_ess_balance_tracking_source",
            return_value=({"cluster": 1}, {"source_id": "a"}, 12.0, ""),
        ), patch.object(controller, "_prepare_victron_ess_balance_tracking_profile", return_value=(None, "profile-blocked")):
            self.assertEqual(
                controller._prepare_victron_ess_balance_tracking_state(service, 124.0, True, {}),
                ({"cluster": 1}, None, None, "profile-blocked"),
            )

        with patch.object(controller, "_victron_ess_balance_learning_profile", return_value={"key": "profile", "site_regime": "export", "reserve_phase": "above_reserve_band"}), patch.object(
            controller, "_merge_victron_ess_balance_learning_profile_metrics"
        ), patch.object(controller, "_victron_ess_balance_refresh_stable_tuning"), patch.object(
            controller, "_victron_ess_balance_note_action_direction", return_value=0
        ), patch.object(controller, "_populate_victron_ess_balance_runtime_safety_metrics"), patch.object(
            controller, "_victron_ess_balance_safety_block_reason", return_value=""
        ), patch.object(controller, "_victron_ess_balance_activation_allowed", return_value=True):
            self.assertEqual(
                controller._prepare_victron_ess_balance_tracking_profile(service, 100.0, {}, {"source_id": "a"}, 10.0, {}),
                ("profile", ""),
            )

        with patch.object(controller, "_victron_ess_balance_overshoot_cooldown_active", return_value=True), patch.object(
            controller, "_maybe_restore_victron_ess_balance_stable_tuning"
        ) as restore_tuning:
            self.assertEqual(controller._victron_ess_balance_safety_block_reason(service, 130.0, {}), "overshoot-cooldown-active")
            restore_tuning.assert_called_once()
        with patch.object(controller, "_victron_ess_balance_overshoot_cooldown_active", return_value=False), patch.object(
            controller, "_victron_ess_balance_oscillation_lockout_active", return_value=True
        ), patch.object(controller, "_maybe_restore_victron_ess_balance_stable_tuning") as restore_tuning:
            self.assertEqual(controller._victron_ess_balance_safety_block_reason(service, 131.0, {}), "oscillation-lockout-active")
            restore_tuning.assert_called_once()
        with patch.object(controller, "_victron_ess_balance_overshoot_cooldown_active", return_value=False), patch.object(
            controller, "_victron_ess_balance_oscillation_lockout_active", return_value=False
        ):
            service._victron_ess_balance_safe_state_active = True
            service._victron_ess_balance_safe_state_reason = "old"
            self.assertEqual(controller._victron_ess_balance_safety_block_reason(service, 130.0, {}), "")
            self.assertFalse(service._victron_ess_balance_safe_state_active)
        with patch.object(controller, "_update_victron_ess_balance_telemetry") as update_telemetry:
            controller._victron_ess_balance_update_tracking_telemetry(service, 132.0, {"cluster": 1}, -10.0, "profile", {})
            update_telemetry.assert_called_once()
        with patch.object(controller, "_victron_ess_balance_write_setpoint", return_value=False):
            metrics = {}
            controller._victron_ess_balance_apply_write_outcome(service, 133.0, 70.0, -20.0, "profile", metrics)
            self.assertEqual(metrics["battery_discharge_balance_victron_bias_reason"], "write-failed")
        with patch.object(controller, "_victron_ess_balance_write_error", return_value=None):
            self.assertTrue(controller._victron_ess_balance_write_setpoint(service, "svc", "/path", 10.0))

        controller._merge_victron_ess_balance_metrics(service, {"x": 1})
        self.assertEqual(service._last_auto_metrics, {"x": 1})


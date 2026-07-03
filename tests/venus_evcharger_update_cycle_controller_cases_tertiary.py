# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_update_cycle_controller_support import *


class TestUpdateCycleControllerTertiary(UpdateCycleControllerTestBase):
    def test_auto_phase_decision_helpers_publish_explicit_contracts(self):
        service = _auto_phase_service(
            supported_phase_selections=("P1_P2", "P1", "P1_P2_P3", "P1"),
            requested_phase_selection="P1_P2",
            active_phase_selection="P1_P2",
            _last_auto_metrics={"surplus": "3200.5"},
            _auto_phase_target_candidate="P1_P2_P3",
            _auto_phase_target_since="123.5",
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        supported = controller._ordered_auto_phase_selections(service)
        self.assertEqual(supported, ("P1", "P1_P2", "P1_P2_P3"))
        self.assertEqual(controller._current_phase_selection(service, supported), "P1_P2")
        service.requested_phase_selection = "P1_P2_P3"
        service.active_phase_selection = "P1_P2"
        self.assertEqual(controller._current_phase_selection(service, ("P1", "P1_P2")), "P1_P2")
        self.assertEqual(controller._auto_phase_metric_surplus_watts(service), 3200.5)

        controller._record_auto_phase_metrics(
            service,
            current_selection="P1_P2",
            target_selection="P1_P2_P3",
            phase_reason="phase-upshift",
            threshold_watts=3000.0,
        )

        self.assertEqual(service._last_auto_metrics["phase_current"], "P1_P2")
        self.assertEqual(service._last_auto_metrics["phase_target"], "P1_P2_P3")
        self.assertEqual(service._last_auto_metrics["phase_reason"], "phase-upshift")
        self.assertEqual(service._last_auto_metrics["phase_threshold_watts"], 3000.0)
        self.assertEqual(service._last_auto_metrics["phase_candidate"], "P1_P2_P3")
        self.assertEqual(service._last_auto_metrics["phase_candidate_since"], 123.5)

    def test_auto_phase_policy_state_and_idle_targets_are_explicit(self):
        service = _auto_phase_service(supported_phase_selections=("P1", "P1_P2"))
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        service.auto_policy.phase.enabled = False
        self.assertEqual(
            controller._auto_phase_policy_state(service, ("P1", "P1_P2")),
            (None, "phase-policy-disabled", None),
        )

        service.auto_policy.phase.enabled = True
        self.assertEqual(controller._auto_phase_policy_state(service, ("P1",)), (None, "single-phase-only", None))
        self.assertIsNone(controller._auto_phase_policy_state(service, ("P1", "P1_P2")))

        policy = service.auto_policy.phase
        policy.prefer_lowest_phase_when_idle = True
        self.assertIsNone(controller._idle_auto_phase_target(policy, ("P1", "P1_P2"), "P1_P2", True, False))
        self.assertIsNone(controller._idle_auto_phase_target(policy, ("P1", "P1_P2"), "P1_P2", False, True))
        self.assertEqual(
            controller._idle_auto_phase_target(policy, ("P1", "P1_P2"), "P1_P2", False, False),
            ("P1", "idle-lowest-phase", None),
        )

        policy.prefer_lowest_phase_when_idle = False
        self.assertEqual(
            controller._idle_auto_phase_target(policy, ("P1", "P1_P2"), "P1_P2", False, False),
            (None, "idle-hold-phase", None),
        )

    def test_auto_phase_threshold_helpers_reject_missing_or_invalid_inputs(self):
        service = _auto_phase_service(min_current=6.0, voltage_mode="phase")
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertEqual(controller._phase_selection_count("P1"), 1)
        self.assertEqual(controller._phase_selection_count("P1_P2"), 2)
        self.assertEqual(controller._phase_selection_count("P1_P2_P3"), 3)
        self.assertTrue(controller._phase_selection_is_upshift("P1", "P1_P2"))
        self.assertFalse(controller._phase_selection_is_upshift("P1_P2", "P1"))
        self.assertIsNone(controller._phase_selection_voltage(service, "P1", 0.0))
        self.assertIsNone(controller._phase_selection_min_surplus_watts(service, "P1", 0.0))

        service.min_current = 0.0
        self.assertIsNone(controller._phase_selection_min_surplus_watts(service, "P1", 230.0))

        service.min_current = 6.0
        self.assertEqual(controller._phase_selection_min_surplus_watts(service, "P1_P2", 230.0), 2760.0)
        self.assertEqual(
            controller._phase_upshift_threshold(service, service.auto_policy.phase, "P1_P2", 230.0),
            3010.0,
        )

    def test_auto_phase_selection_helpers_cover_fallback_contracts(self):
        controller = UpdateCycleController(
            _auto_phase_service(),
            _phase_values,
            lambda reason: {"init": 0}.get(reason, 99),
        )

        self.assertEqual(controller._ordered_auto_phase_selections(SimpleNamespace()), ("P1",))
        self.assertEqual(
            controller._ordered_auto_phase_selections(SimpleNamespace(supported_phase_selections=())),
            ("P1",),
        )
        self.assertEqual(
            controller._ordered_auto_phase_selections(
                SimpleNamespace(supported_phase_selections=("P1_P2_P3", "invalid", "P1_P2", "P1_P2"))
            ),
            ("P1", "P1_P2", "P1_P2_P3"),
        )
        self.assertEqual(
            controller._ordered_auto_phase_selections(
                SimpleNamespace(supported_phase_selections=("P1_P2_P3", "P1_P2"))
            ),
            ("P1_P2", "P1_P2_P3"),
        )

        supported = ("P1", "P1_P2", "P1_P2_P3")
        self.assertEqual(
            controller._current_phase_selection(
                SimpleNamespace(requested_phase_selection="P1_P2_P3", active_phase_selection="P1"),
                supported,
            ),
            "P1_P2_P3",
        )
        self.assertEqual(
            controller._current_phase_selection(
                SimpleNamespace(requested_phase_selection="P1_P2_P3", active_phase_selection="P1_P2"),
                ("P1", "P1_P2"),
            ),
            "P1_P2",
        )
        self.assertEqual(
            controller._current_phase_selection(
                SimpleNamespace(requested_phase_selection="bad", active_phase_selection="also-bad"),
                supported,
            ),
            "P1",
        )
        self.assertEqual(controller._current_phase_selection(SimpleNamespace(), ("P1_P2", "P1_P2_P3")), "P1_P2")
        self.assertEqual(
            controller._current_phase_selection(
                SimpleNamespace(requested_phase_selection=None, active_phase_selection="P1_P2_P3"),
                ("P1_P2", "P1_P2_P3"),
            ),
            "P1_P2",
        )
        self.assertEqual(
            controller._current_phase_selection(
                SimpleNamespace(requested_phase_selection="bad", active_phase_selection=None),
                ("P1_P2", "P1_P2_P3"),
            ),
            "P1_P2",
        )
        self.assertEqual(
            controller._current_phase_selection(
                SimpleNamespace(requested_phase_selection="P1"),
                ("P1_P2", "P1_P2_P3"),
            ),
            "P1_P2",
        )

    def test_auto_phase_policy_and_metric_helpers_cover_absent_and_invalid_inputs(self):
        controller = UpdateCycleController(
            _auto_phase_service(),
            _phase_values,
            lambda reason: {"init": 0}.get(reason, 99),
        )
        phase_policy = SimpleNamespace(enabled=True)

        self.assertIsNone(controller._auto_phase_policy(SimpleNamespace(auto_policy=None)))
        self.assertIsNone(controller._auto_phase_policy(SimpleNamespace(auto_policy=SimpleNamespace())))
        self.assertIs(controller._auto_phase_policy(SimpleNamespace(auto_policy=SimpleNamespace(phase=phase_policy))), phase_policy)

        self.assertIsNone(controller._auto_phase_metric_surplus_watts(SimpleNamespace()))
        self.assertIsNone(controller._auto_phase_metric_surplus_watts(SimpleNamespace(_last_auto_metrics=[])))
        self.assertIsNone(controller._auto_phase_metric_surplus_watts(SimpleNamespace(_last_auto_metrics={})))
        self.assertIsNone(controller._auto_phase_metric_surplus_watts(SimpleNamespace(_last_auto_metrics={"surplus": "bad"})))
        self.assertEqual(controller._auto_phase_metric_surplus_watts(SimpleNamespace(_last_auto_metrics={"surplus": 0.0})), 0.0)

    def test_auto_phase_policy_defaults_treat_missing_flags_as_enabled(self):
        controller = UpdateCycleController(
            _auto_phase_service(),
            _phase_values,
            lambda reason: {"init": 0}.get(reason, 99),
        )
        policy = SimpleNamespace()

        self.assertIsNone(
            controller._auto_phase_policy_state(
                SimpleNamespace(auto_policy=SimpleNamespace(phase=policy)),
                ("P1", "P1_P2"),
            )
        )
        self.assertEqual(
            controller._idle_auto_phase_target(policy, ("P1", "P1_P2"), "P1_P2", False, False),
            ("P1", "idle-lowest-phase", None),
        )

    def test_auto_phase_voltage_and_min_surplus_helpers_cover_line_voltage_edges(self):
        service = _auto_phase_service(min_current=6.0, voltage_mode="line")
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        phase_voltage = controller._phase_selection_voltage(service, "P1_P2_P3", 400.0)
        self.assertAlmostEqual(phase_voltage, 400.0 / math.sqrt(3.0))
        self.assertIsNone(controller._phase_selection_voltage(service, "P1_P2_P3", 0.0))
        self.assertIsNone(controller._phase_selection_voltage(service, "P1_P2_P3", -1.0))
        self.assertEqual(controller._phase_selection_voltage(service, "P1", 0.5), 0.5)

        expected_three_phase_min = 6.0 * (400.0 / math.sqrt(3.0)) * 3.0
        self.assertAlmostEqual(
            controller._phase_selection_min_surplus_watts(service, "P1_P2_P3", 400.0),
            expected_three_phase_min,
        )
        service.min_current = "bad"
        self.assertIsNone(controller._phase_selection_min_surplus_watts(service, "P1_P2_P3", 400.0))
        self.assertEqual(controller._phase_selection_voltage(SimpleNamespace(), "P1_P2_P3", 400.0), 400.0)
        self.assertEqual(
            controller._phase_selection_min_surplus_watts(
                _auto_phase_service(min_current=0.5, voltage_mode="phase"),
                "P1",
                230.0,
            ),
            115.0,
        )
        self.assertIsNone(
            controller._phase_selection_min_surplus_watts(SimpleNamespace(voltage_mode="phase"), "P1", 230.0)
        )

    def test_auto_phase_upshift_helpers_cover_thresholds_and_block_reasons(self):
        service = _auto_phase_service(min_current=6.0, voltage_mode="phase")
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        policy = service.auto_policy.phase
        policy.upshift_headroom_watts = 123.0

        self.assertIsNone(
            controller._upshift_auto_phase_target(service, policy, ("P1", "P1_P2"), 1, "P1_P2", 9999.0, 230.0, 100.0)
        )
        self.assertIsNone(
            controller._upshift_auto_phase_target(service, policy, ("P1", "P1_P2"), 0, "P1", 2882.9, 230.0, 100.0)
        )
        self.assertEqual(
            controller._phase_upshift_threshold(service, policy, "P1_P2", 230.0),
            2883.0,
        )
        self.assertEqual(
            controller._phase_upshift_threshold(service, SimpleNamespace(), "P1_P2", 230.0),
            3010.0,
        )
        self.assertEqual(
            controller._upshift_auto_phase_target(service, policy, ("P1", "P1_P2"), 0, "P1", 2883.0, 230.0, 100.0),
            ("P1_P2", "phase-upshift", 2883.0),
        )

        with patch.object(UpdateCycleController, "_phase_switch_lockout_active", return_value=True) as lockout:
            self.assertEqual(
                controller._phase_upshift_block_reason(service, "P1", "P1_P2", 100.0),
                "phase-upshift-blocked-lockout",
            )
        lockout.assert_called_once_with(service, 100.0, "P1_P2")

        with patch.object(UpdateCycleController, "_phase_switch_lockout_active", return_value=False), patch.object(
            UpdateCycleController,
            "_phase_switch_mismatch_retry_active",
            return_value=True,
        ) as mismatch:
            self.assertEqual(
                controller._phase_upshift_block_reason(service, "P1", "P1_P2", 101.0),
                "phase-upshift-blocked-mismatch",
            )
        mismatch.assert_called_once_with(service, "P1", "P1_P2", 101.0)

        with patch.object(UpdateCycleController, "_phase_switch_lockout_active", return_value=True):
            self.assertEqual(
                controller._upshift_auto_phase_target(service, policy, ("P1", "P1_P2"), 0, "P1", 2883.0, 230.0, 102.0),
                (None, "phase-upshift-blocked-lockout", 2883.0),
            )

    def test_auto_phase_target_helpers_delegate_complete_contract_arguments(self):
        service = _auto_phase_service()
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        policy = service.auto_policy.phase

        with patch.object(UpdateCycleController, "_auto_phase_policy_state", return_value=None), patch.object(
            UpdateCycleController,
            "_auto_phase_policy",
            return_value=policy,
        ), patch.object(
            UpdateCycleController,
            "_idle_auto_phase_target",
            return_value=None,
        ) as idle_target, patch.object(
            UpdateCycleController,
            "_surplus_auto_phase_target",
            return_value=("P1_P2", "phase-upshift", 2883.0),
        ) as surplus_target:
            self.assertEqual(
                controller._auto_phase_target_selection(service, ("P1", "P1_P2"), "P1", True, False, 230.0, 111.0),
                ("P1_P2", "phase-upshift", 2883.0),
            )
        idle_target.assert_called_once_with(policy, ("P1", "P1_P2"), "P1", True, False)
        surplus_target.assert_called_once_with(service, policy, ("P1", "P1_P2"), "P1", 230.0, 111.0)

        service._last_auto_metrics = {"surplus": 2883.0}
        with patch.object(UpdateCycleController, "_upshift_auto_phase_target", return_value=None) as upshift, patch.object(
            UpdateCycleController,
            "_downshift_auto_phase_target",
            return_value=("P1", "phase-downshift", 2610.0),
        ) as downshift:
            self.assertEqual(
                controller._surplus_auto_phase_target(service, policy, ("P1", "P1_P2"), "P1_P2", 230.0, 112.0),
                ("P1", "phase-downshift", 2610.0),
            )
        upshift.assert_called_once_with(service, policy, ("P1", "P1_P2"), 1, "P1_P2", 2883.0, 230.0, 112.0)
        downshift.assert_called_once_with(service, policy, ("P1", "P1_P2"), "P1_P2", 1, 2883.0, 230.0)

        with patch.object(UpdateCycleController, "_phase_upshift_threshold", return_value=2883.0) as threshold, patch.object(
            UpdateCycleController,
            "_phase_upshift_block_reason",
            return_value=None,
        ) as block_reason:
            self.assertEqual(
                controller._upshift_auto_phase_target(service, policy, ("P1", "P1_P2"), 0, "P1", 2883.0, 230.0, 113.0),
                ("P1_P2", "phase-upshift", 2883.0),
            )
        threshold.assert_called_once_with(service, policy, "P1_P2", 230.0)
        block_reason.assert_called_once_with(service, "P1", "P1_P2", 113.0)

    def test_auto_phase_target_selection_delegates_policy_idle_and_surplus_paths(self):
        service = _auto_phase_service()
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        service.auto_policy.phase.enabled = False
        self.assertEqual(
            controller._auto_phase_target_selection(service, ("P1", "P1_P2"), "P1", True, True, 230.0, 100.0),
            (None, "phase-policy-disabled", None),
        )

        service.auto_policy.phase.enabled = True
        self.assertEqual(
            controller._auto_phase_target_selection(service, ("P1", "P1_P2"), "P1_P2", False, False, 230.0, 100.0),
            ("P1", "idle-lowest-phase", None),
        )

        service.auto_policy.phase.prefer_lowest_phase_when_idle = False
        self.assertEqual(
            controller._auto_phase_target_selection(service, ("P1", "P1_P2"), "P1_P2", False, False, 230.0, 100.0),
            (None, "idle-hold-phase", None),
        )

        service._last_auto_metrics = {"surplus": 3010.0}
        self.assertEqual(
            controller._auto_phase_target_selection(service, ("P1", "P1_P2"), "P1", True, False, 230.0, 100.0),
            ("P1_P2", "phase-upshift", 3010.0),
        )

    def test_surplus_auto_phase_target_covers_missing_hold_upshift_and_downshift(self):
        service = _auto_phase_service()
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        policy = service.auto_policy.phase

        service._last_auto_metrics = {}
        self.assertEqual(
            controller._surplus_auto_phase_target(service, policy, ("P1", "P1_P2"), "P1", 230.0, 100.0),
            (None, "phase-surplus-missing", None),
        )
        service._last_auto_metrics = {"surplus": 2900.0}
        self.assertEqual(
            controller._surplus_auto_phase_target(service, policy, ("P1", "P1_P2"), "P1", 230.0, 100.0),
            (None, "phase-hold", None),
        )
        service._last_auto_metrics = {"surplus": 3010.0}
        self.assertEqual(
            controller._surplus_auto_phase_target(service, policy, ("P1", "P1_P2"), "P1", 230.0, 100.0),
            ("P1_P2", "phase-upshift", 3010.0),
        )
        service._last_auto_metrics = {"surplus": 2609.9}
        self.assertEqual(
            controller._surplus_auto_phase_target(service, policy, ("P1", "P1_P2"), "P1_P2", 230.0, 100.0),
            ("P1", "phase-downshift", 2610.0),
        )

    def test_phase_switch_waiting_and_stabilizing_helpers_cover_remaining_branches(self):
        service = _auto_phase_service(
            _phase_switch_pending_selection="P1_P2",
            _phase_switch_state="waiting-relay-off",
            _phase_switch_requested_at=98.0,
            _phase_switch_resume_relay=True,
            _save_runtime_state=MagicMock(),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        service._peek_pending_relay_command.return_value = (True, 99.0)
        self.assertFalse(controller._phase_switch_waiting_ready(service, False, True, 100.0))
        self.assertEqual(
            controller._orchestrate_waiting_phase_switch(service, "P1_P2", False, 10.0, 2.0, True, 100.0, False),
            (False, 10.0, 2.0, True, False),
        )

        service._peek_pending_relay_command.return_value = (None, None)
        service._apply_phase_selection = MagicMock(side_effect=RuntimeError("apply failed"))
        result = controller._orchestrate_waiting_phase_switch(service, "P1_P2", False, 11.0, 3.0, True, 101.0, False)
        self.assertEqual(result[-1], None)
        self.assertEqual(service.requested_phase_selection, "P1")
        service._mark_failure.assert_called()
        service._warning_throttled.assert_called()

        service._phase_switch_state = "stabilizing"
        service._phase_switch_pending_selection = "P1_P2"
        service._phase_switch_stable_until = 120.0
        self.assertEqual(
            controller._orchestrate_stabilizing_phase_switch(service, "P1_P2", {}, False, 0.0, 0.0, False, 110.0, False),
            (False, 0.0, 0.0, False, False),
        )

        service._phase_switch_stable_until = 100.0
        service._phase_switch_lockout_selection = "P1_P2"
        with patch.object(controller, "_resume_after_phase_switch_pause", return_value=(True, 0.0, 0.0, False)) as resume_mock:
            result = controller._orchestrate_stabilizing_phase_switch(
                service,
                "P1_P2",
                {"_phase_selection": "P1_P2"},
                False,
                0.0,
                0.0,
                False,
                121.0,
                False,
            )
        self.assertEqual(result, (True, 0.0, 0.0, False, None))
        resume_mock.assert_called_once()
        self.assertIsNone(service._phase_switch_lockout_selection)

    def test_relay_decision_failure_records_charger_transport_retry(self):
        service = SimpleNamespace(
            _charger_backend=SimpleNamespace(set_enabled=MagicMock()),
            _last_charger_transport_reason=None,
            _last_charger_transport_source=None,
            _last_charger_transport_detail=None,
            _last_charger_transport_at=None,
            _charger_retry_reason=None,
            _charger_retry_source=None,
            _source_retry_after={},
            auto_shelly_soft_fail_seconds=10.0,
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )

        UpdateCycleController._handle_relay_decision_failure(service, ModbusSlaveOfflineError("offline"))

        self.assertEqual(service._last_charger_transport_reason, "offline")
        self.assertEqual(service._last_charger_transport_source, "enable")
        self.assertEqual(service._charger_retry_reason, "offline")
        self.assertEqual(service._charger_retry_source, "enable")
        self.assertIn("charger", service._source_retry_after)

    def test_normalize_learned_charge_power_state_falls_back_to_unknown_for_invalid_values(self):
        self.assertEqual(UpdateCycleController._normalize_learned_charge_power_state("weird"), "unknown")

    def test_software_update_check_marks_update_available_from_manifest_hash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bootstrap_state = Path(temp_dir) / ".bootstrap-state"
            bootstrap_state.mkdir(parents=True, exist_ok=True)
            (bootstrap_state / "installed_bundle_sha256").write_text("oldhash\n", encoding="utf-8")
            (bootstrap_state / "installed_version").write_text("1.2.3\n", encoding="utf-8")
            service = self._software_update_service(temp_dir)
            controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
            response = MagicMock()
            response.raise_for_status.return_value = None
            response.json.return_value = {"version": "1.2.4", "bundle_sha256": "newhash"}

            with patch("venus_evcharger.update.controller.requests.get", return_value=response) as mock_get:
                controller._run_software_update_check(service, 100.0)

            mock_get.assert_called_once_with(
                "https://example.invalid/bootstrap_manifest.json",
                timeout=UpdateCycleController.SOFTWARE_UPDATE_REQUEST_TIMEOUT_SECONDS,
            )
            self.assertEqual(service._software_update_state, "available")
            self.assertTrue(service._software_update_available)
            self.assertEqual(service._software_update_available_version, "1.2.4")
            self.assertEqual(service._software_update_current_version, "1.2.3")
            self.assertEqual(service._software_update_detail, "manifest")
            self.assertEqual(service._software_update_last_check_at, 100.0)
            self.assertEqual(
                service._software_update_next_check_at,
                100.0 + UpdateCycleController.SOFTWARE_UPDATE_CHECK_INTERVAL_SECONDS,
            )

    def test_software_update_helper_methods_cover_text_and_state_branches(self) -> None:
        from unittest.mock import mock_open

        file_payload = mock_open(read_data="  mocked-version \n")
        with patch("venus_evcharger.update.software_update_state.open", file_payload, create=True):
            self.assertEqual(UpdateCycleController._read_text_file("/tmp/version.txt"), "mocked-version")
        file_payload.assert_called_once_with("/tmp/version.txt", "r", encoding="utf-8")

        with tempfile.TemporaryDirectory() as temp_dir:
            bootstrap_state = Path(temp_dir) / ".bootstrap-state"
            bootstrap_state.mkdir(parents=True, exist_ok=True)
            (bootstrap_state / "installed_version").write_text("2.0.0\nextra\n", encoding="utf-8")
            (bootstrap_state / "installed_bundle_sha256").write_text("abc123  payload with spaces\n", encoding="utf-8")
            service = self._software_update_service(temp_dir)

            self.assertEqual(UpdateCycleController._read_text_file(""), "")
            self.assertEqual(UpdateCycleController._read_text_file(Path(temp_dir) / "missing.txt"), "")
            self.assertEqual(UpdateCycleController._local_software_update_version(service), "2.0.0")
            self.assertEqual(UpdateCycleController._local_installed_bundle_hash(service), "abc123")

            service._software_update_available = False
            service._software_update_last_check_at = None
            self.assertEqual(UpdateCycleController._software_update_state_for_no_update_block(service), "idle")
            service._software_update_last_check_at = 100.0
            self.assertEqual(UpdateCycleController._software_update_state_for_no_update_block(service), "up-to-date")
            service._software_update_available = True
            self.assertEqual(UpdateCycleController._software_update_state_for_no_update_block(service), "available-blocked")

            UpdateCycleController._set_software_update_state(
                service,
                "available",
                detail="detail",
                available=True,
                available_version="2.0.1",
                last_result="success",
            )
            self.assertEqual(service._software_update_state, "available")
            self.assertEqual(service._software_update_detail, "detail")
            self.assertTrue(service._software_update_available)
            self.assertEqual(service._software_update_available_version, "2.0.1")
            self.assertEqual(service._software_update_last_result, "success")

    def test_software_update_local_sources_cover_fallback_and_empty_layouts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            service = self._software_update_service(temp_dir)

            self.assertEqual(UpdateCycleController._local_software_update_version(service), "")
            self.assertEqual(UpdateCycleController._local_installed_bundle_hash(service), "")
            self.assertFalse(UpdateCycleController._software_update_no_update_active(service))

            (repo_root / "version.txt").write_text(" 3.1.0 \nignored\n", encoding="utf-8")
            self.assertEqual(UpdateCycleController._local_software_update_version(service), "3.1.0")

            (repo_root / "noUpdate").write_text("", encoding="utf-8")
            UpdateCycleController._refresh_software_update_local_state(service)
            self.assertEqual(service._software_update_current_version, "3.1.0")
            self.assertEqual(service._software_update_no_update_active, 1)

            service_without_paths = SimpleNamespace()
            self.assertEqual(UpdateCycleController._local_software_update_version(service_without_paths), "")
            self.assertEqual(UpdateCycleController._local_installed_bundle_hash(service_without_paths), "")
            self.assertFalse(UpdateCycleController._software_update_no_update_active(service_without_paths))
            self.assertEqual(
                UpdateCycleController._software_update_text_attr(
                    SimpleNamespace(software_update_repo_root=None),
                    "software_update_repo_root",
                ),
                "",
            )

            empty_root_service = self._software_update_service(
                "",
                software_update_repo_root="",
                software_update_no_update_file="",
            )
            with patch.object(UpdateCycleController, "_read_text_file", return_value="unexpected") as read_mock:
                self.assertEqual(UpdateCycleController._local_software_update_version(empty_root_service), "")
                self.assertEqual(UpdateCycleController._local_installed_bundle_hash(empty_root_service), "")
            read_mock.assert_not_called()

            with patch("venus_evcharger.update.software_update_state.os.path.isfile", return_value=True) as isfile_mock:
                self.assertFalse(UpdateCycleController._software_update_no_update_active(SimpleNamespace()))
            isfile_mock.assert_not_called()

    def test_software_update_contract_helpers_cover_payload_and_availability_edges(self) -> None:
        service = self._software_update_service("", _software_update_available=True, _software_update_last_check_at=10.0)

        self.assertEqual(UpdateCycleController._software_update_payload_value({"version": " 1.2.3 \n"}, "version"), "1.2.3")
        self.assertEqual(UpdateCycleController._software_update_payload_value({"version": None}, "version"), "")
        self.assertEqual(UpdateCycleController._software_update_payload_value({"version": 123}, "version"), "123")
        self.assertEqual(UpdateCycleController._software_update_payload_value({}, "missing"), "")

        self.assertFalse(UpdateCycleController._software_update_manifest_available("1.2.3", "same", "1.2.2", "same"))
        self.assertTrue(UpdateCycleController._software_update_manifest_available("1.2.3", "new", "1.2.3", "old"))
        self.assertFalse(UpdateCycleController._software_update_manifest_available("1.2.3", "new", "1.2.3", ""))
        self.assertFalse(UpdateCycleController._software_update_manifest_available("1.2.3", "", "1.2.3", ""))
        self.assertTrue(UpdateCycleController._software_update_manifest_available("1.2.4", "", "1.2.3", ""))
        self.assertFalse(UpdateCycleController._software_update_manifest_available("", "", "1.2.3", ""))

        with patch.object(UpdateCycleController, "_software_update_no_update_active", return_value=False):
            self.assertEqual(UpdateCycleController._software_update_availability_state(service, True), "available")
            self.assertEqual(UpdateCycleController._software_update_availability_state(service, False), "up-to-date")

        self.assertEqual(UpdateCycleController._software_update_state_for_no_update_block(SimpleNamespace()), "idle")

        service._software_update_state = "old"
        service._software_update_detail = "keep-detail"
        service._software_update_available = True
        service._software_update_available_version = "old-version"
        service._software_update_last_result = "success"
        UpdateCycleController._set_software_update_state(service, "checking")
        self.assertEqual(service._software_update_state, "checking")
        self.assertEqual(service._software_update_detail, "")
        self.assertTrue(service._software_update_available)
        self.assertEqual(service._software_update_available_version, "old-version")
        self.assertEqual(service._software_update_last_result, "success")

    def test_software_update_check_sources_are_trimmed_and_include_installed_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            bootstrap_state = repo_root / ".bootstrap-state"
            bootstrap_state.mkdir(parents=True, exist_ok=True)
            (bootstrap_state / "installed_bundle_sha256").write_text(" hash-value  archive.zip\n", encoding="utf-8")
            service = self._software_update_service(
                temp_dir,
                software_update_manifest_source="  https://example.invalid/manifest.json  ",
                software_update_version_source="\nhttps://example.invalid/version.txt\n",
                _software_update_current_version=42,
            )

            self.assertEqual(
                UpdateCycleController._software_update_check_sources(service),
                (
                    "https://example.invalid/manifest.json",
                    "https://example.invalid/version.txt",
                    "42",
                    "hash-value",
                ),
            )
            self.assertEqual(
                UpdateCycleController._software_update_check_sources(SimpleNamespace()),
                ("", "", "", ""),
            )

    def test_software_update_check_calls_remote_helpers_with_normalized_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            bootstrap_state = repo_root / ".bootstrap-state"
            bootstrap_state.mkdir(parents=True, exist_ok=True)
            (bootstrap_state / "installed_bundle_sha256").write_text("old-hash payload\n", encoding="utf-8")
            (repo_root / "version.txt").write_text("1.2.3\n", encoding="utf-8")
            service = self._software_update_service(temp_dir)
            original_set_state = UpdateCycleController._set_software_update_state
            controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

            with patch.object(
                UpdateCycleController,
                "_set_software_update_state",
                side_effect=original_set_state,
            ) as set_state_mock, patch.object(
                UpdateCycleController,
                "_software_update_manifest_result",
                return_value=("", False, "manifest-empty"),
            ) as manifest_mock, patch.object(
                UpdateCycleController,
                "_software_update_version_result",
                return_value=("1.2.4", True, "version-file"),
            ) as version_mock:
                controller._run_software_update_check(service, 100.0)

            self.assertEqual(set_state_mock.call_args_list[0].args[:2], (service, "checking"))
            self.assertEqual(set_state_mock.call_args_list[0].kwargs, {"detail": ""})
            manifest_mock.assert_called_once_with(
                "https://example.invalid/bootstrap_manifest.json",
                "1.2.3",
                "old-hash",
            )
            version_mock.assert_called_once_with("https://example.invalid/version.txt", "1.2.3")
            self.assertEqual(service._software_update_state, "available")
            self.assertEqual(service._software_update_available_version, "1.2.4")

    def test_software_update_check_covers_version_source_and_failure_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            (repo_root / "version.txt").write_text("1.2.3\n", encoding="utf-8")
            service = self._software_update_service(
                temp_dir,
                software_update_manifest_source="",
                software_update_version_source="https://example.invalid/version.txt",
            )
            controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
            response = MagicMock()
            response.raise_for_status.return_value = None
            response.text = "1.2.4\n"

            with patch("venus_evcharger.update.controller.requests.get", return_value=response):
                controller._run_software_update_check(service, 100.0)

            self.assertEqual(service._software_update_state, "available")
            self.assertEqual(service._software_update_available_version, "1.2.4")
            self.assertEqual(service._software_update_detail, "version-file")

            service._software_update_available = True
            service._software_update_available_version = "stale-version"
            service._software_update_next_check_at = 999.0
            with patch("venus_evcharger.update.controller.requests.get", side_effect=RuntimeError("network down")):
                controller._run_software_update_check(service, 120.0)

            self.assertEqual(service._software_update_state, "check-failed")
            self.assertEqual(service._software_update_detail, "network down")
            self.assertFalse(service._software_update_available)
            self.assertEqual(service._software_update_available_version, "")
            self.assertEqual(service._software_update_last_check_at, 120.0)
            self.assertEqual(
                service._software_update_next_check_at,
                120.0 + UpdateCycleController.SOFTWARE_UPDATE_CHECK_INTERVAL_SECONDS,
            )

    def test_software_update_check_covers_up_to_date_blocked_and_no_source_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            (repo_root / "version.txt").write_text("1.2.3\n", encoding="utf-8")
            service = self._software_update_service(
                temp_dir,
                software_update_manifest_source="",
                software_update_version_source="",
                _software_update_available=True,
                _software_update_available_version="old",
                _software_update_detail="old-detail",
            )
            controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

            controller._run_software_update_check(service, 100.0)

            self.assertEqual(service._software_update_state, "up-to-date")
            self.assertFalse(service._software_update_available)
            self.assertEqual(service._software_update_available_version, "")
            self.assertEqual(service._software_update_detail, "")
            self.assertEqual(service._software_update_last_check_at, 100.0)

            (repo_root / "noUpdate").write_text("", encoding="utf-8")
            response = MagicMock()
            response.raise_for_status.return_value = None
            response.json.return_value = {"version": "1.2.4"}
            service.software_update_manifest_source = "https://example.invalid/bootstrap_manifest.json"
            service.software_update_version_source = ""

            with patch("venus_evcharger.update.controller.requests.get", return_value=response):
                controller._run_software_update_check(service, 120.0)

            self.assertEqual(service._software_update_state, "available-blocked")
            self.assertTrue(service._software_update_available)
            self.assertEqual(service._software_update_available_version, "1.2.4")

    def test_software_update_check_uses_manifest_version_without_bundle_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            (repo_root / "version.txt").write_text("1.2.3\n", encoding="utf-8")
            service = self._software_update_service(temp_dir)
            controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
            response = MagicMock()
            response.raise_for_status.return_value = None
            response.json.return_value = {"version": "1.2.4"}

            with patch("venus_evcharger.update.controller.requests.get", return_value=response):
                controller._run_software_update_check(service, 100.0)

            self.assertTrue(service._software_update_available)
            self.assertEqual(service._software_update_available_version, "1.2.4")

    def test_software_update_check_falls_back_to_version_when_manifest_has_no_version(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            (repo_root / "version.txt").write_text("1.2.3\n", encoding="utf-8")
            service = self._software_update_service(temp_dir)
            controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
            manifest_response = MagicMock()
            manifest_response.raise_for_status.return_value = None
            manifest_response.json.return_value = {"bundle_sha256": ""}
            version_response = MagicMock()
            version_response.raise_for_status.return_value = None
            version_response.text = "1.2.5\n"

            with patch(
                "venus_evcharger.update.controller.requests.get",
                side_effect=[manifest_response, version_response],
            ) as get_mock:
                controller._run_software_update_check(service, 100.0)

            self.assertEqual(get_mock.call_count, 2)
            self.assertEqual(service._software_update_state, "available")
            self.assertTrue(service._software_update_available)
            self.assertEqual(service._software_update_available_version, "1.2.5")
            self.assertEqual(service._software_update_detail, "version-file")

    def test_software_update_manifest_and_log_handle_helpers_cover_remaining_edges(self) -> None:
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = ["not-a-dict"]

        with patch("venus_evcharger.update.controller.requests.get", return_value=response):
            self.assertEqual(
                UpdateCycleController._software_update_manifest_result(
                    "https://example.invalid/bootstrap_manifest.json",
                    "1.2.3",
                    "",
                ),
                ("", False, ""),
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            relative_log_path = str(Path(temp_dir) / "nested" / "software-update.log")
            log_handle = UpdateCycleController._software_update_log_handle(relative_log_path)
            log_handle.close()
            self.assertTrue((Path(temp_dir) / "nested").is_dir())

        UpdateCycleController._close_open_log_handle(None)
        service = self._software_update_service("/tmp")
        service._software_update_process_log_handle = None
        UpdateCycleController._close_software_update_log_handle(service)
        with patch.object(UpdateCycleController, "_software_update_no_update_active", return_value=True):
            self.assertEqual(
                UpdateCycleController._software_update_availability_state(service, True),
                "available-blocked",
            )

        self.assertEqual(
            UpdateCycleController._software_update_command("/data/venus-evcharger", "/tmp/restart.sh"),
            ["/bin/bash", "-lc", 'cd "/data/venus-evcharger" && "./install.sh" && "/tmp/restart.sh"'],
        )
        self.assertTrue(UpdateCycleController._software_update_due(100.0, 99.9))
        self.assertTrue(UpdateCycleController._software_update_due(100.0, 100.0))
        self.assertFalse(UpdateCycleController._software_update_due(100.0, 100.1))
        self.assertFalse(UpdateCycleController._software_update_due(100.0, "100"))

    def test_software_update_run_paths_and_completion_contracts_cover_edges(self) -> None:
        service = self._software_update_service(
            "",
            software_update_install_script=None,
            software_update_repo_root=None,
            software_update_restart_script=None,
            _software_update_available=True,
            _software_update_available_version="9.9.9",
        )

        self.assertEqual(UpdateCycleController._software_update_run_paths(service), ("", "", ""))
        self.assertEqual(
            UpdateCycleController._completed_software_update_state(service, 0),
            ("installed", "completed", False, "", "success"),
        )
        self.assertEqual(
            UpdateCycleController._completed_software_update_state(service, 7),
            ("install-failed", "exit 7", True, "9.9.9", "failed"),
        )

        service._software_update_run_requested_at = 50.0
        UpdateCycleController._software_update_mark_unavailable(service, "missing")
        self.assertEqual(service._software_update_state, "update-unavailable")
        self.assertEqual(service._software_update_detail, "missing")
        self.assertEqual(service._software_update_last_result, "failed")
        self.assertIsNone(service._software_update_run_requested_at)

        service._software_update_run_requested_at = 60.0
        service._software_update_last_result = "running"
        UpdateCycleController._software_update_mark_install_failed(service, RuntimeError("boom"))
        self.assertEqual(service._software_update_state, "install-failed")
        self.assertEqual(service._software_update_detail, "boom")
        self.assertEqual(service._software_update_last_result, "failed")
        self.assertIsNone(service._software_update_run_requested_at)

    def test_software_update_launch_uses_explicit_paths_and_records_process_state(self) -> None:
        service = self._software_update_service(
            "/repo",
            software_update_log_path="/tmp/update.log",
            _software_update_run_requested_at=77.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        process = MagicMock()
        log_handle = MagicMock()

        with patch.object(
            UpdateCycleController,
            "_spawn_software_update_process",
            return_value=(process, log_handle),
        ) as spawn_mock:
            self.assertTrue(controller._launch_software_update_run(service, ("/repo", "/restart.sh"), 123.0, "manual"))

        spawn_mock.assert_called_once_with("/tmp/update.log", "/repo", "/restart.sh")
        self.assertIs(service._software_update_process, process)
        self.assertIs(service._software_update_process_log_handle, log_handle)
        self.assertEqual(service._software_update_last_run_at, 123.0)
        self.assertIsNone(service._software_update_run_requested_at)
        self.assertEqual(service._software_update_state, "running")
        self.assertEqual(service._software_update_detail, "manual")
        self.assertEqual(service._software_update_last_result, "running")

    def test_software_update_run_and_poll_cover_process_lifecycle_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            (repo_root / "install.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            restart_dir = repo_root / "deploy" / "venus"
            restart_dir.mkdir(parents=True, exist_ok=True)
            (restart_dir / "restart_venus_evcharger_service.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            service = self._software_update_service(temp_dir)
            controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

            active_process = MagicMock()
            service._software_update_process = active_process
            self.assertFalse(controller._start_software_update_run(service, 100.0, "manual"))
            self.assertIsNone(service._software_update_run_requested_at)

            service._software_update_process = None
            service.software_update_install_script = str(repo_root / "missing-install.sh")
            self.assertFalse(controller._start_software_update_run(service, 100.0, "manual"))
            self.assertEqual(service._software_update_state, "update-unavailable")
            self.assertEqual(service._software_update_detail, "install.sh missing")

            service.software_update_install_script = str(repo_root / "install.sh")
            fake_process = MagicMock()
            with patch("venus_evcharger.update.controller.subprocess.Popen", return_value=fake_process) as popen_mock:
                self.assertTrue(controller._start_software_update_run(service, 130.0, "manual"))

            popen_mock.assert_called_once()
            self.assertIs(service._software_update_process, fake_process)
            self.assertEqual(service._software_update_state, "running")
            self.assertEqual(service._software_update_detail, "manual")
            self.assertEqual(service._software_update_last_run_at, 130.0)
            log_handle = service._software_update_process_log_handle

            service._software_update_process = fake_process
            fake_process.poll.return_value = None
            controller._poll_software_update_process(service)
            self.assertIs(service._software_update_process, fake_process)

            service._software_update_available = True
            service._software_update_available_version = "stale-success-version"
            service._software_update_last_result = "running"
            fake_process.poll.return_value = 0
            controller._poll_software_update_process(service)
            self.assertIsNone(service._software_update_process)
            self.assertIsNone(service._software_update_process_log_handle)
            self.assertEqual(service._software_update_state, "installed")
            self.assertFalse(service._software_update_available)
            self.assertEqual(service._software_update_available_version, "")
            self.assertEqual(service._software_update_last_result, "success")

            failing_process = MagicMock()
            failing_process.poll.return_value = 9
            failing_log = MagicMock()
            service._software_update_process = failing_process
            service._software_update_process_log_handle = failing_log
            service._software_update_available = True
            service._software_update_available_version = "9.9.9"
            service._software_update_last_result = "running"
            controller._poll_software_update_process(service)
            failing_log.close.assert_called_once_with()
            self.assertIsNone(service._software_update_process)
            self.assertIsNone(service._software_update_process_log_handle)
            self.assertEqual(service._software_update_state, "install-failed")
            self.assertEqual(service._software_update_detail, "exit 9")
            self.assertTrue(service._software_update_available)
            self.assertEqual(service._software_update_available_version, "9.9.9")
            self.assertEqual(service._software_update_last_result, "failed")
            if log_handle is not None and hasattr(log_handle, "close"):
                log_handle.close()

    def test_software_update_housekeeping_due_helpers_cover_non_due_and_running_states(self) -> None:
        service = self._software_update_service(
            "",
            _software_update_next_check_at=200.0,
            _software_update_boot_auto_due_at=200.0,
            _software_update_run_requested_at=None,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with patch.object(UpdateCycleController, "_run_software_update_check") as check_mock:
            controller._software_update_run_due_check(service, 100.0)
        check_mock.assert_not_called()

        with patch.object(UpdateCycleController, "_start_software_update_run") as start_mock:
            controller._software_update_run_due_boot_update(service, 100.0)
            controller._software_update_run_due_manual_trigger(service, 100.0)
        start_mock.assert_not_called()

        service._software_update_run_requested_at = 90.0
        service._software_update_boot_auto_due_at = 90.0
        controller._clear_software_update_triggers_while_running(service, 100.0, False)
        self.assertEqual(service._software_update_run_requested_at, 90.0)
        self.assertEqual(service._software_update_boot_auto_due_at, 90.0)

        controller._clear_software_update_triggers_while_running(service, 100.0, True)
        self.assertIsNone(service._software_update_run_requested_at)
        self.assertIsNone(service._software_update_boot_auto_due_at)

    def test_software_update_housekeeping_invokes_linear_steps_with_service(self) -> None:
        service = self._software_update_service("", _software_update_next_check_at=999.0)
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with patch.object(UpdateCycleController, "_poll_software_update_process") as poll_mock, patch.object(
            UpdateCycleController,
            "_refresh_software_update_local_state",
        ) as refresh_mock, patch.object(
            UpdateCycleController,
            "_clear_software_update_triggers_while_running",
        ) as clear_mock, patch.object(
            UpdateCycleController,
            "_software_update_run_due_check",
        ) as check_mock, patch.object(
            UpdateCycleController,
            "_software_update_run_due_boot_update",
        ) as boot_mock, patch.object(
            UpdateCycleController,
            "_software_update_run_due_manual_trigger",
        ) as manual_mock:
            controller._software_update_housekeeping(service, 222.0)

        poll_mock.assert_called_once_with(service)
        refresh_mock.assert_called_once_with(service)
        clear_mock.assert_called_once_with(service, 222.0, False)
        check_mock.assert_called_once_with(service, 222.0)
        boot_mock.assert_called_once_with(service, 222.0)
        manual_mock.assert_called_once_with(service, 222.0)

    def test_initialize_software_update_runtime_state_contract_sets_ram_only_defaults(self) -> None:
        import os

        from venus_evcharger.runtime.software_update_setup import (
            _default_version_source,
            _software_update_env,
            _software_update_repo_path,
            initialize_software_update_runtime_state,
        )

        self.assertEqual(_software_update_repo_path("", "install.sh"), "")
        self.assertEqual(_software_update_repo_path("/repo", "deploy", "restart.sh"), "/repo/deploy/restart.sh")
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_software_update_env("VENUS_EVCHARGER_CHANNEL", "main"), "main")
        with patch.dict(os.environ, {"VENUS_EVCHARGER_CHANNEL": "testing"}, clear=True):
            self.assertEqual(_software_update_env("VENUS_EVCHARGER_CHANNEL", "main"), "testing")
        self.assertEqual(
            _default_version_source("owner/project", "stable"),
            "https://raw.githubusercontent.com/owner/project/stable/version.txt",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            (repo_root / "noUpdate").write_text("", encoding="utf-8")
            service = SimpleNamespace()

            with patch.dict(
                os.environ,
                {
                    "VENUS_EVCHARGER_REPO_SLUG": "owner/project",
                    "VENUS_EVCHARGER_CHANNEL": "stable",
                    "VENUS_EVCHARGER_MANIFEST_SOURCE": "https://example.invalid/manifest.json",
                    "VENUS_EVCHARGER_VERSION_SOURCE": "https://example.invalid/version.txt",
                },
                clear=False,
            ):
                initialize_software_update_runtime_state(
                    service,
                    repo_root=str(repo_root),
                    started_at=123.0,
                    current_version="4.5.6",
                    boot_auto_due_at=456.0,
                )

            self.assertEqual(service.started_at, 123.0)
            self.assertEqual(service.software_update_repo_root, str(repo_root))
            self.assertEqual(service.software_update_install_script, str(repo_root / "install.sh"))
            self.assertEqual(
                service.software_update_restart_script,
                str(repo_root / "deploy/venus/restart_venus_evcharger_service.sh"),
            )
            self.assertEqual(service.software_update_no_update_file, str(repo_root / "noUpdate"))
            self.assertEqual(
                service.software_update_log_path,
                "/var/volatile/log/dbus-venus-evcharger/software-update.log",
            )
            self.assertEqual(service.software_update_repo_slug, "owner/project")
            self.assertEqual(service.software_update_channel, "stable")
            self.assertEqual(service.software_update_manifest_source, "https://example.invalid/manifest.json")
            self.assertEqual(service.software_update_version_source, "https://example.invalid/version.txt")
            self.assertEqual(service._software_update_current_version, "4.5.6")
            self.assertEqual(service._software_update_available_version, "")
            self.assertIs(service._software_update_available, False)
            self.assertEqual(service._software_update_state, "idle")
            self.assertEqual(service._software_update_detail, "")
            self.assertIsNone(service._software_update_last_check_at)
            self.assertIsNone(service._software_update_last_run_at)
            self.assertEqual(service._software_update_last_result, "")
            self.assertEqual(service._software_update_no_update_active, 1)
            self.assertEqual(service._software_update_next_check_at, 423.0)
            self.assertEqual(service._software_update_boot_auto_due_at, 456.0)
            self.assertIsNone(service._software_update_process)
            self.assertIsNone(service._software_update_process_log_handle)
            self.assertIsNone(service._software_update_run_requested_at)

            empty_service = SimpleNamespace()
            with patch.dict(os.environ, {}, clear=True):
                initialize_software_update_runtime_state(
                    empty_service,
                    repo_root="",
                    started_at=200.0,
                    current_version="",
                    boot_auto_due_at=None,
                )

            self.assertEqual(empty_service.started_at, 200.0)
            self.assertEqual(empty_service.software_update_repo_root, "")
            self.assertEqual(empty_service.software_update_install_script, "")
            self.assertEqual(empty_service.software_update_restart_script, "")
            self.assertEqual(empty_service.software_update_no_update_file, "")
            self.assertEqual(
                empty_service.software_update_log_path,
                "/var/volatile/log/dbus-venus-evcharger/software-update.log",
            )
            self.assertEqual(empty_service.software_update_repo_slug, "martinthebrain/venus-evcharger-service")
            self.assertEqual(empty_service.software_update_channel, "main")
            self.assertEqual(empty_service.software_update_manifest_source, "")
            self.assertEqual(
                empty_service.software_update_version_source,
                "https://raw.githubusercontent.com/"
                "martinthebrain/venus-evcharger-service/main/version.txt",
            )
            self.assertEqual(empty_service._software_update_current_version, "")
            self.assertEqual(empty_service._software_update_available_version, "")
            self.assertIs(empty_service._software_update_available, False)
            self.assertEqual(empty_service._software_update_state, "idle")
            self.assertEqual(empty_service._software_update_detail, "")
            self.assertIsNone(empty_service._software_update_last_check_at)
            self.assertIsNone(empty_service._software_update_last_run_at)
            self.assertEqual(empty_service._software_update_last_result, "")
            self.assertIsNone(empty_service._software_update_process)
            self.assertIsNone(empty_service._software_update_process_log_handle)
            self.assertIsNone(empty_service._software_update_run_requested_at)
            self.assertEqual(empty_service._software_update_no_update_active, 0)
            self.assertEqual(empty_service._software_update_next_check_at, 500.0)
            self.assertIsNone(empty_service._software_update_boot_auto_due_at)

            no_marker_root = repo_root / "without-marker"
            no_marker_root.mkdir()
            no_marker_service = SimpleNamespace()
            initialize_software_update_runtime_state(
                no_marker_service,
                repo_root=str(no_marker_root),
                started_at=300.0,
                current_version="1.0.0",
                boot_auto_due_at=None,
            )

            self.assertEqual(no_marker_service.software_update_no_update_file, str(no_marker_root / "noUpdate"))
            self.assertEqual(no_marker_service._software_update_no_update_active, 0)

    def test_software_update_run_and_housekeeping_cover_failure_and_due_check_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            (repo_root / "install.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            restart_dir = repo_root / "deploy" / "venus"
            restart_dir.mkdir(parents=True, exist_ok=True)
            (restart_dir / "restart_venus_evcharger_service.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            service = self._software_update_service(temp_dir, _software_update_next_check_at=100.0)
            controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

            with patch("venus_evcharger.update.controller.subprocess.Popen", side_effect=RuntimeError("spawn failed")):
                self.assertFalse(controller._start_software_update_run(service, 120.0, "manual"))

            self.assertEqual(service._software_update_state, "install-failed")
            self.assertEqual(service._software_update_detail, "spawn failed")

            with patch.object(UpdateCycleController, "_run_software_update_check") as check_mock:
                controller._software_update_housekeeping(service, 120.0)
            check_mock.assert_called_once_with(service, 120.0)

            process = MagicMock()
            process.poll.return_value = 1
            failing_log = MagicMock()
            failing_log.close.side_effect = OSError("close failed")
            service._software_update_process = process
            service._software_update_process_log_handle = failing_log
            controller._poll_software_update_process(service)
            self.assertEqual(service._software_update_state, "install-failed")

    def test_software_update_run_failure_tolerates_log_close_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            (repo_root / "install.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            restart_dir = repo_root / "deploy" / "venus"
            restart_dir.mkdir(parents=True, exist_ok=True)
            (restart_dir / "restart_venus_evcharger_service.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            service = self._software_update_service(temp_dir)
            controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

            fake_log = MagicMock()
            fake_log.close.side_effect = OSError("close failed")
            with patch("builtins.open", return_value=fake_log), patch(
                "venus_evcharger.update.controller.subprocess.Popen",
                side_effect=RuntimeError("spawn failed"),
            ):
                self.assertFalse(controller._start_software_update_run(service, 120.0, "manual"))

            self.assertEqual(service._software_update_state, "install-failed")

    def test_update_cycle_health_helpers_cover_blocking_reason_variants(self) -> None:
        service = SimpleNamespace(
            auto_shelly_soft_fail_seconds=10.0,
            _warning_throttled=MagicMock(),
            _last_charger_transport_source="charger",
            _last_charger_transport_detail="timeout",
            _last_charger_state_status="charging",
            _last_charger_state_fault="fault",
            _last_switch_interlock_ok=False,
            _contactor_fault_counts={
                "contactor-suspected-open": 2,
                "contactor-suspected-welded": 3,
            },
            _contactor_lockout_source="feedback",
            _last_switch_feedback_closed=True,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with patch.object(controller, "charger_health_override", return_value="charger-transport-timeout"):
            self.assertEqual(controller._blocking_charger_health(True, False, 100.0), "charger-transport-timeout")
        with patch.object(controller, "charger_health_override", return_value="charger-fault"):
            self.assertEqual(controller._blocking_charger_health(True, False, 100.0), "charger-fault")
        with patch.object(controller, "charger_health_override", return_value=None):
            self.assertIsNone(controller._blocking_charger_health(True, False, 100.0))

        for reason in (
            "contactor-interlock",
            "contactor-suspected-open",
            "contactor-suspected-welded",
            "contactor-lockout-open",
            "contactor-lockout-welded",
            "switch-feedback-mismatch",
        ):
            with self.subTest(reason=reason):
                with patch.object(controller, "switch_feedback_health_override", return_value=reason):
                    self.assertEqual(
                        controller._blocking_switch_feedback_health(True, True, 2300.0, 10.0, True, 100.0),
                        reason,
                    )

        with patch.object(controller, "switch_feedback_health_override", return_value=None):
            self.assertIsNone(controller._blocking_switch_feedback_health(True, True, 2300.0, 10.0, True, 100.0))

        self.assertTrue(controller._desired_relay_target(service, False, True, None, None, None))
        service._auto_decide_relay = MagicMock(return_value=False)
        self.assertFalse(controller._desired_relay_target(service, True, None, None, None, None))

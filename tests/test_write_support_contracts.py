# SPDX-License-Identifier: GPL-3.0-or-later
"""State and publish invariants for write-controller support primitives."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from venus_evcharger.controllers import write_support as support_module
from venus_evcharger.controllers.write import ControlWriteController


class TestWriteSupportBoundaryContracts(unittest.TestCase):
    def test_snapshot_and_restore_delegate_all_declared_state_categories(self) -> None:
        svc = SimpleNamespace()
        snapshot = {"attrs": {}}
        with patch.object(support_module, "capture_write_state", return_value=snapshot) as capture:
            self.assertIs(ControlWriteController._snapshot_write_state(svc), snapshot)
        capture.assert_called_once_with(
            svc,
            attrs=ControlWriteController.SNAPSHOT_ATTRS,
            deque_attrs=ControlWriteController.SNAPSHOT_DEQUE_ATTRS,
            value_attrs=ControlWriteController.SNAPSHOT_VALUE_ATTRS,
            mapping_attrs=ControlWriteController.SNAPSHOT_MAPPING_ATTRS,
        )
        with patch.object(support_module, "restore_write_state") as restore:
            ControlWriteController._restore_write_state(svc, snapshot)
        restore.assert_called_once_with(svc, snapshot)

    def test_relay_queue_and_explicit_marker_set_irreversible_boundary(self) -> None:
        svc = SimpleNamespace(queue_relay_command=MagicMock())
        controller = ControlWriteController(SimpleNamespace())
        controller._queue_relay_command(svc, True, 10.0)
        svc.queue_relay_command.assert_called_once_with(True, 10.0)
        self.assertIs(controller._external_side_effect_started, True)
        controller._external_side_effect_started = False
        controller._mark_external_side_effect_started()
        self.assertIs(controller._external_side_effect_started, True)

    def test_local_placeholder_publish_logs_exact_failure_and_succeeds_silently(self) -> None:
        success = SimpleNamespace(publish_local_pm_status=MagicMock())
        with patch.object(support_module.logging, "warning") as warning:
            ControlWriteController._publish_local_pm_status_best_effort(success, True, 10.0)
        success.publish_local_pm_status.assert_called_once_with(True, 10.0)
        warning.assert_not_called()

        error = KeyError("dbus")
        failed = SimpleNamespace(publish_local_pm_status=MagicMock(side_effect=error))
        with patch.object(support_module.logging, "warning") as warning:
            ControlWriteController._publish_local_pm_status_best_effort(failed, False, 11.0)
        warning.assert_called_once_with(
            "Local relay placeholder publish failed after queuing relay=%s: %s",
            0,
            error,
            exc_info=error,
        )
        with patch.object(support_module.logging, "warning") as warning:
            ControlWriteController._publish_local_pm_status_best_effort(failed, True, 12.0)
        warning.assert_called_once_with(
            "Local relay placeholder publish failed after queuing relay=%s: %s",
            1,
            error,
            exc_info=error,
        )

    def test_mode_normalization_log_is_emitted_only_for_changed_mode(self) -> None:
        with patch.object(support_module.logging, "info") as log:
            ControlWriteController._log_normalized_mode(1, 1)
            log.assert_not_called()
            ControlWriteController._log_normalized_mode(5, 2)
        log.assert_called_once_with(
            "Unsupported mode %s requested for target mode, normalizing to %s",
            5,
            2,
        )


class TestWriteSupportModeContracts(unittest.TestCase):
    def test_auto_decision_reset_and_cutover_free_activation_set_all_flags(self) -> None:
        svc = SimpleNamespace(
            auto_start_condition_since=1.0,
            auto_stop_condition_since=2.0,
            clear_auto_samples=MagicMock(),
            auto_mode_cutover_pending=True,
            ignore_min_offtime_once=True,
        )
        ControlWriteController._reset_auto_decision_state(svc)
        self.assertIsNone(svc.auto_start_condition_since)
        self.assertIsNone(svc.auto_stop_condition_since)
        svc.clear_auto_samples.assert_called_once_with()
        ControlWriteController._activate_auto_without_cutover(svc)
        self.assertIs(svc.auto_mode_cutover_pending, False)
        self.assertIs(svc.ignore_min_offtime_once, False)

    def test_auto_cutover_queues_off_and_sets_exact_transition_state(self) -> None:
        svc = SimpleNamespace(
            virtual_enable=0,
            virtual_startstop=1,
            auto_mode_cutover_pending=False,
            ignore_min_offtime_once=True,
        )
        controller = ControlWriteController(SimpleNamespace())
        with (
            patch.object(controller, "_queue_relay_command") as queue,
            patch.object(controller, "_publish_local_pm_status_best_effort") as publish,
        ):
            controller._queue_auto_cutover(svc, 10.0)
        queue.assert_called_once_with(svc, False, 10.0)
        publish.assert_called_once_with(svc, False, 10.0)
        self.assertEqual((svc.virtual_enable, svc.virtual_startstop), (1, 0))
        self.assertIs(svc.auto_mode_cutover_pending, True)
        self.assertIs(svc.ignore_min_offtime_once, False)

    def test_transition_to_auto_distinguishes_existing_auto_live_relay_and_off_relay(self) -> None:
        port = SimpleNamespace(
            mode_uses_auto_logic=MagicMock(return_value=True),
            relay_may_be_on_for_cutover=MagicMock(),
            manual_override_until=5.0,
        )
        controller = ControlWriteController(port)
        with patch.object(controller, "_queue_auto_cutover") as queue:
            controller._handle_mode_transition_to_auto(1, 10.0)
        queue.assert_not_called()
        port.relay_may_be_on_for_cutover.assert_not_called()
        self.assertEqual(port.manual_override_until, 5.0)

        port.mode_uses_auto_logic.return_value = False
        port.relay_may_be_on_for_cutover.return_value = True
        with patch.object(controller, "_queue_auto_cutover") as queue:
            controller._handle_mode_transition_to_auto(0, 11.0)
        queue.assert_called_once_with(port, 11.0)
        self.assertEqual(port.manual_override_until, 0.0)

        port.relay_may_be_on_for_cutover.return_value = False
        with patch.object(controller, "_activate_auto_without_cutover") as activate:
            controller._handle_mode_transition_to_auto(0, 12.0)
        activate.assert_called_once_with(port)
        self.assertEqual(port.manual_override_until, 0.0)

    def test_transition_to_manual_transfers_auto_permission_to_relay_target(self) -> None:
        port = SimpleNamespace(
            mode_uses_auto_logic=MagicMock(return_value=True),
            virtual_enable=1,
            virtual_startstop=0,
        )
        controller = ControlWriteController(port)
        with patch.object(controller, "_apply_manual_enable_like_request") as apply:
            controller._handle_mode_transition_to_manual(2, 10.0)
        apply.assert_called_once_with(port, True, 10.0)
        port.mode_uses_auto_logic.assert_called_once_with(2)

        port.virtual_enable = 0
        port.virtual_startstop = 1
        port.mode_uses_auto_logic.reset_mock()
        with patch.object(controller, "_apply_manual_enable_like_request") as apply:
            controller._handle_mode_transition_to_manual(1, 11.0)
        apply.assert_called_once_with(port, False, 11.0)
        port.mode_uses_auto_logic.assert_called_once_with(1)

    def test_transition_to_manual_avoids_redundant_or_non_transition_writes(self) -> None:
        port = SimpleNamespace(
            mode_uses_auto_logic=MagicMock(return_value=True),
            virtual_enable=1,
            virtual_startstop=1,
        )
        controller = ControlWriteController(port)
        with patch.object(controller, "_apply_manual_enable_like_request") as apply:
            controller._handle_mode_transition_to_manual(2, 10.0)
        apply.assert_not_called()

        port.mode_uses_auto_logic.return_value = False
        port.virtual_startstop = 0
        with patch.object(controller, "_apply_manual_enable_like_request") as apply:
            controller._handle_mode_transition_to_manual(0, 11.0)
        apply.assert_not_called()

    def test_worker_snapshot_clears_inputs_outside_auto_and_preserves_them_in_auto(self) -> None:
        snapshot = {"pv_power": 1, "battery_soc": 2, "grid_power": 3}
        svc = SimpleNamespace(
            get_worker_snapshot=MagicMock(return_value=snapshot),
            update_worker_snapshot=MagicMock(),
        )
        ControlWriteController._snapshot_for_mode(svc, 10.0, False)
        ControlWriteController._snapshot_for_mode(svc, 11.0, True)
        self.assertEqual(
            svc.update_worker_snapshot.call_args_list,
            [
                call(captured_at=10.0, auto_mode_active=False, pv_power=None, battery_soc=None, grid_power=None),
                call(captured_at=11.0, auto_mode_active=True, pv_power=1, battery_soc=2, grid_power=3),
            ],
        )

    def test_startstop_values_and_mode_publish_use_exact_auto_semantics(self) -> None:
        svc = SimpleNamespace(
            virtual_enable=1,
            virtual_startstop=0,
            virtual_mode=2,
            mode_uses_auto_logic=MagicMock(return_value=True),
            publish_field=MagicMock(),
        )
        self.assertEqual(ControlWriteController._auto_startstop_value(svc), 1)
        self.assertEqual(ControlWriteController._startstop_value_for_mode(svc, True), 1)
        self.assertEqual(ControlWriteController._startstop_value_for_mode(svc, False), 0)
        ControlWriteController._publish_startstop_enable(svc, 10.0)
        self.assertEqual(
            svc.publish_field.call_args_list,
            [
                call("start_stop", 1, 10.0, force=True),
                call("enable", 1, 10.0, force=True),
            ],
        )
        svc.publish_field.reset_mock()
        ControlWriteController._publish_mode_paths(svc, 11.0, True)
        self.assertEqual(
            svc.publish_field.call_args_list,
            [
                call("mode", 2, 11.0, force=True),
                call("start_stop", 1, 11.0, force=True),
                call("enable", 1, 11.0, force=True),
            ],
        )
        svc.publish_field.reset_mock()
        ControlWriteController._publish_mode_paths(svc, 12.0, False)
        self.assertEqual(
            svc.publish_field.call_args_list,
            [
                call("mode", 2, 12.0, force=True),
                call("start_stop", 0, 12.0, force=True),
                call("enable", 1, 12.0, force=True),
            ],
        )


class TestWriteSupportPhaseContracts(unittest.TestCase):
    def test_supported_phase_text_forwards_lockout_contract_and_joins_in_order(self) -> None:
        svc = SimpleNamespace(
            supported_phase_selections=("P1", "P1_P2"),
            _phase_switch_lockout_selection="P1_P2",
            _phase_switch_lockout_until=20.0,
        )
        with patch.object(
            support_module,
            "effective_supported_phase_selections",
            return_value=("P1",),
        ) as effective:
            self.assertEqual(ControlWriteController._supported_phase_selection_text(svc, 10.0), "P1")
        effective.assert_called_once_with(
            ("P1", "P1_P2"),
            lockout_selection="P1_P2",
            lockout_until=20.0,
            now=10.0,
        )
        empty = SimpleNamespace()
        with patch.object(
            support_module,
            "effective_supported_phase_selections",
            return_value=("P1",),
        ) as effective:
            self.assertEqual(ControlWriteController._supported_phase_selection_text(empty, 12.0), "P1")
        effective.assert_called_once_with(
            ("P1",),
            lockout_selection=None,
            lockout_until=None,
            now=12.0,
        )

    def test_phase_switch_queue_and_publish_paths_set_every_field(self) -> None:
        svc = SimpleNamespace(
            requested_phase_selection="P1",
            active_phase_selection="P1",
            publish_field=MagicMock(),
        )
        ControlWriteController._queue_phase_switch_state(
            svc,
            "P1_P2",
            10.0,
            resume_relay=1,
        )
        self.assertEqual(svc.requested_phase_selection, "P1_P2")
        self.assertEqual(svc._phase_switch_pending_selection, "P1_P2")
        self.assertEqual(svc._phase_switch_state, "waiting-relay-off")
        self.assertEqual(svc._phase_switch_requested_at, 10.0)
        self.assertIsNone(svc._phase_switch_stable_until)
        self.assertIs(svc._phase_switch_resume_relay, True)

        with patch.object(
            ControlWriteController,
            "_supported_phase_selection_text",
            return_value="P1,P1_P2",
        ) as supported:
            ControlWriteController._publish_phase_selection_paths(svc, 11.0)
        supported.assert_called_once_with(svc, 11.0)
        self.assertEqual(
            svc.publish_field.call_args_list,
            [
                call("phase_selection", "P1_P2", 11.0, force=True),
                call("phase_selection_active", "P1", 11.0, force=True),
                call("supported_phase_selections", "P1,P1_P2", 11.0, force=True),
            ],
        )

        default_svc = SimpleNamespace(publish_field=MagicMock())
        with patch.object(
            ControlWriteController,
            "_supported_phase_selection_text",
            return_value="P1",
        ) as supported:
            ControlWriteController._publish_phase_lockout_paths(default_svc, 12.0)
        supported.assert_called_once_with(default_svc, 12.0)
        self.assertEqual(
            default_svc.publish_field.call_args_list[3],
            call("auto_phase_supported_configured", "P1", 12.0, force=True),
        )

    def test_phase_lockout_clear_and_publish_cover_complete_public_state(self) -> None:
        svc = SimpleNamespace(
            supported_phase_selections=("P1", "P1_P2"),
            publish_field=MagicMock(),
        )
        ControlWriteController._clear_phase_lockout_state(svc)
        self.assertEqual(svc._phase_switch_mismatch_counts, {})
        self.assertIs(svc._phase_switch_mismatch_active, False)
        self.assertIsNone(svc._phase_switch_last_mismatch_selection)
        self.assertIsNone(svc._phase_switch_last_mismatch_at)
        self.assertIsNone(svc._phase_switch_lockout_selection)
        self.assertEqual(svc._phase_switch_lockout_reason, "")
        self.assertIsNone(svc._phase_switch_lockout_at)
        self.assertIsNone(svc._phase_switch_lockout_until)

        with patch.object(ControlWriteController, "_supported_phase_selection_text", return_value="P1"):
            ControlWriteController._publish_phase_lockout_paths(svc, 10.0)
        self.assertEqual(
            svc.publish_field.call_args_list,
            [
                call("auto_phase_lockout_active", 0, 10.0, force=True),
                call("auto_phase_lockout_target", "", 10.0, force=True),
                call("auto_phase_lockout_reason", "", 10.0, force=True),
                call("auto_phase_supported_configured", "P1,P1_P2", 10.0, force=True),
                call("auto_phase_supported_effective", "P1", 10.0, force=True),
                call("auto_phase_degraded_active", 1, 10.0, force=True),
                call("auto_phase_lockout_age", -1, 10.0, force=True),
                call("auto_phase_lockout_reset", 0, 10.0, force=True),
            ],
        )


class TestWriteSupportContactorAndManualContracts(unittest.TestCase):
    def test_contactor_clear_and_publish_cover_complete_public_state(self) -> None:
        svc = SimpleNamespace(publish_field=MagicMock())
        ControlWriteController._clear_contactor_lockout_state(svc)
        self.assertEqual(svc._contactor_fault_counts, {})
        for name in (
            "_contactor_fault_active_reason",
            "_contactor_fault_active_since",
            "_contactor_lockout_at",
            "_contactor_suspected_open_since",
            "_contactor_suspected_welded_since",
        ):
            self.assertIsNone(getattr(svc, name))
        self.assertEqual(svc._contactor_lockout_reason, "")
        self.assertEqual(svc._contactor_lockout_source, "")
        ControlWriteController._publish_contactor_lockout_paths(svc, 10.0)
        self.assertEqual(
            svc.publish_field.call_args_list,
            [
                call("auto_contactor_fault_count", 0, 10.0, force=True),
                call("auto_contactor_lockout_active", 0, 10.0, force=True),
                call("auto_contactor_lockout_reason", "", 10.0, force=True),
                call("auto_contactor_lockout_source", "", 10.0, force=True),
                call("auto_contactor_lockout_age", -1, 10.0, force=True),
                call("auto_contactor_lockout_reset", 0, 10.0, force=True),
            ],
        )

    def test_auto_disable_and_manual_relay_path_set_coupled_state(self) -> None:
        svc = SimpleNamespace(
            virtual_enable=1,
            virtual_startstop=1,
            auto_manual_override_seconds=30.0,
            charger_enable_available=MagicMock(return_value=False),
        )
        controller = ControlWriteController(SimpleNamespace())
        with (
            patch.object(controller, "_queue_relay_command") as queue,
            patch.object(controller, "_publish_local_pm_status_best_effort") as publish,
        ):
            controller._apply_auto_disable(svc, 10.0)
        queue.assert_called_once_with(svc, False, 10.0)
        publish.assert_called_once_with(svc, False, 10.0)
        self.assertEqual((svc.virtual_enable, svc.virtual_startstop), (0, 0))

        with (
            patch.object(controller, "_queue_relay_command") as queue,
            patch.object(controller, "_publish_local_pm_status_best_effort") as publish,
        ):
            controller._apply_manual_enable_like_request(svc, True, 20.0)
        queue.assert_called_once_with(svc, True, 20.0)
        publish.assert_called_once_with(svc, True, 20.0)
        self.assertEqual((svc.virtual_enable, svc.virtual_startstop), (1, 1))
        self.assertEqual(svc.manual_override_until, 50.0)

    def test_manual_charger_path_marks_side_effect_without_relay_placeholder(self) -> None:
        svc = SimpleNamespace(
            virtual_enable=0,
            virtual_startstop=0,
            auto_manual_override_seconds=30.0,
            charger_enable_available=MagicMock(return_value=True),
            charger_set_enabled=MagicMock(),
        )
        controller = ControlWriteController(SimpleNamespace())
        with (
            patch.object(controller, "_queue_relay_command") as queue,
            patch.object(controller, "_publish_local_pm_status_best_effort") as publish,
        ):
            controller._apply_manual_enable_like_request(svc, True, 20.0)
        svc.charger_set_enabled.assert_called_once_with(True)
        queue.assert_not_called()
        publish.assert_not_called()
        self.assertIs(controller._external_side_effect_started, True)
        self.assertEqual((svc.virtual_enable, svc.virtual_startstop), (1, 1))
        self.assertEqual(svc.manual_override_until, 50.0)

        controller._external_side_effect_started = False
        controller._apply_manual_enable_like_request(svc, False, 30.0)
        svc.charger_set_enabled.assert_called_with(False)
        self.assertEqual((svc.virtual_enable, svc.virtual_startstop), (0, 0))
        self.assertEqual(svc.manual_override_until, 60.0)

    def test_manual_relay_off_sets_both_public_flags_to_zero(self) -> None:
        svc = SimpleNamespace(
            virtual_enable=1,
            virtual_startstop=1,
            auto_manual_override_seconds=30.0,
            charger_enable_available=MagicMock(return_value=False),
        )
        controller = ControlWriteController(SimpleNamespace())
        with (
            patch.object(controller, "_queue_relay_command") as queue,
            patch.object(controller, "_publish_local_pm_status_best_effort") as publish,
        ):
            controller._apply_manual_enable_like_request(svc, False, 20.0)
        queue.assert_called_once_with(svc, False, 20.0)
        publish.assert_called_once_with(svc, False, 20.0)
        self.assertEqual((svc.virtual_enable, svc.virtual_startstop), (0, 0))
        self.assertEqual(svc.manual_override_until, 50.0)


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact port-interaction contracts for write-controller handlers."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from venus_evcharger.control import ControlResult
from venus_evcharger.controllers import write as write_module
from venus_evcharger.controllers.write import ControlWriteController


def _port(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "_service": SimpleNamespace(),
        "time_now": MagicMock(return_value=50.0),
        "state_summary": MagicMock(return_value="state"),
        "publish_field": MagicMock(),
        "begin_publication_transaction": MagicMock(),
        "commit_publication_transaction": MagicMock(),
        "discard_publication_transaction": MagicMock(),
        "mode_uses_auto_logic": MagicMock(return_value=False),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class TestWriteControllerSimpleHandlerContracts(unittest.TestCase):
    def test_autostart_write_normalizes_publishes_and_logs_exactly(self) -> None:
        port = _port()
        controller = ControlWriteController(port)
        with patch.object(write_module.logging, "info") as log:
            controller._handle_autostart_write("1")
        self.assertEqual(port.virtual_autostart, 1)
        port.publish_field.assert_called_once_with("auto_start", 1, 50.0, force=True)
        log.assert_called_once_with("Control target auto_start=%s %s", 1, "state")

    def test_current_setting_targets_publish_exact_fields_and_side_effect_state(self) -> None:
        port = _port(
            charger_current_available=MagicMock(return_value=True),
            charger_set_current=MagicMock(),
            virtual_set_current=0.0,
            max_current=0.0,
            min_current=0.0,
        )
        controller = ControlWriteController(port)
        controller._handle_current_setting_write("set_current", "12.5")
        port.charger_set_current.assert_called_once_with(12.5)
        self.assertTrue(controller._external_side_effect_started)
        controller._handle_current_setting_write("max_current", "16")
        controller._handle_current_setting_write("min_current", "6")
        self.assertEqual(
            port.publish_field.call_args_list,
            [
                call("set_current", 12.5, 50.0, force=True),
                call("max_current", 16.0, 50.0, force=True),
                call("min_current", 6.0, 50.0, force=True),
            ],
        )

    def test_reset_and_update_zero_writes_only_acknowledge_the_command(self) -> None:
        cases = (
            ("_handle_phase_lockout_reset_write", "auto_phase_lockout_reset"),
            ("_handle_contactor_lockout_reset_write", "auto_contactor_lockout_reset"),
            ("_handle_software_update_run_write", "auto_software_update_run"),
        )
        for method_name, field in cases:
            with self.subTest(method=method_name):
                port = _port()
                controller = ControlWriteController(port)
                getattr(controller, method_name)(0)
                port.publish_field.assert_called_once_with(field, 0, 50.0, force=True)

    def test_phase_lockout_reset_clears_and_publishes_all_derived_paths(self) -> None:
        port = _port()
        controller = ControlWriteController(port)
        with (
            patch.object(controller, "_clear_phase_lockout_state") as clear,
            patch.object(controller, "_publish_phase_selection_paths") as selection,
            patch.object(controller, "_publish_phase_lockout_paths") as lockout,
            patch.object(write_module.logging, "info") as log,
        ):
            controller._handle_phase_lockout_reset_write(1)
        clear.assert_called_once_with(port._service)
        selection.assert_called_once_with(port, 50.0)
        lockout.assert_called_once_with(port, 50.0)
        log.assert_called_once_with(
            "Control target auto_phase_lockout_reset=1 cleared phase lockout state %s",
            "state",
        )

    def test_contactor_lockout_reset_clears_and_publishes_all_derived_paths(self) -> None:
        port = _port()
        controller = ControlWriteController(port)
        with (
            patch.object(controller, "_clear_contactor_lockout_state") as clear,
            patch.object(controller, "_publish_contactor_lockout_paths") as publish,
            patch.object(write_module.logging, "info") as log,
        ):
            controller._handle_contactor_lockout_reset_write(1)
        clear.assert_called_once_with(port._service)
        publish.assert_called_once_with(port, 50.0)
        log.assert_called_once_with(
            "Control target auto_contactor_lockout_reset=1 cleared contactor lockout state %s",
            "state",
        )

    def test_software_update_request_records_time_acknowledges_and_logs(self) -> None:
        port = _port()
        controller = ControlWriteController(port)
        with patch.object(write_module.logging, "info") as log:
            controller._handle_software_update_run_write(1)
        self.assertEqual(port._software_update_run_requested_at, 50.0)
        port.publish_field.assert_called_once_with(
            "auto_software_update_run", 0, 50.0, force=True
        )
        log.assert_called_once_with(
            "Control target auto_software_update_run=1 queued a software update request %s",
            "state",
        )


class TestWriteControllerModeAndRelayContracts(unittest.TestCase):
    def test_mode_write_runs_transition_state_and_publish_pipeline_in_order(self) -> None:
        port = _port(
            virtual_mode=0,
            normalize_mode=MagicMock(return_value=1),
            mode_uses_auto_logic=MagicMock(return_value=True),
        )
        controller = ControlWriteController(port)
        with (
            patch.object(controller, "_log_normalized_mode") as normalized,
            patch.object(controller, "_handle_mode_transition_to_auto") as transition,
            patch.object(controller, "_reset_auto_decision_state") as reset,
            patch.object(controller, "_snapshot_for_mode") as snapshot,
            patch.object(controller, "_publish_mode_paths") as publish,
            patch.object(write_module.logging, "info") as log,
        ):
            controller._handle_mode_write(5)
        port.normalize_mode.assert_called_once_with(5)
        port.mode_uses_auto_logic.assert_called_once_with(1)
        normalized.assert_called_once_with(5, 1)
        transition.assert_called_once_with(0, 50.0)
        reset.assert_called_once_with(port)
        snapshot.assert_called_once_with(port, 50.0, True)
        publish.assert_called_once_with(port, 50.0, True)
        self.assertEqual(port.virtual_mode, 1)
        log.assert_called_once_with(
            "Control target mode requested=%s previous=%s applied=%s %s",
            5,
            0,
            1,
            "state",
        )

    def test_startstop_auto_disable_and_manual_request_use_distinct_pipelines(self) -> None:
        auto_port = _port(virtual_mode=1, mode_uses_auto_logic=MagicMock(return_value=True))
        auto = ControlWriteController(auto_port)
        with (
            patch.object(auto, "_apply_auto_disable") as disable,
            patch.object(auto, "_publish_startstop_enable") as publish,
            patch.object(write_module.logging, "info") as log,
        ):
            auto._handle_startstop_write(False)
        disable.assert_called_once_with(auto_port, 50.0)
        publish.assert_called_once_with(auto_port, 50.0, auto_mode_active=True)
        log.assert_called_once_with("Control target start_stop=%s auto_mode=%s %s", 0, 1, "state")

        manual_port = _port(virtual_mode=0, mode_uses_auto_logic=MagicMock(return_value=False))
        manual = ControlWriteController(manual_port)
        with (
            patch.object(manual, "_apply_manual_startstop_request") as apply,
            patch.object(manual, "_publish_startstop_enable") as publish,
            patch.object(write_module.logging, "info") as log,
        ):
            manual._handle_startstop_write(True)
        apply.assert_called_once_with(manual_port, True, 50.0)
        publish.assert_called_once_with(manual_port, 50.0, auto_mode_active=False)
        log.assert_called_once_with("Control target start_stop=%s auto_mode=%s %s", 1, 0, "state")

        allow_port = _port(
            virtual_mode=1,
            virtual_enable=0,
            mode_uses_auto_logic=MagicMock(return_value=True),
        )
        allow = ControlWriteController(allow_port)
        with (
            patch.object(allow, "_publish_startstop_enable"),
            patch.object(write_module.logging, "info"),
        ):
            allow._handle_startstop_write(True)
        self.assertEqual(allow_port.virtual_enable, 1)
        self.assertEqual(
            allow_port.mode_uses_auto_logic.call_args_list,
            [call(1), call(1)],
        )

    def test_enable_auto_allow_and_manual_disable_use_distinct_pipelines(self) -> None:
        auto_port = _port(
            virtual_mode=1,
            virtual_enable=0,
            mode_uses_auto_logic=MagicMock(return_value=True),
        )
        auto = ControlWriteController(auto_port)
        with (
            patch.object(auto, "_apply_auto_disable") as disable,
            patch.object(auto, "_publish_startstop_enable") as publish,
            patch.object(write_module.logging, "info") as log,
        ):
            auto._handle_enable_write(True)
        disable.assert_not_called()
        self.assertEqual(auto_port.virtual_enable, 1)
        publish.assert_called_once_with(auto_port, 50.0, auto_mode_active=True)
        log.assert_called_once_with("Control target enable=%s auto_mode=%s %s", 1, 1, "state")

        manual_port = _port(virtual_mode=0, mode_uses_auto_logic=MagicMock(return_value=False))
        manual = ControlWriteController(manual_port)
        with (
            patch.object(manual, "_apply_manual_enable_request") as apply,
            patch.object(manual, "_publish_startstop_enable") as publish,
            patch.object(write_module.logging, "info") as log,
        ):
            manual._handle_enable_write(False)
        apply.assert_called_once_with(manual_port, False, 50.0)
        publish.assert_called_once_with(manual_port, 50.0, auto_mode_active=False)
        log.assert_called_once_with("Control target enable=%s auto_mode=%s %s", 0, 0, "state")
        self.assertEqual(
            manual_port.mode_uses_auto_logic.call_args_list,
            [call(0), call(0), call(0)],
        )


class TestWriteControllerPhaseAndFailureContracts(unittest.TestCase):
    def test_in_flight_publication_commits_pending_transaction_once(self) -> None:
        port = _port()

        with patch.object(write_module.logging, "warning") as warning:
            ControlWriteController._commit_in_flight_publications(port)

        port.commit_publication_transaction.assert_called_once_with()
        warning.assert_not_called()

    def test_in_flight_publication_failure_is_logged_and_suppressed(self) -> None:
        error = ValueError("publish failed")
        port = _port(commit_publication_transaction=MagicMock(side_effect=error))

        with patch.object(write_module.logging, "warning") as warning:
            ControlWriteController._commit_in_flight_publications(port)

        port.commit_publication_transaction.assert_called_once_with()
        warning.assert_called_once_with(
            "Control state publication failed after an irreversible command: %s",
            error,
            exc_info=error,
        )

    def test_unsupported_phase_selection_reports_value_and_supported_set(self) -> None:
        port = _port(
            supported_phase_selections=("P1", "P1_P2"),
            normalize_phase_selection=MagicMock(return_value="P1_P2_P3"),
        )
        controller = ControlWriteController(port)
        with self.assertRaisesRegex(
            ValueError,
            r"^Unsupported phase selection 'requested' \(supported: P1,P1_P2\)$",
        ):
            controller._handle_phase_selection_write("requested")

    def test_phase_selection_stages_live_cutover_with_exact_safety_sequence(self) -> None:
        port = _port(
            supported_phase_selections=("P1", "P1_P2"),
            normalize_phase_selection=MagicMock(return_value="P1_P2"),
            phase_selection_requires_pause=MagicMock(return_value=True),
            relay_may_be_on_for_cutover=MagicMock(return_value=True),
        )
        controller = ControlWriteController(port)
        with (
            patch.object(controller, "_queue_phase_switch_state") as queue_phase,
            patch.object(controller, "_queue_relay_command") as queue_relay,
            patch.object(controller, "_publish_local_pm_status_best_effort") as local_status,
            patch.object(controller, "_publish_phase_selection_paths") as publish,
            patch.object(write_module.logging, "info") as log,
        ):
            controller._handle_phase_selection_write("requested")
        queue_phase.assert_called_once_with(
            port._service,
            "P1_P2",
            50.0,
            resume_relay=True,
        )
        queue_relay.assert_called_once_with(port, False, 50.0)
        local_status.assert_called_once_with(port, False, 50.0)
        publish.assert_called_once_with(port, 50.0)
        log.assert_called_once_with(
            "Control target phase_selection requested=%s staged=%s %s",
            "requested",
            "P1_P2",
            "state",
        )

    def test_phase_selection_applies_and_publishes_exact_selection(self) -> None:
        port = _port(
            supported_phase_selections=("P1", "P1_P2"),
            normalize_phase_selection=MagicMock(return_value="P1_P2"),
            phase_selection_requires_pause=MagicMock(return_value=False),
            relay_may_be_on_for_cutover=MagicMock(return_value=False),
            apply_phase_selection=MagicMock(return_value="P1_P2"),
        )
        controller = ControlWriteController(port)
        with (
            patch.object(controller, "_publish_phase_selection_paths") as publish,
            patch.object(write_module.logging, "info") as log,
        ):
            controller._handle_phase_selection_write("requested")
        port.normalize_phase_selection.assert_called_once_with("requested")
        port.apply_phase_selection.assert_called_once_with("P1_P2")
        self.assertEqual(port.requested_phase_selection, "P1_P2")
        self.assertEqual(port.active_phase_selection, "P1_P2")
        publish.assert_called_once_with(port, 50.0)
        log.assert_called_once_with(
            "Control target phase_selection requested=%s applied=%s %s",
            "requested",
            "P1_P2",
            "state",
        )

    def test_reversible_failure_restores_snapshot_and_returns_rejection(self) -> None:
        service = SimpleNamespace()
        port = _port(
            _service=service,
            save_runtime_state=MagicMock(),
            save_runtime_overrides=MagicMock(),
        )
        controller = ControlWriteController(port)
        command = SimpleNamespace(name="set_mode", target="mode", value=1)
        controller._control_api = MagicMock()
        controller._control_api.execute.side_effect = ValueError("bad")
        rejected = MagicMock(spec=ControlResult)
        with (
            patch.object(controller, "_snapshot_write_state", return_value="snapshot"),
            patch.object(controller, "_restore_write_state") as restore,
            patch.object(write_module, "write_failure_is_reversible", return_value=True) as reversible,
            patch.object(write_module.ControlResult, "rejected_result", return_value=rejected) as result,
            patch.object(write_module.logging, "warning") as warning,
        ):
            self.assertIs(controller.handle_control_command(command), rejected)
        reversible.assert_called_once_with(False)
        restore.assert_called_once_with(service, "snapshot")
        result.assert_called_once_with(command, detail="bad")
        warning.assert_called_once_with(
            "Control command %s target=%s value=%s failed: %s",
            "set_mode",
            "mode",
            1,
            controller._control_api.execute.side_effect,
            exc_info=controller._control_api.execute.side_effect,
        )
        self.assertFalse(controller._external_side_effect_started)

    def test_irreversible_failure_keeps_state_and_returns_accepted_in_flight(self) -> None:
        service = SimpleNamespace()
        port = _port(
            _service=service,
            save_runtime_state=MagicMock(),
            save_runtime_overrides=MagicMock(),
        )
        controller = ControlWriteController(port)
        command = SimpleNamespace(name="set_mode", target="mode", value=1)
        error = ValueError("bad")

        def fail_after_side_effect(_controller: object, _command: object) -> None:
            controller._external_side_effect_started = True
            raise error

        controller._control_api = MagicMock()
        controller._control_api.execute.side_effect = fail_after_side_effect
        accepted = MagicMock(spec=ControlResult)
        with (
            patch.object(controller, "_snapshot_write_state", return_value="snapshot"),
            patch.object(controller, "_restore_write_state") as restore,
            patch.object(write_module, "write_failure_is_reversible", return_value=False) as reversible,
            patch.object(
                write_module.ControlResult,
                "accepted_in_flight_result",
                return_value=accepted,
            ) as result,
            patch.object(write_module.logging, "warning") as warning,
        ):
            self.assertIs(controller.handle_control_command(command), accepted)
        reversible.assert_called_once_with(True)
        restore.assert_not_called()
        result.assert_called_once_with(
            command,
            detail="bad",
            external_side_effect_started=True,
        )
        warning.assert_called_once_with(
            "Control command %s target=%s value=%s failed after external side effects started; "
            "keeping in-flight state: %s",
            "set_mode",
            "mode",
            1,
            error,
            exc_info=error,
        )
        self.assertIs(controller._external_side_effect_started, False)


if __name__ == "__main__":
    unittest.main()

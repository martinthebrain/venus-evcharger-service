# SPDX-License-Identifier: GPL-3.0-or-later
"""Persistence contracts for completed automatic phase-switch transitions."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

from venus_evcharger.backend.models import PhaseSelection
from venus_evcharger.update.relay_phase_decision import AutoPhaseTargetSelector
from venus_evcharger.update.relay_phase_publish import RelayTelemetryService
from venus_evcharger.update.relay_phase_switch_mismatch import PhaseSwitchMismatchMonitor
from venus_evcharger.update.relay_phase_switch_policy import AutoPhaseSwitchController
from venus_evcharger.update.relay_ports import PhaseSwitchServicePort


class _RuntimeView:
    @staticmethod
    def pending_selection(svc: PhaseSwitchServicePort) -> PhaseSelection | None:
        del svc
        return None

    @staticmethod
    def state_active(pending_selection: PhaseSelection | None, switch_state: str) -> bool:
        del pending_selection, switch_state
        return False


class _LocalPmPublisher:
    def __init__(self) -> None:
        self.calls: list[tuple[bool, float]] = []

    def publish_local_pm_status_best_effort(
        self,
        svc: RelayTelemetryService,
        relay_on: bool,
        now: float,
    ) -> None:
        del svc
        self.calls.append((relay_on, now))


class PhaseSwitchPersistenceContracts(unittest.TestCase):
    @staticmethod
    def _controller() -> tuple[AutoPhaseSwitchController, _LocalPmPublisher]:
        mismatch = PhaseSwitchMismatchMonitor()
        selector = AutoPhaseTargetSelector(mismatch, lambda voltage, _selection, _mode: voltage)
        local_pm = _LocalPmPublisher()
        return (
            AutoPhaseSwitchController(
                selector,
                mismatch,
                _RuntimeView(),
                local_pm,
                waiting_state="waiting-relay-off",
            ),
            local_pm,
        )

    @staticmethod
    def _service(*, requires_pause: bool, save_error: Exception | None = None) -> SimpleNamespace:
        save_runtime_state = MagicMock(side_effect=save_error)
        runtime = SimpleNamespace(
            apply_phase_selection=MagicMock(return_value="P1_P2"),
            mark_failure=MagicMock(),
            pending_relay_command=MagicMock(return_value=(False, 90.0)),
            phase_selection_requires_pause=MagicMock(return_value=requires_pause),
            warning_throttled=MagicMock(),
        )
        return SimpleNamespace(
            runtime=runtime,
            state=SimpleNamespace(save_runtime_state=save_runtime_state),
            auto_shelly_soft_fail_seconds=12.5,
            requested_phase_selection="P1",
            active_phase_selection="P1",
            _auto_phase_target_candidate="P1_P2",
            _auto_phase_target_since=80.0,
            _phase_switch_pending_selection=None,
            _phase_switch_state=None,
            _phase_switch_requested_at=None,
            _phase_switch_stable_until=None,
            _phase_switch_resume_relay=False,
            _phase_switch_mismatch_active=False,
            _phase_switch_mismatch_counts={},
            _phase_switch_last_mismatch_selection=None,
            _phase_switch_last_mismatch_at=None,
            _phase_switch_lockout_selection=None,
            _phase_switch_lockout_reason="",
            _phase_switch_lockout_at=None,
            _phase_switch_lockout_until=None,
        )

    @staticmethod
    def _as_service(service: SimpleNamespace) -> PhaseSwitchServicePort:
        return cast(PhaseSwitchServicePort, service)

    def test_staged_transition_survives_persistence_failure_as_coherent_ram_state(self) -> None:
        error = OSError("runtime state unavailable")
        service = self._service(requires_pause=True, save_error=error)
        controller, local_pm = self._controller()

        result = controller._apply_auto_phase_target(
            self._as_service(service),
            "P1_P2",
            True,
            False,
            100.0,
        )

        self.assertIs(result, False)
        self.assertEqual(service.requested_phase_selection, "P1_P2")
        self.assertEqual(service._phase_switch_pending_selection, "P1_P2")
        self.assertEqual(service._phase_switch_state, "waiting-relay-off")
        self.assertEqual(service._phase_switch_requested_at, 100.0)
        self.assertIsNone(service._phase_switch_stable_until)
        self.assertIs(service._phase_switch_resume_relay, True)
        self.assertIsNone(service._auto_phase_target_candidate)
        self.assertIsNone(service._auto_phase_target_since)
        service.runtime.apply_phase_selection.assert_not_called()
        service.state.save_runtime_state.assert_called_once_with()
        self.assertEqual(local_pm.calls, [(False, 100.0)])
        service.runtime.warning_throttled.assert_called_once_with(
            "phase-switch-state-save-failed-staging",
            12.5,
            "Unable to persist phase-switch state after %s; "
            "keeping the completed in-memory transition for a later runtime-state save: %s",
            "staging",
            error,
            exc_info=error,
        )

    def test_applied_transition_survives_persistence_failure_as_coherent_ram_state(self) -> None:
        error = RuntimeError("runtime state unavailable")
        service = self._service(requires_pause=False, save_error=error)
        controller, local_pm = self._controller()

        result = controller._apply_auto_phase_target(
            self._as_service(service),
            "P1_P2",
            True,
            False,
            100.0,
        )

        self.assertIsNone(result)
        service.runtime.apply_phase_selection.assert_called_once_with("P1_P2")
        self.assertEqual(service.requested_phase_selection, "P1_P2")
        self.assertEqual(service.active_phase_selection, "P1_P2")
        self.assertIsNone(service._auto_phase_target_candidate)
        self.assertIsNone(service._auto_phase_target_since)
        service.state.save_runtime_state.assert_called_once_with()
        self.assertEqual(local_pm.calls, [])
        service.runtime.warning_throttled.assert_called_once_with(
            "phase-switch-state-save-failed-physical-apply",
            12.5,
            "Unable to persist phase-switch state after %s; "
            "keeping the completed in-memory transition for a later runtime-state save: %s",
            "physical-apply",
            error,
            exc_info=error,
        )

    def test_successful_staged_and_applied_transitions_persist_without_warning(self) -> None:
        controller, local_pm = self._controller()
        staged = self._service(requires_pause=True)
        applied = self._service(requires_pause=False)

        self.assertIs(
            controller._apply_auto_phase_target(self._as_service(staged), "P1_P2", True, False, 100.0),
            False,
        )
        self.assertIsNone(
            controller._apply_auto_phase_target(self._as_service(applied), "P1_P2", True, False, 100.0)
        )

        staged.state.save_runtime_state.assert_called_once_with()
        applied.state.save_runtime_state.assert_called_once_with()
        staged.runtime.warning_throttled.assert_not_called()
        applied.runtime.warning_throttled.assert_not_called()
        self.assertIsNone(staged._auto_phase_target_candidate)
        self.assertIsNone(applied._auto_phase_target_candidate)
        self.assertEqual(local_pm.calls, [(False, 100.0)])

    def test_hardware_failure_keeps_its_dedicated_error_contract(self) -> None:
        error = OSError("phase-switch actuator unavailable")
        service = self._service(requires_pause=False)
        service.runtime.apply_phase_selection.side_effect = error
        controller, local_pm = self._controller()

        result = controller._apply_auto_phase_target(
            self._as_service(service),
            "P1_P2",
            True,
            False,
            100.0,
        )

        self.assertIsNone(result)
        service.runtime.mark_failure.assert_called_once_with("shelly")
        service.runtime.warning_throttled.assert_called_once_with(
            "auto-phase-switch-failed",
            12.5,
            "Failed to apply Auto phase selection %s: %s",
            "P1_P2",
            error,
            exc_info=error,
        )
        service.state.save_runtime_state.assert_not_called()
        self.assertEqual(local_pm.calls, [])
        self.assertIsNone(service._auto_phase_target_candidate)
        self.assertIsNone(service._auto_phase_target_since)


if __name__ == "__main__":
    unittest.main()

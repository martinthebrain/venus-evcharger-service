# SPDX-License-Identifier: GPL-3.0-or-later
"""Fail-closed contracts for update-cycle input and capability boundaries."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast
import unittest

from venus_evcharger.auto.policy import AutoPhasePolicy
from venus_evcharger.update.offline_publish import OfflinePublisher, OfflineService
from venus_evcharger.update.relay_phase_decision import AutoPhaseTargetSelector
from venus_evcharger.update.relay_phase_switch_mismatch import PhaseSwitchMismatchMonitor
from venus_evcharger.update.relay_ports import PhaseSwitchServicePort


def _offline_service(output: object) -> OfflineService:
    return cast(
        OfflineService,
        SimpleNamespace(
            _last_confirmed_pm_status={"output": output},
            _last_confirmed_pm_status_at=100.0,
            _worker_poll_interval_seconds=1.0,
            relay_sync_timeout_seconds=2.0,
        ),
    )


def _phase_service(
    supported: tuple[object, ...],
    *,
    active: object = None,
    requested: object = None,
) -> PhaseSwitchServicePort:
    return cast(
        PhaseSwitchServicePort,
        SimpleNamespace(
            supported_phase_selections=supported,
            active_phase_selection=active,
            requested_phase_selection=requested,
            auto_policy=SimpleNamespace(phase=AutoPhasePolicy()),
        ),
    )


class TestUpdateInputValidationContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.selector = AutoPhaseTargetSelector(
            PhaseSwitchMismatchMonitor(),
            lambda voltage, _selection, _mode: voltage,
        )

    def test_offline_pm_output_accepts_only_real_booleans(self) -> None:
        for output in (True, False):
            with self.subTest(output=output):
                service = _offline_service(output)
                self.assertEqual(
                    OfflinePublisher._offline_confirmed_pm_sample(service),
                    ({"output": output}, 100.0),
                )
                self.assertIs(OfflinePublisher._offline_confirmed_relay_state(service, 100.0), output)
                self.assertEqual(
                    OfflinePublisher._fresh_offline_pm_status(service, 100.0),
                    {"output": output},
                )

    def test_offline_pm_output_rejects_truthy_and_falsy_non_booleans(self) -> None:
        invalid_outputs: tuple[object, ...] = ("false", "true", 0, 1, None, (), [])
        for output in invalid_outputs:
            with self.subTest(output=output):
                service = _offline_service(output)
                self.assertIsNone(OfflinePublisher._offline_confirmed_pm_sample(service))
                self.assertFalse(OfflinePublisher._offline_confirmed_relay_state(service, 100.0))
                self.assertIsNone(OfflinePublisher._fresh_offline_pm_status(service, 100.0))

    def test_phase_capabilities_keep_only_valid_normalized_selections(self) -> None:
        service = _phase_service(("P1_P2_P3", "invalid", "2P", None, "L2", "P1_P2"))

        self.assertEqual(
            self.selector._ordered_auto_phase_selections(service),
            ("P1", "P1_P2", "P1_P2_P3"),
        )

    def test_empty_valid_phase_capabilities_disable_auto_phase_changes(self) -> None:
        for raw_supported in ((), ("invalid", None, object())):
            with self.subTest(raw_supported=raw_supported):
                service = _phase_service(
                    raw_supported,
                    active="P1_P2",
                    requested="P1_P2_P3",
                )
                supported = self.selector._ordered_auto_phase_selections(service)
                current = self.selector._current_phase_selection(service, supported)

                self.assertEqual(supported, ())
                self.assertEqual(current, "P1_P2")
                self.assertEqual(
                    self.selector._auto_phase_target_selection(
                        service,
                        supported,
                        current,
                        True,
                        True,
                        230.0,
                        100.0,
                    ),
                    (None, "phase-capabilities-unavailable", None),
                )

    def test_empty_capabilities_use_only_current_state_as_display_fallback(self) -> None:
        self.assertEqual(
            self.selector._current_phase_selection(
                _phase_service((), active="invalid", requested="P1_P2_P3"),
                (),
            ),
            "P1_P2_P3",
        )
        self.assertEqual(
            self.selector._current_phase_selection(
                _phase_service((), active="invalid", requested="invalid"),
                (),
            ),
            "P1",
        )


if __name__ == "__main__":
    unittest.main()

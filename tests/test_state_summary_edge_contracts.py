# SPDX-License-Identifier: GPL-3.0-or-later
"""Edge contracts for absent and incomplete state-summary inputs."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import call, patch

from venus_evcharger.controllers import state_summary as summary_module
from venus_evcharger.controllers.state_summary import StateSummaryBuilder


class TestStateSummaryEdgeContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = StateSummaryBuilder(SimpleNamespace())

    def test_observed_phase_normalizes_confirmed_and_fallback_values_with_exact_defaults(self) -> None:
        svc = SimpleNamespace(
            _last_confirmed_pm_status={},
            _last_charger_state_phase_selection="P1",
        )
        with patch.object(StateSummaryBuilder, "_summary_text", side_effect=("", "P1")) as summary_text:
            self.assertEqual(self.controller._summary_observed_phase(svc), "P1")
        self.assertEqual(
            summary_text.call_args_list,
            [call(None, ""), call("P1", "na")],
        )

    def test_phase_lockout_requires_both_deadline_and_selection(self) -> None:
        with patch("venus_evcharger.controllers.state_summary.time.time", return_value=100.0):
            self.assertEqual(self.controller._summary_phase_lockout_active(SimpleNamespace()), "0")
            self.assertEqual(
                self.controller._summary_phase_lockout_active(
                    SimpleNamespace(_phase_switch_lockout_selection="P1")
                ),
                "0",
            )

    def test_active_phase_lockout_target_uses_the_normalized_selection_contract(self) -> None:
        svc = SimpleNamespace()
        with (
            patch.object(StateSummaryBuilder, "_summary_phase_lockout_active", return_value="1"),
            patch.object(StateSummaryBuilder, "_summary_text", return_value="target") as summary_text,
        ):
            self.assertEqual(self.controller._summary_phase_lockout_target(svc), "target")
        summary_text.assert_called_once_with(None, "na")

    def test_absent_switch_and_contactor_health_is_not_reported_as_a_fault(self) -> None:
        svc = SimpleNamespace()
        self.assertEqual(self.controller._summary_switch_feedback_mismatch(svc), "0")
        self.assertEqual(
            self.controller._summary_switch_feedback_mismatch(
                SimpleNamespace(_last_switch_feedback_closed=False)
            ),
            "0",
        )
        self.assertEqual(self.controller._summary_contactor_suspected_open(svc), "0")
        self.assertEqual(self.controller._summary_contactor_suspected_welded(svc), "0")

    def test_missing_contactor_counter_for_an_active_reason_is_zero(self) -> None:
        svc = SimpleNamespace(_contactor_fault_counts={"other": 4})
        with patch.object(StateSummaryBuilder, "_summary_contactor_count_reason", return_value="open"):
            self.assertEqual(self.controller._summary_contactor_fault_count(svc), "0")

    def test_active_contactor_lockout_reason_uses_the_normalized_reason_contract(self) -> None:
        svc = SimpleNamespace()
        with (
            patch.object(StateSummaryBuilder, "_summary_contactor_lockout_active", return_value="1"),
            patch.object(StateSummaryBuilder, "_summary_text", return_value="reason") as summary_text,
        ):
            self.assertEqual(self.controller._summary_contactor_lockout_reason(svc), "reason")
        summary_text.assert_called_once_with(None, "na")

    def test_absent_virtual_mode_is_forwarded_as_no_scheduled_mode(self) -> None:
        svc = SimpleNamespace()
        with patch.object(summary_module, "mode_uses_scheduled_logic", return_value=False) as uses_scheduled:
            self.assertIsNone(self.controller._scheduled_snapshot(svc, 100.0))
        uses_scheduled.assert_called_once_with(None)

    def test_phase_parts_normalize_switch_state_with_the_idle_default(self) -> None:
        svc = SimpleNamespace(_phase_switch_state="running")
        helper_names = (
            "_summary_observed_phase",
            "_summary_phase_mismatch_active",
            "_summary_phase_lockout_active",
            "_summary_phase_lockout_target",
            "_summary_phase_supported_effective",
            "_summary_phase_degraded_active",
        )
        helper_patchers = [patch.object(StateSummaryBuilder, name, return_value=name) for name in helper_names]
        for helper_patcher in helper_patchers:
            helper_patcher.start()
            self.addCleanup(helper_patcher.stop)
        with patch.object(StateSummaryBuilder, "_summary_text", return_value="state") as summary_text:
            parts = self.controller._summary_phase_parts(svc)
        self.assertEqual(parts[3], "phase_switch=state")
        summary_text.assert_called_once_with("running", "idle")


if __name__ == "__main__":
    unittest.main()

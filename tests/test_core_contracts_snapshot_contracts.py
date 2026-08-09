# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact snapshot, decision-trace, and cutover contracts."""

from __future__ import annotations

import unittest

from venus_evcharger.core.contracts_snapshot import (
    _apply_normalized_pm_payload,
    _clamped_snapshot_timestamp,
    _confirmed_after_requested,
    _cutover_blocked_by_pending_or_active,
    _cutover_confirmation_recent,
    _cutover_request_satisfied,
    _normalized_pm_snapshot_payload,
    _pm_snapshot_future_invalid,
    _resolved_snapshot_captured_at,
    _snapshot_pm_payload,
    cutover_confirmed_off,
    normalized_auto_decision_trace,
    normalized_worker_snapshot,
    write_failure_is_reversible,
)


class TestCoreContractsSnapshotContracts(unittest.TestCase):
    def test_snapshot_timestamp_resolution_and_clamping(self) -> None:
        self.assertEqual(_resolved_snapshot_captured_at(7.0, 8.0, 9.0), 7.0)
        self.assertEqual(_resolved_snapshot_captured_at(None, 8.0, 9.0), 9.0)
        self.assertEqual(_resolved_snapshot_captured_at(None, 8.0, None), 8.0)
        self.assertEqual(_resolved_snapshot_captured_at(None, None, None), 0.0)
        self.assertEqual(
            _clamped_snapshot_timestamp(
                101.5,
                100.0,
                future_tolerance_seconds=1.0,
                clamp_future_timestamps=True,
            ),
            100.0,
        )
        self.assertEqual(
            _clamped_snapshot_timestamp(
                101.5,
                100.0,
                future_tolerance_seconds=2.0,
                clamp_future_timestamps=True,
            ),
            101.5,
        )

    def test_pm_payload_extraction_and_validation(self) -> None:
        self.assertEqual(_snapshot_pm_payload({}), (None, None, False))
        self.assertEqual(
            _snapshot_pm_payload(
                {"pm_status": {1: "value", "output": 1}, "pm_captured_at": "8.5", "pm_confirmed": 1}
            ),
            ({"1": "value", "output": 1}, 8.5, True),
        )
        self.assertTrue(
            _pm_snapshot_future_invalid(
                102.0,
                100.0,
                future_tolerance_seconds=1.0,
                clamp_future_timestamps=True,
            )
        )
        self.assertFalse(
            _pm_snapshot_future_invalid(
                102.0,
                100.0,
                future_tolerance_seconds=2.0,
                clamp_future_timestamps=True,
            )
        )
        self.assertEqual(
            _normalized_pm_snapshot_payload(
                pm_status=None,
                pm_captured_at=9.0,
                pm_confirmed=True,
                captured_at=8.0,
                current=10.0,
                future_tolerance_seconds=1.0,
                clamp_future_timestamps=True,
            ),
            (None, None, False, 8.0),
        )
        self.assertEqual(
            _normalized_pm_snapshot_payload(
                pm_status={"output": 1},
                pm_captured_at=12.0,
                pm_confirmed=True,
                captured_at=10.0,
                current=10.0,
                future_tolerance_seconds=1.0,
                clamp_future_timestamps=True,
            ),
            (None, None, False, 10.0),
        )

    def test_apply_pm_payload_requires_complete_confirmation(self) -> None:
        normalized: dict[str, object] = {}
        _apply_normalized_pm_payload(
            normalized,
            resolved_captured_at=12.0,
            pm_status={"output": 1},
            pm_captured_at=11.0,
            pm_confirmed=True,
        )
        self.assertEqual(
            normalized,
            {
                "captured_at": 12.0,
                "pm_status": {"output": 1},
                "pm_captured_at": 11.0,
                "pm_confirmed": True,
            },
        )
        for status, timestamp in ((None, 11.0), ({"output": 1}, None)):
            incomplete: dict[str, object] = {}
            _apply_normalized_pm_payload(
                incomplete,
                resolved_captured_at=12.0,
                pm_status=status,
                pm_captured_at=timestamp,
                pm_confirmed=True,
            )
            self.assertIs(incomplete["pm_confirmed"], False)

    def test_worker_snapshot_default_boundary_behavior(self) -> None:
        clamped = normalized_worker_snapshot(
            {"captured_at": 101.5, "pm_status": {"output": 1}, "pm_confirmed": True},
            now=100.0,
        )
        self.assertEqual(clamped["captured_at"], 100.0)
        self.assertEqual(clamped["pm_status"], {"output": 1})
        self.assertEqual(clamped["pm_captured_at"], 100.0)
        self.assertIs(clamped["pm_confirmed"], True)
        preserved = normalized_worker_snapshot(
            {"captured_at": 101.5},
            now=100.0,
            clamp_future_timestamps=False,
        )
        self.assertEqual(preserved["captured_at"], 101.5)
        from_pm = normalized_worker_snapshot(
            {"pm_status": {"output": 0}, "pm_captured_at": 8.0, "pm_confirmed": True}
        )
        self.assertEqual(from_pm["captured_at"], 8.0)
        self.assertEqual(from_pm["pm_captured_at"], 8.0)
        current_wins = normalized_worker_snapshot(
            {"pm_status": {"output": 0}, "pm_captured_at": 8.0, "pm_confirmed": True},
            now=10.0,
        )
        self.assertEqual(current_wins["captured_at"], 10.0)
        timestamp_without_status = normalized_worker_snapshot({"pm_captured_at": 8.0})
        self.assertEqual(timestamp_without_status["captured_at"], 8.0)

    def test_auto_decision_trace_normalizes_and_forwards_semantics(self) -> None:
        health_calls: list[str] = []
        derive_calls: list[tuple[str, bool, str]] = []

        def health_code(reason: str) -> int:
            health_calls.append(reason)
            return 7

        def derive_state(reason: str, *, relay_on: bool, learned_charge_power_state: str) -> str:
            derive_calls.append((reason, relay_on, learned_charge_power_state))
            return "charging"

        trace = normalized_auto_decision_trace(
            health_reason="grid-missing-cached",
            cached_inputs=True,
            relay_intent=2,
            learned_charge_power_state="learning",
            metrics={"surplus": "1200.5", "learned_charge_power_state": "stable"},
            health_code_func=health_code,
            derive_auto_state_func=derive_state,
        )
        self.assertEqual(health_calls, ["grid-missing"])
        self.assertEqual(derive_calls, [("grid-missing", True, "stable")])
        self.assertEqual(
            trace,
            {
                "health_reason": "grid-missing-cached",
                "health_code": 107,
                "state": "charging",
                "state_code": 3,
                "metrics": {
                    "surplus": 1200.5,
                    "grid": None,
                    "start_threshold": None,
                    "stop_threshold": None,
                    "threshold_scale": None,
                    "stop_alpha": None,
                    "surplus_volatility": None,
                    "battery_unadjusted_surplus_penalty_w": None,
                    "ev_priority_active": None,
                    "ev_priority_credit_w": None,
                    "ev_priority_available_surplus_w": None,
                    "ev_priority_reclaimable_charge_w": None,
                    "ev_priority_running_load_w": None,
                    "ev_priority_soc": None,
                    "ev_priority_release_soc": None,
                    "learned_charge_power": None,
                    "learned_charge_power_state": "stable",
                    "soc": None,
                    "profile": None,
                    "threshold_mode": None,
                    "stop_alpha_stage": None,
                    "relay_intent": 1,
                    "state": "charging",
                },
            },
        )

    def test_auto_decision_trace_uses_fallback_learning_state(self) -> None:
        derive_calls: list[tuple[str, dict[str, object]]] = []

        def derive_state(reason: str, **values: object) -> str:
            derive_calls.append((reason, values))
            return "idle"

        trace = normalized_auto_decision_trace(
            health_reason=None,
            cached_inputs=False,
            relay_intent=0,
            learned_charge_power_state=" LEARNING ",
            metrics=None,
            health_code_func=lambda reason: 2 if reason == "init" else 99,
            derive_auto_state_func=derive_state,
        )
        self.assertEqual(
            derive_calls,
            [("init", {"relay_on": False, "learned_charge_power_state": "learning"})],
        )
        self.assertEqual(trace["health_reason"], "init")
        self.assertEqual(trace["health_code"], 2)
        self.assertEqual(trace["state"], "idle")
        self.assertEqual(trace["state_code"], 0)
        self.assertEqual(trace["metrics"], {"relay_intent": 0, "state": "idle"})

    def test_cutover_requires_each_safety_condition(self) -> None:
        self.assertTrue(_cutover_blocked_by_pending_or_active(pending_state=False, relay_on=False, confirmed_output=False))
        self.assertTrue(_cutover_blocked_by_pending_or_active(pending_state=None, relay_on=True, confirmed_output=False))
        self.assertTrue(_cutover_blocked_by_pending_or_active(pending_state=None, relay_on=False, confirmed_output=True))
        self.assertFalse(_cutover_blocked_by_pending_or_active(pending_state=None, relay_on=False, confirmed_output=False))
        self.assertTrue(
            _cutover_confirmation_recent(
                confirmed_at=101.5,
                now=100.0,
                max_age_seconds=2.0,
                future_tolerance_seconds=2.0,
            )
        )
        self.assertFalse(
            _cutover_confirmation_recent(
                confirmed_at=101.5,
                now=100.0,
                max_age_seconds=2.0,
                future_tolerance_seconds=1.0,
            )
        )
        self.assertTrue(_confirmed_after_requested(10.0, 10.0))
        self.assertFalse(_confirmed_after_requested(9.9, 10.0))
        self.assertTrue(_cutover_request_satisfied(confirmed_at=10.0, requested_at=None))
        self.assertFalse(_cutover_request_satisfied(confirmed_at=9.0, requested_at=10.0))

    def test_cutover_public_contract_and_write_reversibility(self) -> None:
        base = {
            "relay_on": False,
            "pending_state": None,
            "confirmed_output": False,
            "confirmed_at": 101.5,
            "requested_at": 100.0,
            "now": 100.0,
            "max_age_seconds": 2.0,
        }
        self.assertFalse(cutover_confirmed_off(**base))
        self.assertTrue(cutover_confirmed_off(**base, future_tolerance_seconds=2.0))
        self.assertFalse(cutover_confirmed_off(**{**base, "relay_on": True}, future_tolerance_seconds=2.0))
        self.assertFalse(cutover_confirmed_off(**{**base, "confirmed_output": True}, future_tolerance_seconds=2.0))
        self.assertFalse(cutover_confirmed_off(**{**base, "requested_at": 102.0}, future_tolerance_seconds=2.0))
        self.assertIs(write_failure_is_reversible(False), True)
        self.assertIs(write_failure_is_reversible(True), False)


if __name__ == "__main__":
    unittest.main()

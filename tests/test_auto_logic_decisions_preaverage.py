from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from venus_evcharger.auto.logic_decisions_preaverage import _AutoDecisionPreAverage
from venus_evcharger.auto.logic_types import NO_RELAY_DECISION, RelayDecisionState


class PreAverageHarness(_AutoDecisionPreAverage):
    _NO_DECISION = object()

    def __init__(self) -> None:
        self.service = SimpleNamespace(
            _auto_cached_inputs_used=False,
            virtual_enable=1,
            virtual_mode=1,
        )
        self.calls: list[tuple[Any, ...]] = []
        self.health_calls: list[tuple[str, bool, bool | None]] = []
        self.average_result: tuple[float | None, float | None] = (500.0, -250.0)
        self.common_decision: bool | object = self._NO_DECISION
        self.cutover_decision: bool | object = self._NO_DECISION
        self.daytime_window = True
        self.disabled_decision = False
        self.grid_missing_decision = False
        self.grid_recent = True
        self.grid_recovery_decision: bool | object = self._NO_DECISION
        self.missing_inputs_decision = False
        self.mode_uses_auto = True
        self.non_auto_decision = True
        self.relay_off_decision = False
        self.relay_on_decision = True
        self.resolved_battery_soc: float | None = 65.0
        self.resolve_soc_decision: bool | object = self._NO_DECISION
        self.scheduled_night = False
        self.scheduled_night_decision = True
        self.time_now = 123.0

    def _scheduled_night_charge_active(self, now: float | None = None) -> bool:
        self.calls.append(("scheduled_night_active", now))
        return self.scheduled_night

    def _mode_uses_auto_logic(self, mode: Any) -> bool:
        self.calls.append(("mode_uses_auto", mode))
        return self.mode_uses_auto

    def _handle_non_auto_mode(self, relay_on: bool) -> bool:
        self.calls.append(("non_auto", relay_on))
        return self.non_auto_decision

    def _handle_disabled_mode(self, cached_inputs: bool) -> bool:
        self.calls.append(("disabled", cached_inputs))
        return self.disabled_decision

    def _handle_cutover_pending(self, relay_on: bool, cached_inputs: bool) -> bool | object:
        self.calls.append(("cutover", relay_on, cached_inputs))
        return self.cutover_decision

    def _grid_recently_read(self, grid_power: float | None, now: float) -> bool:
        self.calls.append(("grid_recent", grid_power, now))
        return self.grid_recent

    def _handle_grid_missing(self, relay_on: bool, now: float, cached_inputs: bool) -> bool:
        self.calls.append(("grid_missing", relay_on, now, cached_inputs))
        return self.grid_missing_decision

    def _handle_grid_recovery_start_gate(self, relay_on: bool, now: float, cached_inputs: bool) -> bool | object:
        self.calls.append(("grid_recovery", relay_on, now, cached_inputs))
        return self.grid_recovery_decision

    def _resolve_battery_soc(
        self,
        battery_soc: float | int | None,
        relay_on: bool,
        now: float,
        cached_inputs: bool,
    ) -> tuple[float | None, bool | object]:
        self.calls.append(("resolve_soc", battery_soc, relay_on, now, cached_inputs))
        return self.resolved_battery_soc, self.resolve_soc_decision

    def _handle_missing_inputs(
        self,
        relay_on: bool,
        battery_soc: float,
        grid_power: float | None,
        now: float,
        cached_inputs: bool,
    ) -> bool:
        self.calls.append(("missing_inputs", relay_on, battery_soc, grid_power, now, cached_inputs))
        return self.missing_inputs_decision

    def _handle_common_runtime_gates(self, relay_on: bool, now: float, cached_inputs: bool) -> bool | object:
        self.calls.append(("common_runtime", relay_on, now, cached_inputs))
        return self.common_decision

    def is_within_auto_daytime_window(self, current_dt: Any | None = None) -> bool:
        self.calls.append(("daytime", current_dt))
        return self.daytime_window

    def _update_average_metrics(
        self,
        now: float,
        pv_power: float,
        grid_power: float,
        battery_soc: float,
        relay_on: bool,
    ) -> tuple[float | None, float | None]:
        self.calls.append(("averages", now, pv_power, grid_power, battery_soc, relay_on))
        return self.average_result

    def set_health(self, reason: str, cached: bool = False, relay_intent: bool | None = None) -> None:
        self.health_calls.append((reason, cached, relay_intent))

    def _handle_relay_on(
        self,
        avg_surplus_power: float,
        avg_grid_power: float,
        battery_soc: float,
        daytime_window_open: bool,
        now: float,
        cached_inputs: bool,
    ) -> bool:
        self.calls.append(("relay_on", avg_surplus_power, avg_grid_power, battery_soc, daytime_window_open, now, cached_inputs))
        return self.relay_on_decision

    def _handle_relay_off(
        self,
        avg_surplus_power: float,
        avg_grid_power: float,
        battery_soc: float,
        daytime_window_open: bool,
        now: float,
        cached_inputs: bool,
    ) -> bool:
        self.calls.append(("relay_off", avg_surplus_power, avg_grid_power, battery_soc, daytime_window_open, now, cached_inputs))
        return self.relay_off_decision

    def _learning_policy_now(self) -> float:
        self.calls.append(("now",))
        return self.time_now

    def _scheduled_night_decision(self, relay_on: bool, now: float, cached_inputs: bool) -> bool:
        self.calls.append(("scheduled_night_decision", relay_on, now, cached_inputs))
        return self.scheduled_night_decision


class TestPreAverageDecisions(unittest.TestCase):
    def test_pre_average_gate_result_distinguishes_pending_from_resolved(self) -> None:
        harness = PreAverageHarness()

        self.assertIsNone(harness._pre_average_gate_result(NO_RELAY_DECISION))

        false_state = RelayDecisionState.resolved(False)
        self.assertEqual(harness._pre_average_gate_result(false_state), (false_state, None))
        true_state = RelayDecisionState.resolved(True)
        self.assertEqual(harness._pre_average_gate_result(true_state), (true_state, None))

    def test_mode_decision_orders_non_auto_disabled_and_cutover_gates(self) -> None:
        harness = PreAverageHarness()
        harness.mode_uses_auto = False

        non_auto = harness._pre_average_mode_decision(harness.service, relay_on=False, cached_inputs=True)

        self.assertFalse(non_auto.is_pending)
        self.assertTrue(non_auto.resolved_value())
        self.assertEqual(harness.calls, [("mode_uses_auto", 1), ("non_auto", False)])

        harness = PreAverageHarness()
        harness.service.virtual_enable = 0
        disabled = harness._pre_average_mode_decision(harness.service, relay_on=True, cached_inputs=True)

        self.assertFalse(disabled.is_pending)
        self.assertFalse(disabled.resolved_value())
        self.assertEqual(harness.calls, [("mode_uses_auto", 1), ("disabled", True)])

        harness = PreAverageHarness()
        pending = harness._pre_average_mode_decision(harness.service, relay_on=True, cached_inputs=False)

        self.assertIs(pending, NO_RELAY_DECISION)
        self.assertEqual(harness.calls, [("mode_uses_auto", 1), ("cutover", True, False)])

        harness = PreAverageHarness()
        harness.cutover_decision = False
        cutover = harness._pre_average_mode_decision(harness.service, relay_on=True, cached_inputs=False)

        self.assertFalse(cutover.is_pending)
        self.assertFalse(cutover.resolved_value())

        harness = PreAverageHarness()
        delattr(harness.service, "virtual_enable")
        missing_enable = harness._pre_average_mode_decision(harness.service, relay_on=False, cached_inputs=True)

        self.assertIs(missing_enable, NO_RELAY_DECISION)
        self.assertEqual(harness.calls, [("mode_uses_auto", 1), ("cutover", False, True)])

    def test_input_gate_prioritizes_scheduled_night_grid_missing_and_recovery(self) -> None:
        harness = PreAverageHarness()
        harness.scheduled_night = True

        self.assertIs(harness._pre_average_input_gate_decision(False, -100.0, 10.0, True), NO_RELAY_DECISION)
        self.assertEqual(harness.calls, [("scheduled_night_active", 10.0)])

        harness = PreAverageHarness()
        harness.grid_recent = False
        grid_missing = harness._pre_average_input_gate_decision(True, None, 11.0, True)

        self.assertFalse(grid_missing.is_pending)
        self.assertFalse(grid_missing.resolved_value())
        self.assertEqual(
            harness.calls,
            [
                ("scheduled_night_active", 11.0),
                ("grid_recent", None, 11.0),
                ("grid_missing", True, 11.0, True),
            ],
        )

        harness = PreAverageHarness()
        recovery_pending = harness._pre_average_input_gate_decision(False, -50.0, 12.0, False)

        self.assertIs(recovery_pending, NO_RELAY_DECISION)
        self.assertEqual(
            harness.calls,
            [
                ("scheduled_night_active", 12.0),
                ("grid_recent", -50.0, 12.0),
                ("grid_recovery", False, 12.0, False),
            ],
        )

        harness = PreAverageHarness()
        harness.grid_recovery_decision = True
        recovery_resolved = harness._pre_average_input_gate_decision(False, -50.0, 12.0, False)

        self.assertFalse(recovery_resolved.is_pending)
        self.assertTrue(recovery_resolved.resolved_value())

    def test_battery_soc_decision_preserves_resolved_soc_and_terminal_decision(self) -> None:
        harness = PreAverageHarness()
        harness.resolved_battery_soc = 72.5

        battery_soc, early = harness._pre_average_battery_soc_result(70, True, 20.0, False)

        self.assertEqual(battery_soc, 72.5)
        self.assertIsNone(early)
        self.assertEqual(harness.calls, [("resolve_soc", 70, True, 20.0, False)])

        harness = PreAverageHarness()
        harness.resolved_battery_soc = None
        harness.resolve_soc_decision = False

        battery_soc, early = harness._pre_average_battery_soc_result(None, False, 21.0, True)

        self.assertIsNone(battery_soc)
        self.assertIsNotNone(early)
        assert early is not None
        decision, returned_soc = early
        self.assertFalse(decision.resolved_value())
        self.assertIsNone(returned_soc)

    def test_missing_input_result_requires_pv_and_grid_before_averaging(self) -> None:
        harness = PreAverageHarness()

        decision, battery_soc = harness._pre_average_missing_input_result(
            relay_on=True,
            pv_power=1200.0,
            battery_soc=66.0,
            grid_power=-800.0,
            now=30.0,
            cached_inputs=False,
        )

        self.assertIs(decision, NO_RELAY_DECISION)
        self.assertEqual(battery_soc, 66.0)
        self.assertEqual(harness.calls, [])

        harness = PreAverageHarness()
        harness.missing_inputs_decision = True

        decision, battery_soc = harness._pre_average_missing_input_result(
            relay_on=False,
            pv_power=None,
            battery_soc=67.0,
            grid_power=-700.0,
            now=31.0,
            cached_inputs=True,
        )

        self.assertTrue(decision.resolved_value())
        self.assertIsNone(battery_soc)
        self.assertEqual(harness.calls, [("missing_inputs", False, 67.0, -700.0, 31.0, True)])

        harness = PreAverageHarness()
        decision, battery_soc = harness._pre_average_missing_input_result(
            relay_on=True,
            pv_power=500.0,
            battery_soc=68.0,
            grid_power=None,
            now=32.0,
            cached_inputs=False,
        )

        self.assertFalse(decision.resolved_value())
        self.assertIsNone(battery_soc)
        self.assertEqual(harness.calls, [("missing_inputs", True, 68.0, None, 32.0, False)])

    def test_pre_average_decision_stops_at_each_terminal_gate(self) -> None:
        harness = PreAverageHarness()
        harness.mode_uses_auto = False

        decision, battery_soc = harness._pre_average_decision(False, 1000.0, 70.0, -500.0, 40.0, False)

        self.assertTrue(decision.resolved_value())
        self.assertIsNone(battery_soc)
        self.assertEqual(harness.calls, [("mode_uses_auto", 1), ("non_auto", False)])

        harness = PreAverageHarness()
        harness.scheduled_night = True

        decision, battery_soc = harness._pre_average_decision(False, 1000.0, 70.0, -500.0, 41.0, True)

        self.assertIs(decision, NO_RELAY_DECISION)
        self.assertIsNone(battery_soc)
        self.assertEqual(
            harness.calls,
            [
                ("mode_uses_auto", 1),
                ("cutover", False, True),
                ("scheduled_night_active", 41.0),
                ("scheduled_night_active", 41.0),
            ],
        )

        harness = PreAverageHarness()
        harness.resolve_soc_decision = True

        decision, battery_soc = harness._pre_average_decision(False, 1000.0, None, -500.0, 42.0, False)

        self.assertTrue(decision.resolved_value())
        self.assertIsNone(battery_soc)
        self.assertEqual(
            harness.calls,
            [
                ("mode_uses_auto", 1),
                ("cutover", False, False),
                ("scheduled_night_active", 42.0),
                ("grid_recent", -500.0, 42.0),
                ("grid_recovery", False, 42.0, False),
                ("scheduled_night_active", 42.0),
                ("resolve_soc", None, False, 42.0, False),
            ],
        )

        harness = PreAverageHarness()
        harness.missing_inputs_decision = True

        decision, battery_soc = harness._pre_average_decision(True, None, 69.0, -333.0, 43.0, True)

        self.assertTrue(decision.resolved_value())
        self.assertIsNone(battery_soc)
        self.assertEqual(
            harness.calls,
            [
                ("mode_uses_auto", 1),
                ("cutover", True, True),
                ("scheduled_night_active", 43.0),
                ("grid_recent", -333.0, 43.0),
                ("grid_recovery", True, 43.0, True),
                ("scheduled_night_active", 43.0),
                ("resolve_soc", 69.0, True, 43.0, True),
                ("missing_inputs", True, 65.0, -333.0, 43.0, True),
            ],
        )

    def test_post_average_decision_and_average_metrics_report_exact_state(self) -> None:
        harness = PreAverageHarness()
        harness.common_decision = False

        decision, daytime = harness._post_average_decision(True, 10.0, -20.0, 75.0, 50.0, True)

        self.assertFalse(decision.resolved_value())
        self.assertIsNone(daytime)
        self.assertEqual(harness.calls, [("common_runtime", True, 50.0, True)])

        harness = PreAverageHarness()
        harness.daytime_window = False

        decision, daytime = harness._post_average_decision(False, 10.0, -20.0, 75.0, 51.0, False)

        self.assertIs(decision, NO_RELAY_DECISION)
        self.assertFalse(daytime)
        self.assertEqual(harness.calls, [("common_runtime", False, 51.0, False), ("daytime", None)])

        harness = PreAverageHarness()
        self.assertEqual(harness._averaged_auto_metrics(60.0, 1000.0, -300.0, 80.0, True, False), (500.0, -250.0))
        self.assertEqual(harness.health_calls, [])
        self.assertEqual(harness.calls, [("averages", 60.0, 1000.0, -300.0, 80.0, True)])

        harness = PreAverageHarness()
        harness.average_result = (None, -250.0)

        self.assertIsNone(harness._averaged_auto_metrics(61.0, 1000.0, -300.0, 80.0, False, True))
        self.assertEqual(harness.health_calls, [("averaging", True, False)])

        harness = PreAverageHarness()
        harness.average_result = (500.0, None)

        self.assertIsNone(harness._averaged_auto_metrics(62.0, 1000.0, -300.0, 80.0, True, False))
        self.assertEqual(harness.health_calls, [("averaging", False, True)])

    def test_decision_from_averages_uses_terminal_common_gate_or_relay_branch(self) -> None:
        harness = PreAverageHarness()
        harness.common_decision = True

        self.assertTrue(harness._decision_from_averages(False, 150.0, -50.0, 70.0, 90.0, False))
        self.assertEqual(harness.calls, [("common_runtime", False, 90.0, False)])

        harness = PreAverageHarness()
        post_average_calls: list[tuple[Any, ...]] = []

        def post_average_recorder(
            relay_on: bool,
            avg_surplus_power: float,
            avg_grid_power: float,
            battery_soc: float,
            now: float,
            cached_inputs: bool,
        ) -> tuple[RelayDecisionState, bool]:
            post_average_calls.append((relay_on, avg_surplus_power, avg_grid_power, battery_soc, now, cached_inputs))
            return NO_RELAY_DECISION, True

        setattr(harness, "_post_average_decision", post_average_recorder)
        self.assertTrue(harness._decision_from_averages(True, 153.0, -53.0, 73.0, 93.0, True))
        self.assertEqual(post_average_calls, [(True, 153.0, -53.0, 73.0, 93.0, True)])
        self.assertEqual(harness.calls, [("relay_on", 153.0, -53.0, 73.0, True, 93.0, True)])

        harness = PreAverageHarness()
        self.assertTrue(harness._decision_from_averages(True, 151.0, -51.0, 71.0, 91.0, True))
        self.assertEqual(
            harness.calls,
            [
                ("common_runtime", True, 91.0, True),
                ("daytime", None),
                ("relay_on", 151.0, -51.0, 71.0, True, 91.0, True),
            ],
        )

        harness = PreAverageHarness()
        harness.daytime_window = False

        self.assertFalse(harness._decision_from_averages(False, 152.0, -52.0, 72.0, 92.0, False))
        self.assertEqual(
            harness.calls,
            [
                ("common_runtime", False, 92.0, False),
                ("daytime", None),
                ("relay_off", 152.0, -52.0, 72.0, False, 92.0, False),
            ],
        )

    def test_auto_decide_relay_forwards_cached_inputs_time_and_preaverage_result(self) -> None:
        harness = PreAverageHarness()
        harness.service._auto_cached_inputs_used = True
        harness.common_decision = True

        self.assertTrue(harness.auto_decide_relay(relay_on=False, pv_power=1100.0, battery_soc=77.0, grid_power=-600.0))
        self.assertEqual(harness.calls[0], ("now",))
        self.assertIn(("averages", 123.0, 1100.0, -600.0, 65.0, False), harness.calls)
        self.assertIn(("common_runtime", False, 123.0, True), harness.calls)

        harness = PreAverageHarness()
        harness.non_auto_decision = False
        harness.mode_uses_auto = False

        self.assertFalse(harness.auto_decide_relay(relay_on=True, pv_power=900.0, battery_soc=55.0, grid_power=-200.0))
        self.assertEqual(harness.calls, [("now",), ("mode_uses_auto", 1), ("non_auto", True)])

        harness = PreAverageHarness()
        pre_average_calls: list[tuple[Any, ...]] = []
        after_pre_average_calls: list[tuple[Any, ...]] = []

        def pre_average_recorder(
            relay_on: bool,
            pv_power: float | None,
            battery_soc: float | int | None,
            grid_power: float | None,
            now: float,
            cached_inputs: bool,
        ) -> tuple[RelayDecisionState, float | None]:
            pre_average_calls.append((relay_on, pv_power, battery_soc, grid_power, now, cached_inputs))
            return NO_RELAY_DECISION, 44.0

        def after_pre_average_recorder(
            relay_on: bool,
            pv_power: float | None,
            battery_soc: float | None,
            grid_power: float | None,
            now: float,
            cached_inputs: bool,
            pre_average_decision: RelayDecisionState,
        ) -> bool:
            after_pre_average_calls.append(
                (relay_on, pv_power, battery_soc, grid_power, now, cached_inputs, pre_average_decision)
            )
            return cached_inputs

        setattr(harness, "_pre_average_decision", pre_average_recorder)
        setattr(harness, "_auto_decision_after_pre_average", after_pre_average_recorder)
        harness.service._auto_cached_inputs_used = True

        self.assertTrue(harness.auto_decide_relay(relay_on=False, pv_power=901.0, battery_soc=56.0, grid_power=-201.0))
        self.assertEqual(pre_average_calls, [(False, 901.0, 56.0, -201.0, 123.0, True)])
        self.assertEqual(after_pre_average_calls, [(False, 901.0, 44.0, -201.0, 123.0, True, NO_RELAY_DECISION)])

        harness = PreAverageHarness()
        pre_average_calls = []
        after_pre_average_calls = []
        setattr(harness, "_pre_average_decision", pre_average_recorder)
        setattr(harness, "_auto_decision_after_pre_average", after_pre_average_recorder)
        delattr(harness.service, "_auto_cached_inputs_used")

        self.assertFalse(harness.auto_decide_relay(relay_on=True, pv_power=902.0, battery_soc=57.0, grid_power=-202.0))
        self.assertEqual(pre_average_calls, [(True, 902.0, 57.0, -202.0, 123.0, False)])
        self.assertEqual(after_pre_average_calls, [(True, 902.0, 44.0, -202.0, 123.0, False, NO_RELAY_DECISION)])

    def test_decision_after_pre_average_handles_scheduled_night_averaging_and_missing_averages(self) -> None:
        harness = PreAverageHarness()
        resolved = RelayDecisionState.resolved(False)

        self.assertFalse(
            harness._auto_decision_after_pre_average(
                relay_on=True,
                pv_power=1000.0,
                battery_soc=70.0,
                grid_power=-400.0,
                now=130.0,
                cached_inputs=False,
                pre_average_decision=resolved,
            )
        )
        self.assertEqual(harness.calls, [])

        harness = PreAverageHarness()
        harness.scheduled_night = True

        self.assertTrue(
            harness._auto_decision_after_pre_average(
                relay_on=False,
                pv_power=None,
                battery_soc=None,
                grid_power=None,
                now=131.0,
                cached_inputs=True,
                pre_average_decision=NO_RELAY_DECISION,
            )
        )
        self.assertEqual(
            harness.calls,
            [
                ("scheduled_night_active", 131.0),
                ("scheduled_night_decision", False, 131.0, True),
            ],
        )

        harness = PreAverageHarness()
        harness.average_result = (None, None)

        self.assertTrue(
            harness._auto_decision_after_pre_average(
                relay_on=True,
                pv_power=1001.0,
                battery_soc=71.0,
                grid_power=-401.0,
                now=132.0,
                cached_inputs=False,
                pre_average_decision=NO_RELAY_DECISION,
            )
        )
        self.assertEqual(harness.health_calls, [("averaging", False, True)])

        harness = PreAverageHarness()
        harness.common_decision = False

        self.assertFalse(
            harness._auto_decision_after_pre_average(
                relay_on=True,
                pv_power=1002.0,
                battery_soc=72.0,
                grid_power=-402.0,
                now=133.0,
                cached_inputs=True,
                pre_average_decision=NO_RELAY_DECISION,
            )
        )
        self.assertIn(("common_runtime", True, 133.0, True), harness.calls)

    def test_required_average_inputs_rejects_missing_values(self) -> None:
        self.assertEqual(PreAverageHarness._required_average_inputs(1.0, 2.0, 3.0), (1.0, 2.0, 3.0))

        with self.assertRaises(AssertionError):
            PreAverageHarness._required_average_inputs(None, 2.0, 3.0)
        with self.assertRaises(AssertionError):
            PreAverageHarness._required_average_inputs(1.0, None, 3.0)
        with self.assertRaises(AssertionError):
            PreAverageHarness._required_average_inputs(1.0, 2.0, None)

    def test_decision_from_available_averages_returns_current_relay_until_ready(self) -> None:
        harness = PreAverageHarness()

        self.assertTrue(harness._decision_from_available_averages(True, 60.0, 140.0, False, None))
        self.assertFalse(harness._decision_from_available_averages(False, 60.0, 140.0, False, None))
        self.assertEqual(harness.calls, [])

        harness.common_decision = True
        self.assertTrue(harness._decision_from_available_averages(False, 61.0, 141.0, True, (10.0, -5.0)))
        self.assertEqual(harness.calls, [("common_runtime", False, 141.0, True)])

        harness = PreAverageHarness()
        average_calls: list[tuple[Any, ...]] = []

        def decision_from_averages_recorder(
            relay_on: bool,
            avg_surplus_power: float,
            avg_grid_power: float,
            battery_soc: float,
            now: float,
            cached_inputs: bool,
        ) -> bool:
            average_calls.append((relay_on, avg_surplus_power, avg_grid_power, battery_soc, now, cached_inputs))
            return True

        setattr(harness, "_decision_from_averages", decision_from_averages_recorder)

        self.assertTrue(harness._decision_from_available_averages(False, 62.0, 142.0, True, (11.0, -6.0)))
        self.assertEqual(average_calls, [(False, 11.0, -6.0, 62.0, 142.0, True)])


if __name__ == "__main__":
    unittest.main()

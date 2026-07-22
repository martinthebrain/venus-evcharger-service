#!/usr/bin/env python3
"""Behavioral contracts for DBus adapter SLO and pressure regulation."""

from __future__ import annotations

import json
import unittest

from venus_evcharger.dbus_adapter.health.slo import (
    SloThresholds,
    core_read_stale,
    effective_gui_max_age_seconds,
    effective_mainloop_gap_max_ms,
    higher_pressure_state,
    max_core_read_age,
    pressure_limited_publish_burst,
    pressure_limited_queue_budgets,
    regulated_publish_burst,
    runtime_pressure_state,
    slo_checks_from_observed,
    slo_payload,
    slo_targets,
    stale_core_read_keys,
)


def thresholds() -> SloThresholds:
    return SloThresholds(
        gui_max_age_seconds=2.0,
        core_read_max_age_seconds=5.0,
        queue_max_age_seconds=7.0,
        mainloop_gap_max_ms=100.0,
        tick_seconds=0.2,
        max_tick_seconds=0.6,
    )


class DbusAdapterHealthSloContractTests(unittest.TestCase):
    def test_payload_preserves_check_order_and_copies_inputs(self) -> None:
        checks = {"first": True, "second": False, "third": False}
        targets = {"age": 5.0}
        observed = {"age": 6.0}
        payload = slo_payload(checks, targets, observed)
        self.assertEqual(
            payload,
            {
                "state": "violated",
                "violated": ["second", "third"],
                "checks": checks,
                "targets": targets,
                "observed": observed,
            },
        )
        self.assertIsNot(payload["checks"], checks)
        self.assertIsNot(payload["targets"], targets)
        self.assertIsNot(payload["observed"], observed)
        self.assertEqual(
            slo_payload({"fresh": True}, {}, {}),
            {"state": "ok", "violated": [], "checks": {"fresh": True}, "targets": {}, "observed": {}},
        )

    def test_effective_targets_include_adaptive_tick_and_core_read_floors(self) -> None:
        current = thresholds()
        self.assertEqual(effective_gui_max_age_seconds(current), 10.0)
        self.assertEqual(effective_mainloop_gap_max_ms(current), 1500.0)
        self.assertEqual(
            slo_targets(current),
            {
                "gui_max_age_s": 10.0,
                "gui_measurement_max_age_s": 10.0,
                "gui_control_max_age_s": 10.0,
                "gui_session_max_age_s": 10.0,
                "configured_gui_max_age_s": 2.0,
                "core_read_max_age_s": 5.0,
                "queue_max_age_s": 7.0,
                "mainloop_gap_max_ms": 1500.0,
            },
        )
        configured = SloThresholds(12.0, 1.0, 7.0, 3000.0, 0.2, 0.6)
        self.assertEqual(effective_gui_max_age_seconds(configured), 12.0)
        self.assertEqual(effective_mainloop_gap_max_ms(configured), 3000.0)

    def test_checks_accept_exact_limits_and_reject_values_just_above(self) -> None:
        at_limit = {
            "gui_max_age_s": 10.0,
            "gui_measurement_max_age_s": 10.0,
            "gui_control_max_age_s": 10.0,
            "gui_session_max_age_s": 10.0,
            "core_read_max_age_s": 5.0,
            "queue_oldest_age_s": 7.0,
            "mainloop_max_gap_ms_60s": 1500.0,
        }
        self.assertEqual(slo_checks_from_observed(at_limit, thresholds()), {key: True for key in (
            "gui_fresh",
            "gui_measurements_fresh",
            "gui_controls_fresh",
            "gui_session_fresh",
            "core_reads_fresh",
            "queue_age_ok",
            "mainloop_gap_ok",
        )})
        over_limit = {key: value + 0.1 for key, value in at_limit.items()}
        self.assertEqual(slo_checks_from_observed(over_limit, thresholds()), {key: False for key in (
            "gui_fresh",
            "gui_measurements_fresh",
            "gui_controls_fresh",
            "gui_session_fresh",
            "core_reads_fresh",
            "queue_age_ok",
            "mainloop_gap_ok",
        )})
        self.assertTrue(all(slo_checks_from_observed({}, thresholds()).values()))
        tiny = SloThresholds(0.1, 0.1, 0.1, 0.1, 0.0001, 0.0001)
        self.assertTrue(all(slo_checks_from_observed({}, tiny).values()))

    def test_core_read_age_and_staleness_require_fresh_complete_entries(self) -> None:
        freshness = {
            "grid_power_w_age_s": "3.5",
            "pv_power_w_age_s": 7.25,
            "battery_soc_age_s": "bad",
            "ignored_age_s": 99.0,
        }
        self.assertEqual(max_core_read_age(freshness), 7.25)
        self.assertEqual(max_core_read_age({}), 0.0)
        for key, age in (("grid_power_w", 1.5), ("pv_power_w", 2.5), ("battery_soc", 3.5)):
            self.assertEqual(max_core_read_age({f"{key}_age_s": age}), age)

        complete = {"grid_power_w_status": "fresh", "grid_power_w_age_s": 5.0}
        self.assertFalse(core_read_stale("grid_power_w", complete, max_age_seconds=5.0))
        self.assertTrue(core_read_stale("grid_power_w", complete | {"grid_power_w_age_s": 5.01}, max_age_seconds=5.0))
        self.assertTrue(core_read_stale("grid_power_w", complete | {"grid_power_w_status": "stale"}, max_age_seconds=5.0))
        self.assertTrue(core_read_stale("grid_power_w", {"grid_power_w_status": "fresh"}, max_age_seconds=5.0))
        self.assertTrue(core_read_stale("grid_power_w", {"grid_power_w_age_s": 1.0}, max_age_seconds=5.0))
        self.assertEqual(
            stale_core_read_keys(
                complete | {"pv_power_w_status": "error", "pv_power_w_age_s": 0.1},
                ("grid_power_w", "pv_power_w", "battery_soc"),
                max_age_seconds=5.0,
            ),
            {"pv_power_w", "battery_soc"},
        )

    def test_regulated_burst_uses_strict_thresholds_caps_and_gap_reduction(self) -> None:
        current = thresholds()
        self.assertEqual(regulated_publish_burst(queue_age=7.0, eventloop_gap_ms=1500.0, base_burst=4, thresholds=current), 4)
        self.assertEqual(regulated_publish_burst(queue_age=7.1, eventloop_gap_ms=1500.0, base_burst=4, thresholds=current), 12)
        self.assertEqual(regulated_publish_burst(queue_age=7.1, eventloop_gap_ms=1500.0, base_burst=1, thresholds=current), 5)
        self.assertEqual(regulated_publish_burst(queue_age=70.0, eventloop_gap_ms=1500.0, base_burst=20, thresholds=current), 50)
        self.assertEqual(regulated_publish_burst(queue_age=7.1, eventloop_gap_ms=1500.1, base_burst=4, thresholds=current), 2)
        self.assertEqual(regulated_publish_burst(queue_age=7.0, eventloop_gap_ms=1500.1, base_burst=5, thresholds=current), 2)
        self.assertEqual(regulated_publish_burst(queue_age=7.1, eventloop_gap_ms=1500.1, base_burst=1, thresholds=current), 1)

    def test_pressure_state_uses_highest_severity_and_unknown_is_ok(self) -> None:
        self.assertEqual(runtime_pressure_state("ok", "ok"), "ok")
        self.assertEqual(runtime_pressure_state("busy", "ok"), "congested")
        self.assertEqual(runtime_pressure_state("constrained", "congested"), "slow")
        self.assertEqual(runtime_pressure_state("unknown", "protective"), "protective")
        self.assertEqual(runtime_pressure_state("unknown", "unknown"), "ok")
        self.assertEqual(higher_pressure_state("congested", "slow"), "slow")
        self.assertEqual(higher_pressure_state("protective", "slow"), "protective")
        self.assertEqual(higher_pressure_state("slow", "slow"), "slow")

    def test_publish_pressure_caps_never_return_less_than_one(self) -> None:
        self.assertEqual(pressure_limited_publish_burst(0, base_burst=20, pressure_state="ok"), 1)
        self.assertEqual(pressure_limited_publish_burst(99, base_burst=20, pressure_state="congested"), 10)
        self.assertEqual(pressure_limited_publish_burst(3, base_burst=20, pressure_state="congested"), 3)
        self.assertEqual(pressure_limited_publish_burst(99, base_burst=20, pressure_state="slow"), 5)
        self.assertEqual(pressure_limited_publish_burst(2, base_burst=20, pressure_state="slow"), 2)
        self.assertEqual(pressure_limited_publish_burst(99, base_burst=20, pressure_state="protective"), 1)
        self.assertEqual(pressure_limited_publish_burst(99, base_burst=1, pressure_state="slow"), 1)
        self.assertEqual(pressure_limited_publish_burst(99, base_burst=7, pressure_state="slow"), 1)
        self.assertEqual(pressure_limited_publish_burst(99, base_burst=3, pressure_state="congested"), 1)
        self.assertEqual(pressure_limited_publish_burst(0, base_burst=1, pressure_state="congested"), 1)
        with self.assertRaisesRegex(ValueError, "^base publish burst must be positive$"):
            pressure_limited_publish_burst(1, base_burst=0, pressure_state="ok")
        with self.assertRaisesRegex(ValueError, "^unknown gateway pressure state: invalid$"):
            pressure_limited_publish_burst(1, base_burst=1, pressure_state=json.loads('"invalid"'))

    def test_queue_pressure_caps_copy_budgets_and_do_not_increase_existing_limits(self) -> None:
        budgets = {
            "gui-critical-publish": 50,
            "local-publish": 30,
            "diagnostic": 1,
            "discovery": 1,
            "introspection": 1,
            "remote-write": 4,
        }
        self.assertEqual(pressure_limited_queue_budgets(budgets, base_local_publish_burst=20, pressure_state="ok"), budgets)
        self.assertEqual(
            pressure_limited_queue_budgets(budgets, base_local_publish_burst=20, pressure_state="congested"),
            {"gui-critical-publish": 10, "local-publish": 5, "diagnostic": 0, "discovery": 0, "introspection": 0, "remote-write": 4},
        )
        self.assertEqual(
            pressure_limited_queue_budgets(budgets, base_local_publish_burst=20, pressure_state="slow"),
            {"gui-critical-publish": 5, "local-publish": 1, "diagnostic": 0, "discovery": 0, "introspection": 0, "remote-write": 4},
        )
        self.assertEqual(
            pressure_limited_queue_budgets(budgets, base_local_publish_burst=20, pressure_state="protective"),
            {"gui-critical-publish": 1, "local-publish": 1, "diagnostic": 0, "discovery": 0, "introspection": 0, "remote-write": 4},
        )
        self.assertEqual(
            pressure_limited_queue_budgets(
                {"gui-critical-publish": 2, "local-publish": 0, "diagnostic": 0},
                base_local_publish_burst=20,
                pressure_state="congested",
            ),
            {
                "gui-critical-publish": 2,
                "local-publish": 0,
                "diagnostic": 0,
                "discovery": 0,
                "introspection": 0,
            },
        )
        self.assertEqual(
            pressure_limited_queue_budgets({}, base_local_publish_burst=7, pressure_state="slow"),
            {
                "gui-critical-publish": 1,
                "local-publish": 1,
                "diagnostic": 0,
                "discovery": 0,
                "introspection": 0,
            },
        )
        self.assertEqual(
            pressure_limited_queue_budgets({}, base_local_publish_burst=7, pressure_state="congested"),
            {
                "gui-critical-publish": 3,
                "local-publish": 1,
                "diagnostic": 0,
                "discovery": 0,
                "introspection": 0,
            },
        )
        high = {"gui-critical-publish": 50, "local-publish": 50, "diagnostic": 1}
        self.assertEqual(
            pressure_limited_queue_budgets(high, base_local_publish_burst=7, pressure_state="slow"),
            {
                "gui-critical-publish": 1,
                "local-publish": 1,
                "diagnostic": 0,
                "discovery": 0,
                "introspection": 0,
            },
        )
        self.assertEqual(
            pressure_limited_queue_budgets(high, base_local_publish_burst=7, pressure_state="congested"),
            {
                "gui-critical-publish": 3,
                "local-publish": 1,
                "diagnostic": 0,
                "discovery": 0,
                "introspection": 0,
            },
        )
        self.assertEqual(
            pressure_limited_queue_budgets(high, base_local_publish_burst=1, pressure_state="congested"),
            {
                "gui-critical-publish": 1,
                "local-publish": 1,
                "diagnostic": 0,
                "discovery": 0,
                "introspection": 0,
            },
        )
        with self.assertRaisesRegex(ValueError, "^base publish burst must be positive$"):
            pressure_limited_queue_budgets({}, base_local_publish_burst=0, pressure_state="ok")
        with self.assertRaisesRegex(ValueError, "^unknown gateway pressure state: invalid$"):
            pressure_limited_queue_budgets(
                {},
                base_local_publish_burst=1,
                pressure_state=json.loads('"invalid"'),
            )
        self.assertEqual(
            budgets,
            {
                "gui-critical-publish": 50,
                "local-publish": 30,
                "diagnostic": 1,
                "discovery": 1,
                "introspection": 1,
                "remote-write": 4,
            },
        )


if __name__ == "__main__":
    unittest.main()

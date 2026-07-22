# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway adapter cache, queue, and SLO metric contracts."""

from __future__ import annotations

from tests.support.dbus_gateway_adapter_harness import (
    DbusCacheStore,
    GatewayAdapterContractCase,
    dbus_path_key,
    health_freshness_module,
    health_queue_module,
    health_slo_module,
)


class GatewayHealthMetricCases(GatewayAdapterContractCase):
    """Exercise cache, queue, and SLO metric contracts."""

    def test_cache_freshness_helpers_report_status_age_and_path_contracts(self) -> None:
        values = {
            "grid_power_w": {"age_s": "1.25", "status": "fresh"},
            "pv_power_w": {"age_s": 2.5, "status": "error"},
            "battery_soc": {"updated_at": 95.0},
            dbus_path_key("svc", "/Fresh"): {"updated_at": 90.0, "value": "12.5"},
            dbus_path_key("svc", "/Future"): {"updated_at": 110.0, "value": object()},
            "unknown_status": {},
        }

        self.assertEqual(
            health_freshness_module.status_counts(values),
            {"fresh": 1, "error": 1, "unknown": 4},
        )
        self.assertEqual(
            health_freshness_module.important_freshness(values),
            {
                "grid_power_w_age_s": 1.25,
                "pv_power_w_age_s": 2.5,
                "battery_soc_age_s": 0.0,
                "grid_power_w_status": "fresh",
                "pv_power_w_status": "error",
                "battery_soc_status": "missing",
            },
        )
        self.assertEqual(health_freshness_module.cached_entry_age(values[dbus_path_key("svc", "/Fresh")], 100.0), 10.0)
        self.assertEqual(health_freshness_module.cached_entry_age(values[dbus_path_key("svc", "/Future")], 100.0), 0.0)
        self.assertEqual(health_freshness_module.cached_entry_age({"updated_at": 0.5}, 1.0), 0.5)
        self.assertEqual(health_freshness_module.cached_entry_age(object(), 100.0), 0.0)
        self.assertEqual(
            health_freshness_module.max_cached_path_age(values, "svc", {"/Fresh", "/Future", "/Missing"}, 100.0),
            10.0,
        )
        self.assertEqual(health_freshness_module.missing_cached_path_count(values, "svc", {"/Fresh", "/Missing"}), 1.0)
        self.assertEqual(health_freshness_module.cached_entry_float(values[dbus_path_key("svc", "/Fresh")]), 12.5)
        self.assertEqual(health_freshness_module.cached_entry_float(values[dbus_path_key("svc", "/Future")]), 0.0)
        self.assertEqual(health_freshness_module.cached_entry_float(object()), 0.0)

        cache = DbusCacheStore(stale_after_seconds=10.0)
        cache.update_value("grid_power_w", 123.0, source="grid", status="fresh", now=99.0)
        cache.update_value("pv_power_w", 45.0, source="pv", status="cached", now=98.0)
        cache.mark_error("pv_power_w", source="pv", error="no reply", now=100.0)
        snapshot = health_freshness_module.cache_freshness(cache, 100.0)
        self.assertEqual(snapshot["value_count"], 2)
        self.assertEqual(snapshot["status_counts"], {"fresh": 1, "error": 1})
        self.assertEqual(snapshot["grid_power_w_age_s"], 1.0)
        self.assertEqual(snapshot["pv_power_w_status"], "error")
        self.assertEqual(snapshot["battery_soc_status"], "missing")

    def test_queue_health_helpers_report_classes_ages_and_drain_contracts(self) -> None:
        pending = [
            ("slow-old.json", {"queue_class": "read-slow", "created_at": 90.0}),
            ("slow-updated.json", {"queue_class": "read-slow", "created_at": 1.0, "updated_at": 97.0}),
            ("remote.json", {"queue_class": "remote-write", "created_at": 95.0}),
            (
                "fast-fallback.json",
                {"kind": "refresh_energy_inputs", "scope": "grid", "created_at": 96.0},
            ),
            (
                "gui-fallback.json",
                {
                    "kind": "publish_evcs_fields",
                    "publication_priority": "critical",
                    "created_at": 98.0,
                },
            ),
        ]
        core_pending = [
            ("core.json", {"created_at": 80.0}),
            ("core-updated.json", {"created_at": 1.0, "updated_at": 99.0}),
        ]

        self.assertEqual(health_queue_module.command_activity_at({"created_at": 1.0, "updated_at": 99.0}, 100.0), 99.0)
        self.assertEqual(health_queue_module.command_activity_at({"created_at": "bad"}, 100.0), 100.0)
        self.assertEqual(health_queue_module.command_activity_at({"updated_at": 101.0}, 100.0), 101.0)
        self.assertEqual(health_queue_module.command_activity_at({"updated_at": 0.5}, 1.0), 0.5)
        self.assertEqual(health_queue_module.oldest_command_age(pending, 100.0), 10.0)
        self.assertEqual(health_queue_module.oldest_command_age([("future.json", {"created_at": 101.0})], 100.0), 0.0)
        self.assertEqual(health_queue_module.oldest_command_age([], 100.0), 0.0)
        self.assertEqual(health_queue_module.physical_command_count_from_pending(pending, None), len(pending))
        self.assertEqual(health_queue_module.physical_command_count_from_pending(pending, 7), 7)

        self.assertEqual(
            health_queue_module.queue_class_health(
                [("future.json", {"queue_class": "diagnostic", "created_at": 101.0})], 100.0
            ),
            {"diagnostic": {"pending": 1, "oldest_age_s": 0.0}},
        )
        self.assertEqual(
            health_queue_module.queue_class_health(
                [("tiny.json", {"queue_class": "diagnostic", "created_at": 99.5})], 100.0
            ),
            {"diagnostic": {"pending": 1, "oldest_age_s": 0.5}},
        )
        self.assertEqual(
            health_queue_module.queue_class_health(
                [("bad.json", {"queue_class": "diagnostic", "created_at": "bad"})], 100.0
            ),
            {"diagnostic": {"pending": 1, "oldest_age_s": 0.0}},
        )
        self.assertEqual(
            health_queue_module.queue_class_health(pending, 100.0),
            {
                "gui-critical-publish": {"pending": 1, "oldest_age_s": 2.0},
                "read-fast": {"pending": 1, "oldest_age_s": 4.0},
                "read-slow": {"pending": 2, "oldest_age_s": 10.0},
                "remote-write": {"pending": 1, "oldest_age_s": 5.0},
            },
        )
        self.assertEqual(
            health_queue_module.queue_health(
                pending,
                core_pending,
                100.0,
                physical_count=7,
                write_scheduler_health={"processed_commands_60s": "30", "last_processed_at": "88.5"},
            ),
            {
                "pending_command_count": 5,
                "physical_command_count": 7,
                "oldest_command_age_s": 10.0,
                "core_command_count": 2,
                "oldest_core_command_age_s": 20.0,
                "processed_commands_60s": 30,
                "queue_drain_rate_per_s": 0.5,
                "last_processed_at": 88.5,
            },
        )
        self.assertEqual(
            health_queue_module.queue_health(pending, [], 100.0)["physical_command_count"],
            len(pending),
        )

    def test_slo_helpers_report_exact_targets_boundaries_and_burst_contracts(self) -> None:
        thresholds = health_slo_module.SloThresholds(
            gui_max_age_seconds=2.0,
            core_read_max_age_seconds=5.0,
            queue_max_age_seconds=7.0,
            mainloop_gap_max_ms=100.0,
            tick_seconds=0.2,
            max_tick_seconds=0.6,
        )
        observed_at_limit = {
            "gui_max_age_s": 10.0,
            "gui_measurement_max_age_s": 10.0,
            "gui_control_max_age_s": 10.0,
            "gui_session_max_age_s": 10.0,
            "core_read_max_age_s": 5.0,
            "queue_oldest_age_s": 7.0,
            "mainloop_max_gap_ms_60s": 1500.0,
        }

        self.assertEqual(health_slo_module.effective_gui_max_age_seconds(thresholds), 10.0)
        self.assertEqual(health_slo_module.effective_mainloop_gap_max_ms(thresholds), 1500.0)
        self.assertEqual(
            health_slo_module.slo_targets(thresholds),
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
        self.assertEqual(
            health_slo_module.slo_checks_from_observed(observed_at_limit, thresholds),
            {
                "gui_fresh": True,
                "gui_measurements_fresh": True,
                "gui_controls_fresh": True,
                "gui_session_fresh": True,
                "core_reads_fresh": True,
                "queue_age_ok": True,
                "mainloop_gap_ok": True,
            },
        )
        violated_observed = {
            "gui_max_age_s": 10.1,
            "gui_measurement_max_age_s": 10.2,
            "gui_control_max_age_s": 10.3,
            "gui_session_max_age_s": 10.4,
            "core_read_max_age_s": 5.1,
            "queue_oldest_age_s": 7.1,
            "mainloop_max_gap_ms_60s": 1500.1,
        }
        violated_checks = health_slo_module.slo_checks_from_observed(violated_observed, thresholds)
        self.assertEqual(
            violated_checks,
            {
                "gui_fresh": False,
                "gui_measurements_fresh": False,
                "gui_controls_fresh": False,
                "gui_session_fresh": False,
                "core_reads_fresh": False,
                "queue_age_ok": False,
                "mainloop_gap_ok": False,
            },
        )
        self.assertEqual(
            health_slo_module.slo_payload(
                violated_checks,
                health_slo_module.slo_targets(thresholds),
                violated_observed,
            ),
            {
                "state": "violated",
                "violated": [
                    "gui_fresh",
                    "gui_measurements_fresh",
                    "gui_controls_fresh",
                    "gui_session_fresh",
                    "core_reads_fresh",
                    "queue_age_ok",
                    "mainloop_gap_ok",
                ],
                "checks": violated_checks,
                "targets": health_slo_module.slo_targets(thresholds),
                "observed": violated_observed,
            },
        )
        self.assertEqual(
            health_slo_module.slo_payload({"gui_fresh": True}, {"gui_max_age_s": 10.0}, {}),
            {
                "state": "ok",
                "violated": [],
                "checks": {"gui_fresh": True},
                "targets": {"gui_max_age_s": 10.0},
                "observed": {},
            },
        )
        self.assertEqual(health_slo_module.slo_checks_from_observed({}, thresholds)["gui_fresh"], True)
        tiny_thresholds = health_slo_module.SloThresholds(
            gui_max_age_seconds=0.1,
            core_read_max_age_seconds=0.1,
            queue_max_age_seconds=0.1,
            mainloop_gap_max_ms=0.1,
            tick_seconds=0.0001,
            max_tick_seconds=0.0001,
        )
        self.assertEqual(
            health_slo_module.slo_checks_from_observed({}, tiny_thresholds),
            {
                "gui_fresh": True,
                "gui_measurements_fresh": True,
                "gui_controls_fresh": True,
                "gui_session_fresh": True,
                "core_reads_fresh": True,
                "queue_age_ok": True,
                "mainloop_gap_ok": True,
            },
        )

        mainloop_configured = health_slo_module.SloThresholds(
            gui_max_age_seconds=12.0,
            core_read_max_age_seconds=1.0,
            queue_max_age_seconds=7.0,
            mainloop_gap_max_ms=3000.0,
            tick_seconds=0.2,
            max_tick_seconds=0.6,
        )
        self.assertEqual(health_slo_module.effective_gui_max_age_seconds(mainloop_configured), 12.0)
        self.assertEqual(health_slo_module.effective_mainloop_gap_max_ms(mainloop_configured), 3000.0)
        self.assertEqual(
            health_slo_module.max_core_read_age(
                {
                    "grid_power_w_age_s": "3.5",
                    "pv_power_w_age_s": 7.25,
                    "battery_soc_age_s": "bad",
                    "ignored_age_s": 99.0,
                }
            ),
            7.25,
        )
        self.assertEqual(health_slo_module.max_core_read_age({"grid_power_w_age_s": 1.5}), 1.5)
        self.assertEqual(health_slo_module.max_core_read_age({"pv_power_w_age_s": 2.5}), 2.5)
        self.assertEqual(health_slo_module.max_core_read_age({"battery_soc_age_s": 3.5}), 3.5)
        self.assertEqual(health_slo_module.max_core_read_age({"ignored_age_s": 99.0}), 0.0)
        self.assertEqual(
            health_slo_module.stale_core_read_keys(
                {
                    "grid_power_w_status": "fresh",
                    "grid_power_w_age_s": 4.9,
                    "pv_power_w_status": "stale",
                    "pv_power_w_age_s": 0.1,
                },
                ("grid_power_w", "pv_power_w", "battery_soc"),
                max_age_seconds=5.0,
            ),
            {"pv_power_w", "battery_soc"},
        )

        self.assertEqual(
            health_slo_module.regulated_publish_burst(
                queue_age=7.0,
                eventloop_gap_ms=1500.0,
                base_burst=4,
                thresholds=thresholds,
            ),
            4,
        )
        self.assertEqual(
            health_slo_module.regulated_publish_burst(
                queue_age=7.1,
                eventloop_gap_ms=1500.0,
                base_burst=4,
                thresholds=thresholds,
            ),
            12,
        )
        self.assertEqual(
            health_slo_module.regulated_publish_burst(
                queue_age=7.1,
                eventloop_gap_ms=1500.0,
                base_burst=1,
                thresholds=thresholds,
            ),
            5,
        )
        self.assertEqual(
            health_slo_module.regulated_publish_burst(
                queue_age=70.0,
                eventloop_gap_ms=1500.0,
                base_burst=20,
                thresholds=thresholds,
            ),
            50,
        )
        self.assertEqual(
            health_slo_module.regulated_publish_burst(
                queue_age=7.1,
                eventloop_gap_ms=1500.1,
                base_burst=4,
                thresholds=thresholds,
            ),
            2,
        )
        self.assertEqual(
            health_slo_module.regulated_publish_burst(
                queue_age=7.0,
                eventloop_gap_ms=1500.1,
                base_burst=5,
                thresholds=thresholds,
            ),
            2,
        )
        self.assertEqual(
            health_slo_module.regulated_publish_burst(
                queue_age=7.1,
                eventloop_gap_ms=1500.1,
                base_burst=1,
                thresholds=thresholds,
            ),
            1,
        )
        self.assertEqual(health_slo_module.runtime_pressure_state("ok", "protective"), "protective")
        self.assertEqual(health_slo_module.runtime_pressure_state("constrained", "ok"), "slow")
        self.assertEqual(health_slo_module.runtime_pressure_state("ok", "slow"), "slow")
        self.assertEqual(health_slo_module.runtime_pressure_state("busy", "ok"), "congested")
        self.assertEqual(health_slo_module.runtime_pressure_state("ok", "congested"), "congested")
        self.assertEqual(health_slo_module.runtime_pressure_state("ok", "ok"), "ok")
        self.assertEqual(
            health_slo_module.regulated_publish_burst(
                queue_age=70.0,
                eventloop_gap_ms=1500.0,
                base_burst=20,
                thresholds=thresholds,
                pressure_state="congested",
            ),
            10,
        )
        self.assertEqual(
            health_slo_module.pressure_limited_publish_burst(99, base_burst=20, pressure_state="slow"),
            5,
        )
        self.assertEqual(
            health_slo_module.pressure_limited_publish_burst(99, base_burst=20, pressure_state="protective"),
            1,
        )
        self.assertEqual(
            health_slo_module.pressure_limited_queue_budgets(
                {"gui-critical-publish": 50, "local-publish": 30, "diagnostic": 1},
                base_local_publish_burst=20,
                pressure_state="slow",
            ),
            {"gui-critical-publish": 5, "local-publish": 1, "diagnostic": 0},
        )

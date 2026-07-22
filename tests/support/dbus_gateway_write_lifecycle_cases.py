# SPDX-License-Identifier: GPL-3.0-or-later
"""Semantic command lifecycle and prioritization scenarios."""

from __future__ import annotations

from tests.support.dbus_gateway_adapter_harness import (
    GatewayAdapterContractCase,
    evcs_publication,
    patch,
    time,
    write_health_module,
)
from venus_evcharger.ipc.gateway_operations import gx_relay_refresh_command


class GatewayWriteLifecycleCases(GatewayAdapterContractCase):
    """Verify scheduler health and ordering with semantic command kinds."""

    def test_scheduler_configuration_clamps_queue_budgets(self) -> None:
        with self.adapter_scenario(
            "[DEFAULT]\n"
            "DbusGatewayLocalPublishBurstLimit=0\n"
            "DbusGatewayLocalPublishTickBudgetMs=0\n"
            "DbusGatewayQueueBudgetRemoteWrite=0\n"
            "DbusGatewayQueueBudgetDiagnostic=-1\n"
        ) as scenario:
            scheduler = scenario.adapter.write_scheduler
            self.assertEqual(scheduler.local_publish_burst_limit, 1)
            self.assertEqual(scheduler.local_publish_tick_budget_seconds, 0.001)
            self.assertEqual(scheduler.queue_class_budgets["remote-write"], 1)
            self.assertEqual(scheduler.queue_class_budgets["diagnostic"], 0)

    def test_semantic_priority_order_is_stable_by_age(self) -> None:
        safety = {**evcs_publication({"mode": 0}, priority="critical"), "created_at": 3.0}
        older_operation = {**gx_relay_refresh_command(0), "created_at": 1.0}
        newer_operation = {**gx_relay_refresh_command(1), "created_at": 2.0}

        with patch.object(vars(write_health_module)["time"], "time", return_value=4.0):
            ordered = write_health_module.DbusWriteSchedulerHealth.prioritized_commands(
                [("newer", newer_operation), ("safety", safety), ("older", older_operation)]
            )

        self.assertEqual([path for path, _command in ordered], ["safety", "older", "newer"])

    def test_aged_refresh_is_promoted_without_reclassifying_publication(self) -> None:
        aged = {"kind": "refresh_services", "queue_class": "read-slow", "created_at": 5.0, "priority": "read"}
        fresh = {"kind": "refresh_services", "queue_class": "read-slow", "created_at": 5.1, "priority": "read"}
        publication = {**evcs_publication(), "created_at": 1.0}

        self.assertTrue(write_health_module.aged_refresh_command(aged, 20.0))
        self.assertFalse(write_health_module.aged_refresh_command(fresh, 20.0))
        self.assertFalse(write_health_module.aged_refresh_command(publication, 20.0))
        self.assertEqual(write_health_module.effective_command_priority_rank(aged, 20.0), 1.5)

    def test_lifecycle_counts_keep_total_and_rolling_windows_separate(self) -> None:
        with self.adapter_scenario() as scenario:
            scheduler = scenario.adapter.write_scheduler
            with patch.object(
                vars(write_health_module)["time"],
                "time",
                side_effect=[100.0, 170.0],
            ):
                scheduler.record_lifecycle(evcs_publication(), "applied")
                scheduler.record_lifecycle(gx_relay_refresh_command(0), "deferred")

            scheduler.prune_lifecycle(165.0)

            self.assertEqual(scheduler._lifecycle_counts, {"applied": 1, "deferred": 1})
            self.assertEqual(scheduler.lifecycle_counts_60s(), {"deferred": 1})

    def test_empty_lifecycle_state_is_normalized_to_unknown(self) -> None:
        with self.adapter_scenario() as scenario:
            scheduler = scenario.adapter.write_scheduler
            scheduler.record_lifecycle(evcs_publication(), "")
            self.assertEqual(scheduler.health()["lifecycle_counts"], {"unknown": 1})

    def test_health_exposes_semantic_scheduler_capacity(self) -> None:
        with self.adapter_scenario() as scenario:
            scheduler = scenario.adapter.write_scheduler
            scheduler.record_budget(evcs_publication({"mode": 1}, priority="critical"))
            scheduler.record_processed()

            health = scheduler.health(now=time.time())

            self.assertEqual(health["processed_commands_60s"], 1)
            self.assertEqual(health["queue_class_usage_1s"], {"gui-critical-publish": 1})
            self.assertIn("queue_class_budgets", health)
            self.assertIn("dynamic_local_publish_burst_limit", health)

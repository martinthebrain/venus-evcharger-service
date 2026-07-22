# SPDX-License-Identifier: GPL-3.0-or-later
"""Semantic write-scheduler health boundary contracts."""

from __future__ import annotations

from tests.support.dbus_gateway_adapter_harness import (
    GatewayAdapterContractCase,
    builtins,
    evcs_publication,
    json,
    patch,
    write_health_module,
)
from venus_evcharger.ipc.gateway_operations import gx_relay_refresh_command, gx_relay_set_command
from venus_evcharger.dbus_adapter.health.slo import GatewayPressureState


class GatewayWriteHealthBoundaryCases(GatewayAdapterContractCase):
    """Exercise exact budget, pressure, and lifecycle boundaries."""

    def test_queue_budget_defaults_and_unknown_rank_are_exact(self) -> None:
        self.assertEqual(
            write_health_module.UNKNOWN_QUEUE_CLASS_RANK,
            max(write_health_module._QUEUE_CLASS_RANKS.values()) + 1,
        )
        self.assertEqual(
            write_health_module.DbusWriteSchedulerHealth._queue_class_budgets(
                {
                    "DbusGatewayQueueBudgetGuiCriticalPublish": "1",
                    "DbusGatewayQueueBudgetLocalPublish": "1",
                    "DbusGatewayQueueBudgetReadFast": "1",
                }
            ),
            {
                "startup/register": 100,
                "gui-critical-publish": 1,
                "local-publish": 1,
                "remote-write": 2,
                "read-fast": 1,
                "read-slow": 2,
                "discovery": 1,
                "introspection": 1,
                "diagnostic": 1,
            },
        )

    def test_pressure_states_reduce_semantic_publication_capacity(self) -> None:
        with self.adapter_scenario(
            "[DEFAULT]\nDbusGatewayQueueBudgetGuiCriticalPublish=7\nDbusGatewayQueueBudgetLocalPublish=6\n"
        ) as scenario:
            scheduler = scenario.adapter.write_scheduler
            scheduler.local_publish_burst_limit = 20

            expected: dict[GatewayPressureState, tuple[int, int, int, int]] = {
                "congested": (10, 10, 5, 0),
                "slow": (5, 5, 1, 0),
                "protective": (1, 1, 1, 0),
            }
            for state, values in expected.items():
                scheduler.set_dynamic_local_publish_burst(50, pressure_state=state)
                self.assertEqual(
                    (
                        scheduler.dynamic_local_publish_burst_limit,
                        scheduler.queue_class_budgets["gui-critical-publish"],
                        scheduler.queue_class_budgets["local-publish"],
                        scheduler.queue_class_budgets["diagnostic"],
                    ),
                    values,
                )

    def test_budget_and_event_pruning_keep_boundary_entries(self) -> None:
        with self.adapter_scenario() as scenario:
            scheduler = scenario.adapter.write_scheduler
            scheduler._budget_events.extend(((98.5, "remote-write"), (99.0, "remote-write")))
            scheduler._processed_events.extend((39.0, 40.0))
            scheduler._lifecycle_events.extend(
                ((39.0, "applied", "remote-write"), (40.0, "deferred", "remote-write"))
            )

            scheduler.prune_budget(100.0)
            scheduler.prune_processed(100.0)
            scheduler.prune_lifecycle(100.0)

            self.assertEqual(list(scheduler._budget_events), [(99.0, "remote-write")])
            self.assertEqual(list(scheduler._processed_events), [40.0])
            self.assertEqual(list(scheduler._lifecycle_events), [(40.0, "deferred", "remote-write")])

    def test_budget_uses_semantic_queue_class(self) -> None:
        with self.adapter_scenario("[DEFAULT]\nDbusGatewayQueueBudgetRemoteWrite=1\n") as scenario:
            scheduler = scenario.adapter.write_scheduler
            operation = gx_relay_set_command(
                0,
                "NO",
                True,
                ensure_manual=False,
                verify_settle_seconds=0.1,
                verify_retry_seconds=1.0,
            )
            with patch.object(vars(write_health_module)["time"], "time", return_value=200.0):
                scheduler.record_budget(operation)

            self.assertEqual(list(scheduler._budget_events), [(200.0, "remote-write")])
            self.assertFalse(scheduler.budget_available(operation, 200.0))

    def test_lifecycle_journal_contains_only_semantic_command_metadata(self) -> None:
        with self.adapter_scenario() as scenario:
            scheduler = scenario.adapter.write_scheduler
            lifecycle_path = scenario.root / "logs" / "write-health.jsonl"
            scenario.adapter.command_lifecycle_path = str(lifecycle_path)
            with patch.object(vars(write_health_module)["time"], "time", return_value=220.0):
                scheduler.record_lifecycle(
                    gx_relay_set_command(
                        0,
                        "NO",
                        True,
                        ensure_manual=False,
                        verify_settle_seconds=0.1,
                        verify_retry_seconds=1.0,
                    ),
                    "applied",
                )
                scheduler.record_lifecycle(evcs_publication({"mode": 1}), "queued")

            rows = [json.loads(line) for line in lifecycle_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(rows[0]["kind"], "gx_relay_set_enabled")
            self.assertEqual(rows[0]["queue_class"], "remote-write")
            self.assertEqual(rows[1]["kind"], "publish_evcs_fields")
            self.assertEqual(rows[1]["queue_class"], "local-publish")
            self.assertTrue(all("service" not in row and "path" not in row for row in rows))

    def test_lifecycle_journal_io_failure_is_advisory(self) -> None:
        with self.adapter_scenario() as scenario:
            scheduler = scenario.adapter.write_scheduler
            scenario.adapter.command_lifecycle_path = str(scenario.root / "commands.jsonl")
            with (
                patch.object(builtins, "open", side_effect=OSError("full")),
                patch.object(vars(write_health_module)["logging"], "debug") as log_debug,
            ):
                scheduler.record_lifecycle(evcs_publication(), "dropped")
            log_debug.assert_called_once_with(
                "Unable to append DBus gateway command lifecycle event",
                exc_info=True,
            )

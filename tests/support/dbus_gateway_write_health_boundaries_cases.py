# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway adapter write health boundary contracts."""

from __future__ import annotations

from tests.support.dbus_gateway_adapter_harness import (
    DbusAdapter,
    GatewayAdapterContractCase,
    Path,
    builtins,
    gateway_paths,
    json,
    patch,
    tempfile,
    unittest,
    write_health_module,
)


class GatewayWriteHealthBoundaryCases(GatewayAdapterContractCase):
    """Exercise write health boundary contracts."""

    def test_write_scheduler_health_contract_boundaries_are_exact(self) -> None:
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

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                "DbusGatewayQueueBudgetGuiCriticalPublish=7\n"
                "DbusGatewayQueueBudgetLocalPublish=6\n"
                "DbusGatewayQueueBudgetReadFast=1\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            scheduler = adapter.write_scheduler

            scheduler.local_publish_burst_limit = 1
            scheduler.set_dynamic_local_publish_burst(5)
            self.assertEqual(scheduler.queue_class_budgets["gui-critical-publish"], 7)
            self.assertEqual(scheduler.queue_class_budgets["local-publish"], 6)
            self.assertEqual(scheduler.dynamic_local_publish_burst_limit, 5)

            scheduler.local_publish_burst_limit = 20
            scheduler.set_dynamic_local_publish_burst(50, pressure_state="congested")
            self.assertEqual(scheduler.dynamic_local_publish_burst_limit, 10)
            self.assertEqual(scheduler.queue_class_budgets["gui-critical-publish"], 10)
            self.assertEqual(scheduler.queue_class_budgets["local-publish"], 5)
            self.assertEqual(scheduler.queue_class_budgets["diagnostic"], 0)
            scheduler.set_dynamic_local_publish_burst(50, pressure_state="slow")
            self.assertEqual(scheduler.dynamic_local_publish_burst_limit, 5)
            self.assertEqual(scheduler.queue_class_budgets["gui-critical-publish"], 5)
            self.assertEqual(scheduler.queue_class_budgets["local-publish"], 1)
            scheduler.set_dynamic_local_publish_burst(50, pressure_state="protective")
            self.assertEqual(scheduler.dynamic_local_publish_burst_limit, 1)
            self.assertEqual(scheduler.queue_class_budgets["gui-critical-publish"], 1)
            self.assertEqual(scheduler.queue_class_budgets["local-publish"], 1)

            scheduler._budget_events.clear()
            scheduler._budget_events.extend(
                [
                    (99.0, "read-fast"),
                    (99.2, "read-fast"),
                    (99.4, "diagnostic"),
                ]
            )
            self.assertEqual(
                scheduler.queue_class_usage_1s(),
                {"diagnostic": 1, "read-fast": 2},
            )

            scheduler._budget_events.clear()
            scheduler._budget_events.extend([(98.5, "read-fast"), (99.0, "read-fast")])
            scheduler.prune_budget(100.0)
            self.assertEqual(list(scheduler._budget_events), [(99.0, "read-fast")])

            scheduler._processed_events.clear()
            scheduler._processed_events.extend([39.0, 40.0])
            scheduler.prune_processed(100.0)
            self.assertEqual(list(scheduler._processed_events), [40.0])

            scheduler._lifecycle_events.clear()
            scheduler._lifecycle_events.extend(
                [
                    (39.0, "applied", "remote-write"),
                    (40.0, "applied", "remote-write"),
                ]
            )
            scheduler.prune_lifecycle(100.0)
            self.assertEqual(list(scheduler._lifecycle_events), [(40.0, "applied", "remote-write")])

            scheduler._budget_events.clear()
            with patch.object(write_health_module.time, "time", return_value=200.0):
                scheduler.record_budget({"kind": "publish_value", "path": "/Mode", "queue_class": "diagnostic"})
            self.assertEqual(list(scheduler._budget_events), [(200.0, "diagnostic")])
            self.assertFalse(scheduler.budget_available({"queue_class": "diagnostic"}, 200.0))

            scheduler._lifecycle_counts.clear()
            scheduler._lifecycle_events.clear()
            adapter.command_lifecycle_path = ""
            lifecycle_open = unittest.mock.mock_open()
            with (
                patch.object(builtins, "open", lifecycle_open),
                patch.object(
                    write_health_module.time,
                    "time",
                    return_value=210.0,
                ),
            ):
                scheduler.record_lifecycle(
                    {"kind": "publish_value", "path": "/Mode", "queue_class": "diagnostic"},
                    "deferred",
                )
                scheduler.record_lifecycle(
                    {"kind": "publish_value", "path": "/Mode", "queue_class": "diagnostic"},
                    "deferred",
                )
            lifecycle_open.assert_not_called()
            self.assertEqual(scheduler._lifecycle_counts, {"deferred": 2})
            self.assertEqual(
                scheduler.lifecycle_counts_60s(),
                {"deferred": 2},
            )
            self.assertEqual(
                list(scheduler._lifecycle_events),
                [(210.0, "deferred", "diagnostic"), (210.0, "deferred", "diagnostic")],
            )

            lifecycle_path = Path(temp_dir) / "logs" / "write-health.jsonl"
            adapter.command_lifecycle_path = str(lifecycle_path)
            with patch.object(write_health_module.time, "time", return_value=220.0):
                scheduler.record_lifecycle({"kind": "set_value", "queue_class": "remote-write"}, "applied")
                scheduler.record_lifecycle({"kind": "publish_value", "path": "/Mode"}, "queued")
            lifecycle_lines = lifecycle_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lifecycle_lines), 2)
            self.assertEqual(json.loads(lifecycle_lines[0])["at"], 220.0)
            self.assertEqual(json.loads(lifecycle_lines[0])["queue_class"], "remote-write")
            self.assertEqual(json.loads(lifecycle_lines[1])["queue_class"], "gui-critical-publish")

            retained_lifecycle_path = Path(temp_dir) / "logs" / "write-health-retained.jsonl"
            adapter.command_lifecycle_path = str(retained_lifecycle_path)
            adapter.command_lifecycle_max_bytes = 180
            with patch.object(write_health_module.time, "time", return_value=221.0):
                scheduler.record_lifecycle({"kind": "set_value", "queue_class": "remote-write", "id": "old"}, "applied")
                scheduler.record_lifecycle({"kind": "publish_value", "path": "/Mode", "id": "new"}, "queued")
            retained_lines = retained_lifecycle_path.read_text(encoding="utf-8").splitlines()
            self.assertLessEqual(retained_lifecycle_path.stat().st_size, 180)
            self.assertEqual(json.loads(retained_lines[-1])["queue_class"], "gui-critical-publish")

            with (
                patch.object(builtins, "open", side_effect=OSError("full")),
                patch.object(
                    write_health_module.logging,
                    "debug",
                ) as log_debug,
            ):
                scheduler.record_lifecycle({"kind": "noop"}, "dropped")
            log_debug.assert_called_once_with(
                "Unable to append DBus gateway command lifecycle event",
                exc_info=True,
            )

            commands = [
                ("newer", {"id": "newer", "queue_class": "read-fast", "created_at": 20.0, "priority": "read"}),
                ("older", {"id": "older", "queue_class": "read-fast", "created_at": 10.0, "priority": "read"}),
            ]
            with patch.object(write_health_module.time, "time", return_value=21.0):
                self.assertEqual(
                    [path for path, _command in scheduler.prioritized_commands(commands)],
                    ["older", "newer"],
                )

            health = scheduler.health(now=220.0)
            self.assertIn("queue_class_usage_1s", health)
            self.assertEqual(health["queue_class_usage_1s"], {"diagnostic": 1})

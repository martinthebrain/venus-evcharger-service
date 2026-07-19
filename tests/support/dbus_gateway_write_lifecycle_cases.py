# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway adapter write lifecycle and remote-write health contracts."""

from __future__ import annotations

from tests.support.dbus_gateway_adapter_harness import (
    BusItemInterfaceStub,
    DbusAdapter,
    DbusBusStub,
    GatewayAdapterContractCase,
    MagicMock,
    Path,
    gateway_paths,
    install_mock,
    patch,
    tempfile,
    write_health_module,
)


class GatewayWriteLifecycleCases(GatewayAdapterContractCase):
    """Exercise write lifecycle and remote-write health contracts."""

    def test_write_scheduler_health_budgets_lifecycle_and_remote_write_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            default_config_path = Path(temp_dir) / "default-config.ini"
            default_config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            default_adapter = DbusAdapter(
                str(default_config_path),
                paths=gateway_paths(str(Path(temp_dir) / "default-run")),
            )
            default_scheduler = default_adapter.write_scheduler
            self.assertEqual(default_scheduler.local_publish_burst_limit, 20)
            self.assertEqual(default_scheduler.dynamic_local_publish_burst_limit, 20)
            self.assertEqual(default_scheduler.local_publish_tick_budget_seconds, 0.075)
            self.assertEqual(default_scheduler.startup_registration_batch_limit, 100)
            self.assertEqual(default_scheduler.startup_registration_tick_budget_seconds, 0.15)
            self.assertEqual(default_adapter.command_lifecycle_max_bytes, 1_048_576)
            self.assertEqual(default_adapter.health_log_max_bytes, 524_288)
            self.assertEqual(default_scheduler.last_processed_at, 0.0)
            self.assertEqual(default_scheduler.registered_paths, set())
            self.assertEqual(default_scheduler.last_values, {})
            self.assertEqual(list(default_scheduler._processed_events), [])
            self.assertEqual(list(default_scheduler._budget_events), [])
            self.assertEqual(list(default_scheduler._lifecycle_events), [])
            self.assertEqual(default_scheduler._lifecycle_counts, {})

            clamped_config_path = Path(temp_dir) / "clamped-config.ini"
            clamped_config_path.write_text(
                "[DEFAULT]\n"
                "DbusGatewayLocalPublishBurstLimit=0\n"
                "DbusGatewayLocalPublishTickBudgetMs=0\n"
                "DbusGatewayStartupRegistrationBatchLimit=0\n"
                "DbusGatewayStartupRegistrationTickBudgetMs=0\n",
                encoding="utf-8",
            )
            clamped_adapter = DbusAdapter(
                str(clamped_config_path),
                paths=gateway_paths(str(Path(temp_dir) / "clamped-run")),
            )
            self.assertEqual(clamped_adapter.write_scheduler.local_publish_burst_limit, 1)
            self.assertEqual(clamped_adapter.write_scheduler.dynamic_local_publish_burst_limit, 1)
            self.assertEqual(clamped_adapter.write_scheduler.local_publish_tick_budget_seconds, 0.001)
            self.assertEqual(clamped_adapter.write_scheduler.startup_registration_batch_limit, 1)
            self.assertEqual(clamped_adapter.write_scheduler.startup_registration_tick_budget_seconds, 0.001)

            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                "DbusGatewayQueueBudgetStartupRegister=0\n"
                "DbusGatewayQueueBudgetGuiCriticalPublish=2\n"
                "DbusGatewayQueueBudgetLocalPublish=0\n"
                "DbusGatewayQueueBudgetRemoteWrite=3\n"
                "DbusGatewayQueueBudgetReadFast=4\n"
                "DbusGatewayQueueBudgetReadSlow=0\n"
                "DbusGatewayQueueBudgetDiscovery=0\n"
                "DbusGatewayQueueBudgetIntrospection=0\n"
                "DbusGatewayQueueBudgetDiagnostic=0\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            scheduler = adapter.write_scheduler

            self.assertEqual(
                write_health_module.DbusWriteSchedulerHealth._queue_class_budgets({}),
                {
                    "startup/register": 100,
                    "gui-critical-publish": 50,
                    "local-publish": 30,
                    "remote-write": 2,
                    "read-fast": 4,
                    "read-slow": 2,
                    "discovery": 1,
                    "introspection": 1,
                    "diagnostic": 1,
                },
            )
            self.assertEqual(
                scheduler.queue_class_budgets,
                {
                    "startup/register": 1,
                    "gui-critical-publish": 2,
                    "local-publish": 1,
                    "remote-write": 3,
                    "read-fast": 4,
                    "read-slow": 0,
                    "discovery": 0,
                    "introspection": 0,
                    "diagnostic": 0,
                },
            )
            self.assertEqual(scheduler.queue_class_budgets["startup/register"], 1)
            self.assertEqual(scheduler.queue_class_budgets["local-publish"], 1)
            self.assertEqual(scheduler.queue_class_budgets["read-slow"], 0)
            self.assertEqual(write_health_module.remote_command_timeout({"timeout": object()}), 1.0)
            self.assertEqual(write_health_module.remote_command_timeout({}), 1.0)
            self.assertEqual(write_health_module.remote_command_timeout({"timeout": 0.0}), 1.0)
            self.assertEqual(write_health_module.remote_command_timeout({"timeout": "2.5"}), 2.5)
            self.assertIsNone(write_health_module.remote_command_target({"service": "", "path": "/Mode"}))
            self.assertIsNone(write_health_module.remote_command_target({"service": "svc", "path": ""}))
            self.assertEqual(
                write_health_module.remote_command_target({"service": "svc", "path": "/Mode"}),
                ("svc", "/Mode"),
            )

            scheduler.set_dynamic_local_publish_burst(0)
            self.assertEqual(scheduler.dynamic_local_publish_burst_limit, 1)
            self.assertEqual(scheduler.queue_class_budgets["gui-critical-publish"], 2)
            self.assertEqual(scheduler.queue_class_budgets["local-publish"], 1)
            scheduler.local_publish_burst_limit = 1
            scheduler.set_dynamic_local_publish_burst(5)
            self.assertEqual(scheduler.dynamic_local_publish_burst_limit, 5)
            self.assertEqual(scheduler.queue_class_budgets["gui-critical-publish"], 5)
            self.assertEqual(scheduler.queue_class_budgets["local-publish"], 5)
            scheduler.local_publish_burst_limit = 5
            scheduler.set_dynamic_local_publish_burst(5)
            self.assertEqual(scheduler.queue_class_budgets["gui-critical-publish"], 2)
            self.assertEqual(scheduler.queue_class_budgets["local-publish"], 1)

            scheduler._budget_events.append((99.0, "read-fast"))
            scheduler._budget_events.append((98.9, "read-fast"))
            self.assertEqual(scheduler.budget_usage("read-fast", 100.0), 1)
            with patch.object(write_health_module.time, "time", return_value=100.0):
                scheduler.record_budget({"kind": "publish_value", "path": "/Mode"})
            self.assertIn("gui-critical-publish", scheduler.queue_class_usage_1s())
            self.assertTrue(scheduler.budget_available({"queue_class": "read-fast"}, 100.0))
            scheduler.queue_class_budgets["read-fast"] = 1
            self.assertFalse(scheduler.budget_available({"queue_class": "read-fast"}, 100.0))
            self.assertTrue(scheduler.budget_available({"queue_class": "ad-hoc"}, 100.0))
            scheduler._budget_events.append((100.0, "ad-hoc"))
            self.assertFalse(scheduler.budget_available({"queue_class": "ad-hoc"}, 100.0))
            scheduler.queue_class_budgets["unknown"] = 0
            self.assertFalse(scheduler.budget_available({"queue_class": "unknown"}, 100.0))

            commands = [
                ("unknown", {"id": "unknown", "queue_class": "unknown", "created_at": 1.0}),
                ("aged", {"id": "aged", "queue_class": "read-slow", "created_at": 1.0, "priority": "diagnostic"}),
                ("fresh", {"id": "fresh", "queue_class": "read-fast", "created_at": 20.0, "priority": "diagnostic"}),
            ]
            with patch.object(write_health_module.time, "time", return_value=20.0):
                self.assertEqual(scheduler.prioritized_commands(commands)[0][0], "aged")
                same_priority = [
                    ("slow", {"id": "slow", "queue_class": "read-slow", "created_at": 19.0, "priority": "read"}),
                    ("remote", {"id": "remote", "queue_class": "remote-write", "created_at": 19.0, "priority": "read"}),
                    ("local", {"id": "local", "queue_class": "local-publish", "created_at": 19.0, "priority": "read"}),
                ]
                self.assertEqual(
                    [path for path, _command in scheduler.prioritized_commands(same_priority)],
                    ["remote", "local", "slow"],
                )
                self.assertTrue(write_health_module.aged_refresh_command(commands[1][1], 20.0))
                self.assertEqual(write_health_module.effective_command_priority_rank(commands[1][1], 20.0), 1.5)
                self.assertFalse(
                    write_health_module.aged_refresh_command({"queue_class": "read-slow", "created_at": 5.1}, 20.0)
                )
                self.assertTrue(
                    write_health_module.aged_refresh_command({"queue_class": "read-slow", "created_at": 5.0}, 20.0)
                )
                self.assertFalse(
                    write_health_module.aged_refresh_command({"queue_class": "local-publish", "created_at": 1.0}, 20.0)
                )
                self.assertFalse(
                    write_health_module.aged_refresh_command({"queue_class": "read-fast", "created_at": 0.0}, 20.0)
                )

            lifecycle_path = Path(temp_dir) / "logs" / "commands.jsonl"
            adapter.command_lifecycle_path = str(lifecycle_path)
            with patch.object(write_health_module.time, "time", return_value=123.0):
                scheduler.record_lifecycle({"kind": "set_value", "queue_class": "remote-write"}, "applied")
                scheduler.record_lifecycle({"kind": "publish_value", "path": "/Mode"}, "")
            self.assertEqual(scheduler._lifecycle_counts["applied"], 1)
            self.assertEqual(scheduler._lifecycle_counts["unknown"], 1)
            lifecycle_log = lifecycle_path.read_text(encoding="utf-8")
            self.assertIn('"at":123.0', lifecycle_log)
            self.assertIn('"queue_class":"remote-write"', lifecycle_log)
            self.assertIn('"state":"unknown"', lifecycle_log)
            self.assertEqual(scheduler.lifecycle_counts_60s(), {"applied": 1, "unknown": 1})

            adapter.command_lifecycle_max_bytes = -42
            with (
                patch.object(write_health_module.time, "time", return_value=124.0),
                patch.object(
                    write_health_module,
                    "append_jsonl",
                ) as append_jsonl,
            ):
                scheduler.record_lifecycle(
                    {"kind": "set_value", "id": "remote-1", "queue_class": "remote-write"},
                    "deferred",
                )
            append_jsonl.assert_called_once()
            append_path, append_payload = append_jsonl.call_args.args
            self.assertEqual(append_path, str(lifecycle_path))
            self.assertEqual(
                append_payload,
                {
                    "at": 124.0,
                    "state": "deferred",
                    "queue_class": "remote-write",
                    "kind": "set_value",
                    "id": "remote-1",
                    "coalesce_key": "",
                },
            )
            self.assertEqual(append_jsonl.call_args.kwargs, {"max_bytes": 0})

            health = scheduler.health(now=123.0)
            self.assertEqual(health["last_processed_at"], scheduler.last_processed_at)
            self.assertEqual(health["local_publish_burst_limit"], scheduler.local_publish_burst_limit)
            self.assertEqual(health["dynamic_local_publish_burst_limit"], 5)
            self.assertEqual(
                health["local_publish_tick_budget_ms"], scheduler.local_publish_tick_budget_seconds * 1000.0
            )
            self.assertEqual(health["startup_registration_batch_limit"], scheduler.startup_registration_batch_limit)
            self.assertEqual(
                health["startup_registration_tick_budget_ms"],
                scheduler.startup_registration_tick_budget_seconds * 1000.0,
            )
            self.assertEqual(health["lifecycle_counts"]["applied"], 1)
            self.assertEqual(health["lifecycle_counts_60s"], {"applied": 1, "deferred": 1, "unknown": 1})

            self.assertEqual(scheduler.set_remote_value({"service": "", "path": "/P"}), "dropped")
            self.assertEqual(scheduler.set_remote_value({"service": "svc", "path": ""}), "dropped")

            fake_bus = DbusBusStub()
            fake_iface = BusItemInterfaceStub()
            install_mock(adapter.connection, "bus", MagicMock(return_value=fake_bus))
            install_mock(adapter, "timed_dbus_operation", MagicMock(side_effect=lambda _kind, fn: fn()))
            with patch.object(write_health_module.dbus, "Interface", return_value=fake_iface) as interface_factory:
                self.assertEqual(
                    scheduler.set_remote_value({"service": "svc", "path": "/P", "value": 9, "timeout": 2.5}),
                    "applied",
                )
            self.assertEqual(fake_bus.get_object_calls, [("svc", "/P", False)])
            interface_factory.assert_called_once_with(fake_bus.dbus_object, "com.victronenergy.BusItem")
            self.assertEqual(fake_iface.set_calls, [(9, 2.5)])
            self.assertEqual(adapter.cache.values["path:svc/P"]["value"], 9)
            self.assertEqual(adapter.cache.values["path:svc/P"]["source"], "svc/P")
            self.assertEqual(adapter.cache.values["path:svc/P"]["confidence"], 0.9)

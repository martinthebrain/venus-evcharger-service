# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway adapter local burst and registration time-budget scenarios."""

from __future__ import annotations

from tests.support.dbus_gateway_adapter_harness import (
    DbusAdapter,
    DbusCommandInbox,
    FakeVeDbusService,
    GatewayAdapterContractCase,
    MagicMock,
    Path,
    RecordingDbusService,
    gateway_paths,
    install_mock,
    patch,
    tempfile,
    time,
    write_publish_module,
)


class GatewayWriteBurstCases(GatewayAdapterContractCase):
    """Exercise local burst and registration time-budget scenarios."""

    def test_expired_command_is_removed_and_journaled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lifecycle_path = Path(temp_dir) / "run" / "lifecycle.jsonl"
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                f"[DEFAULT]\nDbusGatewayCommandLifecyclePath={lifecycle_path}\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            command_path = adapter.commands.enqueue(
                {
                    "kind": "refresh_services",
                    "created_at": time.time() - 10.0,
                    "deadline_s": 1.0,
                    "priority": "read",
                }
            )

            self.assertTrue(adapter.write_scheduler.process_one())

            self.assertFalse(Path(command_path).exists())
            health = adapter.write_scheduler.health(now=time.time())
            self.assertEqual(health["lifecycle_counts"]["expired"], 1)
            self.assertIn('"state":"expired"', lifecycle_path.read_text(encoding="utf-8"))

    def test_gui_publish_burst_drains_large_local_queue(self) -> None:
        with self.adapter_scenario(
            "[DEFAULT]\n"
            "DbusGatewayLocalPublishBurstLimit=250\n"
            "DbusGatewayLocalPublishTickBudgetMs=10000\n"
            "DbusGatewayQueueBudgetLocalPublish=250\n",
        ) as scenario:
            adapter = scenario.adapter
            service = RecordingDbusService()
            adapter.set_dbus_service(service, registered=True)
            for index in range(200):
                path = f"/LoadTest/{index}"
                adapter.write_scheduler.registered_paths.add(path)
                adapter.commands.enqueue(
                    {
                        "kind": "publish_value",
                        "path": path,
                        "value": index,
                        "priority": "publish",
                        "coalesce_key": f"publish:{path}",
                    }
                )

            processed = adapter.write_scheduler.process_local_publish_burst(200)

            self.assertEqual(processed, 200)
            self.assertEqual(len(service.writes), 200)
            self.assertEqual(adapter.commands.load_pending(), [])

    def test_gui_publish_burst_stops_at_tick_time_budget(self) -> None:
        with self.adapter_scenario(
            "[DEFAULT]\n"
            "DbusGatewayLocalPublishBurstLimit=10\n"
            "DbusGatewayLocalPublishTickBudgetMs=1\n"
            "DbusGatewayQueueBudgetLocalPublish=10\n",
        ) as scenario:
            adapter = scenario.adapter
            install_mock(adapter, "timed_local_publish", MagicMock(side_effect=lambda operation: operation()))
            service = RecordingDbusService()
            adapter.set_dbus_service(service, registered=True)
            for index in range(5):
                path = f"/BudgetTest/{index}"
                adapter.write_scheduler.registered_paths.add(path)
                adapter.commands.enqueue(
                    {
                        "kind": "publish_value",
                        "path": path,
                        "value": index,
                        "priority": "publish",
                        "coalesce_key": f"publish:{path}",
                    }
                )

            with patch.object(write_publish_module.time, "monotonic", side_effect=[0.0, 0.0, 0.002]):
                processed = adapter.write_scheduler.process_local_publish_burst()

            self.assertEqual(processed, 1)
            self.assertEqual(len(service.writes), 1)
            self.assertEqual(len(adapter.commands.load_pending()), 4)

    def test_local_publish_burst_skip_and_defer_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\nDbusGatewayLocalPublishBurstLimit=3\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            self.assertEqual(adapter.write_scheduler.process_local_publish_burst(), 0)
            adapter.set_dbus_service(FakeVeDbusService(), registered=True)
            self.assertEqual(adapter.write_scheduler.process_local_publish_burst(), 0)
            adapter.commands.enqueue({"kind": "set_value", "service": "svc", "path": "/A", "priority": "user"})
            self.assertEqual(adapter.write_scheduler.process_local_publish_burst(), 0)
            adapter.commands.enqueue(
                {
                    "kind": "publish_value",
                    "path": "/Missing",
                    "value": 1,
                    "priority": "publish",
                    "coalesce_key": "publish:/Missing",
                }
            )
            self.assertEqual(adapter.write_scheduler.process_local_publish_burst(), 1)
            install_mock(adapter.write_scheduler, "process_command", MagicMock(return_value="deferred"))
            adapter.commands.enqueue(
                {
                    "kind": "publish_value",
                    "path": "/Later",
                    "value": 1,
                    "priority": "publish",
                    "coalesce_key": "publish:/Later",
                }
            )
            self.assertEqual(adapter.write_scheduler.process_local_publish_burst(), 0)
            self.assertIsNotNone(adapter.write_scheduler.next_local_publish_command())
            for command_path, _command in adapter.commands.load_pending():
                adapter.commands.remove(command_path)
            self.assertIsNone(adapter.write_scheduler.next_local_publish_command())

    def test_local_publish_timer_records_success_and_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            self.assertEqual(adapter.write_scheduler.timed_local_publish(lambda: "ok"), "ok")
            self.assertGreater(adapter.circuit.health()["successes_60s"], 0)

            with self.assertRaises(RuntimeError):
                adapter.write_scheduler.timed_local_publish(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
            self.assertGreater(adapter.circuit.health()["errors_60s"], 0)

    def test_startup_registration_batch_stops_at_tick_time_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                "DbusGatewayStartupRegistrationBatchLimit=10\n"
                "DbusGatewayStartupRegistrationTickBudgetMs=1\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.set_dbus_service(FakeVeDbusService())
            for index in range(5):
                adapter.commands.enqueue(
                    {
                        "kind": "register_path",
                        "path": f"/RegisterBudget/{index}",
                        "value": index,
                    }
                )
            commands = adapter.write_scheduler.prioritized_commands(
                DbusCommandInbox.coalesce(adapter.commands.load_pending())
            )

            with patch.object(write_publish_module.time, "monotonic", side_effect=[0.0, 0.0, 0.002, 0.002]):
                self.assertTrue(adapter.write_scheduler.process_startup_registration_batch(commands))

            self.assertEqual(len(adapter.write_scheduler.registered_paths), 1)
            self.assertEqual(len(adapter.commands.load_pending()), 4)

            mixed = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-mixed")))
            install_mock(mixed.write_scheduler, "register_path", MagicMock(side_effect=["deferred", "applied"]))
            self.assertTrue(
                mixed.write_scheduler.process_startup_registration_batch(
                    [
                        ("deferred", {"kind": "register_path", "path": "/Deferred", "priority": "publish"}),
                        ("service", {"kind": "register_service", "priority": "publish"}),
                        ("applied", {"kind": "register_path", "path": "/Applied", "priority": "publish"}),
                    ]
                )
            )
            self.assertEqual(mixed.write_scheduler.register_path.call_count, 2)
            service_then_path = DbusAdapter(
                str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-service-then-path"))
            )
            install_mock(service_then_path.write_scheduler, "register_path", MagicMock(return_value="applied"))
            self.assertTrue(
                service_then_path.write_scheduler.process_startup_registration_batch(
                    [
                        ("service", {"kind": "register_service", "priority": "publish"}),
                        ("path", {"kind": "register_path", "path": "/AfterService", "priority": "publish"}),
                    ]
                )
            )
            unknown_then_path = DbusAdapter(
                str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-unknown-then-path"))
            )
            install_mock(unknown_then_path.write_scheduler, "register_path", MagicMock(return_value="applied"))
            self.assertTrue(
                unknown_then_path.write_scheduler.process_startup_registration_batch(
                    [
                        ("unknown", {"kind": "unknown", "priority": "publish"}),
                        ("path", {"kind": "register_path", "path": "/AfterUnknown", "priority": "publish"}),
                    ]
                )
            )

    def test_startup_registration_service_only_and_zero_limit_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            commands = [("svc", {"kind": "register_service", "priority": "publish"})]
            self.assertTrue(adapter.write_scheduler.process_startup_registration_batch(commands))
            self.assertTrue(adapter.dbus_service.registered)

            limited = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-limited")))
            limited.write_scheduler.startup_registration_batch_limit = 0
            limited.commands.enqueue({"kind": "register_path", "path": "/A", "priority": "publish"})
            limited_commands = limited.write_scheduler.prioritized_commands(
                DbusCommandInbox.coalesce(limited.commands.load_pending())
            )
            self.assertFalse(limited.write_scheduler.process_startup_registration_batch(limited_commands))

            deferred_service = DbusAdapter(
                str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-deferred-service"))
            )
            install_mock(deferred_service.write_scheduler, "process_command", MagicMock(return_value="deferred"))
            self.assertFalse(
                deferred_service.write_scheduler.process_startup_registration_batch(
                    [("svc", {"kind": "register_service", "priority": "publish"})]
                )
            )
            deferred_service.write_scheduler.process_command.assert_called_once_with(
                {"kind": "register_service", "priority": "publish"},
                command_file="svc",
            )

            waiting = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-waiting")))
            waiting.commands.enqueue({"kind": "register_path", "path": "/A", "priority": "publish"})
            self.assertTrue(waiting.write_scheduler.remaining_register_paths())
            self.assertFalse(
                waiting.write_scheduler.process_startup_registration_batch(
                    [("svc", {"kind": "register_service", "priority": "publish"})]
                )
            )
            self.assertFalse(waiting.dbus_service_registered)

            no_paths = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-no-paths")))
            self.assertEqual(no_paths.write_scheduler.process_startup_register_paths([], time.monotonic()), (False, 0))
            no_paths.write_scheduler.startup_registration_batch_limit = 1
            self.assertFalse(
                no_paths.write_scheduler.should_process_startup_service(
                    ("svc", {"kind": "register_service"}),
                    processed=1,
                    started=time.monotonic(),
                )
            )

    def test_queue_class_budget_defers_over_budget_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\nDbusGatewayQueueBudgetRemoteWrite=1\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            first = {"kind": "set_value", "service": "svc", "path": "/A", "created_at": 1.0, "priority": "user"}
            second = {"kind": "set_value", "service": "svc", "path": "/B", "created_at": 2.0, "priority": "user"}

            self.assertIs(adapter.write_scheduler.select_next_command([("a", first), ("b", second)])[1], first)
            adapter.write_scheduler.record_budget(first)
            self.assertIsNone(adapter.write_scheduler.select_next_command([("a", first), ("b", second)]))

            adapter.write_scheduler.prune_budget(time.time() + 2.0)
            self.assertIs(adapter.write_scheduler.select_next_command([("a", first), ("b", second)])[1], first)
            health = adapter.write_scheduler.health(now=time.time())
            self.assertEqual(health["queue_class_budgets"]["remote-write"], 1)

            adapter.write_scheduler.prune_budget(time.time() + 2.0)
            install_mock(adapter.write_scheduler, "process_command", MagicMock(return_value="deferred"))
            self.assertEqual(adapter.write_scheduler.process_loaded_command("deferred.json", first), "deferred")
            self.assertFalse(adapter.write_scheduler.budget_available(first, time.time()))

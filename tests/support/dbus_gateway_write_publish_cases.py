# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway adapter local publication and registration contracts."""

from __future__ import annotations

from tests.support.dbus_gateway_adapter_harness import (
    CommandFileList,
    CommandMapping,
    DbusAdapter,
    DbusCommandInbox,
    FakeVeDbusService,
    GatewayAdapterContractCase,
    MagicMock,
    Path,
    RecordingDbusService,
    dbus_path_key,
    gateway_paths,
    install_mock,
    json,
    patch,
    read_json_file,
    tempfile,
    time,
    write_health_module,
    write_publish_module,
    write_support_module,
)


class GatewayWritePublishCases(GatewayAdapterContractCase):
    """Exercise local publication and registration contracts."""

    def test_publish_desired_processes_one_path_per_tick(self) -> None:
        with self.adapter_scenario("[DEFAULT]\nDbusGatewayLocalPublishBurstLimit=1\n") as scenario:
            adapter = scenario.adapter
            service = RecordingDbusService()
            adapter.set_dbus_service(service, registered=True)
            adapter.write_scheduler.registered_paths.update({"/A", "/B"})
            command_path = adapter.commands.enqueue(
                {
                    "kind": "publish_desired",
                    "paths": {"/A": 1, "/B": 2},
                    "coalesce_key": "publish-batch",
                    "priority": "publish",
                }
            )

            self.assertTrue(adapter.write_scheduler.process_one())
            self.assertEqual(service.writes, [("/A", 1)])
            remaining = read_json_file(command_path, {})
            self.assertEqual(remaining["paths"], {"/B": 2})

            adapter.rate_limiter.next_at["write"] = time.monotonic()
            self.assertTrue(adapter.write_scheduler.process_one())
            self.assertEqual(service.writes, [("/A", 1), ("/B", 2)])
            self.assertFalse(Path(command_path).exists())

    def test_publish_desired_bursts_local_evcs_paths_without_remote_rate_limit(self) -> None:
        with self.adapter_scenario("[DEFAULT]\nDbusGatewayLocalPublishBurstLimit=3\n") as scenario:
            adapter = scenario.adapter
            service = RecordingDbusService()
            adapter.set_dbus_service(service)
            adapter.write_scheduler.registered_paths.update({"/A", "/B", "/C", "/D"})
            command_path = adapter.commands.enqueue(
                {
                    "kind": "publish_desired",
                    "paths": {"/A": 1, "/B": 2, "/C": 3, "/D": 4},
                    "coalesce_key": "publish-batch",
                    "priority": "publish",
                }
            )

            self.assertTrue(adapter.write_scheduler.process_one())
            self.assertEqual(service.writes, [("/A", 1), ("/B", 2), ("/C", 3)])
            self.assertEqual(read_json_file(command_path, {})["paths"], {"/D": 4})
            self.assertEqual(adapter.write_scheduler.health(now=time.time())["processed_commands_60s"], 0)

            self.assertTrue(adapter.write_scheduler.process_one())
            self.assertEqual(service.writes, [("/A", 1), ("/B", 2), ("/C", 3), ("/D", 4)])
            self.assertFalse(Path(command_path).exists())
            self.assertEqual(adapter.write_scheduler.health(now=time.time())["processed_commands_60s"], 1)

    def test_publish_desired_prioritizes_gui_paths_inside_large_snapshot(self) -> None:
        with self.adapter_scenario("[DEFAULT]\nDbusGatewayLocalPublishBurstLimit=3\n") as scenario:
            adapter = scenario.adapter
            service = RecordingDbusService()
            adapter.set_dbus_service(service)
            adapter.write_scheduler.registered_paths.update(
                {"/Auto/Reason", "/Mode", "/Status", "/StartStop", "/Ac/L2/Power"}
            )
            command_path = adapter.commands.enqueue(
                {
                    "kind": "publish_desired",
                    "paths": {
                        "/Auto/Reason": "idle",
                        "/Ac/L2/Power": 0.0,
                        "/Mode": 1,
                        "/Status": 6,
                        "/StartStop": 1,
                    },
                    "coalesce_key": "publish-batch",
                    "priority": "publish",
                }
            )

            self.assertTrue(adapter.write_scheduler.process_one())

            self.assertEqual(service.writes, [("/Mode", 1), ("/StartStop", 1), ("/Status", 6)])
            self.assertEqual(
                read_json_file(command_path, {})["paths"],
                {"/Ac/L2/Power": 0.0, "/Auto/Reason": "idle"},
            )

    def test_repeated_local_publish_refreshes_cache_without_rewriting_dbus(self) -> None:
        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            service = RecordingDbusService()
            adapter.set_dbus_service(service, registered=True)
            adapter.write_scheduler.registered_paths.add("/Ac/Power")

            self.assertEqual(adapter.write_scheduler.publish_path("/Ac/Power", 1200.0), "applied")
            self.assertEqual(service.writes, [("/Ac/Power", 1200.0)])
            key = dbus_path_key(adapter.service_name, "/Ac/Power")
            adapter.cache.update_value(key, 1200.0, source="old", now=time.time() - 60.0)

            self.assertEqual(adapter.write_scheduler.publish_path("/Ac/Power", 1200.0), "applied")

            self.assertEqual(service.writes, [("/Ac/Power", 1200.0)])
            refreshed = adapter.cache.value_snapshot(adapter.cache.values[key], time.time())
            self.assertEqual(refreshed["value"], 1200.0)
            self.assertEqual(refreshed["status"], "fresh")
            self.assertLess(refreshed["age_s"], 1.0)

            adapter.write_scheduler.last_values["/Unregistered"] = "same"
            self.assertEqual(adapter.write_scheduler.publish_path("/Unregistered", "same"), "applied")
            self.assertNotIn(dbus_path_key(adapter.service_name, "/Unregistered"), adapter.cache.values)

    def test_write_scheduler_registers_paths_gui_writes_and_command_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            self.assertEqual(adapter.write_scheduler.process_command({"kind": "register_service"}), "applied")
            self.assertTrue(adapter.dbus_service.registered)
            outcome = adapter.write_scheduler.process_command(
                {"kind": "register_path", "path": "/Mode", "value": 1, "writeable": True}
            )
            self.assertEqual(outcome, "applied")
            self.assertIn("/Mode", adapter.write_scheduler.registered_paths)
            self.assertEqual(
                adapter.write_scheduler.process_command({"kind": "register_path", "path": "/Mode"}), "applied"
            )
            self.assertTrue(adapter.write_scheduler.handle_gui_write("/Mode", 2))
            self.assertEqual(adapter.core_commands.load_pending()[0][1]["value"], 2)

            self.assertEqual(
                adapter.write_scheduler.publish_command({"kind": "publish_desired", "paths": []}), "dropped"
            )
            self.assertEqual(
                adapter.write_scheduler.publish_command({"kind": "publish_desired", "paths": {}}), "applied"
            )
            self.assertEqual(
                adapter.write_scheduler.publish_command({"kind": "publish_desired", "paths": {"/Missing": 1}}),
                "dropped",
            )
            adapter.write_scheduler.local_publish_burst_limit = 1
            adapter.write_scheduler.registered_paths.update({"/A", "/B"})
            self.assertEqual(
                adapter.write_scheduler.publish_command({"kind": "publish_desired", "paths": {"/A": 1, "/B": 2}}),
                "deferred",
            )
            self.assertEqual(adapter.dbus_service["/A"], 1)
            adapter.write_scheduler.registered_paths.update({"/Ac/Power", "/Session/Time"})
            self.assertEqual(
                adapter.write_scheduler.publish_command(
                    {"kind": "publish_fields", "fields": {"ac_power_w": 1200.0, "session_time_s": 30}}
                ),
                "deferred",
            )
            self.assertEqual(adapter.dbus_service["/Ac/Power"], 1200.0)
            self.assertEqual(adapter.write_scheduler.publish_path("", 1), "applied")
            self.assertEqual(adapter.write_scheduler.publish_path("/Missing", 1), "dropped")
            self.assertEqual(
                adapter.write_scheduler.publish_command({"kind": "publish_value", "path": "/Missing", "value": 1}),
                "dropped",
            )
            self.assertEqual(
                adapter.write_scheduler.publish_command({"kind": "publish_fields", "fields": []}), "dropped"
            )
            self.assertEqual(adapter.write_scheduler.process_command({"kind": "unknown"}), "dropped")
            self.assertEqual(adapter.write_scheduler.process_command({"kind": "set_value"}), "dropped")
            self.assertFalse(adapter.write_scheduler.command_expired({"deadline_s": "bad"}))
            adapter.write_scheduler.drop_stale_coalesced_commands("/tmp/none", {})
            processed = Path(adapter.paths.command_dir) / "processed.json"
            stale = Path(adapter.paths.command_dir) / "stale.json"
            stale.parent.mkdir(parents=True, exist_ok=True)

            processed.write_text(json.dumps({"kind": "publish_value", "coalesce_key": "same"}), encoding="utf-8")
            stale.write_text(json.dumps({"kind": "publish_value", "coalesce_key": "same"}), encoding="utf-8")
            adapter.write_scheduler.drop_stale_coalesced_commands(str(processed), {"coalesce_key": "same"})
            self.assertTrue(processed.exists())
            self.assertFalse(stale.exists())

            commands = [
                ("fresh-publish", {"kind": "publish_value", "priority": "publish", "created_at": 999.0}),
                ("old-discovery", {"kind": "refresh_services", "priority": "discovery", "created_at": 990.0}),
            ]
            with patch.object(write_health_module.time, "time", return_value=1000.0):
                prioritized = adapter.write_scheduler.prioritized_commands(commands)
                self.assertEqual(adapter.write_scheduler.select_next_command(prioritized)[0], "fresh-publish")
            with patch.object(write_health_module.time, "time", return_value=1010.0):
                prioritized = adapter.write_scheduler.prioritized_commands(commands)
                self.assertEqual(adapter.write_scheduler.select_next_command(prioritized)[0], "old-discovery")
            protected = [("fresh-user", {"priority": "user", "created_at": 999.0}), *commands]
            with patch.object(write_health_module.time, "time", return_value=1010.0):
                prioritized = adapter.write_scheduler.prioritized_commands(protected)
                self.assertEqual(adapter.write_scheduler.select_next_command(prioritized)[0], "fresh-user")
            adapter.write_scheduler.queue_class_budgets["diagnostic"] = 0
            self.assertIsNone(adapter.write_scheduler.select_next_command([("diag", {"kind": "unknown"})]))
            self.assertFalse(adapter.write_scheduler.budget_available({"queue_class": "diagnostic"}, time.time()))
            adapter.write_scheduler.queue_class_budgets["diagnostic"] = 1
            adapter.commands.enqueue(
                {
                    "kind": "publish_value",
                    "path": "/Mode",
                    "value": 1,
                    "priority": "publish",
                    "coalesce_key": "publish:/Mode",
                }
            )
            self.assertFalse(adapter.write_scheduler.process_one(include_local_publish=False))
            for command_path, _command in adapter.commands.load_pending():
                adapter.commands.remove(command_path)

            publish_burst = DbusCommandInbox.coalesce(
                [
                    (
                        "old-auto",
                        {
                            "id": "old-auto",
                            "kind": "publish_value",
                            "priority": "publish",
                            "path": "/Auto/DbusBackoffBaseSeconds",
                            "created_at": 1.0,
                            "coalesce_key": "publish:/Auto/DbusBackoffBaseSeconds",
                        },
                    ),
                    (
                        "fresh-session",
                        {
                            "id": "fresh-session",
                            "kind": "publish_value",
                            "priority": "publish",
                            "path": "/Session/Time",
                            "created_at": 2.0,
                            "coalesce_key": "publish:/Session/Time",
                        },
                    ),
                    (
                        "old-l2-energy",
                        {
                            "id": "old-l2-energy",
                            "kind": "publish_value",
                            "priority": "publish",
                            "path": "/Ac/L2/Energy/Forward",
                            "created_at": 0.5,
                            "coalesce_key": "publish:/Ac/L2/Energy/Forward",
                        },
                    ),
                ]
            )
            self.assertEqual(adapter.write_scheduler.select_next_command(publish_burst)[0], "old-l2-energy")
            self.assertIsNone(adapter.write_scheduler.select_next_command(publish_burst, include_local_publish=False))
            with patch.object(write_health_module.time, "time", return_value=0.0):
                adapter.write_scheduler.record_budget({"queue_class": "local-publish"})
            adapter.write_scheduler.prune_budget(time.time())
            self.assertEqual(adapter.write_scheduler.queue_class_usage_1s(), {})
            with patch.object(write_publish_module.time, "time", return_value=0.0):
                adapter.write_scheduler.record_processed()
            adapter.write_scheduler.prune_processed(time.time())
            self.assertEqual(adapter.write_scheduler.health(now=time.time())["processed_commands_60s"], 0)
            with patch.object(write_health_module.time, "time", return_value=0.0):
                adapter.write_scheduler.record_lifecycle({"queue_class": "local-publish"}, "applied")
            adapter.write_scheduler.prune_lifecycle(time.time())
            self.assertEqual(adapter.write_scheduler.lifecycle_counts_60s(), {})
            self.assertEqual(write_support_module.float_or_zero(object()), 0.0)

    def test_write_scheduler_publish_contract_edges_are_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.set_dbus_service(FakeVeDbusService(), registered=True)

            self.assertEqual(adapter.write_scheduler.register_path({}), "applied")
            self.assertEqual(adapter.write_scheduler.registered_paths, set())
            self.assertEqual(adapter.dbus_service.added_paths, {})

            self.assertEqual(
                adapter.write_scheduler.register_path({"path": "/Mode", "value": 1, "writeable": True}),
                "applied",
            )
            self.assertEqual(
                adapter.dbus_service.added_paths["/Mode"],
                {
                    "value": 1,
                    "writeable": True,
                    "onchangecallback": adapter.write_scheduler.handle_gui_write,
                },
            )
            self.assertEqual(adapter.write_scheduler.last_values["/Mode"], 1)

            self.assertEqual(
                adapter.write_scheduler.register_path({"path": "/Status", "value": 0, "writeable": False}),
                "applied",
            )
            self.assertEqual(
                adapter.dbus_service.added_paths["/Status"],
                {"value": 0, "writeable": False, "onchangecallback": None},
            )
            self.assertEqual(adapter.write_scheduler.last_values["/Status"], 0)

            self.assertTrue(adapter.write_scheduler.handle_gui_write("/Mode", 2))
            self.assertEqual(adapter.write_scheduler.last_values["/Mode"], 2)
            gui_command = adapter.core_commands.load_pending()[0][1]
            self.assertEqual(gui_command["kind"], "user_command")
            self.assertEqual(gui_command["source"], "dbus-gui")
            self.assertEqual(gui_command["origin"], "gateway-local-write-callback")
            self.assertEqual(gui_command["path"], "/Mode")
            self.assertEqual(gui_command["value"], 2)
            self.assertEqual(gui_command["priority"], "user")
            self.assertEqual(gui_command["coalesce_key"], "core:/Mode")

            with patch.object(adapter.json_writer, "write") as json_write:
                adapter.write_scheduler.local_publish_burst_limit = 1
                adapter.write_scheduler.registered_paths.update({"/DeferredA", "/DeferredB"})
                self.assertEqual(
                    adapter.write_scheduler.publish_command(
                        {"kind": "publish_desired", "paths": {"/DeferredA": 1, "/DeferredB": 2}}
                    ),
                    "deferred",
                )
            json_write.assert_not_called()

            self.assertEqual(adapter.write_scheduler.publish_command({"kind": "publish_value", "value": 1}), "applied")

            adapter.write_scheduler.local_publish_burst_limit = 2
            self.assertEqual(
                adapter.write_scheduler.publish_command(
                    {"kind": "publish_desired", "paths": {"/DeferredA": 3, "/MissingAfterPartial": 4}}
                ),
                "deferred",
            )

            with patch.object(write_publish_module.logging, "debug") as log_debug:
                self.assertEqual(adapter.write_scheduler.publish_path("/Missing", 1), "dropped")
            log_debug.assert_called_once_with(
                "Dropping publish for unregistered DBus path %s",
                "/Missing",
            )

            adapter.write_scheduler.registered_paths.add("/Ac/Power")
            self.assertEqual(adapter.write_scheduler.publish_path("/Ac/Power", 1200.0), "applied")
            key = dbus_path_key(adapter.service_name, "/Ac/Power")
            self.assertEqual(adapter.cache.values[key]["source"], f"{adapter.service_name}/Ac/Power")
            self.assertEqual(adapter.cache.values[key]["confidence"], 1.0)
            self.assertEqual(adapter.cache.values[key]["value"], 1200.0)

            stale = Path(adapter.paths.command_dir) / "stale.json"
            stale.parent.mkdir(parents=True, exist_ok=True)
            stale.write_text(json.dumps({"kind": "publish_value", "coalesce_key": "XXXX"}), encoding="utf-8")
            adapter.write_scheduler.drop_stale_coalesced_commands("processed.json", {})
            self.assertTrue(stale.exists())
            adapter.commands.remove(str(stale))

            remote_path = adapter.commands.enqueue(
                {"kind": "set_value", "service": "svc", "path": "/Remote", "priority": "user"}
            )
            self.assertIsNone(adapter.write_scheduler.next_local_publish_command())
            adapter.commands.remove(remote_path)

            local_path = adapter.commands.enqueue(
                {
                    "kind": "publish_value",
                    "path": "/Ac/Power",
                    "value": 1300.0,
                    "priority": "publish",
                    "coalesce_key": "publish:/Ac/Power",
                }
            )
            self.assertEqual(adapter.write_scheduler.next_local_publish_command()[0], local_path)

            seen_pending: list[CommandFileList | None] = []

            def _process_loaded(
                path: str,
                command: CommandMapping,
                *,
                pending_commands: CommandFileList | None = None,
            ) -> str:
                seen_pending.append(pending_commands)
                return "applied"

            install_mock(adapter.write_scheduler, "process_loaded_command", MagicMock(side_effect=_process_loaded))
            self.assertEqual(adapter.write_scheduler.process_local_publish_burst(limit=1), 1)
            self.assertIsNotNone(seen_pending[0])
            assert seen_pending[0] is not None
            self.assertGreaterEqual(len(seen_pending[0]), 1)

            with patch.object(write_publish_module.time, "time", return_value=333.0):
                adapter.write_scheduler.record_processed()
            self.assertEqual(adapter.write_scheduler.last_processed_at, 333.0)
            self.assertEqual(list(adapter.write_scheduler._processed_events)[-1], 333.0)

            candidate = write_publish_module._LocalPublishCandidate(
                processed=0,
                remaining_budget=1,
                pending_commands=[],
                started=time.monotonic(),
            )
            install_mock(adapter.write_scheduler, "_skip_local_publish_command", MagicMock(return_value=True))
            self.assertEqual(
                adapter.write_scheduler._process_local_publish_candidate("path", {"kind": "set_value"}, candidate),
                "skip",
            )
            install_mock(adapter.write_scheduler, "_skip_local_publish_command", MagicMock(return_value=False))
            install_mock(adapter.write_scheduler, "process_loaded_command", MagicMock(return_value="deferred"))
            self.assertEqual(
                adapter.write_scheduler._process_local_publish_candidate("path", {"kind": "publish_value"}, candidate),
                "break",
            )
            self.assertTrue(adapter.write_scheduler._local_publish_burst_done(0, 0, time.monotonic()))
            self.assertTrue(adapter.write_scheduler._local_publish_burst_done(1, 1, time.monotonic()))

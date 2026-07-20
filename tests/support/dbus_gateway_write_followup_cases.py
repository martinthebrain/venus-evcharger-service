# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway adapter follow-up burst and remote-write scenarios."""

from __future__ import annotations

from tests.support.dbus_gateway_adapter_harness import (
    BusItemInterfaceStub,
    DbusAdapter,
    DbusOperationDeferred,
    GatewayAdapterContractCase,
    MagicMock,
    Path,
    RecordingDbusService,
    gateway_paths,
    install_mock,
    patch,
    read_json_file,
    tempfile,
    time,
    write_core_module,
    write_health_module,
)


class GatewayWriteFollowupCases(GatewayAdapterContractCase):
    """Exercise follow-up burst and remote-write scenarios."""

    def test_next_scheduled_command_runs_followup_burst_only_after_local_publish_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\nDbusGatewayLocalPublishBurstLimit=4\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            self.assertIsNone(adapter.write_scheduler.last_scheduled_outcome)
            adapter.write_scheduler.last_scheduled_outcome = "deferred"
            install_mock(adapter.commands, "load_pending", MagicMock(return_value=[]))
            self.assertFalse(adapter.write_scheduler.process_one())
            self.assertIsNone(adapter.write_scheduler.last_scheduled_outcome)
            install_mock(adapter.write_scheduler, "process_loaded_command", MagicMock(return_value="applied"))
            install_mock(adapter.write_scheduler, "process_local_publish_burst", MagicMock())

            self.assertTrue(
                adapter.write_scheduler.process_next_scheduled_command(
                    [("publish.json", {"kind": "publish_value", "path": "/Mode"})],
                    include_local_publish=True,
                )
            )
            adapter.write_scheduler.process_local_publish_burst.assert_called_once_with(3)

            adapter.write_scheduler.process_local_publish_burst.reset_mock()
            install_mock(adapter.write_scheduler, "process_loaded_command", MagicMock(return_value="deferred"))
            self.assertTrue(
                adapter.write_scheduler.process_next_scheduled_command(
                    [("publish.json", {"kind": "publish_value", "path": "/Mode"})],
                    include_local_publish=True,
                )
            )
            self.assertEqual(adapter.write_scheduler.last_scheduled_outcome, "deferred")
            adapter.write_scheduler.process_local_publish_burst.assert_not_called()

            install_mock(adapter.write_scheduler, "process_loaded_command", MagicMock(return_value="applied"))
            self.assertTrue(
                adapter.write_scheduler.process_next_scheduled_command(
                    [("remote.json", {"kind": "set_value", "service": "svc", "path": "/Remote"})],
                    include_local_publish=True,
                )
            )
            adapter.write_scheduler.process_local_publish_burst.assert_not_called()

            adapter.write_scheduler.local_publish_burst_limit = 0
            install_mock(adapter.write_scheduler, "process_loaded_command", MagicMock(return_value="applied"))
            self.assertTrue(
                adapter.write_scheduler.process_next_scheduled_command(
                    [("publish.json", {"kind": "publish_value", "path": "/Mode"})],
                    include_local_publish=True,
                )
            )
            adapter.write_scheduler.process_local_publish_burst.assert_called_once_with(0)

    def test_process_loaded_command_applies_drop_defer_and_expiry_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            applied = adapter.commands.enqueue({"kind": "set_value", "priority": "user", "created_at": 1.0})
            install_mock(adapter.write_scheduler, "process_command", MagicMock(return_value="applied"))
            self.assertEqual(
                adapter.write_scheduler.process_loaded_command(applied, read_json_file(applied, {})),
                "applied",
            )
            self.assertFalse(Path(applied).exists())

            dropped = adapter.commands.enqueue({"kind": "set_value", "priority": "user", "created_at": 2.0})
            install_mock(adapter.write_scheduler, "process_command", MagicMock(return_value="dropped"))
            self.assertEqual(
                adapter.write_scheduler.process_loaded_command(dropped, read_json_file(dropped, {})),
                "dropped",
            )
            self.assertFalse(Path(dropped).exists())

            deferred = adapter.commands.enqueue({"kind": "set_value", "priority": "user", "created_at": 3.0})
            install_mock(adapter.write_scheduler, "process_command", MagicMock(return_value="deferred"))
            self.assertEqual(
                adapter.write_scheduler.process_loaded_command(deferred, read_json_file(deferred, {})),
                "deferred",
            )
            self.assertTrue(Path(deferred).exists())

            expired = adapter.commands.enqueue(
                {"kind": "set_value", "priority": "user", "created_at": time.time() - 10.0, "deadline_s": 1.0}
            )
            self.assertEqual(
                adapter.write_scheduler.process_loaded_command(expired, read_json_file(expired, {})),
                "dropped",
            )
            self.assertFalse(Path(expired).exists())
            lifecycle = adapter.write_scheduler.health(now=time.time())["lifecycle_counts"]
            self.assertGreaterEqual(lifecycle["applied"], 1)
            self.assertGreaterEqual(lifecycle["dropped"], 1)
            self.assertGreaterEqual(lifecycle["deferred"], 1)
            self.assertGreaterEqual(lifecycle["expired"], 1)

    def test_process_loaded_command_forwards_pending_commands_to_stale_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            pending = [("stale.json", {"coalesce_key": "same"})]
            command = {"kind": "set_value", "priority": "user", "created_at": 1.0, "coalesce_key": "same"}
            install_mock(adapter.write_scheduler, "process_command", MagicMock(return_value="applied"))
            install_mock(adapter.write_scheduler, "drop_stale_coalesced_commands", MagicMock())

            self.assertEqual(
                adapter.write_scheduler.process_loaded_command("command.json", command, pending_commands=pending),
                "applied",
            )

            adapter.write_scheduler.drop_stale_coalesced_commands.assert_called_once_with(
                "command.json",
                command,
                pending_commands=pending,
            )

            expired = {"kind": "set_value", "created_at": time.time() - 10.0, "deadline_s": 1.0, "coalesce_key": "same"}
            adapter.write_scheduler.drop_stale_coalesced_commands.reset_mock()
            self.assertEqual(
                adapter.write_scheduler.process_loaded_command("expired.json", expired, pending_commands=pending),
                "dropped",
            )
            adapter.write_scheduler.drop_stale_coalesced_commands.assert_called_once_with(
                "expired.json",
                expired,
                pending_commands=pending,
            )

    def test_command_expired_handles_created_at_and_boundary_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            with patch.object(write_core_module.time, "time", return_value=10.0):
                self.assertFalse(adapter.write_scheduler.command_expired({"created_at": 0.0, "deadline_s": 1.0}))
                self.assertTrue(adapter.write_scheduler.command_expired({"created_at": 1.0, "deadline_s": 1.0}))
                self.assertFalse(adapter.write_scheduler.command_expired({"created_at": 9.0, "deadline_s": 1.0}))
                self.assertFalse(adapter.write_scheduler.command_expired({"created_at": 12.0, "deadline_s": 1.0}))

    def test_command_outcome_returns_deferred_and_logs_retry_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            command = {"kind": "set_value", "priority": "user"}

            install_mock(
                adapter.write_scheduler,
                "process_command",
                MagicMock(side_effect=DbusOperationDeferred("wait")),
            )
            self.assertEqual(adapter.write_scheduler.command_outcome("defer.json", command), "deferred")

            install_mock(
                adapter.write_scheduler,
                "process_command",
                MagicMock(side_effect=RuntimeError("boom")),
            )
            with patch("venus_evcharger.dbus_adapter.write.core.logging.exception") as logged:
                self.assertEqual(adapter.write_scheduler.command_outcome("retry.json", command), "deferred")

            logged.assert_called_once()
            self.assertEqual(logged.call_args.args[0], "Gateway command failed; keeping for retry path=%s: %s")
            self.assertEqual(logged.call_args.args[1], "retry.json")
            self.assertIsInstance(logged.call_args.args[2], RuntimeError)

    def test_local_publish_burst_can_run_before_non_local_scheduler_slot(self) -> None:
        with self.adapter_scenario(
            "[DEFAULT]\nDbusGatewayLocalPublishBurstLimit=2\nDbusIntrospectionEnabled=0\n",
        ) as scenario:
            adapter = scenario.adapter
            adapter.cache.update_services(["svc"])
            adapter.write_scheduler.registered_paths.update({"/Session/Time", "/Ac/Power"})
            service = RecordingDbusService()
            adapter.set_dbus_service(service, registered=True)
            adapter.commands.enqueue(
                {
                    "kind": "publish_value",
                    "path": "/Session/Time",
                    "value": 10,
                    "coalesce_key": "publish:/Session/Time",
                    "priority": "publish",
                }
            )
            adapter.commands.enqueue(
                {
                    "kind": "publish_value",
                    "path": "/Ac/Power",
                    "value": 2000,
                    "coalesce_key": "publish:/Ac/Power",
                    "priority": "publish",
                }
            )
            adapter.commands.enqueue({"kind": "refresh_services", "priority": "read"})
            install_mock(adapter, "poll_one_due_read_once", MagicMock(return_value=False))
            install_mock(adapter, "refresh_services_if_due_once", MagicMock(return_value=False))
            install_mock(adapter, "enqueue_background_introspection_if_due", MagicMock())
            install_mock(adapter, "list_services", MagicMock(return_value=["svc"]))
            install_mock(adapter.discovery, "refresh_services", MagicMock(return_value=["svc"]))

            self.assertTrue(adapter.process_one_dbus_operation_once())

            self.assertCountEqual(service.writes, [("/Ac/Power", 2000), ("/Session/Time", 10)])
            self.assertEqual(adapter.commands.load_pending(), [])
            self.assertGreaterEqual(adapter.write_scheduler.health(now=time.time())["processed_commands_60s"], 3)

    def test_write_scheduler_set_remote_value_uses_dbus_and_updates_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            fake_iface = BusItemInterfaceStub()
            fake_obj = object()
            get_object = install_mock(adapter.connection, "get_object", MagicMock(return_value=fake_obj))

            with patch.object(write_health_module.dbus, "Interface", return_value=fake_iface):
                outcome = adapter.write_scheduler.set_remote_value(
                    {"service": "svc", "path": "/Set", "value": 9, "timeout": 2.0}
                )

            self.assertEqual(outcome, "applied")
            get_object.assert_called_once_with("svc", "/Set", introspect=False)
            self.assertEqual(fake_iface.set_calls, [(9, 2.0)])
            self.assertEqual(adapter.cache.values["path:svc/Set"]["value"], 9)

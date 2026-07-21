# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway adapter command dispatch and persistence contracts."""

from __future__ import annotations

from tests.support.dbus_gateway_adapter_harness import (
    DbusAdapter,
    DbusOperationDeferred,
    GatewayAdapterContractCase,
    MagicMock,
    Path,
    builtins,
    gateway_paths,
    install_mock,
    patch,
    tempfile,
    time,
    unittest,
)


class GatewayWriteCommandDispatchCases(GatewayAdapterContractCase):
    """Exercise command dispatch and persistence contracts."""

    def test_write_scheduler_process_one_defers_on_priority_and_exceptions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.circuit.protective_until = time.time() + 10
            path = adapter.commands.enqueue({"kind": "refresh_services", "priority": "diagnostic"})

            self.assertTrue(adapter.write_scheduler.process_one())
            self.assertTrue(Path(path).exists())
            adapter.write_scheduler.prune_budget(time.time() + 2.0)

            adapter.circuit.protective_until = 0
            install_mock(
                adapter.write_scheduler,
                "process_command",
                MagicMock(side_effect=DbusOperationDeferred("write")),
            )
            self.assertTrue(adapter.write_scheduler.process_one())
            self.assertTrue(Path(path).exists())
            adapter.write_scheduler.prune_budget(time.time() + 2.0)

            install_mock(
                adapter.write_scheduler,
                "process_command",
                MagicMock(side_effect=RuntimeError("boom")),
            )
            self.assertTrue(adapter.write_scheduler.process_one())
            self.assertTrue(Path(path).exists())

            empty_adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "empty-run")))
            self.assertFalse(empty_adapter.write_scheduler.process_one())
            empty_adapter.write_scheduler.record_lifecycle({"kind": "noop"}, "queued")
            self.assertEqual(empty_adapter.write_scheduler.health(now=time.time())["lifecycle_counts"]["queued"], 1)
            empty_adapter.command_lifecycle_path = ""
            empty_adapter.write_scheduler.record_lifecycle({"kind": "noop"}, "dropped")
            bad_lifecycle = Path(temp_dir) / "bad-lifecycle.jsonl"
            empty_adapter.command_lifecycle_path = str(bad_lifecycle)
            with patch.object(builtins, "open", side_effect=OSError("full")):
                empty_adapter.write_scheduler.record_lifecycle({"kind": "noop"}, "dropped")
            empty_adapter.command_lifecycle_path = "lifecycle-without-dir.jsonl"
            lifecycle_handle = unittest.mock.mock_open()
            with patch.object(builtins, "open", lifecycle_handle):
                empty_adapter.write_scheduler.record_lifecycle({"kind": "noop"}, "queued")
            lifecycle_handle.assert_not_called()

    def test_process_one_can_skip_local_publish_and_process_remote_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            local_path = adapter.commands.enqueue(
                {
                    "kind": "publish_value",
                    "path": "/Mode",
                    "value": 1,
                    "priority": "publish",
                    "coalesce_key": "publish:/Mode",
                }
            )
            remote_path = adapter.commands.enqueue(
                {
                    "kind": "set_value",
                    "service": "svc",
                    "path": "/Remote",
                    "value": 2,
                    "priority": "user",
                    "coalesce_key": "remote:/Remote",
                }
            )
            install_mock(adapter.write_scheduler, "process_command", MagicMock(return_value="applied"))

            self.assertTrue(adapter.write_scheduler.process_one(include_local_publish=False))

            processed = adapter.write_scheduler.process_command.call_args.args[0]
            self.assertEqual(processed["kind"], "set_value")
            self.assertTrue(Path(local_path).exists())
            self.assertFalse(Path(remote_path).exists())

    def test_process_command_enforces_circuit_before_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            install_mock(adapter.circuit, "allows_priority", MagicMock(return_value=False))
            install_mock(adapter.write_scheduler, "dispatch_command", MagicMock(return_value="applied"))

            self.assertEqual(
                adapter.write_scheduler.process_command({"kind": "set_value"}, command_file=""), "deferred"
            )

            adapter.circuit.allows_priority.assert_called_once_with("diagnostic")
            adapter.write_scheduler.dispatch_command.assert_not_called()

            install_mock(adapter.circuit, "allows_priority", MagicMock(return_value=True))
            command = {"kind": "set_value", "priority": "user"}
            self.assertEqual(adapter.write_scheduler.process_command(command, command_file="cmd.json"), "applied")
            adapter.circuit.allows_priority.assert_called_once_with("user")
            adapter.write_scheduler.dispatch_command.assert_called_once_with(command, command_file="cmd.json")

            adapter.write_scheduler.dispatch_command.reset_mock()
            install_mock(adapter.circuit, "allows_priority", MagicMock(return_value=True))
            self.assertEqual(adapter.write_scheduler.process_command(command), "applied")
            adapter.write_scheduler.dispatch_command.assert_called_once_with(command, command_file="")

    def test_dispatch_command_passes_command_file_to_publish_handlers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            install_mock(adapter.write_scheduler, "publish_command", MagicMock(return_value="applied"))
            install_mock(adapter.write_scheduler, "set_remote_value", MagicMock(return_value="applied"))
            setattr(adapter, "adapter_non_write_called", False)
            install_mock(adapter, "process_non_write_command", MagicMock(return_value="dropped"))

            publish = {"kind": "publish_value", "path": "/Mode"}
            desired = {"kind": "publish_desired", "paths": {"/Mode": 1}}
            fields = {"kind": "publish_fields", "fields": {"mode": 2}}
            remote = {"kind": "set_value", "service": "svc", "path": "/Mode"}
            unknown = {"kind": "unknown"}

            self.assertEqual(adapter.write_scheduler.dispatch_command(publish, command_file="publish.json"), "applied")
            adapter.write_scheduler.publish_command.assert_called_with(publish, command_file="publish.json")
            self.assertEqual(adapter.write_scheduler.dispatch_command(desired, command_file="desired.json"), "applied")
            adapter.write_scheduler.publish_command.assert_called_with(desired, command_file="desired.json")
            self.assertEqual(adapter.write_scheduler.dispatch_command(fields, command_file="fields.json"), "applied")
            adapter.write_scheduler.publish_command.assert_called_with(fields, command_file="fields.json")
            self.assertEqual(adapter.write_scheduler.dispatch_command(remote, command_file="remote.json"), "applied")
            adapter.write_scheduler.set_remote_value.assert_called_once_with(remote)
            self.assertEqual(adapter.write_scheduler.dispatch_command(unknown, command_file="unknown.json"), "dropped")
            adapter.process_non_write_command.assert_called_once_with(unknown)

    def test_publish_fields_rewrites_to_desired_paths_and_preserves_command_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            install_mock(adapter.write_scheduler, "_publish_desired", MagicMock(return_value="deferred"))

            command = {
                "kind": "publish_fields",
                "fields": {"mode": 2, "ac_power_w": 1200.0},
                "priority": "publish",
            }

            self.assertEqual(
                adapter.write_scheduler.publish_command(command, command_file="fields.json"),
                "deferred",
            )
            adapter.write_scheduler._publish_desired.assert_called_once_with(
                {
                    "kind": "publish_desired",
                    "fields": {"mode": 2, "ac_power_w": 1200.0},
                    "priority": "publish",
                    "paths": {"/Mode": 2, "/Ac/Power": 1200.0},
                },
                command_file="fields.json",
            )

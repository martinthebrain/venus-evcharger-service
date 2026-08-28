# SPDX-License-Identifier: GPL-3.0-or-later
"""Stable test entry point for split DBus gateway adapter scenarios.

The concrete cases live in small responsibility-focused support modules.
This class keeps historical unittest and mutation-audit node IDs stable.
"""

from __future__ import annotations

from unittest.mock import call

from tests.support import dbus_gateway_adapter_cases as adapter_cases
from tests.support.dbus_gateway_adapter_harness import (
    MagicMock,
    Path,
    evcs_registration,
    install_mock,
    patch,
    process_loop_module,
    tempfile,
)
from venus_evcharger.ipc.core_commands import core_control_command_payload
from venus_evcharger.ipc.energy import EnergyRefreshRequest


class DbusGatewayAdapterSchedulerTests(adapter_cases.AllGatewayAdapterCases):
    """Collect the responsibility-focused gateway adapter contract cases."""

    def test_health_snapshot_includes_gateway_diagnostics(self) -> None:
        """Replace the removed registered-path fixture with semantic registration."""
        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            self.assertEqual(adapter.write_scheduler.publication_executor.process(evcs_registration()), "applied")
            adapter.commands.enqueue(
                EnergyRefreshRequest("scheduler-topology", "topology", 0.0).to_command(source="scheduler-test")
            )
            adapter.core_command_mailbox.enqueue(
                core_control_command_payload(
                    "set_mode",
                    "mode",
                    1,
                    source="http",
                    origin="scheduler-test",
                )
            )

            health = adapter.health_snapshot()

            self.assertEqual(health["pending_command_count"], 1)
            self.assertEqual(health["core_command_count"], 1)
            self.assertEqual(
                health["registered_path_count"],
                adapter.publication_registry.registered_path_count,
            )

    def test_run_initializes_gateway_loop_and_closes_transport(self) -> None:
        """The loop starts IPC; publication registration is command-driven."""
        with tempfile.TemporaryDirectory() as temp_dir:
            scenario_context = self.adapter_scenario(
                "[DEFAULT]\nDbusGatewayMinTickSeconds=0.9995\n",
                run_directory=str(Path(temp_dir) / "run"),
            )
            with scenario_context as scenario:
                adapter = scenario.adapter
                fake_loop = MagicMock()
                install_mock(adapter.connection, "connect", MagicMock())
                install_mock(adapter.runtime_role, "install_signal_handlers", MagicMock())
                install_mock(adapter.socket_role, "start_socket", MagicMock())
                install_mock(adapter.socket_role, "install_glib_watch", MagicMock())
                install_mock(adapter.socket_role, "close_socket", MagicMock())
                install_mock(
                    adapter.operation_broker,
                    "cancel_current",
                    MagicMock(return_value=False),
                )

                with (
                    patch.object(process_loop_module, "DBusGMainLoop") as dbus_mainloop,
                    patch.object(process_loop_module.GLib, "MainLoop", return_value=fake_loop),
                    patch.object(process_loop_module.GLib, "timeout_add", return_value=123) as timeout_add,
                    patch.object(process_loop_module.os, "makedirs") as makedirs,
                ):
                    adapter.run()

                dbus_mainloop.assert_called_once_with(set_as_default=True)
                adapter.connection.connect.assert_called_once_with()
                adapter.runtime_role.install_signal_handlers.assert_called_once()
                self.assertEqual(
                    makedirs.call_args_list,
                    [
                        ((adapter.paths.run_dir,), {"exist_ok": True}),
                        ((adapter.paths.command_dir,), {"exist_ok": True}),
                        ((adapter.paths.core_command_dir,), {"exist_ok": True}),
                    ],
                )
                adapter.socket_role.start_socket.assert_called_once()
                adapter.socket_role.install_glib_watch.assert_called_once()
                self.assertEqual(
                    timeout_add.call_args_list,
                    [
                        call(
                            max(50, int(adapter.min_tick_seconds * 1000)),
                            adapter.loop_role._timer_tick,
                        ),
                    ],
                )
                fake_loop.run.assert_called_once_with()
                self.assertIs(adapter._main_loop, fake_loop)
                self.assertTrue(adapter._stop)
                adapter.operation_broker.cancel_current.assert_called_once_with(
                    "gateway shutdown",
                )
                adapter.socket_role.close_socket.assert_called_once()


if __name__ == "__main__":
    import unittest

    unittest.main()

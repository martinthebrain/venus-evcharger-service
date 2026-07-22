# SPDX-License-Identifier: GPL-3.0-or-later
"""Stable test entry point for split DBus gateway adapter scenarios.

The concrete cases live in small responsibility-focused support modules.
This class keeps historical unittest and mutation-audit node IDs stable.
"""

from __future__ import annotations

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
            self.assertEqual(adapter.write_scheduler.process_publication(evcs_registration()), "applied")
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
            scenario_context = self.adapter_scenario(run_directory=str(Path(temp_dir) / "run"))
            with scenario_context as scenario:
                adapter = scenario.adapter
                fake_loop = MagicMock()
                install_mock(adapter, "install_signal_handlers", MagicMock())
                install_mock(adapter, "start_socket", MagicMock())
                install_mock(adapter, "close_socket", MagicMock())

                with (
                    patch.object(process_loop_module, "DBusGMainLoop") as dbus_mainloop,
                    patch.object(process_loop_module.GLib, "MainLoop", return_value=fake_loop),
                    patch.object(process_loop_module.GLib, "timeout_add", return_value=123) as timeout_add,
                ):
                    adapter.run()

                dbus_mainloop.assert_called_once_with(set_as_default=True)
                adapter.install_signal_handlers.assert_called_once()
                adapter.start_socket.assert_called_once()
                timeout_add.assert_called_once_with(max(50, int(adapter.min_tick_seconds * 1000)), adapter.tick)
                fake_loop.run.assert_called_once_with()
                self.assertTrue(adapter._stop)
                adapter.close_socket.assert_called_once()


if __name__ == "__main__":
    import unittest

    unittest.main()

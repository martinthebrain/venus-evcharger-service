# SPDX-License-Identifier: GPL-3.0-or-later
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, call, patch

from venus_evcharger.bootstrap.runtime_loops import register_runtime_timers, start_runtime_loops


def _service(*, configured: bool) -> SimpleNamespace:
    runtime = SimpleNamespace(
        mark_mainloop_thread=MagicMock(),
        start_io_worker=MagicMock(),
        start_update_worker=MagicMock(),
        start_control_command_worker=MagicMock(),
        start_mainloop_watchdog=MagicMock(),
        schedule_update_cycle=MagicMock(return_value=True),
        flush_dbus_publish_queue=MagicMock(return_value=True),
        mainloop_heartbeat_tick=MagicMock(return_value=True),
    )
    state = SimpleNamespace(start_companion_bridge=MagicMock(), summary=MagicMock(return_value="mode=1"))
    update = SimpleNamespace(update=MagicMock(return_value=True), sign_of_life=MagicMock(return_value=True))
    control = SimpleNamespace(start_server=MagicMock())
    return SimpleNamespace(
        runtime=runtime,
        state=state,
        update=update,
        control=control,
        poll_interval_ms=1000,
        sign_of_life_minutes=10,
        runtime_state_path="/run/state.json",
        topology_configured=configured,
        host_configured=configured,
        _dbus_publish_flush_interval_ms=200,
    )


class BootstrapRuntimeLoopContracts(unittest.TestCase):
    def test_register_runtime_timers_uses_role_facade_callbacks(self) -> None:
        gobject = MagicMock()
        service = _service(configured=True)

        register_runtime_timers(service, gobject)

        self.assertEqual(
            gobject.timeout_add.call_args_list,
            [
                call(1000, service.runtime.schedule_update_cycle),
                call(200, service.runtime.flush_dbus_publish_queue),
                call(1000, service.runtime.mainloop_heartbeat_tick),
            ],
        )

    def test_start_runtime_loops_starts_all_roles_for_configured_topology(self) -> None:
        gobject = MagicMock()
        service = _service(configured=True)
        with patch("venus_evcharger.bootstrap.runtime_loops.os.getpid", return_value=4242), patch(
            "venus_evcharger.bootstrap.runtime_loops.logging.info"
        ) as info:
            start_runtime_loops(service, gobject)

        service.runtime.mark_mainloop_thread.assert_called_once_with()
        service.runtime.start_io_worker.assert_called_once_with()
        service.control.start_server.assert_called_once_with()
        service.runtime.start_update_worker.assert_called_once_with()
        service.runtime.start_control_command_worker.assert_called_once_with()
        service.runtime.start_mainloop_watchdog.assert_called_once_with()
        service.state.start_companion_bridge.assert_called_once_with()
        gobject.timeout_add.assert_any_call(600000, service.update.sign_of_life)
        info.assert_called_once_with(
            "Initialized Venus EV charger service pid=%s runtime_state=%s %s",
            4242,
            "/run/state.json",
            "mode=1",
        )

    def test_start_runtime_loops_skips_only_io_for_unconfigured_topology(self) -> None:
        gobject = MagicMock()
        service = _service(configured=False)
        with patch("venus_evcharger.bootstrap.runtime_loops.logging.info") as info:
            start_runtime_loops(service, gobject)

        service.runtime.start_io_worker.assert_not_called()
        service.control.start_server.assert_called_once_with()
        service.runtime.start_update_worker.assert_called_once_with()
        self.assertIn(
            call("No load topology is configured yet; skipping runtime I/O worker startup"),
            info.call_args_list,
        )


if __name__ == "__main__":
    unittest.main()

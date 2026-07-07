# SPDX-License-Identifier: GPL-3.0-or-later
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from venus_evcharger.bootstrap.runtime_loops import (
    call_runtime_hook,
    register_runtime_timers,
    start_runtime_loops,
    start_runtime_optional_hooks,
)


class BootstrapRuntimeLoopContracts(unittest.TestCase):
    def test_call_runtime_hook_invokes_only_callable_hooks(self) -> None:
        hook = MagicMock()
        service = SimpleNamespace(callable_hook=hook, non_callable_hook=None)

        self.assertTrue(call_runtime_hook(service, "callable_hook"))
        self.assertFalse(call_runtime_hook(service, "non_callable_hook"))
        self.assertFalse(call_runtime_hook(service, "missing_hook"))
        hook.assert_called_once_with()

    def test_start_runtime_optional_hooks_calls_known_hooks_in_order(self) -> None:
        calls: list[str] = []
        service = SimpleNamespace(
            _start_update_worker=lambda: calls.append("update"),
            _start_control_command_worker=lambda: calls.append("commands"),
            _start_mainloop_watchdog=lambda: calls.append("watchdog"),
            _start_companion_dbus_bridge=lambda: calls.append("bridge"),
        )

        start_runtime_optional_hooks(service)

        self.assertEqual(calls, ["update", "commands", "watchdog", "bridge"])

    def test_start_runtime_optional_hooks_skips_missing_and_non_callable_hooks(self) -> None:
        service = SimpleNamespace(
            _start_update_worker=MagicMock(),
            _start_control_command_worker=None,
            _start_companion_dbus_bridge=MagicMock(),
        )

        start_runtime_optional_hooks(service)

        service._start_update_worker.assert_called_once_with()
        service._start_companion_dbus_bridge.assert_called_once_with()

    def test_register_runtime_timers_uses_update_fallback_and_signals_optional_ticks(self) -> None:
        gobject_module = MagicMock()
        service = SimpleNamespace(
            poll_interval_ms=1000,
            _update=MagicMock(),
            _flush_dbus_publish_queue=MagicMock(),
            _mainloop_heartbeat_tick=MagicMock(),
        )

        register_runtime_timers(service, gobject_module)

        gobject_module.timeout_add.assert_any_call(1000, service._update)
        gobject_module.timeout_add.assert_any_call(200, service._flush_dbus_publish_queue)
        gobject_module.timeout_add.assert_any_call(1000, service._mainloop_heartbeat_tick)

    def test_register_runtime_timers_prefers_schedule_hook_and_configured_flush_interval(self) -> None:
        gobject_module = MagicMock()
        service = SimpleNamespace(
            poll_interval_ms=1500,
            _update=MagicMock(),
            _schedule_update_cycle=MagicMock(),
            _flush_dbus_publish_queue=MagicMock(),
            _dbus_publish_flush_interval_ms=123,
        )

        register_runtime_timers(service, gobject_module)

        gobject_module.timeout_add.assert_any_call(1500, service._schedule_update_cycle)
        gobject_module.timeout_add.assert_any_call(123, service._flush_dbus_publish_queue)
        self.assertFalse(any(call.args == (1500, service._update) for call in gobject_module.timeout_add.call_args_list))

    def test_register_runtime_timers_skips_non_callable_optional_hooks(self) -> None:
        gobject_module = MagicMock()
        service = SimpleNamespace(
            poll_interval_ms=1000,
            _update=MagicMock(),
            _flush_dbus_publish_queue=None,
            _mainloop_heartbeat_tick=None,
        )

        register_runtime_timers(service, gobject_module)

        self.assertEqual(gobject_module.timeout_add.call_args_list, [unittest.mock.call(1000, service._update)])

    def test_start_runtime_loops_starts_configured_topology_and_schedules_sign_of_life(self) -> None:
        gobject_module = MagicMock()
        service = SimpleNamespace(
            _mark_mainloop_thread=MagicMock(),
            _start_io_worker=MagicMock(),
            _start_control_api_server=MagicMock(),
            _start_companion_dbus_bridge=MagicMock(),
            topology_configured=True,
            runtime_state_path="/run/state.json",
            _state_summary=MagicMock(return_value="mode=1"),
            poll_interval_ms=1000,
            sign_of_life_minutes=10,
            _update=MagicMock(),
            _sign_of_life=MagicMock(),
        )

        with patch("venus_evcharger.bootstrap.runtime_loops.logging.info") as info_mock:
            with patch("venus_evcharger.bootstrap.runtime_loops.os.getpid", return_value=4242):
                start_runtime_loops(service, gobject_module)

        service._mark_mainloop_thread.assert_called_once_with()
        service._start_io_worker.assert_called_once_with()
        service._start_control_api_server.assert_called_once_with()
        service._start_companion_dbus_bridge.assert_called_once_with()
        gobject_module.timeout_add.assert_any_call(1000, service._update)
        gobject_module.timeout_add.assert_any_call(600000, service._sign_of_life)
        info_mock.assert_called_once_with(
            "Initialized Venus EV charger service pid=%s runtime_state=%s %s",
            4242,
            "/run/state.json",
            "mode=1",
        )

    def test_start_runtime_loops_skips_io_worker_for_unconfigured_topology(self) -> None:
        gobject_module = MagicMock()
        service = SimpleNamespace(
            _start_io_worker=MagicMock(),
            _start_control_api_server=MagicMock(),
            topology_configured=False,
            host_configured=False,
            runtime_state_path="/run/state.json",
            _state_summary=MagicMock(return_value="mode=0"),
            poll_interval_ms=1000,
            sign_of_life_minutes=1,
            _update=MagicMock(),
            _sign_of_life=MagicMock(),
        )

        with patch("venus_evcharger.bootstrap.runtime_loops.logging.info") as info_mock:
            with patch("venus_evcharger.bootstrap.runtime_loops.os.getpid", return_value=99):
                start_runtime_loops(service, gobject_module)

        service._start_io_worker.assert_not_called()
        service._start_control_api_server.assert_called_once_with()
        gobject_module.timeout_add.assert_any_call(60000, service._sign_of_life)
        self.assertEqual(
            info_mock.call_args_list,
            [
                unittest.mock.call("No load topology is configured yet; skipping runtime I/O worker startup"),
                unittest.mock.call(
                    "Initialized Venus EV charger service pid=%s runtime_state=%s %s",
                    99,
                    "/run/state.json",
                    "mode=0",
                ),
            ],
        )


if __name__ == "__main__":
    unittest.main()

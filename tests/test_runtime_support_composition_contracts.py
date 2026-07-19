# SPDX-License-Identifier: GPL-3.0-or-later
"""Architecture and facade contracts for composed runtime support."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from venus_evcharger.control import ControlCommand
from venus_evcharger.runtime.async_mainloop_control import ControlCommandQueue
from venus_evcharger.runtime.async_mainloop_executor import RuntimeExecutor
from venus_evcharger.runtime.async_mainloop_publish import DbusPublishQueue
from venus_evcharger.runtime.async_mainloop_state import AsyncRuntimeState
from venus_evcharger.runtime.async_mainloop_watchdog import MainloopWatchdog
from venus_evcharger.runtime.audit import RuntimeAuditLogger
from venus_evcharger.runtime.audit_fields import RuntimeAuditFields
from venus_evcharger.runtime.health import RuntimeHealthMonitor
from venus_evcharger.runtime.setup import RuntimeSetup
from venus_evcharger.runtime.state_store import RuntimeStateStore
from venus_evcharger.runtime.support import RuntimeSupportController


RUNTIME_COMPONENTS = (
    RuntimeSupportController,
    RuntimeStateStore,
    AsyncRuntimeState,
    ControlCommandQueue,
    RuntimeExecutor,
    MainloopWatchdog,
    DbusPublishQueue,
    RuntimeSetup,
    RuntimeAuditFields,
    RuntimeAuditLogger,
    RuntimeHealthMonitor,
)


class RuntimeSupportCompositionContractTests(unittest.TestCase):
    def test_runtime_components_are_linear_objects_without_mro_fragments(self) -> None:
        for component in RUNTIME_COMPONENTS:
            with self.subTest(component=component.__name__):
                self.assertEqual(component.__bases__, (object,))

    def test_composition_root_wires_one_shared_service_and_explicit_ports(self) -> None:
        service = SimpleNamespace()
        controller = RuntimeSupportController(service, lambda *_args: 0, lambda _reason: 0)

        for component in (
            controller.state,
            controller.async_state,
            controller.control_commands,
            controller.executor,
            controller.mainloop_watchdog,
            controller.publish_queue,
            controller.setup,
            controller.audit,
            controller.health,
        ):
            self.assertIs(component.service, service)

        self.assertFalse(hasattr(controller.audit_fields, "service"))

        self.assertIs(controller.executor.control_commands, controller.control_commands)
        self.assertIs(controller.mainloop_watchdog.thread_guard, controller.async_state)
        self.assertIs(controller.publish_queue.thread_guard, controller.async_state)
        self.assertIs(controller.publish_queue.watchdog, controller.mainloop_watchdog)
        self.assertIs(controller.setup.state_store, controller.state)
        self.assertIs(controller.setup.async_state, controller.async_state)
        self.assertIs(controller.audit.fields, controller.audit_fields)
        self.assertIs(controller.audit.state_store, controller.state)
        self.assertIs(controller.health.state_store, controller.state)

    def test_controller_exposes_only_productive_facade_not_legacy_test_hooks(self) -> None:
        controller = RuntimeSupportController(SimpleNamespace(), lambda *_args: 0, lambda _reason: 0)

        with self.assertRaisesRegex(RuntimeError, "Direct DBus access is disabled"):
            controller.create_system_bus()
        empty_snapshot = controller.empty_worker_snapshot()
        cloned_snapshot = controller.clone_worker_snapshot(empty_snapshot)
        self.assertEqual(cloned_snapshot, empty_snapshot)
        self.assertIsNot(cloned_snapshot, empty_snapshot)

        for obsolete_name in (
            "_runtime_executor_loop",
            "_runtime_executor_run_once",
            "_drain_control_commands_once",
            "_flush_companion_dbus_publish_queue",
            "_auto_audit_key",
            "_get_system_bus",
        ):
            self.assertFalse(hasattr(controller, obsolete_name), obsolete_name)

    def test_productive_facade_delegates_to_owned_components(self) -> None:
        controller = RuntimeSupportController(SimpleNamespace(), lambda *_args: 0, lambda _reason: 0)
        controller.setup = MagicMock(spec=RuntimeSetup)
        controller.state = MagicMock(spec=RuntimeStateStore)
        controller.async_state = MagicMock(spec=AsyncRuntimeState)
        controller.publish_queue = MagicMock(spec=DbusPublishQueue)
        controller.executor = MagicMock(spec=RuntimeExecutor)
        controller.control_commands = MagicMock(spec=ControlCommandQueue)
        controller.mainloop_watchdog = MagicMock(spec=MainloopWatchdog)
        controller.health = MagicMock(spec=RuntimeHealthMonitor)
        controller.audit = MagicMock(spec=RuntimeAuditLogger)
        command = ControlCommand(name="set_mode", path="/Mode", value=2)

        controller.initialize_runtime_support()
        controller.reset_system_bus()
        controller.ensure_system_bus_state()
        controller.init_worker_state()
        controller.ensure_worker_state()
        controller.set_worker_snapshot({"captured_at": 1.0})
        controller.update_worker_snapshot(grid_power=120.0)
        controller.get_worker_snapshot()
        controller.ensure_observability_state()
        controller.mark_mainloop_thread()
        controller.dbus_publish_direct_allowed()
        controller.assert_dbus_mainloop_thread("publish")
        controller.enqueue_dbus_publish_values([("/Mode", 2)], 10.0)
        controller.enqueue_dbus_publish_fields([("mode", 2)], 11.0)
        controller.enqueue_dbus_update_index_bump(12.0)
        controller.enqueue_companion_dbus_publish(13.0)
        controller.flush_dbus_publish_queue()
        controller.start_update_worker()
        controller.schedule_update_cycle()
        controller.start_control_command_worker()
        controller.enqueue_control_command(command)
        controller.mainloop_heartbeat_tick()
        controller.start_mainloop_watchdog()
        controller.is_update_stale(14.0)
        controller.watchdog_recover(15.0)
        controller.warning_throttled("dbus", 5.0, "message %s", "value")
        controller.mark_failure("dbus")
        controller.mark_recovery("dbus", "recovered %s", "now")
        controller.source_retry_ready("dbus", 16.0)
        controller.source_retry_remaining("dbus", 17.0)
        controller.delay_source_retry("dbus", 18.0, 3.0)
        controller.write_auto_audit_event("waiting", True)

        controller.setup.initialize_runtime_support.assert_called_once_with()
        controller.setup.reset_system_bus.assert_called_once_with()
        controller.setup.ensure_system_bus_state.assert_called_once_with()
        controller.state.initialize_worker_state.assert_called_once_with()
        controller.state.ensure_worker_state.assert_called_once_with()
        controller.state.set_worker_snapshot.assert_called_once_with({"captured_at": 1.0})
        controller.state.update_worker_snapshot.assert_called_once_with(grid_power=120.0)
        controller.state.get_worker_snapshot.assert_called_once_with()
        controller.state.ensure_observability_state.assert_called_once_with()
        controller.async_state.mark_mainloop_thread.assert_called_once_with()
        controller.async_state.direct_publish_allowed.assert_called_once_with()
        controller.async_state.assert_mainloop_thread.assert_called_once_with("publish")
        controller.publish_queue.enqueue_values.assert_called_once_with([("/Mode", 2)], 10.0)
        controller.publish_queue.enqueue_fields.assert_called_once_with([("mode", 2)], 11.0)
        controller.publish_queue.enqueue_update_index_bump.assert_called_once_with(12.0)
        controller.publish_queue.enqueue_companion_publish.assert_called_once_with(13.0)
        controller.publish_queue.flush.assert_called_once_with()
        controller.executor.start_update_worker.assert_called_once_with()
        controller.executor.schedule_update_cycle.assert_called_once_with()
        controller.executor.start_control_command_worker.assert_called_once_with()
        controller.control_commands.enqueue.assert_called_once_with(command)
        controller.mainloop_watchdog.heartbeat_tick.assert_called_once_with()
        controller.mainloop_watchdog.start.assert_called_once_with()
        controller.health.is_update_stale.assert_called_once_with(14.0)
        controller.health.watchdog_recover.assert_called_once_with(15.0)
        controller.health.warning_throttled.assert_called_once_with(
            "dbus", 5.0, "message %s", "value"
        )
        controller.health.mark_failure.assert_called_once_with("dbus")
        controller.health.mark_recovery.assert_called_once_with("dbus", "recovered %s", "now")
        controller.health.source_retry_ready.assert_called_once_with("dbus", 16.0)
        controller.health.source_retry_remaining.assert_called_once_with("dbus", 17.0)
        controller.health.delay_source_retry.assert_called_once_with("dbus", 18.0, 3.0)
        controller.audit.write_auto_audit_event.assert_called_once_with("waiting", True)


if __name__ == "__main__":
    unittest.main()

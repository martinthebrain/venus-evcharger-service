# SPDX-License-Identifier: GPL-3.0-or-later
"""Architecture and facade contracts for composed runtime support."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from venus_evcharger.control import ControlCommand
from venus_evcharger.readback_store import InMemoryReadbackStore
from venus_evcharger.runtime.async_mainloop_control import ControlCommandQueue
from venus_evcharger.runtime.async_mainloop_executor import RuntimeExecutor
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
    RuntimeSetup,
    RuntimeAuditFields,
    RuntimeAuditLogger,
    RuntimeHealthMonitor,
)


class RuntimeSupportCompositionContractTests(unittest.TestCase):
    def test_runtime_components_are_linear_objects(self) -> None:
        for component in RUNTIME_COMPONENTS:
            with self.subTest(component=component.__name__):
                self.assertEqual(component.__bases__, (object,))

    def test_composition_owns_only_runtime_responsibilities(self) -> None:
        service = SimpleNamespace()
        controller = RuntimeSupportController(
            service,
            lambda *_args: 0,
            lambda _reason: 0,
            script_path="/data/evcharger/service.py",
        )

        for component in (
            controller.state,
            controller.async_state,
            controller.control_commands,
            controller.executor,
            controller.mainloop_watchdog,
            controller.setup,
            controller.audit,
            controller.health,
        ):
            self.assertIs(component.service, service)
        self.assertIsInstance(service._readback_store, InMemoryReadbackStore)
        self.assertFalse(hasattr(controller.audit_fields, "service"))
        self.assertIs(controller.executor.control_commands, controller.control_commands)
        self.assertIs(controller.setup.state_store, controller.state)
        self.assertIs(controller.setup.async_state, controller.async_state)
        self.assertEqual(controller.setup.repo_root, "/data/evcharger")
        self.assertIs(controller.audit.fields, controller.audit_fields)
        self.assertIs(controller.audit.state_store, controller.state)
        self.assertIs(controller.health.state_store, controller.state)

    def test_productive_facade_delegates_to_owned_components(self) -> None:
        controller = RuntimeSupportController(SimpleNamespace(), lambda *_args: 0, lambda _reason: 0)
        controller.setup = MagicMock(spec=RuntimeSetup)
        controller.state = MagicMock(spec=RuntimeStateStore)
        controller.executor = MagicMock(spec=RuntimeExecutor)
        controller.control_commands = MagicMock(spec=ControlCommandQueue)
        controller.mainloop_watchdog = MagicMock(spec=MainloopWatchdog)
        controller.health = MagicMock(spec=RuntimeHealthMonitor)
        controller.audit = MagicMock(spec=RuntimeAuditLogger)
        command = ControlCommand(name="set_mode", target="mode", value=2)

        controller.initialize_runtime_support()
        controller.init_worker_state()
        controller.ensure_worker_state()
        controller.set_worker_snapshot({"captured_at": 1.0})
        controller.update_worker_snapshot(grid_power=120.0)
        controller.get_worker_snapshot()
        controller.ensure_observability_state()
        controller.start_update_worker()
        controller.schedule_update_cycle()
        controller.start_control_command_worker()
        controller.enqueue_control_command(command)
        controller.mainloop_heartbeat_tick()
        controller.start_mainloop_watchdog()
        controller.is_update_stale(14.0)
        controller.watchdog_recover(15.0)
        controller.warning_throttled("gateway", 5.0, "message %s", "value")
        controller.mark_failure("gateway")
        controller.mark_recovery("gateway", "recovered %s", "now")
        controller.source_retry_ready("gateway", 16.0)
        controller.source_retry_remaining("gateway", 17.0)
        controller.delay_source_retry("gateway", 18.0, 3.0)
        controller.write_auto_audit_event("waiting", True)

        controller.setup.initialize_runtime_support.assert_called_once_with()
        controller.state.initialize_worker_state.assert_called_once_with()
        controller.state.ensure_worker_state.assert_called_once_with()
        controller.state.set_worker_snapshot.assert_called_once_with({"captured_at": 1.0})
        controller.state.update_worker_snapshot.assert_called_once_with(grid_power=120.0)
        controller.state.get_worker_snapshot.assert_called_once_with()
        controller.state.ensure_observability_state.assert_called_once_with()
        controller.executor.start_update_worker.assert_called_once_with()
        controller.executor.schedule_update_cycle.assert_called_once_with()
        controller.executor.start_control_command_worker.assert_called_once_with()
        controller.control_commands.enqueue.assert_called_once_with(command)
        controller.mainloop_watchdog.heartbeat_tick.assert_called_once_with()
        controller.mainloop_watchdog.start.assert_called_once_with()
        controller.health.is_update_stale.assert_called_once_with(14.0)
        controller.health.watchdog_recover.assert_called_once_with(15.0)
        controller.health.warning_throttled.assert_called_once_with("gateway", 5.0, "message %s", "value")
        controller.health.mark_failure.assert_called_once_with("gateway")
        controller.health.mark_recovery.assert_called_once_with("gateway", "recovered %s", "now")
        controller.health.source_retry_ready.assert_called_once_with("gateway", 16.0)
        controller.health.source_retry_remaining.assert_called_once_with("gateway", 17.0)
        controller.health.delay_source_retry.assert_called_once_with("gateway", 18.0, 3.0)
        controller.audit.write_auto_audit_event.assert_called_once_with("waiting", True)


if __name__ == "__main__":
    unittest.main()

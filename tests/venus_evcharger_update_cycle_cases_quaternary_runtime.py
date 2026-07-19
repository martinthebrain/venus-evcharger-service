# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_update_cycle_controller_support import *

from venus_evcharger.core.dbus_backpressure import CoreDbusBackpressurePolicy


class _UpdateCycleQuaternaryRuntimeCases:
    def test_update_cycle_helpers_cover_offline_inputs_and_relay_resolution_edges(self) -> None:
        service = SimpleNamespace(
            _last_confirmed_pm_status="bad",
            _last_confirmed_pm_status_at=100.0,
            relay_sync_timeout_seconds=3.0,
            virtual_mode=1,
            _auto_cached_inputs_used=True,
            _auto_decide_relay=MagicMock(return_value=True),
            _bump_update_index=MagicMock(),
            time_now=MagicMock(return_value=123.0),
            _last_successful_update_at=None,
            _last_recovery_attempt_at=1.0,
            last_update=0.0,
            _warning_throttled=MagicMock(),
            auto_shelly_soft_fail_seconds=10.0,
            _last_charger_transport_source="source",
            _last_charger_transport_detail="detail",
            _last_charger_state_status="charging",
            _last_charger_state_fault=None,
            _last_switch_feedback_closed=True,
            _contactor_fault_counts={},
            _contactor_lockout_source="",
            _publish_companion_dbus_bridge=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertIsNone(controller.components.offline._fresh_offline_pm_status(service, 101.0))
        self.assertEqual(controller.components.offline._offline_power_state(), (0.0, 0.0, 0))
        self.assertEqual(controller.components.inputs.resolve_auto_inputs({}, 100.0, False), (None, None, None))
        self.assertFalse(service._auto_cached_inputs_used)

        controller.components.runtime_cycle.complete_update_cycle(
            False, 200.0, False, 0.0, 0.0, 0, None, None, None
        )
        service._bump_update_index.assert_not_called()
        self.assertEqual(service._last_successful_update_at, 123.0)

        controller.components.runtime_cycle.complete_update_cycle(
            True, 201.0, False, 0.0, 0.0, 0, None, None, None
        )
        service._bump_update_index.assert_called_once_with(201.0)
        self.assertEqual(service._publish_companion_dbus_bridge.call_count, 2)
        service._publish_companion_dbus_bridge.assert_called_with(123.0)

        with patch.object(
            controller.components.relay.foundation.phase_switch,
            "orchestrate_pending_phase_switch",
            return_value=(True, 2300.0, 10.0, True, None),
        ), patch.object(
            controller.components.runtime_cycle,
            "_blocking_switch_feedback_health",
            return_value="switch-feedback-mismatch",
        ), patch.object(
            controller.components.runtime_cycle,
            "_blocking_charger_health",
            return_value=None,
        ), patch.object(
            controller.components.relay.foundation.auto_phase,
            "maybe_apply_auto_phase_selection",
            return_value=True,
        ), patch.object(
            controller.components.relay.foundation.targets,
            "apply_current_target",
        ) as apply_target:
            result = controller.components.runtime_cycle._resolved_relay_decision({}, True, 2300.0, 230.0, 10.0, True, 100.0, True, 5000.0, 50.0, -1000.0)

        self.assertEqual(result, (True, 2300.0, 10.0, True, True, "switch-feedback-mismatch"))
        apply_target.assert_called_once_with(service, True, 100.0, True)

    def test_software_update_run_is_blocked_by_no_update_marker(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            (repo_root / "noUpdate").write_text("", encoding="utf-8")
            service = self._software_update_service(
                temp_dir,
                _software_update_run_requested_at=50.0,
                _software_update_available=True,
                _software_update_last_check_at=100.0,
            )
            controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

            started = controller.components.software_update._start_software_update_run(service, 120.0, "manual")

            self.assertFalse(started)
            self.assertEqual(service._software_update_state, "available-blocked")
            self.assertEqual(service._software_update_detail, "noUpdate marker present")
            self.assertIsNone(service._software_update_run_requested_at)
            self.assertIsNone(service._software_update_process)

    def test_software_update_run_requires_restart_script(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            (repo_root / "install.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            service = self._software_update_service(temp_dir, _software_update_run_requested_at=50.0)
            controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

            started = controller.components.software_update._start_software_update_run(service, 120.0, "manual")

            self.assertFalse(started)
            self.assertEqual(service._software_update_state, "update-unavailable")
            self.assertEqual(service._software_update_detail, "restart script missing")
            self.assertIsNone(service._software_update_run_requested_at)
            self.assertIsNone(service._software_update_process)

    def test_software_update_housekeeping_starts_boot_delayed_run_when_due(self):
        service = self._software_update_service(
            "",
            _software_update_next_check_at=10_000.0,
            _software_update_boot_auto_due_at=100.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with patch.object(SoftwareUpdateController, "_start_software_update_run", return_value=True) as start_run:
            controller.components.software_update._software_update_housekeeping(service, 120.0)

        self.assertIsNone(service._software_update_boot_auto_due_at)
        start_run.assert_called_once_with(service, 120.0, "boot-auto")

    def test_software_update_housekeeping_starts_manual_run_when_requested(self):
        service = self._software_update_service(
            "",
            _software_update_next_check_at=10_000.0,
            _software_update_run_requested_at=110.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with patch.object(SoftwareUpdateController, "_start_software_update_run", return_value=True) as start_run:
            controller.components.software_update._software_update_housekeeping(service, 120.0)

        start_run.assert_called_once_with(service, 120.0, "manual")

    def test_software_update_periodic_check_never_starts_installer(self):
        service = self._software_update_service(
            "",
            _software_update_next_check_at=100.0,
            _software_update_boot_auto_due_at=None,
            _software_update_run_requested_at=None,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with patch.object(SoftwareUpdateController, "_run_software_update_check") as check, patch.object(
            SoftwareUpdateController,
            "_start_software_update_run",
        ) as start_run:
            controller.components.software_update._software_update_housekeeping(service, 120.0)

        check.assert_called_once_with(service, 120.0)
        start_run.assert_not_called()
        self.assertIsNone(service._software_update_process)

    def test_software_update_housekeeping_defers_automatic_work_under_backpressure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            health_path = Path(temp_dir) / "dbus-health.json"
            health_path.write_text(
                '{"captured_at": 120.0, "dbus_health": {"backpressure": {"state": "protective"}}}',
                encoding="utf-8",
            )
            service = self._software_update_service(
                "",
                _software_update_next_check_at=100.0,
                _software_update_boot_auto_due_at=100.0,
                _dbus_backpressure_policy=CoreDbusBackpressurePolicy(
                    str(health_path),
                    now=lambda: 120.0,
                    cache_seconds=0.0,
                ),
            )
            controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

            with patch.object(SoftwareUpdateController, "_run_software_update_check") as check, patch.object(
                SoftwareUpdateController,
                "_start_software_update_run",
            ) as start_run:
                controller.components.software_update._software_update_housekeeping(service, 120.0)

            check.assert_not_called()
            start_run.assert_not_called()
            self.assertEqual(service._software_update_next_check_at, 840.0)
            self.assertEqual(service._software_update_boot_auto_due_at, 840.0)

    def test_software_update_housekeeping_discards_manual_request_while_run_is_already_active(self):
        process = MagicMock()
        process.poll.return_value = None
        service = self._software_update_service(
            "",
            _software_update_process=process,
            _software_update_run_requested_at=110.0,
            _software_update_boot_auto_due_at=100.0,
            _software_update_next_check_at=10_000.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with patch.object(SoftwareUpdateController, "_start_software_update_run", return_value=True) as start_run:
            controller.components.software_update._software_update_housekeeping(service, 120.0)

        self.assertIsNone(service._software_update_run_requested_at)
        self.assertIsNone(service._software_update_boot_auto_due_at)
        start_run.assert_not_called()

    def test_update_flushes_debounced_runtime_overrides_from_main_loop(self):
        service = self._software_update_service("")
        service.time_now = MagicMock(return_value=42.0)
        service._flush_runtime_overrides = MagicMock()
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with patch.object(controller.components.runtime_cycle, "run", return_value=True), patch.object(
            controller.components.software_update,
            "housekeeping",
        ) as housekeeping_mock:
            result = controller.update()

        self.assertTrue(result)
        service._flush_runtime_overrides.assert_called_once_with(42.0)
        housekeeping_mock.assert_called_once_with(service, 42.0)

    def test_current_learning_voltage_signature_uses_last_voltage_fallback_and_none_without_cache(self):
        service = SimpleNamespace(_last_voltage=228.5)
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertEqual(controller.components.learning._current_learning_voltage_signature(0.0), 228.5)

        service._last_voltage = None
        self.assertIsNone(controller.components.learning._current_learning_voltage_signature(0.0))

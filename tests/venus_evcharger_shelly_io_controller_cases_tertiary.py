# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_shelly_io_controller_cases_tertiary_part2 import _TestShellyIoControllerTertiaryPart2
from tests.venus_evcharger_shelly_io_controller_support import *


class TestShellyIoControllerTertiary(_TestShellyIoControllerTertiaryPart2, ShellyIoControllerTestBase):
    def test_fetch_pm_status_keeps_pm_flow_when_native_charger_readback_fails(self):
        service = SimpleNamespace(
            host="192.168.178.76",
            pm_component="Switch",
            pm_id=0,
            rpc_call=MagicMock(return_value={"output": False}),
            _charger_backend=SimpleNamespace(
                read_charger_state=MagicMock(
                    side_effect=ModbusSlaveOfflineError("Modbus slave 1 on /dev/ttyS7 did not respond")
                )
            ),
            supported_phase_selections=("P1",),
            requested_phase_selection="P1",
            active_phase_selection="P1",
            auto_shelly_soft_fail_seconds=10.0,
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
            _mark_recovery=MagicMock(),
            _time_now=MagicMock(return_value=100.0),
            _source_retry_after={},
        )

        controller = ShellyIoController(service)
        pm_status = controller.fetch_pm_status()

        self.assertFalse(pm_status["output"])
        service._mark_failure.assert_called_once_with("charger")
        service._warning_throttled.assert_called_once()
        service._mark_recovery.assert_not_called()
        self.assertEqual(service._last_charger_transport_reason, "offline")
        self.assertEqual(service._last_charger_transport_source, "read")
        self.assertEqual(service._last_charger_transport_detail, "Modbus slave 1 on /dev/ttyS7 did not respond")
        self.assertEqual(service._last_charger_transport_at, 100.0)
        self.assertEqual(service._charger_retry_reason, "offline")
        self.assertEqual(service._charger_retry_source, "read")
        self.assertEqual(service._charger_retry_until, 120.0)
        self.assertEqual(service._source_retry_after["charger"], 120.0)

    def test_fetch_pm_status_keeps_pm_flow_when_smartevse_readback_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=smartevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.60\nPort=502\nUnitId=1\n",
            )
            service = SimpleNamespace(
                host="192.168.178.76",
                pm_component="Switch",
                pm_id=0,
                rpc_call=MagicMock(return_value={"output": False}),
                supported_phase_selections=("P1",),
                requested_phase_selection="P1",
                active_phase_selection="P1",
                auto_shelly_soft_fail_seconds=10.0,
                _mark_failure=MagicMock(),
                _warning_throttled=MagicMock(),
                _mark_recovery=MagicMock(),
                _time_now=MagicMock(return_value=100.0),
                _source_retry_after={},
                shelly_request_timeout_seconds=2.0,
            )
            smartevse_backend = SmartEvseChargerBackend(service, config_path=config_path)
            service._charger_backend = smartevse_backend

            with patch(
                "venus_evcharger.backend.smartevse_charger.create_modbus_transport",
                side_effect=ModbusSlaveOfflineError("Modbus slave 1 on /dev/ttyS7 did not respond"),
            ):
                controller = ShellyIoController(service)
                pm_status = controller.fetch_pm_status()

        self.assertFalse(pm_status["output"])
        service._mark_failure.assert_called_once_with("charger")
        service._warning_throttled.assert_called_once()
        service._mark_recovery.assert_not_called()
        self.assertEqual(service._last_charger_transport_reason, "offline")
        self.assertEqual(service._last_charger_transport_source, "read")
        self.assertEqual(service._last_charger_transport_detail, "Modbus slave 1 on /dev/ttyS7 did not respond")
        self.assertEqual(service._charger_retry_reason, "offline")
        self.assertEqual(service._charger_retry_source, "read")
        self.assertEqual(service._charger_retry_until, 120.0)
        self.assertEqual(service._source_retry_after["charger"], 120.0)

    def test_fetch_pm_status_skips_native_charger_readback_while_retry_backoff_is_active(self):
        charger_backend = SimpleNamespace(read_charger_state=MagicMock())
        service = SimpleNamespace(
            host="192.168.178.76",
            pm_component="Switch",
            pm_id=0,
            rpc_call=MagicMock(return_value={"output": False}),
            _charger_backend=charger_backend,
            supported_phase_selections=("P1",),
            requested_phase_selection="P1",
            active_phase_selection="P1",
            auto_shelly_soft_fail_seconds=10.0,
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
            _mark_recovery=MagicMock(),
            _time_now=MagicMock(return_value=100.0),
            _charger_retry_reason="offline",
            _charger_retry_source="read",
            _charger_retry_until=110.0,
        )

        controller = ShellyIoController(service)
        pm_status = controller.fetch_pm_status()

        self.assertFalse(pm_status["output"])
        charger_backend.read_charger_state.assert_not_called()
        service._mark_failure.assert_not_called()

    def test_phase_selection_requires_pause_uses_switch_capabilities(self):
        switch_backend = SimpleNamespace(
            capabilities=MagicMock(
                return_value=SimpleNamespace(
                    supported_phase_selections=("P1", "P1_P2"),
                    requires_charge_pause_for_phase_change=True,
                )
            )
        )
        service = SimpleNamespace(
            _switch_backend=switch_backend,
            supported_phase_selections=("P1",),
            requested_phase_selection="P1",
            active_phase_selection="P1",
        )

        controller = ShellyIoController(service)

        self.assertTrue(controller.phase_selection_requires_pause())

    def test_build_and_publish_local_pm_status_fill_defaults(self):
        service = SimpleNamespace(
            _last_pm_status={"aenergy": "bad"},
            _last_voltage=231.0,
            _time_now=MagicMock(return_value=100.0),
            _update_worker_snapshot=MagicMock(),
            _last_pm_status_confirmed=True,
        )
        controller = ShellyIoController(service)
        service._build_local_pm_status = controller.build_local_pm_status

        pm_status = controller.build_local_pm_status(False)
        published = controller.publish_local_pm_status(True)

        self.assertEqual(pm_status["output"], False)
        self.assertEqual(pm_status["apower"], 0.0)
        self.assertEqual(pm_status["current"], 0.0)
        self.assertEqual(pm_status["voltage"], 231.0)
        self.assertEqual(pm_status["aenergy"]["total"], 0.0)
        self.assertEqual(published["output"], True)
        self.assertEqual(published["apower"], 0.0)
        self.assertEqual(published["current"], 0.0)
        self.assertEqual(service._last_pm_status_at, 100.0)
        self.assertFalse(service._last_pm_status_confirmed)
        service._update_worker_snapshot.assert_called_once_with(
            captured_at=100.0,
            pm_captured_at=100.0,
            pm_status=published,
            pm_confirmed=False,
        )

    def test_queue_peek_and_clear_pending_relay_command_use_worker_lock(self):
        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            _time_now=MagicMock(return_value=100.0),
            _relay_command_lock=MagicMock(),
            _pending_relay_state=None,
            _pending_relay_requested_at=None,
            relay_sync_timeout_seconds=4.0,
            _relay_sync_expected_state=None,
            _relay_sync_requested_at=None,
            _relay_sync_deadline_at=None,
            _relay_sync_failure_reported=True,
        )
        service._relay_command_lock.__enter__ = MagicMock(return_value=None)
        service._relay_command_lock.__exit__ = MagicMock(return_value=None)
        controller = ShellyIoController(service)

        controller.queue_relay_command(True)
        self.assertEqual(controller.peek_pending_relay_command(), (True, 100.0))
        controller.clear_pending_relay_command(True)

        service._ensure_worker_state.assert_called()
        self.assertEqual(service._pending_relay_state, None)
        self.assertEqual(service._pending_relay_requested_at, None)
        self.assertEqual(service._relay_sync_expected_state, True)
        self.assertEqual(service._relay_sync_requested_at, 100.0)
        self.assertEqual(service._relay_sync_deadline_at, 104.0)
        self.assertFalse(service._relay_sync_failure_reported)

    def test_worker_apply_pending_relay_command_marks_success_and_clears_queue(self):
        service = SimpleNamespace(
            pm_id=0,
            _worker_session=MagicMock(),
            auto_shelly_soft_fail_seconds=10,
            _peek_pending_relay_command=MagicMock(return_value=(True, 90.0)),
            _rpc_call_with_session=MagicMock(return_value={"was_on": False}),
            _clear_pending_relay_command=MagicMock(),
            _mark_relay_changed=MagicMock(),
            _mark_recovery=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _time_now=MagicMock(return_value=100.0),
        )

        controller = ShellyIoController(service)
        controller.worker_apply_pending_relay_command()

        service._rpc_call_with_session.assert_called_once_with(
            service._worker_session,
            "Switch.Set",
            id=0,
            on=True,
        )
        service._clear_pending_relay_command.assert_called_once_with(True)
        service._mark_relay_changed.assert_called_once_with(True, 100.0)
        service._mark_recovery.assert_called_once_with("shelly", "Shelly relay writes recovered")
        service._publish_local_pm_status.assert_called_once_with(True, 100.0)

    def test_worker_apply_pending_relay_command_marks_failure_on_write_error(self):
        service = SimpleNamespace(
            pm_id=0,
            _worker_session=MagicMock(),
            auto_shelly_soft_fail_seconds=10,
            _peek_pending_relay_command=MagicMock(return_value=(True, 90.0)),
            _rpc_call_with_session=MagicMock(side_effect=RuntimeError("boom")),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )

        controller = ShellyIoController(service)
        controller.worker_apply_pending_relay_command()

        service._mark_failure.assert_called_once_with("shelly")
        service._warning_throttled.assert_called_once()

    def test_worker_apply_pending_relay_command_uses_split_switch_backend(self):
        switch_backend = SimpleNamespace(set_enabled=MagicMock())
        service = SimpleNamespace(
            _backend_bundle=_runtime_bundle("split"),
            _switch_backend=switch_backend,
            _peek_pending_relay_command=MagicMock(return_value=(True, 90.0)),
            _clear_pending_relay_command=MagicMock(),
            _mark_relay_changed=MagicMock(),
            _mark_recovery=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _time_now=MagicMock(return_value=100.0),
            _rpc_call_with_session=MagicMock(),
            _worker_session=MagicMock(),
            auto_shelly_soft_fail_seconds=10,
        )

        controller = ShellyIoController(service)
        controller.worker_apply_pending_relay_command()

        switch_backend.set_enabled.assert_called_once_with(True)
        service._rpc_call_with_session.assert_not_called()
        service._clear_pending_relay_command.assert_called_once_with(True)
        service._mark_relay_changed.assert_called_once_with(True, 100.0)
        service._mark_recovery.assert_called_once_with("shelly", "Shelly relay writes recovered")
        service._publish_local_pm_status.assert_called_once_with(True, 100.0)

    def test_worker_apply_pending_relay_command_uses_split_charger_backend_without_switch(self):
        charger_backend = SimpleNamespace(set_enabled=MagicMock())
        service = SimpleNamespace(
            _backend_bundle=_runtime_bundle("split"),
            _switch_backend=None,
            _charger_backend=charger_backend,
            _peek_pending_relay_command=MagicMock(return_value=(True, 90.0)),
            _clear_pending_relay_command=MagicMock(),
            _mark_relay_changed=MagicMock(),
            _mark_recovery=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _time_now=MagicMock(return_value=100.0),
            _rpc_call_with_session=MagicMock(),
            _worker_session=MagicMock(),
            auto_shelly_soft_fail_seconds=10,
        )

        controller = ShellyIoController(service)
        controller.worker_apply_pending_relay_command()

        charger_backend.set_enabled.assert_called_once_with(True)
        service._rpc_call_with_session.assert_not_called()
        service._clear_pending_relay_command.assert_called_once_with(True)
        service._mark_relay_changed.assert_called_once_with(True, 100.0)
        service._mark_recovery.assert_called_once_with("charger", "%s writes recovered", "charger backend")
        service._publish_local_pm_status.assert_called_once_with(True, 100.0)

    def test_io_worker_once_updates_snapshot_and_handles_read_failure(self):
        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            _time_now=MagicMock(side_effect=[100.0, 101.0]),
            _update_worker_snapshot=MagicMock(),
            _worker_apply_pending_relay_command=MagicMock(),
            _worker_fetch_pm_status=MagicMock(return_value={"output": True, "apower": 1200.0}),
            _mark_recovery=MagicMock(),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
            _mode_uses_auto_logic=MagicMock(return_value=True),
            virtual_mode=1,
            auto_shelly_soft_fail_seconds=10,
        )

        controller = ShellyIoController(service)
        controller.io_worker_once()

        service._worker_apply_pending_relay_command.assert_called_once_with()
        service._mark_recovery.assert_called_once_with("shelly", "Shelly status reads recovered")
        self.assertEqual(service._update_worker_snapshot.call_count, 2)
        self.assertEqual(service._update_worker_snapshot.call_args_list[1].kwargs["pm_confirmed"], True)

        failing_service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            _time_now=MagicMock(return_value=100.0),
            _update_worker_snapshot=MagicMock(),
            _worker_apply_pending_relay_command=MagicMock(),
            _worker_fetch_pm_status=MagicMock(side_effect=RuntimeError("read failed")),
            _mark_recovery=MagicMock(),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
            _mode_uses_auto_logic=MagicMock(return_value=True),
            virtual_mode=1,
            auto_shelly_soft_fail_seconds=10,
        )
        controller = ShellyIoController(failing_service)
        controller.io_worker_once()
        failing_service._mark_failure.assert_called_once_with("shelly")
        failing_service._warning_throttled.assert_called_once()
        self.assertEqual(failing_service._update_worker_snapshot.call_count, 2)
        self.assertEqual(
            failing_service._update_worker_snapshot.call_args_list[1].kwargs,
            {
                "captured_at": 100.0,
                "auto_mode_active": True,
                "pm_status": None,
                "pm_captured_at": None,
                "pm_confirmed": False,
            },
        )

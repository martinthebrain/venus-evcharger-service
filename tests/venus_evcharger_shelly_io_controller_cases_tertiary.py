# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_shelly_io_controller_cases_tertiary_part2 import _TestShellyIoControllerTertiaryPart2
from tests.venus_evcharger_shelly_io_controller_support import *


class TestShellyIoControllerTertiary(_TestShellyIoControllerTertiaryPart2, ShellyIoControllerTestBase):
    def test_fetch_pm_status_keeps_pm_flow_when_native_charger_readback_fails(self):
        service = SimpleNamespace(
            host="192.168.178.76",
            pm_component="Switch",
            pm_id=0,
            _charger_backend=SimpleNamespace(
                read_charger_state=MagicMock(
                    side_effect=ModbusSlaveOfflineError("Modbus slave 1 on /dev/ttyS7 did not respond")
                )
            ),
            supported_phase_selections=("P1",),
            requested_phase_selection="P1",
            active_phase_selection="P1",
            auto_shelly_soft_fail_seconds=10.0,
            time_now=MagicMock(return_value=100.0),
            _source_retry_after={},
        )

        controller = ShellyIoController(service)
        controller.requests.fetch_pm_status_rpc = MagicMock(return_value={"output": False})
        pm_status = controller.fetch_pm_status()

        self.assertFalse(pm_status["output"])
        service.runtime.mark_failure.assert_called_once_with("charger")
        service.runtime.warning_throttled.assert_called_once()
        service.runtime.mark_recovery.assert_not_called()
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
                supported_phase_selections=("P1",),
                requested_phase_selection="P1",
                active_phase_selection="P1",
                auto_shelly_soft_fail_seconds=10.0,
                time_now=MagicMock(return_value=100.0),
                _source_retry_after={},
                shelly_request_timeout_seconds=2.0,
            )
            smartevse_backend = SmartEvseChargerBackend(service, config_path=config_path)
            service._charger_backend = smartevse_backend

            with patch(
                "venus_evcharger.backend.native_modbus_backend.create_modbus_transport",
                side_effect=ModbusSlaveOfflineError("Modbus slave 1 on /dev/ttyS7 did not respond"),
            ):
                controller = ShellyIoController(service)
                controller.requests.fetch_pm_status_rpc = MagicMock(return_value={"output": False})
                pm_status = controller.fetch_pm_status()

        self.assertFalse(pm_status["output"])
        service.runtime.mark_failure.assert_called_once_with("charger")
        service.runtime.warning_throttled.assert_called_once()
        service.runtime.mark_recovery.assert_not_called()
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
            _charger_backend=charger_backend,
            supported_phase_selections=("P1",),
            requested_phase_selection="P1",
            active_phase_selection="P1",
            auto_shelly_soft_fail_seconds=10.0,
            time_now=MagicMock(return_value=100.0),
            _charger_retry_reason="offline",
            _charger_retry_source="read",
            _charger_retry_until=110.0,
        )

        controller = ShellyIoController(service)
        controller.requests.fetch_pm_status_rpc = MagicMock(return_value={"output": False})
        pm_status = controller.fetch_pm_status()

        self.assertFalse(pm_status["output"])
        charger_backend.read_charger_state.assert_not_called()
        service.runtime.mark_failure.assert_not_called()

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
            time_now=MagicMock(return_value=100.0),
            _last_pm_status_confirmed=True,
        )
        controller = ShellyIoController(service)
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
        self.assertFalse(published["_pm_confirmed"])
        self.assertEqual(service._last_pm_status, dict(published))
        self.assertEqual(service._last_pm_status_at, 100.0)
        self.assertIs(service._last_pm_status_confirmed, False)
        service.runtime.update_worker_snapshot.assert_called_once_with(
            captured_at=100.0,
            pm_captured_at=100.0,
            pm_status=published,
            pm_confirmed=False,
        )

        explicit_time_service = SimpleNamespace()
        explicit_time_controller = ShellyIoController(explicit_time_service)
        explicit_time_controller.worker.build_local_pm_status = MagicMock(return_value={"output": False})
        explicit_published = explicit_time_controller.publish_local_pm_status(False, now=123.5)
        self.assertEqual(explicit_time_service._last_pm_status_at, 123.5)
        self.assertEqual(explicit_time_service._last_pm_status, {"output": False, "_pm_confirmed": False})
        self.assertIs(explicit_time_service._last_pm_status_confirmed, False)
        explicit_time_service.runtime.update_worker_snapshot.assert_called_once_with(
            captured_at=123.5,
            pm_captured_at=123.5,
            pm_status=explicit_published,
            pm_confirmed=False,
        )

        status_voltage_service = SimpleNamespace(
            _last_pm_status={"voltage": 229.0, "aenergy": {"total": 1.25}},
            _last_voltage=231.0,
        )
        self.assertEqual(ShellyIoController(status_voltage_service).build_local_pm_status(True)["voltage"], 229.0)

        zero_voltage_service = SimpleNamespace(
            _last_pm_status={"voltage": 0.0},
            _last_voltage=232.0,
        )
        self.assertEqual(ShellyIoController(zero_voltage_service).build_local_pm_status(True)["voltage"], 232.0)

        missing_voltage_service = SimpleNamespace()
        self.assertEqual(ShellyIoController(missing_voltage_service).build_local_pm_status(True)["voltage"], 230.0)

        bool_voltage_service = SimpleNamespace(_last_voltage=True)
        self.assertEqual(ShellyIoController(bool_voltage_service).build_local_pm_status(True)["voltage"], 230.0)

    def test_queue_peek_and_clear_pending_relay_command_use_worker_lock(self):
        service = SimpleNamespace(
            time_now=MagicMock(return_value=100.0),
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

        service.runtime.ensure_worker_state.assert_called()
        self.assertEqual(service._pending_relay_state, None)
        self.assertEqual(service._pending_relay_requested_at, None)
        self.assertEqual(service._relay_sync_expected_state, True)
        self.assertEqual(service._relay_sync_requested_at, 100.0)
        self.assertEqual(service._relay_sync_deadline_at, 104.0)
        self.assertFalse(service._relay_sync_failure_reported)

        explicit_service = SimpleNamespace(
            time_now=MagicMock(return_value=999.0),
            _relay_command_lock=MagicMock(),
            _pending_relay_state=None,
            _pending_relay_requested_at=None,
            _relay_sync_expected_state=None,
            _relay_sync_requested_at=None,
            _relay_sync_deadline_at=None,
            _relay_sync_failure_reported=True,
        )
        explicit_service._relay_command_lock.__enter__ = MagicMock(return_value=None)
        explicit_service._relay_command_lock.__exit__ = MagicMock(return_value=None)
        explicit_controller = ShellyIoController(explicit_service)
        explicit_controller.capabilities.warn_if_direct_switching_under_load = MagicMock()

        explicit_controller.queue_relay_command(True, now=55.5)

        explicit_service.time_now.assert_not_called()
        explicit_controller.capabilities.warn_if_direct_switching_under_load.assert_called_once_with(True)
        self.assertTrue(explicit_service._pending_relay_state)
        self.assertEqual(explicit_service._pending_relay_requested_at, 55.5)
        self.assertTrue(explicit_service._relay_sync_expected_state)
        self.assertEqual(explicit_service._relay_sync_requested_at, 55.5)
        self.assertEqual(explicit_service._relay_sync_deadline_at, 57.5)
        self.assertIs(explicit_service._relay_sync_failure_reported, False)

    def test_worker_apply_pending_relay_command_marks_success_and_clears_queue(self):
        service = SimpleNamespace(
            pm_id=0,
            _worker_session=MagicMock(),
            auto_shelly_soft_fail_seconds=10,
            time_now=MagicMock(return_value=100.0),
        )

        controller = ShellyIoController(service)
        controller.queue_relay_command(True, now=90.0)
        controller.requests.rpc_call_with_session = MagicMock(return_value={"was_on": False})
        controller.worker.publish_local_pm_status = MagicMock()
        controller.worker_apply_pending_relay_command()

        controller.requests.rpc_call_with_session.assert_called_once_with(
            service._worker_session,
            "Switch.Set",
            id=0,
            on=True,
        )
        self.assertEqual(controller.peek_pending_relay_command(), (None, None))
        service.auto.mark_relay_changed.assert_called_once_with(True, 100.0)
        service.runtime.mark_recovery.assert_called_once_with("shelly", "Shelly relay writes recovered")
        controller.worker.publish_local_pm_status.assert_called_once_with(True, 100.0)

    def test_worker_apply_pending_relay_command_marks_failure_on_write_error(self):
        service = SimpleNamespace(
            pm_id=0,
            _worker_session=MagicMock(),
            auto_shelly_soft_fail_seconds=10,
        )

        controller = ShellyIoController(service)
        controller.queue_relay_command(True, now=90.0)
        controller.requests.rpc_call_with_session = MagicMock(side_effect=RuntimeError("boom"))
        controller.worker_apply_pending_relay_command()

        service.runtime.mark_failure.assert_called_once_with("shelly")
        service.runtime.warning_throttled.assert_called_once()

    def test_worker_apply_pending_relay_command_uses_split_switch_backend(self):
        switch_backend = SimpleNamespace(set_enabled=MagicMock())
        service = SimpleNamespace(
            _backend_bundle=_runtime_bundle("split"),
            _switch_backend=switch_backend,
            time_now=MagicMock(return_value=100.0),
            _worker_session=MagicMock(),
            auto_shelly_soft_fail_seconds=10,
        )

        controller = ShellyIoController(service)
        controller.queue_relay_command(True, now=90.0)
        controller.requests.rpc_call_with_session = MagicMock()
        controller.worker.publish_local_pm_status = MagicMock()
        controller.worker_apply_pending_relay_command()

        switch_backend.set_enabled.assert_called_once_with(True)
        controller.requests.rpc_call_with_session.assert_not_called()
        self.assertEqual(controller.peek_pending_relay_command(), (None, None))
        service.auto.mark_relay_changed.assert_called_once_with(True, 100.0)
        service.runtime.mark_recovery.assert_called_once_with("shelly", "Shelly relay writes recovered")
        controller.worker.publish_local_pm_status.assert_called_once_with(True, 100.0)

    def test_worker_apply_pending_relay_command_uses_split_charger_backend_without_switch(self):
        charger_backend = SimpleNamespace(set_enabled=MagicMock())
        service = SimpleNamespace(
            _backend_bundle=_runtime_bundle("split"),
            _switch_backend=None,
            _charger_backend=charger_backend,
            time_now=MagicMock(return_value=100.0),
            _worker_session=MagicMock(),
            auto_shelly_soft_fail_seconds=10,
        )

        controller = ShellyIoController(service)
        controller.queue_relay_command(True, now=90.0)
        controller.requests.rpc_call_with_session = MagicMock()
        controller.worker.publish_local_pm_status = MagicMock()
        controller.worker_apply_pending_relay_command()

        charger_backend.set_enabled.assert_called_once_with(True)
        controller.requests.rpc_call_with_session.assert_not_called()
        self.assertEqual(controller.peek_pending_relay_command(), (None, None))
        service.auto.mark_relay_changed.assert_called_once_with(True, 100.0)
        service.runtime.mark_recovery.assert_called_once_with("charger", "%s writes recovered", "charger backend")
        controller.worker.publish_local_pm_status.assert_called_once_with(True, 100.0)

    def test_io_worker_once_updates_snapshot_and_handles_read_failure(self):
        service = SimpleNamespace(
            time_now=MagicMock(side_effect=[100.0, 101.0]),
            virtual_mode=1,
            auto_shelly_soft_fail_seconds=10,
        )

        controller = ShellyIoController(service)
        service.auto.mode_uses_auto_logic.return_value = True
        controller.worker.worker_apply_pending_relay_command = MagicMock()
        controller.worker._fetch_pm_status = MagicMock(return_value={"output": True, "apower": 1200.0})
        controller.io_worker_once()

        controller.worker.worker_apply_pending_relay_command.assert_called_once_with()
        service.runtime.mark_recovery.assert_called_once_with("shelly", "Shelly status reads recovered")
        self.assertEqual(service.runtime.update_worker_snapshot.call_count, 2)
        self.assertEqual(service.runtime.update_worker_snapshot.call_args_list[1].kwargs["pm_confirmed"], True)

        failing_service = SimpleNamespace(
            time_now=MagicMock(return_value=100.0),
            virtual_mode=1,
            auto_shelly_soft_fail_seconds=10,
        )
        controller = ShellyIoController(failing_service)
        failing_service.auto.mode_uses_auto_logic.return_value = True
        controller.worker.worker_apply_pending_relay_command = MagicMock()
        controller.worker._fetch_pm_status = MagicMock(side_effect=RuntimeError("read failed"))
        controller.io_worker_once()
        failing_service.runtime.mark_failure.assert_called_once_with("shelly")
        failing_service.runtime.warning_throttled.assert_called_once()
        self.assertEqual(failing_service.runtime.update_worker_snapshot.call_count, 2)
        self.assertEqual(
            failing_service.runtime.update_worker_snapshot.call_args_list[1].kwargs,
            {
                "captured_at": 100.0,
                "auto_mode_active": True,
                "pm_status": None,
                "pm_captured_at": None,
                "pm_confirmed": False,
            },
        )

    def test_io_worker_once_delegates_precise_snapshot_paths(self):
        retry_service = SimpleNamespace(
            time_now=MagicMock(return_value=100.0),
            virtual_mode=2,
        )
        retry_controller = ShellyIoController(retry_service)
        retry_service.auto.mode_uses_auto_logic.return_value = True
        retry_controller.worker.worker_apply_pending_relay_command = MagicMock()
        retry_controller.worker._fetch_pm_status = MagicMock()
        retry_controller.transport.retry_active = MagicMock(return_value=True)
        retry_controller.worker._update_worker_tick_snapshot = MagicMock()
        retry_controller.worker._update_worker_unconfirmed_snapshot = MagicMock()
        retry_controller.worker._update_worker_confirmed_snapshot = MagicMock()

        retry_controller.io_worker_once()

        retry_controller.worker._update_worker_tick_snapshot.assert_called_once_with(retry_service, 100.0, True)
        retry_controller.worker.worker_apply_pending_relay_command.assert_called_once_with()
        retry_controller.worker._update_worker_unconfirmed_snapshot.assert_called_once_with(retry_service, 100.0, True)
        retry_controller.worker._update_worker_confirmed_snapshot.assert_not_called()
        retry_controller.worker._fetch_pm_status.assert_not_called()

        pm_status = {"output": True}
        success_service = SimpleNamespace(
            time_now=MagicMock(side_effect=[200.0, 201.0]),
        )
        success_controller = ShellyIoController(success_service)
        success_controller.worker.worker_apply_pending_relay_command = MagicMock()
        success_controller.worker._fetch_pm_status = MagicMock(return_value=pm_status)
        success_controller.transport.retry_active = MagicMock(return_value=False)
        success_controller.transport.remember_success = MagicMock()
        success_controller.worker._update_worker_tick_snapshot = MagicMock()
        success_controller.worker._update_worker_unconfirmed_snapshot = MagicMock()
        success_controller.worker._update_worker_confirmed_snapshot = MagicMock()

        success_controller.io_worker_once()

        success_controller.worker._update_worker_tick_snapshot.assert_called_once_with(success_service, 200.0, False)
        success_controller.transport.remember_success.assert_called_once_with(201.0, "Shelly status reads recovered")
        success_controller.worker._update_worker_confirmed_snapshot.assert_called_once_with(success_service, 201.0, False, pm_status)
        success_controller.worker._update_worker_unconfirmed_snapshot.assert_not_called()

        error = RuntimeError("read failed")
        failure_service = SimpleNamespace(
            time_now=MagicMock(return_value=300.0),
            auto_shelly_soft_fail_seconds=10.0,
            _shelly_consecutive_errors=5,
        )
        failure_controller = ShellyIoController(failure_service)
        failure_service.auto.mode_uses_auto_logic.return_value = True
        failure_controller.worker.worker_apply_pending_relay_command = MagicMock()
        failure_controller.worker._fetch_pm_status = MagicMock(side_effect=error)
        failure_controller.transport.retry_active = MagicMock(return_value=False)
        failure_controller.transport.classify_error = MagicMock(return_value="error")
        failure_controller.transport.remember_failure = MagicMock()
        failure_controller.transport.is_common_network_error = MagicMock(return_value=False)
        failure_controller.worker._pending_relay_shelly_retry_remaining = MagicMock(
            side_effect=lambda source, current: 3.0 if source == "shelly" and current == 300.0 else -1.0
        )
        failure_controller.worker._update_worker_tick_snapshot = MagicMock()
        failure_controller.worker._update_worker_unconfirmed_snapshot = MagicMock()
        failure_controller.worker._update_worker_confirmed_snapshot = MagicMock()

        failure_controller.io_worker_once()

        failure_controller.transport.remember_failure.assert_called_once_with("error", "read", error, 300.0)
        failure_service.runtime.mark_failure.assert_called_once_with("shelly")
        failure_service.runtime.warning_throttled.assert_called_once_with(
            "worker-shelly-read-failed-error",
            10.0,
            "Shelly status read failed (%s, consecutive=%s, retry=%ss): %s",
            "error",
            5,
            3.0,
            error,
            exc_info=error,
        )
        failure_controller.worker._update_worker_unconfirmed_snapshot.assert_called_once_with(failure_service, 300.0, True)
        failure_controller.worker._update_worker_confirmed_snapshot.assert_not_called()

    def test_worker_snapshot_helpers_have_precise_payload_contracts(self):
        service = SimpleNamespace(
            virtual_mode=2,
        )
        controller = ShellyIoController(service)
        service.auto.mode_uses_auto_logic.return_value = True

        self.assertTrue(controller.worker._worker_auto_mode_active(service))
        service.auto.mode_uses_auto_logic.assert_called_once_with(2)

        controller.worker._update_worker_tick_snapshot(service, 10.0, True)
        service.runtime.update_worker_snapshot.assert_called_once_with(
            captured_at=10.0,
            auto_mode_active=True,
        )

        service.runtime.update_worker_snapshot.reset_mock()
        controller.worker._update_worker_unconfirmed_snapshot(service, 11.0, False)
        service.runtime.update_worker_snapshot.assert_called_once_with(
            captured_at=11.0,
            auto_mode_active=False,
            pm_status=None,
            pm_captured_at=None,
            pm_confirmed=False,
        )

        service.runtime.update_worker_snapshot.reset_mock()
        pm_status = {"output": True, "apower": 1200.0}
        controller.worker._update_worker_confirmed_snapshot(service, 12.0, True, pm_status)
        service.runtime.update_worker_snapshot.assert_called_once_with(
            captured_at=12.0,
            pm_captured_at=12.0,
            auto_mode_active=True,
            pm_status=pm_status,
            pm_confirmed=True,
        )

        fallback_service = SimpleNamespace(
            auto=SimpleNamespace(mode_uses_auto_logic=MagicMock(return_value=False)),
        )
        self.assertFalse(controller.worker._worker_auto_mode_active(fallback_service))
        fallback_service.auto.mode_uses_auto_logic.assert_called_once_with(0)

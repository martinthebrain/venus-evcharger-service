# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_shelly_io_controller_cases_quaternary_support import *  # noqa: F401,F403

class _TestShellyIoControllerQuaternaryPart2:
    def test_charger_runtime_sync_delegates_snapshot_virtuals_and_phase_state(self):
        service = SimpleNamespace(
            _time_now=MagicMock(return_value=123.0),
            _mode_uses_auto_logic=MagicMock(return_value=True),
            virtual_mode=1,
        )
        controller = ShellyIoController(service)
        state = ChargerState(enabled=True, current_amps=6.0, phase_selection="P1_P2")
        controller._store_runtime_charger_snapshot = MagicMock()
        controller._sync_virtual_enabled_state = MagicMock()
        controller._sync_virtual_current_target = MagicMock()
        controller._sync_runtime_phase_selection_from_charger = MagicMock()

        controller._sync_charger_runtime_state(state)

        service._time_now.assert_called_once_with()
        service._mode_uses_auto_logic.assert_called_once_with(1)
        controller._store_runtime_charger_snapshot.assert_called_once_with(state, 123.0)
        controller._sync_virtual_enabled_state.assert_called_once_with(state, True)
        controller._sync_virtual_current_target.assert_called_once_with(state, 123.0)
        controller._sync_runtime_phase_selection_from_charger.assert_called_once_with(state)

    def test_charger_runtime_snapshot_and_phase_state_contracts_are_explicit(self):
        service = SimpleNamespace(
            requested_phase_selection="P1",
        )
        controller = ShellyIoController(service)
        state = ChargerState(
            enabled=True,
            current_amps=6.0,
            phase_selection="P1_P2",
            actual_current_amps=5.5,
            power_w=1265.0,
            energy_kwh=3.25,
            status_text=" Charging ",
            fault_text=" Fault ",
        )

        controller._store_runtime_charger_snapshot(state, 123.0)

        self.assertTrue(service._last_charger_state_enabled)
        self.assertEqual(service._last_charger_state_current_amps, 6.0)
        self.assertEqual(service._last_charger_state_phase_selection, "P1_P2")
        self.assertEqual(service._last_charger_state_actual_current_amps, 5.5)
        self.assertEqual(service._last_charger_state_power_w, 1265.0)
        self.assertEqual(service._last_charger_state_energy_kwh, 3.25)
        self.assertEqual(service._last_charger_state_status, " Charging ")
        self.assertEqual(service._last_charger_state_fault, " Fault ")
        self.assertEqual(service._last_charger_state_at, 123.0)

        controller._phase_selection_switch_backend = MagicMock(return_value=None)
        controller._charger_supported_phase_selections = MagicMock(return_value=("P1", "P1_P2"))
        controller._remember_phase_selection_state = MagicMock()
        controller._sync_runtime_phase_selection_from_charger(state)
        controller._remember_phase_selection_state.assert_called_once_with(
            supported=("P1", "P1_P2"),
            requested="P1",
            active="P1_P2",
        )

    def test_charger_runtime_numeric_and_status_edges_are_explicit(self):
        service = SimpleNamespace(_last_voltage=400.0, voltage_mode="line")
        controller = ShellyIoController(service)

        self.assertEqual(controller._cached_runtime_voltage(), 400.0)
        self.assertEqual(ShellyIoController(SimpleNamespace())._cached_runtime_voltage(), None)
        self.assertEqual(controller._phase_voltage_for_selection("P1", 400.0), 400.0)
        self.assertAlmostEqual(controller._phase_voltage_for_selection("P1_P2", 400.0), 400.0 / (3.0**0.5))
        self.assertTrue(ShellyIoController._charging_like_status(ChargerState(True, 6.0, "P1", status_text=" Charging ")))
        self.assertFalse(ShellyIoController._charging_like_status(ChargerState(True, 6.0, "P1", status_text="ready")))

        energy_service = SimpleNamespace(
            _charger_estimated_energy_kwh=1.0,
            _charger_estimated_energy_at=100.0,
            _charger_estimated_power_w=1000.0,
        )
        energy_controller = ShellyIoController(energy_service)
        self.assertEqual(energy_controller._integrated_estimated_charger_energy_kwh(500.0, 3700.0), 2.0)
        self.assertEqual(energy_service._charger_estimated_energy_kwh, 2.0)
        self.assertEqual(energy_service._charger_estimated_power_w, 500.0)

    def test_charger_runtime_read_context_and_error_contracts_are_explicit(self):
        service = SimpleNamespace(
            auto_shelly_soft_fail_seconds=10.0,
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )
        controller = ShellyIoController(service)

        controller._charger_read_context = MagicMock(return_value=None)
        self.assertIsNone(controller._read_charger_state_best_effort(77.0))
        controller._charger_read_context.assert_called_once_with(77.0)

        error = ModbusSlaveOfflineError("offline")
        controller._remember_charger_transport_issue = MagicMock()
        controller._remember_charger_retry = MagicMock()
        controller._handle_charger_state_read_error(service, error, 88.0)
        controller._remember_charger_transport_issue.assert_called_once_with("offline", "read", error, 88.0)
        controller._remember_charger_retry.assert_called_once_with("offline", "read", 88.0)
        service._mark_failure.assert_called_once_with("charger")
        service._warning_throttled.assert_called_once_with(
            "charger-state-failed",
            10.0,
            "Charger state read failed: %s",
            error,
            exc_info=error,
        )

    def test_charger_runtime_defaults_and_missing_snapshot_fields_are_safe(self):
        service = SimpleNamespace(
            _time_now=MagicMock(return_value=12.0),
            _mode_uses_auto_logic=MagicMock(side_effect=lambda mode: mode == 1),
        )
        controller = ShellyIoController(service)
        state = ChargerState(enabled=False, current_amps=None, phase_selection=None)
        controller._store_runtime_charger_snapshot = MagicMock()
        controller._sync_virtual_enabled_state = MagicMock()
        controller._sync_virtual_current_target = MagicMock()
        controller._sync_runtime_phase_selection_from_charger = MagicMock()

        controller._sync_charger_runtime_state(state, now=44.0)

        service._mode_uses_auto_logic.assert_called_once_with(0)
        controller._sync_virtual_enabled_state.assert_called_once_with(state, False)
        controller._store_runtime_charger_snapshot.assert_called_once_with(state, 44.0)

        snapshot_service = SimpleNamespace()
        snapshot_controller = ShellyIoController(snapshot_service)
        snapshot_controller._store_runtime_charger_snapshot(SimpleNamespace(), 55.0)
        self.assertIsNone(snapshot_service._last_charger_state_enabled)
        self.assertIsNone(snapshot_service._last_charger_state_current_amps)
        self.assertIsNone(snapshot_service._last_charger_state_phase_selection)
        self.assertIsNone(snapshot_service._last_charger_state_actual_current_amps)
        self.assertIsNone(snapshot_service._last_charger_state_power_w)
        self.assertIsNone(snapshot_service._last_charger_state_energy_kwh)
        self.assertIsNone(snapshot_service._last_charger_state_status)
        self.assertIsNone(snapshot_service._last_charger_state_fault)
        self.assertEqual(snapshot_service._last_charger_state_at, 55.0)

    def test_charger_estimate_retry_and_transport_timestamps_are_explicit(self):
        error = RuntimeError("transport detail")
        service = SimpleNamespace(
            _time_now=MagicMock(return_value=321.0),
            auto_shelly_soft_fail_seconds=10.0,
            _delay_source_retry=MagicMock(),
        )
        controller = ShellyIoController(service)

        controller._remember_charger_estimate(" estimated ", now=None)
        self.assertEqual(service._last_charger_estimate_source, "estimated")
        self.assertEqual(service._last_charger_estimate_at, 321.0)
        controller._clear_charger_estimate()
        self.assertIsNone(service._last_charger_estimate_source)
        self.assertIsNone(service._last_charger_estimate_at)

        controller._remember_charger_transport_issue(" offline ", " read ", error, now=None)
        self.assertEqual(service._last_charger_transport_reason, "offline")
        self.assertEqual(service._last_charger_transport_source, "read")
        self.assertEqual(service._last_charger_transport_detail, "transport detail")
        self.assertEqual(service._last_charger_transport_at, 321.0)

        controller._remember_charger_retry(" offline ", " read ", 400.0)
        service._delay_source_retry.assert_called_once_with("charger", 400.0, 20.0)
        self.assertEqual(service._charger_retry_reason, "offline")
        self.assertEqual(service._charger_retry_source, "read")
        self.assertEqual(service._charger_retry_until, 420.0)

        service._delay_source_retry.reset_mock()
        service.auto_shelly_soft_fail_seconds = 30.0
        controller._remember_charger_retry("offline", "read", 500.0)
        service._delay_source_retry.assert_called_once_with("charger", 500.0, 30.0)
        self.assertEqual(service._charger_retry_until, 530.0)

    def test_charger_runtime_power_and_current_edges_are_explicit(self):
        line_service = SimpleNamespace(_last_voltage=400.0, voltage_mode="line")
        line_controller = ShellyIoController(line_service)
        phase_service = SimpleNamespace(_last_voltage=400.0)
        phase_controller = ShellyIoController(phase_service)

        self.assertEqual(phase_controller._estimated_phase_voltage_v("P1_P2"), 400.0)
        self.assertEqual(line_controller._estimated_phase_voltage_v("P1"), 400.0)
        self.assertAlmostEqual(line_controller._estimated_phase_voltage_v("P1_P2"), 400.0 / (3.0**0.5))
        self.assertEqual(ShellyIoController(SimpleNamespace())._estimated_phase_voltage_v("P1_P2"), 230.0)
        self.assertEqual(ShellyIoController(SimpleNamespace(_last_voltage=0.5))._cached_runtime_voltage(), 0.5)
        self.assertEqual(ShellyIoController(SimpleNamespace(_last_voltage=0.0))._cached_runtime_voltage(), None)
        self.assertEqual(line_controller._phase_voltage_for_selection("P1_P2", 0.0), 230.0)
        self.assertEqual(line_controller._phase_voltage_for_selection("P1_P2", 1.0), 1.0 / (3.0**0.5))

        self.assertFalse(ShellyIoController._charging_like_status(SimpleNamespace()))
        self.assertFalse(ShellyIoController._charging_like_status(ChargerState(True, 6.0, "P1")))
        self.assertTrue(ShellyIoController._charging_like_status(ChargerState(True, 6.0, "P1", status_text="Charging soon")))
        self.assertEqual(ShellyIoController._fallback_pm_charger_current(ChargerState(True, 6.0, "P1")), 6.0)
        self.assertEqual(ShellyIoController._fallback_pm_charger_current(ChargerState(False, 6.0, "P1")), 0.0)
        self.assertEqual(
            ShellyIoController._fallback_pm_charger_current(ChargerState(True, 6.0, "P1", status_text="Charging")),
            6.0,
        )
        self.assertEqual(
            ShellyIoController._fallback_pm_charger_current(ChargerState(True, 6.0, "P1", status_text="ready")),
            0.0,
        )

        self.assertEqual(line_controller._estimated_charger_power_w(6.0, "P1"), 2400.0)
        self.assertAlmostEqual(line_controller._estimated_charger_power_w(6.0, "P1_P2"), 6.0 * (400.0 / (3.0**0.5)) * 2.0)
        self.assertIsNone(line_controller._estimated_charger_power_w(None, "P1_P2"))
        line_controller._sync_estimated_charger_energy_cache(-1.0, -2.0, 123.0)
        self.assertEqual(line_service._charger_estimated_energy_kwh, 0.0)
        self.assertEqual(line_service._charger_estimated_power_w, 0.0)
        self.assertEqual(line_service._charger_estimated_energy_at, 123.0)

        partial_energy_service = SimpleNamespace(
            _charger_estimated_energy_kwh=1.0,
            _charger_estimated_energy_at=100.0,
            _charger_estimated_power_w=None,
        )
        partial_energy = ShellyIoController(partial_energy_service)
        self.assertEqual(partial_energy._integrated_estimated_charger_energy_kwh(0.0, 200.0), 1.0)

        zero_power_service = SimpleNamespace(
            _charger_estimated_energy_kwh=1.0,
            _charger_estimated_energy_at=100.0,
            _charger_estimated_power_w=0.0,
        )
        zero_power = ShellyIoController(zero_power_service)
        self.assertEqual(zero_power._integrated_estimated_charger_energy_kwh(0.0, 3700.0), 1.0)

        same_time_service = SimpleNamespace(
            _charger_estimated_energy_kwh=1.0,
            _charger_estimated_energy_at=100.0,
            _charger_estimated_power_w=1000.0,
        )
        same_time = ShellyIoController(same_time_service)
        self.assertEqual(same_time._integrated_estimated_charger_energy_kwh(1000.0, 100.0), 1.0)

        start_service = SimpleNamespace(virtual_enable=0, virtual_startstop=0)
        ShellyIoController(start_service)._sync_virtual_enabled_state(
            ChargerState(enabled=True, current_amps=None, phase_selection=None),
            auto_mode_active=False,
        )
        self.assertEqual(start_service.virtual_enable, 1)
        self.assertEqual(start_service.virtual_startstop, 1)

        phase_state_service = SimpleNamespace()
        phase_state_controller = ShellyIoController(phase_state_service)
        phase_state_controller._phase_selection_switch_backend = MagicMock(return_value=None)
        phase_state_controller._charger_supported_phase_selections = MagicMock(return_value=("P1_P2",))
        phase_state_controller._remember_phase_selection_state = MagicMock()
        phase_state_controller._sync_runtime_phase_selection_from_charger(
            ChargerState(enabled=True, current_amps=6.0, phase_selection="P1_P2")
        )
        phase_state_controller._remember_phase_selection_state.assert_called_once_with(
            supported=("P1_P2",),
            requested="P1_P2",
            active="P1_P2",
        )

    def test_charger_retry_context_and_successful_read_are_explicit(self):
        state = ChargerState(enabled=True, current_amps=8.0, phase_selection="P1")
        backend = SimpleNamespace(read_charger_state=MagicMock(return_value=state))
        service = SimpleNamespace(
            _charger_backend=backend,
            _time_now=MagicMock(return_value=100.0),
            _charger_retry_until=90.0,
            _mark_recovery=MagicMock(),
        )
        controller = ShellyIoController(service)
        controller._sync_charger_runtime_state = MagicMock()
        controller._clear_charger_transport_issue = MagicMock()
        controller._clear_charger_retry = MagicMock()

        context = controller._charger_read_context(None)
        self.assertIsNotNone(context)
        self.assertEqual(context[2], 100.0)
        self.assertFalse(controller._charger_retry_active(100.0))
        service._charger_retry_until = 105.0
        self.assertTrue(controller._charger_retry_active())
        self.assertFalse(controller._charger_retry_active(110.0))
        self.assertIsNone(controller._charger_read_context(100.0))
        self.assertIsNotNone(controller._charger_read_context(110.0))

        service._charger_retry_until = None
        self.assertIs(controller._read_charger_state_best_effort(77.0), state)
        backend.read_charger_state.assert_called_once_with()
        controller._sync_charger_runtime_state.assert_called_once_with(state, now=77.0)
        controller._clear_charger_transport_issue.assert_called_once_with()
        controller._clear_charger_retry.assert_called_once_with()
        service._mark_recovery.assert_called_once_with("charger", "Charger state reads recovered")

        failing_error = ModbusSlaveOfflineError("offline")
        failing_backend = SimpleNamespace(read_charger_state=MagicMock(side_effect=failing_error))
        failing_service = SimpleNamespace(
            _charger_backend=failing_backend,
            _time_now=MagicMock(return_value=100.0),
            _charger_retry_until=None,
        )
        failing_controller = ShellyIoController(failing_service)
        failing_controller._handle_charger_state_read_error = MagicMock()
        self.assertIsNone(failing_controller._read_charger_state_best_effort(88.0))
        failing_controller._handle_charger_state_read_error.assert_called_once_with(
            failing_service,
            failing_error,
            88.0,
        )

    def test_helper_edges_cover_runtime_sync_and_switch_state_fallbacks(self):
        service = SimpleNamespace(
            _backend_bundle=_runtime_bundle("split"),
            _switch_backend=SimpleNamespace(read_switch_state=MagicMock(side_effect=RuntimeError("switch down"))),
            _charger_backend=SimpleNamespace(settings=SimpleNamespace(supported_phase_selections=("P1_P2_P3",))),
            supported_phase_selections=("P1",),
            requested_phase_selection="P1",
            active_phase_selection="P1",
            _last_switch_feedback_closed=True,
            _last_switch_interlock_ok=True,
            _last_switch_feedback_at=50.0,
            _time_now=lambda: 100.0,
            _mode_uses_auto_logic=lambda mode: bool(mode),
            virtual_mode=0,
            virtual_enable=1,
            virtual_startstop=1,
            virtual_set_current=16.0,
            _source_retry_after={},
        )
        controller = ShellyIoController(service)

        controller._sync_virtual_enabled_state(
            ChargerState(enabled=None, current_amps=None, phase_selection=None),
            auto_mode_active=False,
        )
        controller._sync_virtual_current_target(
            ChargerState(enabled=None, current_amps=None, phase_selection=None),
            100.0,
        )
        controller._sync_runtime_phase_selection_from_charger(
            ChargerState(enabled=None, current_amps=None, phase_selection=None)
        )
        self.assertEqual(controller._safe_split_switch_state(), None)
        self.assertIsNone(service._last_switch_feedback_closed)
        self.assertIsNone(service._last_switch_interlock_ok)
        self.assertIsNone(service._last_switch_feedback_at)
        self.assertEqual(controller._relay_state_from_split_switch(False), False)
        self.assertEqual(controller._split_switch_supported_phase_selections(), ("P1",))

        service._switch_backend = SimpleNamespace(set_phase_selection=MagicMock())
        controller = ShellyIoController(service)
        controller._sync_runtime_phase_selection_from_charger(
            ChargerState(enabled=None, current_amps=None, phase_selection="P1_P2_P3")
        )
        self.assertEqual(service.active_phase_selection, "P1")

        service._switch_backend = None
        controller = ShellyIoController(service)
        self.assertEqual(controller._split_switch_supported_phase_selections(), ("P1_P2_P3",))
        service._switch_backend = SimpleNamespace(
            set_enabled=MagicMock(),
            read_switch_state=MagicMock(side_effect=RuntimeError("switch down")),
        )
        controller = ShellyIoController(service)
        self.assertEqual(controller._relay_state_from_split_switch(True), True)
        service._switch_backend = SimpleNamespace(
            set_enabled=MagicMock(),
            read_switch_state=MagicMock(return_value=SimpleNamespace(enabled=None)),
        )
        controller = ShellyIoController(service)
        self.assertEqual(controller._relay_state_from_split_switch(False), False)

    def test_build_local_pm_status_normalizes_numeric_energy_totals(self):
        service = SimpleNamespace(
            _last_pm_status={"aenergy": {"total": 12}},
            _last_voltage=230.0,
        )
        controller = ShellyIoController(service)

        pm_status = controller.build_local_pm_status(True)

        self.assertEqual(pm_status["aenergy"]["total"], 12.0)

    def test_worker_apply_pending_relay_command_returns_when_queue_is_empty(self):
        service = SimpleNamespace(
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
        )
        controller = ShellyIoController(service)

        controller.worker_apply_pending_relay_command()

        service._peek_pending_relay_command.assert_called_once_with()

    def test_read_split_pm_status_without_meter_requires_recent_charger_state(self):
        service = SimpleNamespace(
            _backend_bundle=_runtime_bundle("split"),
            _meter_backend=None,
            _switch_backend=None,
            _charger_backend=None,
            supported_phase_selections=("P1",),
            requested_phase_selection="P1",
            active_phase_selection="P1",
            auto_shelly_soft_fail_seconds=10.0,
            _time_now=lambda: 100.0,
        )
        controller = ShellyIoController(service)

        with self.assertRaisesRegex(RuntimeError, "requires fresh charger readback"):
            controller._read_split_pm_status_without_meter(None, ("P1",), None, 100.0)

    def test_worker_apply_pending_relay_command_skips_and_tracks_charger_transport_retry(self):
        charger_backend = SimpleNamespace(set_enabled=MagicMock(side_effect=ModbusSlaveOfflineError("offline")))
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
            auto_shelly_soft_fail_seconds=10.0,
            _warning_throttled=MagicMock(),
            _mark_failure=MagicMock(),
            _source_retry_after={},
            _charger_retry_reason="offline",
            _charger_retry_source="read",
            _charger_retry_until=110.0,
        )
        controller = ShellyIoController(service)

        controller.worker_apply_pending_relay_command()
        charger_backend.set_enabled.assert_not_called()

        service._charger_retry_until = None
        controller.worker_apply_pending_relay_command()
        charger_backend.set_enabled.assert_called_once_with(True)
        self.assertEqual(service._last_charger_transport_reason, "offline")
        self.assertEqual(service._last_charger_transport_source, "enable")
        self.assertEqual(service._charger_retry_reason, "offline")
        service._mark_failure.assert_called_once_with("charger")

    def test_helper_edges_cover_runtime_now_split_optionals_and_worker_fallbacks(self):
        service = SimpleNamespace(
            _time_now=lambda: True,
            _source_retry_after=None,
            voltage_mode="phase",
            virtual_enable=0,
            virtual_startstop=7,
            _mode_uses_auto_logic=lambda mode: bool(mode),
            _warning_throttled=MagicMock(),
            _mark_failure=MagicMock(),
            auto_shelly_soft_fail_seconds=10.0,
        )
        controller = ShellyIoController(service)

        self.assertEqual(controller._runtime_now(), 0.0)
        controller._schedule_charger_retry_backoff(service, 100.0, 20.0)
        self.assertEqual(controller._phase_voltage_for_selection("P1_P2", 400.0), 400.0)
        controller._sync_virtual_enabled_state(
            ChargerState(enabled=True, current_amps=None, phase_selection=None),
            auto_mode_active=True,
        )
        self.assertEqual(service.virtual_enable, 1)
        self.assertEqual(service.virtual_startstop, 7)

        minimal_pm = controller._pm_status_from_meter_reading(
            MeterReading(
                relay_on=None,
                power_w=0.0,
                voltage_v=None,
                current_a=None,
                energy_kwh=1.5,
                phase_selection="P1",
                phase_powers_w=None,
                phase_currents_a=None,
            )
        )
        self.assertEqual(minimal_pm, {"apower": 0.0, "aenergy": {"total": 1500.0}, "_phase_selection": "P1"})

        self.assertEqual(controller._normalized_energy_payload({"total": "bad"}), {"total": 0.0})

        service._ensure_worker_state = MagicMock()
        service._relay_command_lock = threading.Lock()
        service._pending_relay_state = False
        service._pending_relay_requested_at = 99.0
        controller.clear_pending_relay_command(True)
        self.assertFalse(service._pending_relay_state)
        self.assertEqual(service._pending_relay_requested_at, 99.0)

        controller._handle_pending_relay_command_error(service, "charger", "charger backend", 100.0, RuntimeError("boom"))
        self.assertFalse(hasattr(service, "_last_charger_transport_reason"))
        service._mark_failure.assert_called_once_with("charger")
        service._warning_throttled.assert_called_once()

    def test_helper_edges_cover_io_worker_loop_zero_iteration_and_non_numeric_runtime_time(self):
        stop_event = SimpleNamespace(is_set=MagicMock(return_value=True))
        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            _worker_stop_event=stop_event,
            _time_now=lambda: "bad",
            _worker_poll_interval_seconds=0.2,
        )
        controller = ShellyIoController(service)

        controller.io_worker_loop()

        service._ensure_worker_state.assert_called_once_with()

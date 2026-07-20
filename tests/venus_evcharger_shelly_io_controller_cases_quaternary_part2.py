# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_shelly_io_controller_cases_quaternary_support import *  # noqa: F401,F403

class _TestShellyIoControllerQuaternaryPart2:
    def test_charger_runtime_sync_delegates_snapshot_virtuals_and_phase_state(self):
        service = SimpleNamespace(
            time_now=MagicMock(return_value=123.0),
            virtual_mode=1,
        )
        controller = ShellyIoController(service)
        service.auto.mode_uses_auto_logic.return_value = True
        state = ChargerState(enabled=True, current_amps=6.0, phase_selection="P1_P2")
        controller.runtime._store_runtime_charger_snapshot = MagicMock()
        controller.runtime._sync_virtual_enabled_state = MagicMock()
        controller.runtime._sync_virtual_current_target = MagicMock()
        controller.runtime._sync_runtime_phase_selection_from_charger = MagicMock()

        controller.runtime._sync_charger_runtime_state(state)

        service.time_now.assert_called_once_with()
        service.auto.mode_uses_auto_logic.assert_called_once_with(1)
        controller.runtime._store_runtime_charger_snapshot.assert_called_once_with(state, 123.0)
        controller.runtime._sync_virtual_enabled_state.assert_called_once_with(state, True)
        controller.runtime._sync_virtual_current_target.assert_called_once_with(state, 123.0)
        controller.runtime._sync_runtime_phase_selection_from_charger.assert_called_once_with(state)

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

        controller.runtime._store_runtime_charger_snapshot(state, 123.0)

        self.assertTrue(service._last_charger_state_enabled)
        self.assertEqual(service._last_charger_state_current_amps, 6.0)
        self.assertEqual(service._last_charger_state_phase_selection, "P1_P2")
        self.assertEqual(service._last_charger_state_actual_current_amps, 5.5)
        self.assertEqual(service._last_charger_state_power_w, 1265.0)
        self.assertEqual(service._last_charger_state_energy_kwh, 3.25)
        self.assertEqual(service._last_charger_state_status, " Charging ")
        self.assertEqual(service._last_charger_state_fault, " Fault ")
        self.assertEqual(service._last_charger_state_at, 123.0)

        controller.capabilities.phase_selection_switch_backend = MagicMock(return_value=None)
        controller.capabilities.charger_supported_phase_selections = MagicMock(return_value=("P1", "P1_P2"))
        controller.capabilities.remember_phase_selection_state = MagicMock()
        controller.runtime._sync_runtime_phase_selection_from_charger(state)
        controller.capabilities.remember_phase_selection_state.assert_called_once_with(
            supported=("P1", "P1_P2"),
            requested="P1",
            active="P1_P2",
        )

    def test_charger_runtime_numeric_and_status_edges_are_explicit(self):
        service = SimpleNamespace(_last_voltage=400.0, voltage_mode="line")
        controller = ShellyIoController(service)

        self.assertEqual(controller.runtime._cached_runtime_voltage(), 400.0)
        self.assertEqual(ShellyIoController(SimpleNamespace()).runtime._cached_runtime_voltage(), None)
        self.assertEqual(controller.runtime._phase_voltage_for_selection("P1", 400.0), 400.0)
        self.assertAlmostEqual(controller.runtime._phase_voltage_for_selection("P1_P2", 400.0), 400.0 / (3.0**0.5))
        self.assertTrue(ShellyChargerRuntime._charging_like_status(ChargerState(True, 6.0, "P1", status_text=" Charging ")))
        self.assertFalse(ShellyChargerRuntime._charging_like_status(ChargerState(True, 6.0, "P1", status_text="ready")))

        energy_service = SimpleNamespace(
            _charger_estimated_energy_kwh=1.0,
            _charger_estimated_energy_at=100.0,
            _charger_estimated_power_w=1000.0,
        )
        energy_controller = ShellyIoController(energy_service)
        self.assertEqual(energy_controller.runtime.integrated_estimated_charger_energy_kwh(500.0, 3700.0), 2.0)
        self.assertEqual(energy_service._charger_estimated_energy_kwh, 2.0)
        self.assertEqual(energy_service._charger_estimated_power_w, 500.0)

    def test_charger_runtime_read_context_and_error_contracts_are_explicit(self):
        service = SimpleNamespace(
            auto_shelly_soft_fail_seconds=10.0,
        )
        controller = ShellyIoController(service)

        controller.runtime._charger_read_context = MagicMock(return_value=None)
        self.assertIsNone(controller.runtime.read_charger_state_best_effort(77.0))
        controller.runtime._charger_read_context.assert_called_once_with(77.0)

        error = ModbusSlaveOfflineError("offline")
        controller.runtime.remember_charger_transport_issue = MagicMock()
        controller.runtime.remember_charger_retry = MagicMock()
        controller.runtime._handle_charger_state_read_error(service, error, 88.0)
        controller.runtime.remember_charger_transport_issue.assert_called_once_with("offline", "read", error, 88.0)
        controller.runtime.remember_charger_retry.assert_called_once_with("offline", "read", 88.0)
        service.runtime.mark_failure.assert_called_once_with("charger")
        service.runtime.warning_throttled.assert_called_once_with(
            "charger-state-failed",
            10.0,
            "Charger state read failed: %s",
            error,
            exc_info=error,
        )

    def test_charger_runtime_defaults_and_missing_snapshot_fields_are_safe(self):
        service = SimpleNamespace(
            time_now=MagicMock(return_value=12.0),
        )
        controller = ShellyIoController(service)
        state = ChargerState(enabled=False, current_amps=None, phase_selection=None)
        controller.runtime._store_runtime_charger_snapshot = MagicMock()
        controller.runtime._sync_virtual_enabled_state = MagicMock()
        controller.runtime._sync_virtual_current_target = MagicMock()
        controller.runtime._sync_runtime_phase_selection_from_charger = MagicMock()

        controller.runtime._sync_charger_runtime_state(state, now=44.0)

        service.auto.mode_uses_auto_logic.assert_called_once_with(0)
        controller.runtime._sync_virtual_enabled_state.assert_called_once_with(state, False)
        controller.runtime._store_runtime_charger_snapshot.assert_called_once_with(state, 44.0)

        snapshot_service = SimpleNamespace()
        snapshot_controller = ShellyIoController(snapshot_service)
        snapshot_controller.runtime._store_runtime_charger_snapshot(SimpleNamespace(), 55.0)
        self.assertIsNone(snapshot_service._last_charger_state_enabled)
        self.assertIsNone(snapshot_service._last_charger_state_current_amps)
        self.assertIsNone(snapshot_service._last_charger_state_phase_selection)
        self.assertIsNone(snapshot_service._last_charger_state_actual_current_amps)
        self.assertIsNone(snapshot_service._last_charger_state_power_w)
        self.assertIsNone(snapshot_service._last_charger_state_energy_kwh)
        self.assertIsNone(snapshot_service._last_charger_state_status)
        self.assertIsNone(snapshot_service._last_charger_state_fault)
        self.assertEqual(snapshot_service._last_charger_state_at, 55.0)

    def test_runtime_cached_charger_state_uses_one_atomic_snapshot_and_age_boundary(self):
        service = SimpleNamespace(time_now=MagicMock(return_value=100.0))
        controller = ShellyIoController(service)
        expected = ChargerState(
            enabled=True,
            current_amps=7.5,
            phase_selection="P1_P2",
            actual_current_amps=7.2,
            power_w=1656.0,
            energy_kwh=3.25,
            status_text="77",
            fault_text=" fault ",
        )
        controller.runtime._store_runtime_charger_snapshot(expected, 95.0)

        self.assertIs(controller.runtime_cache.cached_charger_state(now=100.0, max_age_seconds=5.0), expected)
        self.assertIsNone(controller.runtime_cache.cached_charger_state(now=100.1, max_age_seconds=5.0))
        service.time_now.assert_not_called()

        self.assertIs(controller.runtime_cache.cached_charger_state(max_age_seconds=5.0), expected)
        service.time_now.assert_called_once_with()

        empty_service = SimpleNamespace()
        empty_controller = ShellyIoController(empty_service)
        self.assertIsNone(empty_controller.runtime_cache.cached_charger_state(max_age_seconds=None))
        self.assertFalse(
            empty_controller.runtime_cache._charger_state_has_cached_data(
                ChargerState(enabled=None, current_amps=None, phase_selection=None)
            )
        )
        self.assertTrue(
            empty_controller.runtime_cache._charger_state_has_cached_data(
                ChargerState(enabled=None, current_amps=None, phase_selection=None, status_text="")
            )
        )

    def test_charger_estimate_retry_and_transport_timestamps_are_explicit(self):
        error = RuntimeError("transport detail")
        service = SimpleNamespace(
            time_now=MagicMock(return_value=321.0),
            auto_shelly_soft_fail_seconds=10.0,
        )
        controller = ShellyIoController(service)

        controller.runtime.remember_charger_estimate(" estimated ", now=None)
        self.assertEqual(service._last_charger_estimate_source, "estimated")
        self.assertEqual(service._last_charger_estimate_at, 321.0)
        controller.runtime.clear_charger_estimate()
        self.assertIsNone(service._last_charger_estimate_source)
        self.assertIsNone(service._last_charger_estimate_at)

        controller.runtime.remember_charger_transport_issue(" offline ", " read ", error, now=None)
        self.assertEqual(service._last_charger_transport_reason, "offline")
        self.assertEqual(service._last_charger_transport_source, "read")
        self.assertEqual(service._last_charger_transport_detail, "transport detail")
        self.assertEqual(service._last_charger_transport_at, 321.0)

        controller.runtime.remember_charger_retry(" offline ", " read ", 400.0)
        service.runtime.delay_source_retry.assert_called_once_with("charger", 400.0, 20.0)
        self.assertEqual(service._charger_retry_reason, "offline")
        self.assertEqual(service._charger_retry_source, "read")
        self.assertEqual(service._charger_retry_until, 420.0)

        service.runtime.delay_source_retry.reset_mock()
        service.auto_shelly_soft_fail_seconds = 30.0
        controller.runtime.remember_charger_retry("offline", "read", 500.0)
        service.runtime.delay_source_retry.assert_called_once_with("charger", 500.0, 30.0)
        self.assertEqual(service._charger_retry_until, 530.0)

    def test_charger_runtime_power_and_current_edges_are_explicit(self):
        line_service = SimpleNamespace(_last_voltage=400.0, voltage_mode="line")
        line_controller = ShellyIoController(line_service)
        phase_service = SimpleNamespace(_last_voltage=400.0)
        phase_controller = ShellyIoController(phase_service)

        self.assertEqual(phase_controller.runtime.estimated_phase_voltage_v("P1_P2"), 400.0)
        self.assertEqual(line_controller.runtime.estimated_phase_voltage_v("P1"), 400.0)
        self.assertAlmostEqual(line_controller.runtime.estimated_phase_voltage_v("P1_P2"), 400.0 / (3.0**0.5))
        self.assertEqual(ShellyIoController(SimpleNamespace()).runtime.estimated_phase_voltage_v("P1_P2"), 230.0)
        self.assertEqual(ShellyIoController(SimpleNamespace(_last_voltage=0.5)).runtime._cached_runtime_voltage(), 0.5)
        self.assertEqual(ShellyIoController(SimpleNamespace(_last_voltage=0.0)).runtime._cached_runtime_voltage(), None)
        self.assertEqual(line_controller.runtime._phase_voltage_for_selection("P1_P2", 0.0), 230.0)
        self.assertEqual(line_controller.runtime._phase_voltage_for_selection("P1_P2", 1.0), 1.0 / (3.0**0.5))

        self.assertFalse(ShellyChargerRuntime._charging_like_status(SimpleNamespace()))
        self.assertFalse(ShellyChargerRuntime._charging_like_status(ChargerState(True, 6.0, "P1")))
        self.assertTrue(ShellyChargerRuntime._charging_like_status(ChargerState(True, 6.0, "P1", status_text="Charging soon")))
        self.assertEqual(ShellyChargerRuntime._fallback_pm_charger_current(ChargerState(True, 6.0, "P1")), 6.0)
        self.assertEqual(ShellyChargerRuntime._fallback_pm_charger_current(ChargerState(False, 6.0, "P1")), 0.0)
        self.assertEqual(
            ShellyChargerRuntime._fallback_pm_charger_current(ChargerState(True, 6.0, "P1", status_text="Charging")),
            6.0,
        )
        self.assertEqual(
            ShellyChargerRuntime._fallback_pm_charger_current(ChargerState(True, 6.0, "P1", status_text="ready")),
            0.0,
        )

        self.assertEqual(line_controller.runtime.estimated_charger_power_w(6.0, "P1"), 2400.0)
        self.assertAlmostEqual(line_controller.runtime.estimated_charger_power_w(6.0, "P1_P2"), 6.0 * (400.0 / (3.0**0.5)) * 2.0)
        self.assertIsNone(line_controller.runtime.estimated_charger_power_w(None, "P1_P2"))
        line_controller.runtime.sync_estimated_charger_energy_cache(-1.0, -2.0, 123.0)
        self.assertEqual(line_service._charger_estimated_energy_kwh, 0.0)
        self.assertEqual(line_service._charger_estimated_power_w, 0.0)
        self.assertEqual(line_service._charger_estimated_energy_at, 123.0)

        partial_energy_service = SimpleNamespace(
            _charger_estimated_energy_kwh=1.0,
            _charger_estimated_energy_at=100.0,
            _charger_estimated_power_w=None,
        )
        partial_energy = ShellyIoController(partial_energy_service)
        self.assertEqual(partial_energy.runtime.integrated_estimated_charger_energy_kwh(0.0, 200.0), 1.0)

        zero_power_service = SimpleNamespace(
            _charger_estimated_energy_kwh=1.0,
            _charger_estimated_energy_at=100.0,
            _charger_estimated_power_w=0.0,
        )
        zero_power = ShellyIoController(zero_power_service)
        self.assertEqual(zero_power.runtime.integrated_estimated_charger_energy_kwh(0.0, 3700.0), 1.0)

        same_time_service = SimpleNamespace(
            _charger_estimated_energy_kwh=1.0,
            _charger_estimated_energy_at=100.0,
            _charger_estimated_power_w=1000.0,
        )
        same_time = ShellyIoController(same_time_service)
        self.assertEqual(same_time.runtime.integrated_estimated_charger_energy_kwh(1000.0, 100.0), 1.0)

        start_service = SimpleNamespace(virtual_enable=0, virtual_startstop=0)
        ShellyIoController(start_service).runtime._sync_virtual_enabled_state(
            ChargerState(enabled=True, current_amps=None, phase_selection=None),
            auto_mode_active=False,
        )
        self.assertEqual(start_service.virtual_enable, 1)
        self.assertEqual(start_service.virtual_startstop, 1)

        phase_state_service = SimpleNamespace(requested_phase_selection="P1_P2")
        phase_state_controller = ShellyIoController(phase_state_service)
        phase_state_controller.capabilities.phase_selection_switch_backend = MagicMock(return_value=None)
        phase_state_controller.capabilities.charger_supported_phase_selections = MagicMock(return_value=("P1_P2",))
        phase_state_controller.capabilities.remember_phase_selection_state = MagicMock()
        phase_state_controller.runtime._sync_runtime_phase_selection_from_charger(
            ChargerState(enabled=True, current_amps=6.0, phase_selection="P1_P2")
        )
        phase_state_controller.capabilities.remember_phase_selection_state.assert_called_once_with(
            supported=("P1_P2",),
            requested="P1_P2",
            active="P1_P2",
        )

    def test_charger_retry_context_and_successful_read_are_explicit(self):
        state = ChargerState(enabled=True, current_amps=8.0, phase_selection="P1")
        backend = SimpleNamespace(read_charger_state=MagicMock(return_value=state))
        service = SimpleNamespace(
            _charger_backend=backend,
            time_now=MagicMock(return_value=100.0),
            _charger_retry_until=90.0,
        )
        controller = ShellyIoController(service)
        controller.runtime._sync_charger_runtime_state = MagicMock()
        controller.runtime.clear_charger_transport_issue = MagicMock()
        controller.runtime.clear_charger_retry = MagicMock()

        context = controller.runtime._charger_read_context(None)
        self.assertIsNotNone(context)
        self.assertEqual(context[2], 100.0)
        self.assertFalse(controller.runtime.charger_retry_active(100.0))
        service._charger_retry_until = 105.0
        self.assertTrue(controller.runtime.charger_retry_active())
        self.assertFalse(controller.runtime.charger_retry_active(110.0))
        self.assertIsNone(controller.runtime._charger_read_context(100.0))
        self.assertIsNotNone(controller.runtime._charger_read_context(110.0))

        service._charger_retry_until = None
        self.assertIs(controller.runtime.read_charger_state_best_effort(77.0), state)
        backend.read_charger_state.assert_called_once_with()
        controller.runtime._sync_charger_runtime_state.assert_called_once_with(state, now=77.0)
        controller.runtime.clear_charger_transport_issue.assert_called_once_with()
        controller.runtime.clear_charger_retry.assert_called_once_with()
        service.runtime.mark_recovery.assert_called_once_with("charger", "Charger state reads recovered")

        failing_error = ModbusSlaveOfflineError("offline")
        failing_backend = SimpleNamespace(read_charger_state=MagicMock(side_effect=failing_error))
        failing_service = SimpleNamespace(
            _charger_backend=failing_backend,
            time_now=MagicMock(return_value=100.0),
            _charger_retry_until=None,
        )
        failing_controller = ShellyIoController(failing_service)
        failing_controller.runtime._handle_charger_state_read_error = MagicMock()
        self.assertIsNone(failing_controller.runtime.read_charger_state_best_effort(88.0))
        failing_controller.runtime._handle_charger_state_read_error.assert_called_once_with(
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
            time_now=lambda: 100.0,
            virtual_mode=0,
            virtual_enable=1,
            virtual_startstop=1,
            virtual_set_current=16.0,
            _source_retry_after={},
        )
        controller = ShellyIoController(service)

        controller.runtime._sync_virtual_enabled_state(
            ChargerState(enabled=None, current_amps=None, phase_selection=None),
            auto_mode_active=False,
        )
        controller.runtime._sync_virtual_current_target(
            ChargerState(enabled=None, current_amps=None, phase_selection=None),
            100.0,
        )
        controller.runtime._sync_runtime_phase_selection_from_charger(
            ChargerState(enabled=None, current_amps=None, phase_selection=None)
        )
        self.assertEqual(controller.readback._safe_split_switch_state(), None)
        self.assertIsNone(service._last_switch_feedback_closed)
        self.assertIsNone(service._last_switch_interlock_ok)
        self.assertIsNone(service._last_switch_feedback_at)
        self.assertEqual(controller.readback._relay_state_from_split_switch(False), False)
        self.assertEqual(controller.capabilities.split_switch_supported_phase_selections(), ("P1",))

        service._switch_backend = SimpleNamespace(set_phase_selection=MagicMock())
        controller = ShellyIoController(service)
        controller.runtime._sync_runtime_phase_selection_from_charger(
            ChargerState(enabled=None, current_amps=None, phase_selection="P1_P2_P3")
        )
        self.assertEqual(service.active_phase_selection, "P1")

        service._switch_backend = None
        controller = ShellyIoController(service)
        self.assertEqual(controller.capabilities.split_switch_supported_phase_selections(), ("P1_P2_P3",))
        service._switch_backend = SimpleNamespace(
            set_enabled=MagicMock(),
            read_switch_state=MagicMock(side_effect=RuntimeError("switch down")),
        )
        controller = ShellyIoController(service)
        self.assertEqual(controller.readback._relay_state_from_split_switch(True), True)
        service._switch_backend = SimpleNamespace(
            set_enabled=MagicMock(),
            read_switch_state=MagicMock(return_value=SimpleNamespace(enabled=None)),
        )
        controller = ShellyIoController(service)
        self.assertEqual(controller.readback._relay_state_from_split_switch(False), False)

    def test_build_local_pm_status_normalizes_numeric_energy_totals(self):
        service = SimpleNamespace(
            _last_pm_status={"aenergy": {"total": 12}},
            _last_voltage=230.0,
        )
        controller = ShellyIoController(service)

        pm_status = controller.build_local_pm_status(True)

        self.assertEqual(pm_status["aenergy"]["total"], 12.0)

    def test_worker_apply_pending_relay_command_returns_when_queue_is_empty(self):
        service = SimpleNamespace()
        controller = ShellyIoController(service)
        controller.worker.peek_pending_relay_command = MagicMock(return_value=(None, None))

        controller.worker_apply_pending_relay_command()

        controller.worker.peek_pending_relay_command.assert_called_once_with()

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
            time_now=lambda: 100.0,
        )
        controller = ShellyIoController(service)

        with self.assertRaisesRegex(RuntimeError, "requires fresh charger readback"):
            controller.readback._read_split_pm_status_without_meter(None, ("P1",), None, 100.0)
        try:
            controller.readback._read_split_pm_status_without_meter(None, ("P1",), None, 100.0)
        except RuntimeError as error:
            self.assertEqual(
                str(error),
                "Split mode without meter backend requires fresh charger readback",
            )

    def test_split_pm_without_meter_forwards_cache_switch_and_phase_contracts(self):
        service = SimpleNamespace(requested_phase_selection="P1_P2")
        controller = ShellyIoController(service)
        switch_state = object()
        cached_state = ChargerState(enabled=True, current_amps=8.0, phase_selection="P1")
        controller.readback._runtime_cached_charger_state_for_split = MagicMock(return_value=cached_state)
        controller.readback._resolved_switch_overrides = MagicMock(return_value=(False, "P1_P2_P3"))
        controller.capabilities.remember_phase_selection_state = MagicMock()
        controller.readback._pm_status_from_charger_state = MagicMock(return_value={"ok": True})

        self.assertEqual(
            controller.readback._read_split_pm_status_without_meter(
                switch_state,
                ("P1", "P1_P2_P3"),
                None,
                44.0,
            ),
            {"ok": True},
        )

        controller.readback._runtime_cached_charger_state_for_split.assert_called_once_with(44.0)
        controller.readback._resolved_switch_overrides.assert_called_once_with(switch_state, True, "P1")
        controller.capabilities.remember_phase_selection_state.assert_called_once_with(
            supported=("P1", "P1_P2_P3"),
            requested="P1_P2",
            active="P1_P2_P3",
        )
        controller.readback._pm_status_from_charger_state.assert_called_once_with(
            cached_state,
            relay_on=False,
            active_phase_selection="P1_P2_P3",
        )

        service_without_requested = SimpleNamespace()
        fallback_controller = ShellyIoController(service_without_requested)
        fallback_controller.readback._resolved_switch_overrides = MagicMock(return_value=(True, None))
        fallback_controller.capabilities.remember_phase_selection_state = MagicMock()
        fallback_controller.readback._pm_status_from_charger_state = MagicMock(return_value={})
        fallback_controller.readback._read_split_pm_status_without_meter(None, ("P1",), cached_state, 55.0)
        fallback_controller.capabilities.remember_phase_selection_state.assert_called_once_with(
            supported=("P1",),
            requested="P1",
            active=None,
        )

    def test_split_pm_with_meter_forwards_switch_overrides_and_requested_phase_contracts(self):
        service = SimpleNamespace(requested_phase_selection="P1_P2")
        controller = ShellyIoController(service)
        switch_state = object()
        reading = MeterReading(
            relay_on=True,
            power_w=1234.0,
            voltage_v=231.0,
            current_a=5.3,
            energy_kwh=4.5,
            phase_selection="P1",
        )
        backend = SimpleNamespace(read_meter=MagicMock(return_value=reading))
        controller.readback._resolved_switch_overrides = MagicMock(return_value=(False, "P1_P2_P3"))
        controller.capabilities.remember_phase_selection_state = MagicMock()
        controller.readback._pm_status_from_meter_reading = MagicMock(return_value={"meter": True})

        self.assertEqual(
            controller.readback._read_split_pm_status_with_meter(backend, switch_state, ("P1", "P1_P2_P3")),
            {"meter": True},
        )

        backend.read_meter.assert_called_once_with()
        controller.readback._resolved_switch_overrides.assert_called_once_with(switch_state, True, "P1")
        controller.capabilities.remember_phase_selection_state.assert_called_once_with(
            supported=("P1", "P1_P2_P3"),
            requested="P1_P2",
            active="P1_P2_P3",
        )
        controller.readback._pm_status_from_meter_reading.assert_called_once_with(reading, relay_on=False)

        del service.requested_phase_selection
        controller.readback._resolved_switch_overrides.reset_mock()
        controller.capabilities.remember_phase_selection_state.reset_mock()
        controller.readback._pm_status_from_meter_reading.reset_mock()
        controller.readback._read_split_pm_status_with_meter(backend, None, ("P1",))
        controller.capabilities.remember_phase_selection_state.assert_called_once_with(
            supported=("P1",),
            requested="P1",
            active="P1_P2_P3",
        )

    def test_split_phase_and_relay_resolution_contracts_are_explicit(self):
        service = SimpleNamespace(active_phase_selection="P1_P2", auto_shelly_soft_fail_seconds=12.5)
        controller = ShellyIoController(service)

        self.assertEqual(
            controller.readback._resolved_pm_phase_selection(
                ChargerState(enabled=True, current_amps=6.0, phase_selection="P1"),
                "P1_P2_P3",
            ),
            "P1_P2_P3",
        )
        self.assertEqual(
            controller.readback._resolved_pm_phase_selection(
                ChargerState(enabled=True, current_amps=6.0, phase_selection="P1"),
                None,
            ),
            "P1",
        )
        self.assertEqual(
            controller.readback._resolved_pm_phase_selection(
                ChargerState(enabled=True, current_amps=6.0, phase_selection=None),
                None,
            ),
            "P1_P2",
        )
        self.assertEqual(
            ShellyIoController(SimpleNamespace()).readback._resolved_pm_phase_selection(
                ChargerState(enabled=True, current_amps=6.0, phase_selection=None),
                None,
            ),
            "P1",
        )

        controller.readback._split_switch_state = MagicMock(return_value=SimpleNamespace(enabled=False))
        self.assertFalse(controller.readback._relay_state_from_split_switch(True))
        controller.readback._split_switch_state.assert_called_once_with()

        controller.readback._split_switch_state = MagicMock(return_value=SimpleNamespace(enabled=None))
        self.assertTrue(controller.readback._relay_state_from_split_switch(True))

        controller.runtime_cache.cached_charger_state = MagicMock(return_value="cached")
        self.assertEqual(controller.readback._runtime_cached_charger_state_for_split(77.0), "cached")
        controller.runtime_cache.cached_charger_state.assert_called_once_with(now=77.0, max_age_seconds=12.5)

        zero_age_controller = ShellyIoController(SimpleNamespace(auto_shelly_soft_fail_seconds=0.0))
        zero_age_controller.runtime_cache.cached_charger_state = MagicMock(return_value=None)
        self.assertIsNone(zero_age_controller.readback._runtime_cached_charger_state_for_split(88.0))
        zero_age_controller.runtime_cache.cached_charger_state.assert_called_once_with(now=88.0, max_age_seconds=0.0)

        default_age_controller = ShellyIoController(SimpleNamespace())
        default_age_controller.runtime_cache.cached_charger_state = MagicMock(return_value="cached")
        self.assertEqual(default_age_controller.readback._runtime_cached_charger_state_for_split(99.0), "cached")
        default_age_controller.runtime_cache.cached_charger_state.assert_called_once_with(
            now=99.0,
            max_age_seconds=10.0,
        )

        negative_age_controller = ShellyIoController(SimpleNamespace(auto_shelly_soft_fail_seconds=-5.0))
        negative_age_controller.runtime_cache.cached_charger_state = MagicMock(return_value=None)
        self.assertIsNone(negative_age_controller.readback._runtime_cached_charger_state_for_split(None))
        negative_age_controller.runtime_cache.cached_charger_state.assert_called_once_with(now=None, max_age_seconds=0.0)

    def test_pm_status_from_charger_state_forwards_internal_contracts(self):
        service = SimpleNamespace(phase="L2", time_now=MagicMock(return_value=55.0))
        controller = ShellyIoController(service)
        state = ChargerState(enabled=True, current_amps=6.0, phase_selection="P1")
        controller.readback._resolved_pm_phase_selection = MagicMock(return_value="P1_P2")
        controller.runtime.resolved_pm_charger_current = MagicMock(return_value=8.0)
        controller.readback._resolved_pm_power = MagicMock(return_value=(1600.0, True))
        controller.readback._resolved_pm_energy = MagicMock(return_value=(2.5, False))
        controller.readback._resolved_pm_voltage = MagicMock(return_value=230.0)
        controller.readback._sync_pm_estimate_marker = MagicMock()
        controller.readback._apply_phase_projection = MagicMock()

        pm_status = controller.readback._pm_status_from_charger_state(
            state,
            relay_on=False,
            active_phase_selection="P1_P2_P3",
        )

        self.assertEqual(
            pm_status,
            {
                "apower": 1600.0,
                "aenergy": {"total": 2500.0},
                "_phase_selection": "P1_P2",
                "output": False,
                "current": 8.0,
                "voltage": 230.0,
            },
        )
        controller.readback._resolved_pm_phase_selection.assert_called_once_with(state, "P1_P2_P3")
        controller.runtime.resolved_pm_charger_current.assert_called_once_with(state)
        controller.readback._resolved_pm_power.assert_called_once_with(state, 8.0, "P1_P2")
        controller.readback._resolved_pm_energy.assert_called_once_with(state, 1600.0, 55.0)
        controller.readback._resolved_pm_voltage.assert_called_once_with(service, "P1_P2", True, False)
        controller.readback._sync_pm_estimate_marker.assert_called_once_with(True, False, 55.0)
        controller.readback._apply_phase_projection.assert_called_once_with(pm_status, 8.0, 1600.0, "P1_P2", "L2")

        default_phase_service = SimpleNamespace(time_now=MagicMock(return_value=66.0))
        default_phase_controller = ShellyIoController(default_phase_service)
        default_phase_controller.readback._resolved_pm_phase_selection = MagicMock(return_value="P1")
        default_phase_controller.runtime.resolved_pm_charger_current = MagicMock(return_value=None)
        default_phase_controller.readback._resolved_pm_power = MagicMock(return_value=(0.0, False))
        default_phase_controller.readback._resolved_pm_energy = MagicMock(return_value=(0.0, False))
        default_phase_controller.readback._resolved_pm_voltage = MagicMock(return_value=None)
        default_phase_controller.readback._sync_pm_estimate_marker = MagicMock()
        default_phase_controller.readback._apply_phase_projection = MagicMock()
        default_pm_status = default_phase_controller.readback._pm_status_from_charger_state(
            state,
            relay_on=None,
            active_phase_selection=None,
        )
        default_phase_controller.readback._apply_phase_projection.assert_called_once_with(
            default_pm_status,
            None,
            0.0,
            "P1",
            "L1",
        )

    def test_split_pm_estimation_contracts_cover_flags_sources_voltage_and_projection(self):
        service = SimpleNamespace(_last_voltage=231.0)
        controller = ShellyIoController(service)
        state_with_power = ChargerState(enabled=True, current_amps=6.0, phase_selection="P1", power_w=1400.0)
        controller.runtime.estimated_charger_power_w = MagicMock(return_value=999.0)

        self.assertEqual(controller.readback._resolved_pm_power(state_with_power, 6.0, "P1"), (1400.0, False))
        controller.runtime.estimated_charger_power_w.assert_not_called()

        state_without_power = ChargerState(enabled=True, current_amps=6.0, phase_selection="P1")
        controller.runtime.estimated_charger_power_w = MagicMock(return_value=1230.0)
        self.assertEqual(controller.readback._resolved_pm_power(state_without_power, 6.0, "P1"), (1230.0, True))
        controller.runtime.estimated_charger_power_w.assert_called_once_with(6.0, "P1")

        controller.runtime.estimated_charger_power_w = MagicMock(return_value=None)
        self.assertEqual(controller.readback._resolved_pm_power(state_without_power, None, "P1"), (0.0, False))

        state_with_energy = ChargerState(
            enabled=True,
            current_amps=6.0,
            phase_selection="P1",
            energy_kwh=2.25,
        )
        controller.runtime.sync_estimated_charger_energy_cache = MagicMock()
        controller.runtime.integrated_estimated_charger_energy_kwh = MagicMock(return_value=8.0)
        self.assertEqual(controller.readback._resolved_pm_energy(state_with_energy, 1200.0, 77.0), (2.25, False))
        controller.runtime.sync_estimated_charger_energy_cache.assert_called_once_with(2.25, 1200.0, 77.0)
        controller.runtime.integrated_estimated_charger_energy_kwh.assert_not_called()

        controller.runtime.sync_estimated_charger_energy_cache.reset_mock()
        self.assertEqual(controller.readback._resolved_pm_energy(state_without_power, 1200.0, 88.0), (8.0, True))
        controller.runtime.integrated_estimated_charger_energy_kwh.assert_called_once_with(1200.0, 88.0)
        controller.runtime.sync_estimated_charger_energy_cache.assert_not_called()

        controller.runtime.remember_charger_estimate = MagicMock()
        controller.runtime.clear_charger_estimate = MagicMock()
        controller.readback._sync_pm_estimate_marker(True, False, 10.0)
        controller.runtime.remember_charger_estimate.assert_called_once_with("current-voltage-phase", 10.0)
        controller.runtime.clear_charger_estimate.assert_not_called()

        controller.runtime.remember_charger_estimate.reset_mock()
        controller.readback._sync_pm_estimate_marker(False, True, 20.0)
        controller.runtime.remember_charger_estimate.assert_called_once_with("power-time", 20.0)

        controller.runtime.remember_charger_estimate.reset_mock()
        controller.readback._sync_pm_estimate_marker(False, False, 30.0)
        controller.runtime.remember_charger_estimate.assert_not_called()
        controller.runtime.clear_charger_estimate.assert_called_once_with()

        controller.runtime.estimated_phase_voltage_v = MagicMock(return_value=229.0)
        self.assertEqual(controller.readback._resolved_pm_voltage(service, "P1", True, True), 231.0)
        controller.runtime.estimated_phase_voltage_v.assert_not_called()

        no_voltage_service = SimpleNamespace()
        self.assertIsNone(controller.readback._resolved_pm_voltage(no_voltage_service, "P1", False, False))
        controller.runtime.estimated_phase_voltage_v.assert_not_called()

        self.assertEqual(controller.readback._resolved_pm_voltage(no_voltage_service, "P1_P2", False, True), 229.0)
        controller.runtime.estimated_phase_voltage_v.assert_called_once_with("P1_P2")

        projected: dict[str, object] = {}
        controller.readback._apply_phase_projection(projected, 6.0, 1200.0, "P1", "L2")
        self.assertEqual(projected["_phase_currents_a"], (0.0, 6.0, 0.0))
        self.assertEqual(projected["_phase_powers_w"], (0.0, 1200.0, 0.0))

        projected_without_current: dict[str, object] = {}
        controller.readback._apply_phase_projection(projected_without_current, None, 900.0, "P1", "L3")
        self.assertNotIn("_phase_currents_a", projected_without_current)
        self.assertEqual(projected_without_current["_phase_powers_w"], (0.0, 0.0, 900.0))

    def test_split_switch_resolution_defaults_and_missing_attrs_are_contracts(self):
        controller = ShellyIoController(SimpleNamespace())

        controller.readback._split_switch_state = MagicMock(return_value=SimpleNamespace())
        self.assertTrue(controller.readback._relay_state_from_split_switch(True))
        controller.readback._split_switch_state = MagicMock(return_value=SimpleNamespace())
        self.assertFalse(controller.readback._relay_state_from_split_switch(False))

        controller.readback._split_switch_state = MagicMock(return_value=SimpleNamespace(enabled=True))
        self.assertTrue(controller.readback._relay_state_from_split_switch(False))
        controller.readback._split_switch_state = MagicMock(return_value=SimpleNamespace(enabled=False))
        self.assertFalse(controller.readback._relay_state_from_split_switch(True))

        self.assertEqual(controller.readback._resolved_switch_overrides(None, True, "P1"), (True, "P1"))
        self.assertEqual(controller.readback._resolved_switch_overrides(SimpleNamespace(), False, "P1_P2"), (False, "P1_P2"))
        self.assertEqual(
            controller.readback._resolved_switch_overrides(SimpleNamespace(enabled=None), True, "P1_P2"),
            (True, "P1_P2"),
        )
        self.assertEqual(
            controller.readback._resolved_switch_overrides(SimpleNamespace(enabled=0, phase_selection="P1"), True, "P1_P2"),
            (False, "P1"),
        )
        self.assertEqual(
            controller.readback._resolved_switch_overrides(SimpleNamespace(enabled=True, phase_selection=None), False, "P1"),
            (True, None),
        )

    def test_read_split_pm_status_routes_backend_variants_with_runtime_context(self):
        controller = ShellyIoController(SimpleNamespace())
        charger_state = ChargerState(enabled=True, current_amps=6.0, phase_selection="P1")

        controller.capabilities.split_meter_backend = MagicMock(return_value=None)
        controller.capabilities.split_switch_supported_phase_selections = MagicMock(return_value=("P1", "P1_P2"))
        controller.readback._safe_split_switch_state = MagicMock(return_value="switch-state")
        controller.readback._read_split_pm_status_without_meter = MagicMock(return_value={"without": True})
        controller.readback._read_split_pm_status_with_meter = MagicMock(return_value={"with": True})

        self.assertEqual(controller.readback.read_pm_status(charger_state, now=123.0), {"without": True})
        controller.readback._read_split_pm_status_without_meter.assert_called_once_with(
            "switch-state",
            ("P1", "P1_P2"),
            charger_state,
            123.0,
        )
        controller.readback._read_split_pm_status_with_meter.assert_not_called()

        meter_backend = object()
        controller.capabilities.split_meter_backend = MagicMock(return_value=meter_backend)
        controller.capabilities.split_switch_supported_phase_selections = MagicMock(return_value=("P1_P2_P3",))
        controller.readback._safe_split_switch_state = MagicMock(return_value=None)
        controller.readback._read_split_pm_status_without_meter = MagicMock(return_value={"without": True})
        controller.readback._read_split_pm_status_with_meter = MagicMock(return_value={"with": True})

        self.assertEqual(controller.readback.read_pm_status(charger_state, now=456.0), {"with": True})
        controller.readback._read_split_pm_status_with_meter.assert_called_once_with(
            meter_backend,
            None,
            ("P1_P2_P3",),
        )
        controller.readback._read_split_pm_status_without_meter.assert_not_called()

    def test_phase_selection_capability_contracts_preserve_service_defaults(self):
        service = SimpleNamespace(
            supported_phase_selections=("P1", "P1_P2"),
            requested_phase_selection="P1_P2",
            active_phase_selection="bad",
        )
        controller = ShellyIoController(service)

        controller.capabilities.remember_phase_selection_state()

        self.assertEqual(service.supported_phase_selections, ("P1", "P1_P2"))
        self.assertEqual(service.requested_phase_selection, "P1_P2")
        self.assertEqual(service.active_phase_selection, "P1_P2")

        switch_backend = SimpleNamespace(set_phase_selection=MagicMock())
        service._switch_backend = switch_backend
        self.assertEqual(controller.capabilities.split_switch_supported_phase_selections(), ("P1", "P1_P2"))

        charger_backend = SimpleNamespace(
            set_phase_selection=MagicMock(),
            settings=SimpleNamespace(supported_phase_selections=("P1_P2_P3",)),
        )
        service._switch_backend = None
        service._charger_backend = charger_backend
        self.assertEqual(controller.capabilities.charger_supported_phase_selections(), ("P1_P2_P3",))
        self.assertEqual(controller.capabilities.split_switch_supported_phase_selections(), ("P1_P2_P3",))
        self.assertFalse(controller.capabilities._charger_supports_phase_selection("P1_P2"))
        self.assertTrue(controller.capabilities._charger_supports_phase_selection("P1_P2_P3"))

        service._charger_backend = SimpleNamespace(set_phase_selection=MagicMock(), settings=SimpleNamespace())
        self.assertTrue(controller.capabilities._charger_supports_phase_selection("P1_P2"))
        self.assertEqual(controller.capabilities.charger_supported_phase_selections(), ("P1", "P1_P2"))

    def test_phase_selection_capability_contracts_handle_missing_backend_attrs(self):
        service = SimpleNamespace(
            _backend_bundle=_runtime_bundle("split"),
            supported_phase_selections=("P1_P2", "P1_P2_P3"),
        )
        controller = ShellyIoController(service)

        self.assertEqual(controller.capabilities._service_supported_phase_selections(), ("P1_P2", "P1_P2_P3"))
        self.assertIsNone(controller.capabilities.split_meter_backend())
        self.assertIsNone(controller.capabilities.split_switch_backend())
        self.assertIsNone(controller.capabilities.split_enable_backend())
        self.assertIsNone(controller.capabilities._phase_switch_capabilities())
        self.assertEqual(controller.capabilities.charger_supported_phase_selections(), ("P1_P2", "P1_P2_P3"))
        self.assertEqual(controller.capabilities.split_switch_supported_phase_selections(), ("P1_P2", "P1_P2_P3"))
        self.assertEqual(controller.capabilities.split_enable_source_label(), "Shelly relay")

        minimal_controller = ShellyIoController(SimpleNamespace(auto_shelly_soft_fail_seconds=0.0))
        self.assertEqual(minimal_controller.capabilities._service_supported_phase_selections(), ("P1",))
        self.assertEqual(minimal_controller.capabilities._direct_switch_warning_interval(), 30.0)

    def test_phase_selection_state_uses_supported_defaults_for_invalid_inputs(self):
        service = SimpleNamespace(
            supported_phase_selections=("P1_P2", "P1_P2_P3"),
            requested_phase_selection="bad",
            active_phase_selection="P1_P2_P3",
        )
        controller = ShellyIoController(service)

        controller.capabilities.remember_phase_selection_state(supported=(), requested="bad")

        self.assertEqual(service.supported_phase_selections, ("P1_P2", "P1_P2_P3"))
        self.assertEqual(service.requested_phase_selection, "P1_P2")
        self.assertEqual(service.active_phase_selection, "P1_P2_P3")

        switch_backend = SimpleNamespace(
            set_phase_selection=MagicMock(),
            capabilities=MagicMock(return_value=SimpleNamespace(supported_phase_selections=("P1_P2", "P1_P2_P3"))),
        )
        service._switch_backend = switch_backend
        applied = controller.set_phase_selection("bad")

        self.assertEqual(applied, "P1_P2")
        switch_backend.set_phase_selection.assert_called_once_with("P1_P2")

        with self.assertRaisesRegex(ValueError, r"supported: P1_P2,P1_P2_P3"):
            controller.set_phase_selection("P1")

        sparse_service = SimpleNamespace(
            supported_phase_selections=("P1_P2",),
            requested_phase_selection="P1_P2",
            active_phase_selection="P1_P2",
        )
        sparse_controller = ShellyIoController(sparse_service)
        sparse_controller.capabilities.remember_phase_selection_state()
        self.assertEqual(sparse_service.requested_phase_selection, "P1_P2")
        self.assertEqual(sparse_service.active_phase_selection, "P1_P2")

    def test_switch_capability_warning_contracts_preserve_payload_and_bounds(self):
        switch_backend = SimpleNamespace(
            capabilities=MagicMock(
                return_value=SimpleNamespace(
                    switching_mode="Direct",
                    max_direct_switch_power_w=1500.0,
                    supported_phase_selections=("P1", "P1_P2_P3"),
                    requires_charge_pause_for_phase_change=True,
                )
            )
        )
        service = SimpleNamespace(
            _switch_backend=switch_backend,
            _last_pm_status={"apower": -1800.0},
            _last_pm_status_confirmed=True,
            auto_shelly_soft_fail_seconds=0.2,
            supported_phase_selections=("P1",),
        )
        controller = ShellyIoController(service)

        self.assertEqual(controller.capabilities._switching_mode(), "direct")
        self.assertEqual(controller.capabilities._max_direct_switch_power_w(), 1500.0)
        self.assertEqual(controller.capabilities._current_confirmed_switch_load_power_w(), 1800.0)
        self.assertEqual(controller.capabilities._direct_switch_warning_context(False), (1800.0, 1500.0))
        service._last_pm_status = {"apower": 1500.0}
        self.assertIsNone(controller.capabilities._direct_switch_warning_context(False))
        service._last_pm_status = {"apower": -1800.0}
        self.assertIsNone(controller.capabilities._direct_switch_warning_context(True))
        self.assertEqual(controller.capabilities._direct_switch_warning_interval(), 1.0)
        self.assertTrue(controller.phase_selection_requires_pause())
        self.assertEqual(controller.capabilities.split_switch_supported_phase_selections(), ("P1", "P1_P2_P3"))

        controller.capabilities.warn_if_direct_switching_under_load(False)

        service.runtime.warning_throttled.assert_called_once_with(
            "direct-switch-under-load",
            1.0,
            "Direct Shelly relay OFF requested at %.1fW above configured direct switch limit %.1fW; consider switching_mode=contactor",
            1800.0,
            1500.0,
        )

        service.auto_shelly_soft_fail_seconds = 12.5
        self.assertEqual(controller.capabilities._direct_switch_warning_interval(), 12.5)
        service.auto_shelly_soft_fail_seconds = 0.0
        self.assertEqual(controller.capabilities._direct_switch_warning_interval(), 30.0)

        service._last_pm_status_confirmed = False
        self.assertIsNone(controller.capabilities._current_confirmed_switch_load_power_w())
        service._last_pm_status_confirmed = True
        service._last_pm_status = None
        self.assertIsNone(controller.capabilities._current_confirmed_switch_load_power_w())

        zero_limit_backend = SimpleNamespace(
            capabilities=MagicMock(
                return_value=SimpleNamespace(
                    switching_mode="direct",
                    max_direct_switch_power_w=0.0,
                )
            )
        )
        service._switch_backend = zero_limit_backend
        self.assertIsNone(controller.capabilities._max_direct_switch_power_w())

        small_limit_backend = SimpleNamespace(
            capabilities=MagicMock(
                return_value=SimpleNamespace(
                    switching_mode="direct",
                    max_direct_switch_power_w=1.0,
                )
            )
        )
        service._switch_backend = small_limit_backend
        self.assertEqual(controller.capabilities._max_direct_switch_power_w(), 1.0)

        missing_capability_backend = SimpleNamespace(capabilities=MagicMock(return_value=SimpleNamespace()))
        service._switch_backend = missing_capability_backend
        self.assertEqual(controller.capabilities._switching_mode(), "direct")
        self.assertFalse(controller.phase_selection_requires_pause())
        self.assertEqual(controller.capabilities.split_switch_supported_phase_selections(), ("P1",))

        service._last_pm_status = {"apower": 1800.0}
        self.assertIsNone(controller.capabilities._direct_switch_warning_context(False))
        controller.capabilities.warn_if_direct_switching_under_load(False)
        service.runtime.warning_throttled.assert_called_once()

        service._switch_backend = switch_backend
        service.runtime.warning_throttled.reset_mock()
        controller.capabilities.warn_if_direct_switching_under_load(True)
        service.runtime.warning_throttled.assert_not_called()

        missing_warning_service = SimpleNamespace(
            _switch_backend=switch_backend,
            _last_pm_status={"apower": 1800.0},
            _last_pm_status_confirmed=True,
            auto_shelly_soft_fail_seconds=10.0,
        )
        ShellyIoController(missing_warning_service).capabilities.warn_if_direct_switching_under_load(False)

        charger_enable_service = SimpleNamespace(
            _backend_bundle=_runtime_bundle("split"),
            _switch_backend=None,
            _charger_backend=SimpleNamespace(set_enabled=MagicMock()),
        )
        charger_enable_controller = ShellyIoController(charger_enable_service)
        self.assertEqual(charger_enable_controller.capabilities.split_enable_source_label(), "charger backend")

    def test_switch_snapshot_contracts_store_timestamp_only_for_known_feedback(self):
        service = SimpleNamespace(time_now=MagicMock(return_value=321.0))
        controller = ShellyIoController(service)

        self.assertEqual(controller.capabilities._switch_snapshot_values(None), (None, None))
        self.assertIsNone(controller.capabilities._switch_snapshot_timestamp(None, None, None))

        controller.capabilities.store_runtime_switch_snapshot(
            SwitchState(True, "P1", feedback_closed=True, interlock_ok=False),
            now=None,
        )

        self.assertTrue(service._last_switch_feedback_closed)
        self.assertFalse(service._last_switch_interlock_ok)
        self.assertEqual(service._last_switch_feedback_at, 321.0)

        controller.capabilities.store_runtime_switch_snapshot(
            SwitchState(True, "P1", feedback_closed=None, interlock_ok=True),
            now=456.0,
        )

        self.assertIsNone(service._last_switch_feedback_closed)
        self.assertTrue(service._last_switch_interlock_ok)
        self.assertEqual(service._last_switch_feedback_at, 456.0)

        controller.capabilities.store_runtime_switch_snapshot(
            SwitchState(True, "P1", feedback_closed=True, interlock_ok=None),
            now=None,
        )

        self.assertTrue(service._last_switch_feedback_closed)
        self.assertIsNone(service._last_switch_interlock_ok)
        self.assertEqual(service._last_switch_feedback_at, 321.0)

    def test_worker_apply_pending_relay_command_skips_and_tracks_charger_transport_retry(self):
        charger_backend = SimpleNamespace(set_enabled=MagicMock(side_effect=ModbusSlaveOfflineError("offline")))
        service = SimpleNamespace(
            _backend_bundle=_runtime_bundle("split"),
            _switch_backend=None,
            _charger_backend=charger_backend,
            time_now=MagicMock(return_value=100.0),
            _worker_session=MagicMock(),
            auto_shelly_soft_fail_seconds=10.0,
            _source_retry_after={},
            _charger_retry_reason="offline",
            _charger_retry_source="read",
            _charger_retry_until=110.0,
        )
        controller = ShellyIoController(service)
        controller.queue_relay_command(True, now=90.0)

        controller.worker_apply_pending_relay_command()
        charger_backend.set_enabled.assert_not_called()

        service._charger_retry_until = None
        controller.worker_apply_pending_relay_command()
        charger_backend.set_enabled.assert_called_once_with(True)
        self.assertEqual(service._last_charger_transport_reason, "offline")
        self.assertEqual(service._last_charger_transport_source, "enable")
        self.assertEqual(service._charger_retry_reason, "offline")
        service.runtime.mark_failure.assert_called_once_with("charger")

    def test_helper_edges_cover_runtime_now_split_optionals_and_worker_fallbacks(self):
        service = SimpleNamespace(
            time_now=lambda: 0.0,
            _source_retry_after=None,
            voltage_mode="phase",
            virtual_enable=0,
            virtual_startstop=7,
            auto_shelly_soft_fail_seconds=10.0,
        )
        controller = ShellyIoController(service)
        service.runtime.delay_source_retry = MagicMock()

        self.assertEqual(controller.clock(), 0.0)
        controller.runtime._schedule_charger_retry_backoff(service, 100.0, 20.0)
        self.assertEqual(controller.runtime._phase_voltage_for_selection("P1_P2", 400.0), 400.0)
        controller.runtime._sync_virtual_enabled_state(
            ChargerState(enabled=True, current_amps=None, phase_selection=None),
            auto_mode_active=True,
        )
        self.assertEqual(service.virtual_enable, 1)
        self.assertEqual(service.virtual_startstop, 7)

        minimal_pm = controller.readback._pm_status_from_meter_reading(
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

        self.assertEqual(controller.worker._normalized_energy_payload({"total": "bad"}), {"total": 0.0})

        service.runtime.ensure_worker_state = MagicMock()
        service._relay_command_lock = threading.Lock()
        service._pending_relay_state = False
        service._pending_relay_requested_at = 99.0
        controller.clear_pending_relay_command(True)
        self.assertFalse(service._pending_relay_state)
        self.assertEqual(service._pending_relay_requested_at, 99.0)

        controller.worker._handle_pending_relay_command_error(service, "charger", "charger backend", 100.0, RuntimeError("boom"))
        self.assertIsNone(service._last_charger_transport_reason)
        service.runtime.mark_failure.assert_called_once_with("charger")
        service.runtime.warning_throttled.assert_called_once()

    def test_worker_pending_relay_error_helpers_are_explicit(self):
        service = SimpleNamespace(
            _shelly_consecutive_errors=3,
            auto_shelly_soft_fail_seconds=12.0,
        )
        controller = ShellyIoController(service)
        service.runtime.source_retry_remaining.side_effect = None
        service.runtime.source_retry_remaining.return_value = 7.5

        controller.runtime.charger_retry_active = MagicMock(return_value=True)
        controller.transport.retry_active = MagicMock(return_value=False)
        self.assertTrue(controller.worker._source_retry_blocks_pending_relay("charger", 100.0))
        self.assertFalse(controller.worker._source_retry_blocks_pending_relay("shelly", 100.0))
        self.assertFalse(controller.worker._source_retry_blocks_pending_relay("other", 100.0))
        controller.runtime.charger_retry_active.assert_called_once_with(100.0)
        controller.transport.retry_active.assert_called_once_with(100.0)

        self.assertEqual(controller.worker._pending_relay_shelly_error_count("shelly"), 3)
        self.assertEqual(controller.worker._pending_relay_shelly_error_count("charger"), 0)
        self.assertEqual(controller.worker._pending_relay_shelly_retry_remaining("shelly", 101.0), 7.5)
        service.runtime.source_retry_remaining.assert_called_once_with("shelly", 101.0)
        self.assertEqual(controller.worker._pending_relay_shelly_retry_remaining("charger", 101.0), 0.0)

        network_error = requests.exceptions.ConnectionError("No route to host")
        generic_error = RuntimeError("boom")
        self.assertIsNone(controller.worker._pending_relay_error_exc_info("shelly", network_error))
        self.assertIs(controller.worker._pending_relay_error_exc_info("shelly", generic_error), generic_error)
        self.assertIs(controller.worker._pending_relay_error_exc_info("charger", network_error), network_error)

    def test_worker_pending_relay_error_recording_and_warning_payloads_are_explicit(self):
        service = SimpleNamespace(
            _shelly_consecutive_errors=4,
            auto_shelly_soft_fail_seconds=10.0,
        )
        controller = ShellyIoController(service)

        charger_error = ModbusSlaveOfflineError("offline")
        controller.runtime.remember_charger_transport_issue = MagicMock()
        controller.runtime.remember_charger_retry = MagicMock()
        self.assertEqual(controller.worker._remember_pending_relay_command_error("charger", 100.0, charger_error), "error")
        controller.runtime.remember_charger_transport_issue.assert_called_once_with("offline", "enable", charger_error, 100.0)
        controller.runtime.remember_charger_retry.assert_called_once_with("offline", "enable", 100.0)

        generic_charger_error = RuntimeError("boom")
        controller.runtime.remember_charger_transport_issue.reset_mock()
        controller.runtime.remember_charger_retry.reset_mock()
        self.assertEqual(
            controller.worker._remember_pending_relay_command_error("charger", 101.0, generic_charger_error),
            "error",
        )
        controller.runtime.remember_charger_transport_issue.assert_not_called()
        controller.runtime.remember_charger_retry.assert_not_called()

        shelly_error = requests.exceptions.ConnectionError("No route to host")
        controller.transport.remember_failure = MagicMock()
        self.assertEqual(controller.worker._remember_pending_relay_command_error("shelly", 102.0, shelly_error), "no-route")
        controller.transport.remember_failure.assert_called_once_with("no-route", "relay", shelly_error, 102.0)
        self.assertEqual(controller.worker._remember_pending_relay_command_error("other", 103.0, RuntimeError("x")), "error")

        service.runtime.source_retry_remaining = MagicMock(
            side_effect=lambda source, current: 2.5 if source == "shelly" and current == 104.0 else -1.0
        )
        controller.worker._warn_pending_relay_command_error(service, "shelly", "Shelly relay", 104.0, "no-route", shelly_error)
        service.runtime.mark_failure.assert_called_once_with("shelly")
        service.runtime.warning_throttled.assert_called_once_with(
            "worker-shelly-switch-failed-no-route",
            10.0,
            "%s switch failed (%s, consecutive=%s, retry=%ss): %s",
            "Shelly relay",
            "no-route",
            4,
            2.5,
            shelly_error,
            exc_info=None,
        )

        service.runtime.mark_failure.reset_mock()
        service.runtime.warning_throttled.reset_mock()
        controller.worker._handle_pending_relay_command_error(service, "other", "Other source", 105.0, generic_charger_error)
        service.runtime.mark_failure.assert_called_once_with("other")
        self.assertIs(service.runtime.warning_throttled.call_args.kwargs["exc_info"], generic_charger_error)

    def test_worker_pending_relay_command_error_delegates_context_verbatim(self):
        service = SimpleNamespace()
        controller = ShellyIoController(service)
        error = RuntimeError("boom")
        controller.worker._remember_pending_relay_command_error = MagicMock(return_value="classified")
        controller.worker._warn_pending_relay_command_error = MagicMock()

        controller.worker._handle_pending_relay_command_error(service, "source-key", "Source label", 123.0, error)

        controller.worker._remember_pending_relay_command_error.assert_called_once_with("source-key", 123.0, error)
        controller.worker._warn_pending_relay_command_error.assert_called_once_with(
            service,
            "source-key",
            "Source label",
            123.0,
            "classified",
            error,
        )

        apply_service = SimpleNamespace()
        apply_controller = ShellyIoController(apply_service)
        apply_error = RuntimeError("apply failed")
        apply_controller.worker._pending_relay_command_context = MagicMock(
            return_value=(apply_service, True, "source-key", "Source label", 456.0)
        )
        apply_controller.worker._apply_pending_relay_target = MagicMock(side_effect=apply_error)
        apply_controller.worker._handle_pending_relay_command_error = MagicMock()
        apply_controller.worker._finalize_pending_relay_command = MagicMock()

        apply_controller.worker_apply_pending_relay_command()

        apply_controller.worker._handle_pending_relay_command_error.assert_called_once_with(
            apply_service,
            "source-key",
            "Source label",
            456.0,
            apply_error,
        )
        apply_controller.worker._finalize_pending_relay_command.assert_not_called()

    def test_helper_edges_cover_io_worker_loop_zero_iteration_and_non_numeric_runtime_time(self):
        stop_event = SimpleNamespace(is_set=MagicMock(return_value=True))
        service = SimpleNamespace(
            _worker_stop_event=stop_event,
            time_now=lambda: "bad",
            _worker_poll_interval_seconds=0.2,
        )
        controller = ShellyIoController(service)

        controller.io_worker_loop()

        service.runtime.ensure_worker_state.assert_called_once_with()

    def test_remaining_component_boundary_and_cache_edges_are_explicit(self):
        response = MagicMock()
        response.json.return_value = {"ok": True}
        session = MagicMock()
        session.get.return_value = response
        service = SimpleNamespace(
            session=session,
            use_digest_auth=False,
            username="user",
            password="pass",
        )
        controller = ShellyIoController(service)

        self.assertEqual(controller.requests.request("http://example.invalid"), {"ok": True})
        session.get.assert_called_once_with(
            url="http://example.invalid",
            timeout=2.0,
            auth=("user", "pass"),
        )
        with self.assertRaisesRegex(TypeError, "must expose get"):
            controller.request_with_session(object(), "http://example.invalid")

        self.assertIsNone(controller.capabilities._phase_state_value(explicit=None, attribute="missing"))
        empty_state = ChargerState(enabled=None, current_amps=None, phase_selection=None)
        controller.runtime._store_runtime_charger_snapshot(empty_state, 10.0)
        self.assertIsNone(controller.runtime_cache.cached_charger_state(now=10.0))
        controller.runtime._store_runtime_charger_snapshot(
            ChargerState(enabled=True, current_amps=None, phase_selection=None),
            float("nan"),
        )
        self.assertIsNone(controller.runtime_cache.cached_charger_state(now=10.0))

        service._source_retry_after = {}
        controller.runtime.clear_charger_retry()
        controller.transport.remember_success(11.0, "recovered")
        service._shelly_consecutive_errors = "invalid"
        self.assertEqual(controller.transport.consecutive_errors(), 0)

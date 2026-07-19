# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_shelly_io_controller_support import *


class TestShellyIoControllerSecondary(ShellyIoControllerTestBase):
    def test_fetch_pm_status_split_without_meter_uses_fresh_charger_readback(self):
        switch_backend = SimpleNamespace(
            read_switch_state=MagicMock(
                return_value=SimpleNamespace(enabled=True, phase_selection="P1_P2_P3")
            ),
            capabilities=MagicMock(
                return_value=SimpleNamespace(supported_phase_selections=("P1", "P1_P2_P3"))
            ),
            set_enabled=MagicMock(),
        )
        charger_backend = SimpleNamespace(
            read_charger_state=MagicMock(
                return_value=SimpleNamespace(
                    enabled=False,
                    current_amps=15.5,
                    phase_selection="P1",
                    actual_current_amps=14.8,
                    power_w=10200.0,
                    energy_kwh=21.4,
                )
            )
        )
        service = SimpleNamespace(
            _backend_bundle=_runtime_bundle("split"),
            _meter_backend=None,
            _switch_backend=switch_backend,
            _charger_backend=charger_backend,
            supported_phase_selections=("P1",),
            requested_phase_selection="P1",
            active_phase_selection="P1",
            auto_shelly_soft_fail_seconds=10.0,
            time_now=MagicMock(return_value=100.0),
            virtual_mode=0,
            virtual_enable=1,
            virtual_startstop=1,
            virtual_set_current=16.0,
            _last_voltage=230.0,
        )

        controller = ShellyIoController(service)
        pm_status = controller.fetch_pm_status()

        self.assertTrue(pm_status["output"])
        self.assertEqual(pm_status["apower"], 10200.0)
        self.assertEqual(pm_status["current"], 14.8)
        self.assertEqual(pm_status["voltage"], 230.0)
        self.assertEqual(pm_status["aenergy"]["total"], 21400.0)
        self.assertEqual(pm_status["_phase_selection"], "P1_P2_P3")
        self.assertEqual(pm_status["_phase_powers_w"], (3400.0, 3400.0, 3400.0))
        self.assertEqual(
            pm_status["_phase_currents_a"],
            (14.8 / 3.0, 14.8 / 3.0, 14.8 / 3.0),
        )
        charger_backend.read_charger_state.assert_called_once_with()
        switch_backend.read_switch_state.assert_called_once_with()
        self.assertEqual(service.supported_phase_selections, ("P1", "P1_P2_P3"))
        self.assertEqual(service.active_phase_selection, "P1_P2_P3")

    def test_fetch_pm_status_split_without_meter_estimates_power_and_energy_from_fixed_phase_layout(self):
        charger_backend = SimpleNamespace(
            read_charger_state=MagicMock(
                return_value=SimpleNamespace(
                    enabled=True,
                    current_amps=15.0,
                    phase_selection="P1_P2_P3",
                    actual_current_amps=15.0,
                    power_w=None,
                    energy_kwh=None,
                    status_text="charging",
                )
            ),
            settings=SimpleNamespace(supported_phase_selections=("P1_P2_P3",)),
        )
        service = SimpleNamespace(
            _backend_bundle=_runtime_bundle("split"),
            _meter_backend=None,
            _switch_backend=None,
            _charger_backend=charger_backend,
            supported_phase_selections=("P1",),
            requested_phase_selection="P1",
            active_phase_selection="P1",
            auto_shelly_soft_fail_seconds=10.0,
            time_now=MagicMock(return_value=100.0),
            virtual_mode=0,
            virtual_enable=1,
            virtual_startstop=1,
            virtual_set_current=16.0,
            _last_voltage=None,
        )

        controller = ShellyIoController(service)
        first_pm_status = controller.fetch_pm_status()

        self.assertEqual(first_pm_status["apower"], 10350.0)
        self.assertEqual(first_pm_status["current"], 15.0)
        self.assertEqual(first_pm_status["voltage"], 230.0)
        self.assertEqual(first_pm_status["aenergy"]["total"], 0.0)
        self.assertEqual(first_pm_status["_phase_selection"], "P1_P2_P3")
        self.assertEqual(service.supported_phase_selections, ("P1_P2_P3",))
        self.assertEqual(service.active_phase_selection, "P1_P2_P3")
        self.assertEqual(service._last_charger_estimate_source, "current-voltage-phase")
        self.assertEqual(service._last_charger_estimate_at, 100.0)

        service.time_now.return_value = 160.0
        second_pm_status = controller.fetch_pm_status()

        self.assertEqual(second_pm_status["apower"], 10350.0)
        self.assertEqual(second_pm_status["aenergy"]["total"], 172.5)

    def test_fetch_pm_status_split_without_meter_keeps_smartevse_connected_state_at_zero_estimate(self):
        charger_backend = SimpleNamespace(
            read_charger_state=MagicMock(
                return_value=SimpleNamespace(
                    enabled=True,
                    current_amps=16.0,
                    phase_selection="P1_P2_P3",
                    actual_current_amps=None,
                    power_w=None,
                    energy_kwh=None,
                    status_text="connected",
                )
            ),
            settings=SimpleNamespace(supported_phase_selections=("P1_P2_P3",)),
        )
        service = SimpleNamespace(
            _backend_bundle=_runtime_bundle("split"),
            _meter_backend=None,
            _switch_backend=None,
            _charger_backend=charger_backend,
            supported_phase_selections=("P1",),
            requested_phase_selection="P1",
            active_phase_selection="P1",
            auto_shelly_soft_fail_seconds=10.0,
            time_now=MagicMock(return_value=100.0),
            virtual_mode=0,
            virtual_enable=1,
            virtual_startstop=1,
            virtual_set_current=16.0,
            _last_voltage=None,
        )

        controller = ShellyIoController(service)
        pm_status = controller.fetch_pm_status()

        self.assertEqual(pm_status["apower"], 0.0)
        self.assertEqual(pm_status["current"], 0.0)
        self.assertEqual(pm_status["voltage"], 230.0)
        self.assertEqual(pm_status["aenergy"]["total"], 0.0)

    def test_fetch_pm_status_split_without_meter_uses_recent_cached_charger_state(self):
        switch_backend = SimpleNamespace(
            read_switch_state=MagicMock(return_value=SimpleNamespace(enabled=True, phase_selection="P1")),
            capabilities=MagicMock(return_value=SimpleNamespace(supported_phase_selections=("P1",))),
            set_enabled=MagicMock(),
        )
        charger_backend = SimpleNamespace(read_charger_state=MagicMock(side_effect=RuntimeError("charger down")))
        service = SimpleNamespace(
            _backend_bundle=_runtime_bundle("split"),
            _meter_backend=None,
            _switch_backend=switch_backend,
            _charger_backend=charger_backend,
            supported_phase_selections=("P1",),
            requested_phase_selection="P1",
            active_phase_selection="P1",
            auto_shelly_soft_fail_seconds=10.0,
            time_now=MagicMock(return_value=100.0),
            _last_voltage=230.0,
        )

        controller = ShellyIoController(service)
        service._readback_store.replace_charger(
            TimedChargerState(
                state=ChargerState(
                    enabled=True,
                    current_amps=16.0,
                    phase_selection="P1",
                    actual_current_amps=15.1,
                    power_w=3470.0,
                    energy_kwh=8.75,
                ),
                captured_at=95.0,
            )
        )
        pm_status = controller.fetch_pm_status()

        self.assertTrue(pm_status["output"])
        self.assertEqual(pm_status["apower"], 3470.0)
        self.assertEqual(pm_status["current"], 15.1)
        self.assertEqual(pm_status["aenergy"]["total"], 8750.0)
        service.runtime.mark_failure.assert_called_once_with("charger")
        service.runtime.warning_throttled.assert_called_once()

    def test_set_relay_uses_split_switch_backend(self):
        switch_backend = SimpleNamespace(set_enabled=MagicMock())
        service = SimpleNamespace(
            _backend_bundle=_runtime_bundle("split"),
            _switch_backend=switch_backend,
            rpc_call=MagicMock(),
        )

        controller = ShellyIoController(service)
        result = controller.set_relay(True)

        self.assertEqual(result, {"output": True})
        switch_backend.set_enabled.assert_called_once_with(True)
        service.rpc_call.assert_not_called()

    def test_set_relay_uses_split_charger_backend_when_no_switch_backend_exists(self):
        charger_backend = SimpleNamespace(set_enabled=MagicMock())
        service = SimpleNamespace(
            _backend_bundle=_runtime_bundle("split"),
            _switch_backend=None,
            _charger_backend=charger_backend,
            rpc_call=MagicMock(),
        )

        controller = ShellyIoController(service)
        result = controller.set_relay(False)

        self.assertEqual(result, {"output": False})
        charger_backend.set_enabled.assert_called_once_with(False)
        service.rpc_call.assert_not_called()

    def test_set_phase_selection_updates_switch_and_charger_backends(self):
        switch_backend = SimpleNamespace(
            set_phase_selection=MagicMock(),
            capabilities=MagicMock(
                return_value=SimpleNamespace(
                    supported_phase_selections=("P1", "P1_P2"),
                    requires_charge_pause_for_phase_change=True,
                )
            ),
        )
        charger_backend = SimpleNamespace(set_phase_selection=MagicMock())
        service = SimpleNamespace(
            _switch_backend=switch_backend,
            _charger_backend=charger_backend,
            supported_phase_selections=("P1",),
            requested_phase_selection="P1",
            active_phase_selection="P1",
        )

        controller = ShellyIoController(service)
        applied = controller.set_phase_selection("P1_P2")

        self.assertEqual(applied, "P1_P2")
        switch_backend.set_phase_selection.assert_called_once_with("P1_P2")
        charger_backend.set_phase_selection.assert_called_once_with("P1_P2")
        self.assertEqual(service.supported_phase_selections, ("P1", "P1_P2"))
        self.assertEqual(service.requested_phase_selection, "P1_P2")
        self.assertEqual(service.active_phase_selection, "P1_P2")

    def test_set_phase_selection_skips_single_phase_charger_when_switch_handles_external_multi_phase(self):
        switch_backend = SimpleNamespace(
            set_phase_selection=MagicMock(),
            capabilities=MagicMock(
                return_value=SimpleNamespace(
                    supported_phase_selections=("P1", "P1_P2_P3"),
                    requires_charge_pause_for_phase_change=True,
                )
            ),
        )
        charger_backend = SimpleNamespace(
            set_phase_selection=MagicMock(),
            settings=SimpleNamespace(supported_phase_selections=("P1",)),
        )
        service = SimpleNamespace(
            _switch_backend=switch_backend,
            _charger_backend=charger_backend,
            supported_phase_selections=("P1",),
            requested_phase_selection="P1",
            active_phase_selection="P1",
        )

        controller = ShellyIoController(service)
        applied = controller.set_phase_selection("P1_P2_P3")

        self.assertEqual(applied, "P1_P2_P3")
        switch_backend.set_phase_selection.assert_called_once_with("P1_P2_P3")
        charger_backend.set_phase_selection.assert_not_called()
        self.assertEqual(service.supported_phase_selections, ("P1", "P1_P2_P3"))
        self.assertEqual(service.requested_phase_selection, "P1_P2_P3")
        self.assertEqual(service.active_phase_selection, "P1_P2_P3")

    def test_set_phase_selection_uses_native_charger_when_no_switch_backend_exists(self):
        charger_backend = SimpleNamespace(
            set_phase_selection=MagicMock(),
            settings=SimpleNamespace(supported_phase_selections=("P1", "P1_P2")),
        )
        service = SimpleNamespace(
            _switch_backend=None,
            _charger_backend=charger_backend,
            supported_phase_selections=("P1",),
            requested_phase_selection="P1",
            active_phase_selection="P1",
        )

        controller = ShellyIoController(service)
        applied = controller.set_phase_selection("P1_P2")

        self.assertEqual(applied, "P1_P2")
        charger_backend.set_phase_selection.assert_called_once_with("P1_P2")

    def test_fetch_pm_status_syncs_native_charger_readback_into_runtime_state(self):
        charger_backend = SimpleNamespace(
            read_charger_state=MagicMock(
                return_value=ChargerState(
                    enabled=False,
                    current_amps=12.5,
                    phase_selection="P1_P2",
                    actual_current_amps=12.3,
                    power_w=2830.0,
                    energy_kwh=7.25,
                )
            )
        )
        service = SimpleNamespace(
            host="192.168.178.76",
            pm_component="Switch",
            pm_id=0,
            _charger_backend=charger_backend,
            supported_phase_selections=("P1",),
            requested_phase_selection="P1",
            active_phase_selection="P1",
            virtual_mode=0,
            virtual_enable=1,
            virtual_startstop=1,
            virtual_set_current=16.0,
            time_now=MagicMock(return_value=100.0),
        )

        controller = ShellyIoController(service)
        controller.requests.fetch_pm_status_rpc = MagicMock(
            return_value={
                "output": True,
                "apower": 1800.0,
                "voltage": 230.0,
                "current": 7.8,
                "aenergy": {"total": 1000.0},
            }
        )
        pm_status = controller.fetch_pm_status()

        self.assertTrue(pm_status["output"])
        charger_backend.read_charger_state.assert_called_once_with()
        self.assertEqual(service.virtual_enable, 0)
        self.assertEqual(service.virtual_startstop, 0)
        self.assertEqual(service.virtual_set_current, 12.5)
        self.assertEqual(service._charger_target_current_amps, 12.5)
        self.assertEqual(service._charger_target_current_applied_at, 100.0)
        self.assertFalse(service._last_charger_state_enabled)
        self.assertEqual(service._last_charger_state_current_amps, 12.5)
        self.assertEqual(service._last_charger_state_phase_selection, "P1_P2")
        self.assertEqual(service._last_charger_state_actual_current_amps, 12.3)
        self.assertEqual(service._last_charger_state_power_w, 2830.0)
        self.assertEqual(service._last_charger_state_energy_kwh, 7.25)
        self.assertEqual(service._last_charger_state_at, 100.0)
        self.assertIsNone(service._last_charger_transport_reason)
        self.assertIsNone(service._last_charger_transport_source)
        self.assertIsNone(service._last_charger_transport_detail)
        self.assertIsNone(service._last_charger_transport_at)
        self.assertIsNone(service._charger_retry_reason)
        self.assertIsNone(service._charger_retry_source)
        self.assertIsNone(service._charger_retry_until)
        self.assertEqual(service.active_phase_selection, "P1_P2")
        service.runtime.mark_recovery.assert_called_once_with("charger", "Charger state reads recovered")

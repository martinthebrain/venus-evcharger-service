# SPDX-License-Identifier: GPL-3.0-or-later
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import call

from venus_evcharger.backend.shelly_contactor_switch import ShellyContactorSwitchBackend
from venus_evcharger.backend.shelly_meter import ShellyMeterBackend
from venus_evcharger.backend.shelly_support import phase_currents_for_selection, phase_powers_for_selection
from venus_evcharger.backend.shelly_switch import ShellySwitchBackend
from venus_evcharger.backend.switch_group import SwitchGroupBackend
from tests.venus_evcharger_backend_switch_support import SwitchBackendTestCaseBase, _FakeResponse, MagicMock, tempfile


class TestShellyWallboxBackendSwitchPrimary(SwitchBackendTestCaseBase):
    def test_set_enabled_uses_phase_map_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_switch_config(temp_dir)
            session = MagicMock()
            session.get.return_value = _FakeResponse({})
            backend = ShellySwitchBackend(self._service(session), config_path=config_path)

            backend.set_phase_selection("P1_P2")
            backend.set_enabled(True)

            urls = [call.kwargs["url"] for call in session.get.call_args_list]
            self.assertEqual(
                urls,
                [
                    "http://192.168.1.11/rpc/Switch.Set?id=0&on=true",
                    "http://192.168.1.11/rpc/Switch.Set?id=1&on=true",
                    "http://192.168.1.11/rpc/Switch.Set?id=2&on=false",
                ],
            )

    def test_shelly_switch_defaults_and_capabilities_are_explicit_contracts(self) -> None:
        backend = ShellySwitchBackend(self._service(MagicMock()))

        capabilities = backend.capabilities()

        self.assertEqual(capabilities.switching_mode, "direct")
        self.assertEqual(capabilities.supported_phase_selections, ("P1",))
        self.assertIs(capabilities.requires_charge_pause_for_phase_change, False)
        self.assertEqual(capabilities.max_direct_switch_power_w, 3680.0)
        self.assertEqual(backend._selected_phase_selection, "P1")

    def test_shelly_switch_rejects_meter_only_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            from pathlib import Path

            path = Path(temp_dir) / "meter-profile-switch.ini"
            path.write_text(
                "[Adapter]\nType=shelly_switch\nHost=192.168.1.11\nShellyProfile=pm1_meter_only\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "not valid for switch backends"):
                ShellySwitchBackend(self._service(MagicMock()), config_path=str(path))

    def test_shelly_switch_falls_back_to_first_supported_selection_when_configured_phase_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            from pathlib import Path

            path = Path(temp_dir) / "switch.ini"
            path.write_text(
                "[Adapter]\nType=shelly_switch\nHost=192.168.1.11\nComponent=Switch\nId=0\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2\n"
                "[Phase]\nMeasuredPhaseSelection=P1_P2_P3\n"
                "[PhaseMap]\nP1=0\nP1_P2=0,1\n",
                encoding="utf-8",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse({})
            backend = ShellySwitchBackend(self._service(session), config_path=str(path))

            self.assertEqual(backend._selected_phase_selection, "P1")
            backend.set_enabled(True)

            self.assertEqual(
                [call.kwargs["url"] for call in session.get.call_args_list],
                [
                    "http://192.168.1.11/rpc/Switch.Set?id=0&on=true",
                    "http://192.168.1.11/rpc/Switch.Set?id=1&on=false",
                ],
            )

    def test_shelly_switch_direct_rpc_helpers_are_exact_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_switch_config(temp_dir)
            backend = ShellySwitchBackend(self._service(MagicMock()), config_path=config_path)

            self.assertEqual(backend._all_switch_ids(), (0, 1, 2))
            self.assertEqual(backend._switch_ids_for_selection("P1_P2"), (0, 1))
            self.assertEqual(backend._switch_ids_for_selection(cast(Any, "P9")), (0,))

            original_settings = backend.settings
            backend.settings = replace(original_settings, phase_switch_targets={}, device_id=7)
            self.assertEqual(backend._all_switch_ids(), (7,))
            self.assertEqual(backend._switch_ids_for_selection("P1"), (7,))
            backend.settings = original_settings

            backend._rpc_call = MagicMock(return_value={})
            backend._set_switch_ids((2, 4), False)
            self.assertEqual(
                backend._rpc_call.call_args_list,
                [
                    call("Switch.Set", id=2, on=False),
                    call("Switch.Set", id=4, on=False),
                ],
            )

            backend._rpc_call = MagicMock(return_value={})
            backend._set_switch_ids((3,), True)
            self.assertEqual(backend._rpc_call.call_args_list, [call("Switch.Set", id=3, on=True)])

            with self.assertRaises(TypeError) as invalid_enabled:
                backend._set_switch_ids((3,), cast(Any, None))
            self.assertEqual(str(invalid_enabled.exception), "Shelly switch enabled state must be bool")

            backend._rpc_call = MagicMock(side_effect=[{"output": True}, {}, {"output": 0}])
            self.assertEqual(backend._switch_outputs(), {0: True, 1: False, 2: False})
            self.assertEqual(
                backend._rpc_call.call_args_list,
                [
                    call("Switch.GetStatus", id=0),
                    call("Switch.GetStatus", id=1),
                    call("Switch.GetStatus", id=2),
                ],
            )

            backend._rpc_call = MagicMock(return_value={})
            backend.set_enabled(False)
            self.assertEqual(
                backend._rpc_call.call_args_list,
                [
                    call("Switch.Set", id=0, on=False),
                    call("Switch.Set", id=1, on=False),
                    call("Switch.Set", id=2, on=False),
                ],
            )

    def test_shelly_switch_rejects_unsupported_phase_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_switch_config(temp_dir)
            backend = ShellySwitchBackend(self._service(MagicMock()), config_path=config_path)

            with self.assertRaisesRegex(ValueError, "Unsupported phase selection"):
                backend.set_phase_selection(cast(Any, "P9"))

    def test_read_switch_state_infers_phase_selection_from_active_channels(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_switch_config(temp_dir)
            session = MagicMock()
            session.get.side_effect = [
                _FakeResponse({"output": True}),
                _FakeResponse({"output": True}),
                _FakeResponse({"output": False}),
            ]
            backend = ShellySwitchBackend(self._service(session), config_path=config_path)

            state = backend.read_switch_state()

            self.assertTrue(state.enabled)
            self.assertEqual(state.phase_selection, "P1_P2")

    def test_shelly_switch_returns_last_selected_phase_when_no_channel_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_switch_config(temp_dir)
            session = MagicMock()
            session.get.side_effect = [
                _FakeResponse({"output": False}),
                _FakeResponse({"output": False}),
                _FakeResponse({"output": False}),
            ]
            backend = ShellySwitchBackend(self._service(session), config_path=config_path)
            backend.set_phase_selection("P1_P2")

            state = backend.read_switch_state()

            self.assertFalse(state.enabled)
            self.assertEqual(state.phase_selection, "P1_P2")

    def test_shelly_phase_distribution_helper_and_contactor_switch_backend(self) -> None:
        service = SimpleNamespace(
            phase="L1",
            host="192.168.1.11",
            pm_component="Switch",
            pm_id=0,
            max_current=16.0,
            _last_voltage=230.0,
            session=MagicMock(),
        )
        service.session.get.side_effect = [
            _FakeResponse({"output": True}),
            _FakeResponse({}),
        ]
        backend = ShellyContactorSwitchBackend(service)

        self.assertEqual(phase_powers_for_selection(9.0, "P1_P2_P3"), (3.0, 3.0, 3.0))
        self.assertEqual(phase_powers_for_selection(9.0, "P1", "L2"), (0.0, 9.0, 0.0))
        self.assertEqual(phase_powers_for_selection(9.0, "P1", "L3"), (0.0, 0.0, 9.0))
        self.assertTrue(backend.read_switch_state().enabled)
        backend.set_enabled(True)
        self.assertEqual(
            service.session.get.call_args_list[-1].kwargs["url"],
            "http://192.168.1.11/rpc/Switch.Set?id=0&on=true",
        )

    def test_shelly_meter_and_contactor_backend_cover_guard_paths(self) -> None:
        meter_service = SimpleNamespace(
            phase=None,
            host="192.168.1.20",
            pm_component="Switch",
            pm_id=0,
            max_current=None,
            _last_voltage=None,
            session=MagicMock(),
        )
        meter_service.session.get.return_value = _FakeResponse({"aenergy": {}, "apower": "bad", "voltage": "bad"})
        meter_backend = ShellyMeterBackend(meter_service)

        self.assertEqual(phase_powers_for_selection(9.0, "P1"), (9.0, 0.0, 0.0))
        self.assertIsNone(phase_currents_for_selection(None, "P1"))

        meter = meter_backend.read_meter()
        self.assertIsNone(meter.relay_on)
        self.assertEqual(meter.power_w, 0.0)
        self.assertEqual(meter.energy_kwh, 0.0)
        self.assertEqual(meter.phase_selection, "P1")

        switch_service = SimpleNamespace(
            phase="L1",
            host="192.168.1.11",
            pm_component="Switch",
            pm_id=0,
            max_current=None,
            _last_voltage=None,
            session=MagicMock(),
        )
        switch_service.session.get.return_value = _FakeResponse({"output": False})
        switch_backend = ShellyContactorSwitchBackend(switch_service)

        capabilities = switch_backend.capabilities()
        self.assertIsNone(capabilities.max_direct_switch_power_w)
        self.assertFalse(switch_backend.read_switch_state().enabled)

        switch_backend.set_phase_selection("P1")
        with self.assertRaisesRegex(ValueError, "Unsupported phase selection"):
            switch_backend.set_phase_selection("P1_P2")

    def test_shelly_meter_and_direct_switch_cover_positive_current_and_power_limit(self) -> None:
        meter_service = SimpleNamespace(
            phase="3P",
            host="192.168.1.20",
            pm_component="Switch",
            pm_id=0,
            max_current=16.0,
            _last_voltage=230.0,
            session=MagicMock(),
        )
        meter_service.session.get.return_value = _FakeResponse(
            {
                    "output": True,
                    "apower": 6900.0,
                    "current": 12.0,
                    "voltage": 230.0,
                    "aenergy": {"total": 1200.0},
                }
        )
        meter_backend = ShellyMeterBackend(meter_service)
        switch_backend = ShellySwitchBackend(meter_service)

        self.assertEqual(phase_currents_for_selection(12.0, "P1_P2_P3"), (4.0, 4.0, 4.0))
        self.assertEqual(meter_backend.read_meter().phase_currents_a, (4.0, 4.0, 4.0))
        self.assertEqual(switch_backend.capabilities().max_direct_switch_power_w, 3680.0)

    def test_shelly_switch_returns_selected_phase_when_active_channels_do_not_match_any_map(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_switch_config(temp_dir)
            session = MagicMock()
            session.get.side_effect = [
                _FakeResponse({"output": False}),
                _FakeResponse({"output": False}),
                _FakeResponse({"output": True}),
            ]
            backend = ShellySwitchBackend(self._service(session), config_path=config_path)
            backend.set_phase_selection("P1_P2")

            state = backend.read_switch_state()

            self.assertTrue(state.enabled)
            self.assertEqual(state.phase_selection, "P1_P2")

    def test_shelly_switch_uses_profile_defaults_for_single_channel_switches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._write_switch_config(temp_dir).replace("switch.ini", "profile-switch.ini")
            from pathlib import Path
            Path(path).write_text(
                "[Adapter]\nType=shelly_switch\nHost=192.168.1.33\nShellyProfile=switch_1ch_with_pm\n",
                encoding="utf-8",
            )
            session = MagicMock()
            session.get.side_effect = [_FakeResponse({"output": True}), _FakeResponse({})]
            backend = ShellySwitchBackend(self._service(session), config_path=str(path))

            state = backend.read_switch_state()
            backend.set_enabled(False)

            self.assertEqual(backend.settings.profile_name, "switch_1ch_with_pm")
            self.assertTrue(state.enabled)

    def test_switch_group_set_enabled_coordinates_mixed_child_backends(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_switch_group_config(temp_dir)
            session = MagicMock()
            session.get.return_value = _FakeResponse({})
            session.post.return_value = _FakeResponse({})
            backend = SwitchGroupBackend(self._service(session), config_path=config_path)

            backend.set_phase_selection("P1_P2")
            backend.set_enabled(True)

            self.assertEqual(len(session.post.call_args_list), 2)
            self.assertEqual(
                [call.kwargs["url"] for call in session.get.call_args_list],
                ["http://192.168.1.22/rpc/Switch.Set?id=0&on=true"],
            )

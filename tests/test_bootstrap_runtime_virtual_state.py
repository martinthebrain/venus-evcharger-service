# SPDX-License-Identifier: GPL-3.0-or-later
from collections import deque
from types import SimpleNamespace
import unittest

from venus_evcharger.bootstrap.runtime_virtual_state import (
    charger_backend_supported_phase_selections,
    configured_phase_selection,
    initialize_virtual_state,
    supported_phase_selections_for_service,
    switch_backend_supported_phase_selections,
)


class BootstrapRuntimeVirtualStateContracts(unittest.TestCase):
    def test_configured_phase_selection_is_constrained_to_supported_domain(self) -> None:
        supported = ("P1_P2", "P1_P2_P3")

        self.assertEqual(configured_phase_selection({}, supported), "P1_P2")
        self.assertEqual(configured_phase_selection({"PhaseSelection": "P1_P2_P3"}, supported), "P1_P2_P3")
        self.assertEqual(configured_phase_selection({"PhaseSelection": "P1"}, supported), "P1_P2")
        self.assertEqual(configured_phase_selection({"PhaseSelection": "invalid"}, supported), "P1_P2")

    def test_switch_backend_supported_phases_fallback_and_normalization(self) -> None:
        self.assertEqual(switch_backend_supported_phase_selections(SimpleNamespace()), ("P1",))
        failing = SimpleNamespace(_switch_backend=SimpleNamespace(capabilities=lambda: (_ for _ in ()).throw(RuntimeError())))
        self.assertEqual(switch_backend_supported_phase_selections(failing), ("P1",))
        missing_capability_attr = SimpleNamespace(_switch_backend=SimpleNamespace(capabilities=lambda: SimpleNamespace()))
        self.assertEqual(switch_backend_supported_phase_selections(missing_capability_attr), ("P1",))
        service = SimpleNamespace(
            _switch_backend=SimpleNamespace(
                capabilities=lambda: SimpleNamespace(supported_phase_selections=("P1", "P1_P2", "invalid"))
            )
        )
        self.assertEqual(switch_backend_supported_phase_selections(service), ("P1", "P1_P2", "P1"))

    def test_charger_backend_supported_phases_and_effective_backend_choice(self) -> None:
        charger_only = SimpleNamespace(
            _switch_backend=None,
            _charger_backend=SimpleNamespace(settings=SimpleNamespace(supported_phase_selections=("P1_P2", "P3"))),
        )
        switch_and_charger = SimpleNamespace(
            _switch_backend=SimpleNamespace(capabilities=lambda: SimpleNamespace(supported_phase_selections=("P1",))),
            _charger_backend=SimpleNamespace(settings=SimpleNamespace(supported_phase_selections=("P1_P2",))),
        )

        self.assertEqual(charger_backend_supported_phase_selections(charger_only), ("P1_P2", "P1"))
        self.assertEqual(supported_phase_selections_for_service(charger_only), ("P1_P2", "P1"))
        self.assertEqual(supported_phase_selections_for_service(switch_and_charger), ("P1",))
        self.assertEqual(charger_backend_supported_phase_selections(SimpleNamespace()), ("P1",))
        missing_settings_attr = SimpleNamespace(_charger_backend=SimpleNamespace(settings=SimpleNamespace()))
        self.assertEqual(charger_backend_supported_phase_selections(missing_settings_attr), ("P1",))
        missing_switch_attr = SimpleNamespace(
            _charger_backend=SimpleNamespace(settings=SimpleNamespace(supported_phase_selections=("P1_P2",)))
        )
        self.assertEqual(supported_phase_selections_for_service(missing_switch_attr), ("P1_P2",))
        missing_charger_attr = SimpleNamespace(_switch_backend=None)
        self.assertEqual(supported_phase_selections_for_service(missing_charger_attr), ("P1",))

    def test_initialize_virtual_state_uses_configured_values_and_resets_transient_state(self) -> None:
        service = SimpleNamespace(
            config={
                "DEFAULT": {
                    "Mode": "2",
                    "AutoStart": "0",
                    "StartStop": "1",
                    "Enable": "0",
                    "SetCurrent": "12.5",
                    "PhaseSelection": "P1_P2",
                }
            },
            max_current=16.0,
            _switch_backend=SimpleNamespace(
                capabilities=lambda: SimpleNamespace(supported_phase_selections=("P1", "P1_P2"))
            ),
            charging_started_at=123.0,
            auto_samples=deque([1, 2, 3]),
            learned_charge_power_watts=2100.0,
        )

        initialize_virtual_state(service, int)

        self.assertEqual(service.manual_override_until, 0.0)
        self.assertEqual(service.virtual_mode, 2)
        self.assertEqual(service.virtual_autostart, 0)
        self.assertEqual(service.virtual_startstop, 1)
        self.assertEqual(service.virtual_enable, 0)
        self.assertEqual(service.virtual_set_current, 12.5)
        self.assertEqual(service.supported_phase_selections, ("P1", "P1_P2"))
        self.assertEqual(service.requested_phase_selection, "P1_P2")
        self.assertEqual(service.active_phase_selection, "P1_P2")
        self.assertIsNone(service.charging_started_at)
        self.assertEqual(service.energy_at_start, 0.0)
        self.assertEqual(service.last_status, 0)
        self.assertIsNone(service.auto_start_condition_since)
        self.assertIsNone(service.auto_stop_condition_since)
        self.assertIsNone(service.auto_stop_condition_reason)
        self.assertEqual(list(service.auto_samples), [])
        self.assertIsNone(service._auto_high_soc_profile_active)
        self.assertIsNone(service._stop_smoothed_surplus_power)
        self.assertIsNone(service._stop_smoothed_grid_power)
        self.assertIsNone(service.learned_charge_power_watts)
        self.assertIsNone(service.learned_charge_power_updated_at)
        self.assertEqual(service.learned_charge_power_state, "unknown")
        self.assertIsNone(service.learned_charge_power_learning_since)
        self.assertEqual(service.learned_charge_power_sample_count, 0)
        self.assertIsNone(service.learned_charge_power_phase)
        self.assertIsNone(service.learned_charge_power_voltage)
        self.assertEqual(service.learned_charge_power_signature_mismatch_sessions, 0)
        self.assertIsNone(service.learned_charge_power_signature_checked_session_started_at)
        self.assertIsNone(service.relay_last_changed_at)
        self.assertIsNone(service.relay_last_off_at)
        self.assertIs(service._grid_recovery_required, False)
        self.assertIsNone(service._grid_recovery_since)
        self.assertIs(service._auto_mode_cutover_pending, False)
        self.assertIs(service._ignore_min_offtime_once, False)

    def test_initialize_virtual_state_uses_defaults_and_supported_phase_fallback(self) -> None:
        service = SimpleNamespace(
            config={"DEFAULT": {"StartStop": "0", "PhaseSelection": "P1_P2_P3"}},
            max_current=15.0,
            _switch_backend=SimpleNamespace(
                capabilities=lambda: SimpleNamespace(supported_phase_selections=("P1", "P1_P2"))
            ),
        )

        initialize_virtual_state(service, int)

        self.assertEqual(service.virtual_mode, 0)
        self.assertEqual(service.virtual_autostart, 1)
        self.assertEqual(service.virtual_startstop, 0)
        self.assertEqual(service.virtual_enable, 0)
        self.assertEqual(service.virtual_set_current, 15.0)
        self.assertEqual(service.requested_phase_selection, "P1")
        self.assertEqual(service.active_phase_selection, "P1")

        missing_phase_service = SimpleNamespace(
            config={"DEFAULT": {}},
            max_current=14.0,
            _switch_backend=SimpleNamespace(
                capabilities=lambda: SimpleNamespace(supported_phase_selections=("P1", "P1_P2"))
            ),
        )
        initialize_virtual_state(missing_phase_service, int)
        self.assertEqual(missing_phase_service.requested_phase_selection, "P1")
        self.assertEqual(missing_phase_service.active_phase_selection, "P1")

        charger_first_service = SimpleNamespace(
            config={"DEFAULT": {}},
            max_current=13.0,
            _switch_backend=None,
            _charger_backend=SimpleNamespace(
                settings=SimpleNamespace(supported_phase_selections=("P1_P2", "P1_P2_P3"))
            ),
        )
        initialize_virtual_state(charger_first_service, int)
        self.assertEqual(charger_first_service.requested_phase_selection, "P1_P2")
        self.assertEqual(charger_first_service.active_phase_selection, "P1_P2")

        invalid_charger_phase_service = SimpleNamespace(
            config={"DEFAULT": {"PhaseSelection": "invalid"}},
            max_current=13.0,
            _switch_backend=None,
            _charger_backend=SimpleNamespace(
                settings=SimpleNamespace(supported_phase_selections=("P1_P2", "P1_P2_P3"))
            ),
        )
        initialize_virtual_state(invalid_charger_phase_service, int)
        self.assertEqual(invalid_charger_phase_service.requested_phase_selection, "P1_P2")
        self.assertEqual(invalid_charger_phase_service.active_phase_selection, "P1_P2")


if __name__ == "__main__":
    unittest.main()

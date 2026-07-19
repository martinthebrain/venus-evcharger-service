# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest
from types import SimpleNamespace

from venus_evcharger.controllers.state_restore import RuntimeStateRestorer
from venus_evcharger.controllers.state_restore_victron_ess import VictronEssRuntimeRestorer
from venus_evcharger.controllers.state_runtime_normalize import RuntimeStateNormalizer

from tests.venus_evcharger_test_fixtures import make_runtime_state_service


def _normalize_mode(value: object) -> int:
    return int(value) if isinstance(value, (int, float, str)) else 0


def _restorer(service: object) -> RuntimeStateRestorer:
    return RuntimeStateRestorer(
        service,
        _normalize_mode,
        RuntimeStateNormalizer(),
        VictronEssRuntimeRestorer(),
    )


class StateRestoreContractTests(unittest.TestCase):
    def test_victron_activation_mode_uses_payload_then_service_fallback(self) -> None:
        service = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_activation_mode="export_only"
        )
        controller = VictronEssRuntimeRestorer()

        for raw_value, expected in (
            (" ALWAYS ", "always"),
            ("export_only", "export_only"),
            ("above_reserve_band", "above_reserve_band"),
            ("export_and_above_reserve_band", "export_and_above_reserve_band"),
            ("unsupported", None),
        ):
            with self.subTest(raw_value=raw_value):
                self.assertEqual(
                    controller._victron_ess_balance_activation_mode(
                        {"activation_mode": raw_value}, service
                    ),
                    expected,
                )
        self.assertEqual(
            controller._victron_ess_balance_activation_mode({}, service),
            "export_only",
        )
        self.assertEqual(
            controller._victron_ess_balance_activation_mode({}, SimpleNamespace()),
            "always",
        )
        self.assertEqual(
            controller._victron_ess_balance_activation_mode(
                {"activation_mode": "   "}, SimpleNamespace()
            ),
            "always",
        )

    def test_basic_runtime_state_uses_payload_values_and_clears_one_shot_flag(self) -> None:
        service = make_runtime_state_service(
            virtual_mode=0,
            virtual_autostart=1,
            virtual_enable=1,
            virtual_startstop=0,
            manual_override_until=10.0,
            _auto_mode_cutover_pending=False,
            _ignore_min_offtime_once=True,
        )
        controller = _restorer(service)

        controller._restore_basic_runtime_state(
            service,
            {
                "mode": "2",
                "autostart": "0",
                "enable": "0",
                "startstop": "1",
                "manual_override_until": "123.5",
                "auto_mode_cutover_pending": "1",
            },
        )

        self.assertEqual(service.virtual_mode, 2)
        self.assertEqual(service.virtual_autostart, 0)
        self.assertEqual(service.virtual_enable, 0)
        self.assertEqual(service.virtual_startstop, 1)
        self.assertEqual(service.manual_override_until, 123.5)
        self.assertIs(service._auto_mode_cutover_pending, True)
        self.assertIs(service._ignore_min_offtime_once, False)

        service.virtual_autostart = 1
        service.virtual_enable = 1
        controller._restore_basic_runtime_state(
            service,
            {"autostart": object(), "enable": object()},
        )
        self.assertEqual(service.virtual_autostart, 1)
        self.assertEqual(service.virtual_enable, 1)

    def test_basic_runtime_state_preserves_fields_missing_from_payload(self) -> None:
        service = make_runtime_state_service(
            virtual_mode=2,
            virtual_autostart=0,
            virtual_enable=0,
            virtual_startstop=1,
            manual_override_until=77.5,
            _auto_mode_cutover_pending=True,
            _ignore_min_offtime_once=True,
        )
        controller = _restorer(service)

        controller._restore_basic_runtime_state(service, {})

        self.assertEqual(service.virtual_mode, 2)
        self.assertEqual(service.virtual_autostart, 0)
        self.assertEqual(service.virtual_enable, 0)
        self.assertEqual(service.virtual_startstop, 1)
        self.assertEqual(service.manual_override_until, 77.5)
        self.assertIs(service._auto_mode_cutover_pending, True)
        self.assertIs(service._ignore_min_offtime_once, False)

    def test_learned_charge_power_restore_normalizes_every_persisted_field(self) -> None:
        service = make_runtime_state_service()
        controller = _restorer(service)

        controller._restore_learned_charge_power_state(
            service,
            {
                "learned_charge_power_watts": "2450.5",
                "learned_charge_power_updated_at": "900",
                "learned_charge_power_state": " STABLE ",
                "learned_charge_power_learning_since": "850",
                "learned_charge_power_sample_count": "7",
                "learned_charge_power_phase": "L2",
                "learned_charge_power_voltage": "231.5",
                "learned_charge_power_signature_mismatch_sessions": "3",
                "learned_charge_power_signature_checked_session_started_at": "901",
            },
            1000.0,
        )

        self.assertEqual(service.learned_charge_power_watts, 2450.5)
        self.assertEqual(service.learned_charge_power_updated_at, 900.0)
        self.assertEqual(service.learned_charge_power_state, "stable")
        self.assertEqual(service.learned_charge_power_learning_since, 850.0)
        self.assertEqual(service.learned_charge_power_sample_count, 7)
        self.assertEqual(service.learned_charge_power_phase, "L2")
        self.assertEqual(service.learned_charge_power_voltage, 231.5)
        self.assertEqual(service.learned_charge_power_signature_mismatch_sessions, 3)
        self.assertEqual(service.learned_charge_power_signature_checked_session_started_at, 901.0)

    def test_learned_charge_power_restore_preserves_missing_payload_fields(self) -> None:
        service = make_runtime_state_service(
            learned_charge_power_watts=2100.5,
            learned_charge_power_updated_at=800.0,
            learned_charge_power_state="learning",
            learned_charge_power_learning_since=700.0,
            learned_charge_power_sample_count=6,
            learned_charge_power_phase="L3",
            learned_charge_power_voltage=229.5,
            learned_charge_power_signature_mismatch_sessions=4,
            learned_charge_power_signature_checked_session_started_at=810.0,
        )
        controller = _restorer(service)

        controller._restore_learned_charge_power_state(service, {}, 1000.0)

        self.assertEqual(service.learned_charge_power_watts, 2100.5)
        self.assertEqual(service.learned_charge_power_updated_at, 800.0)
        self.assertEqual(service.learned_charge_power_state, "learning")
        self.assertEqual(service.learned_charge_power_learning_since, 700.0)
        self.assertEqual(service.learned_charge_power_sample_count, 6)
        self.assertEqual(service.learned_charge_power_phase, "L3")
        self.assertEqual(service.learned_charge_power_voltage, 229.5)
        self.assertEqual(service.learned_charge_power_signature_mismatch_sessions, 4)
        self.assertEqual(service.learned_charge_power_signature_checked_session_started_at, 810.0)

        minimal_service = SimpleNamespace()
        controller._restore_learned_charge_power_state(minimal_service, {}, 1000.0)
        self.assertIsNone(minimal_service.learned_charge_power_watts)
        self.assertIsNone(minimal_service.learned_charge_power_updated_at)
        self.assertEqual(minimal_service.learned_charge_power_state, "unknown")
        self.assertIsNone(minimal_service.learned_charge_power_learning_since)
        self.assertEqual(minimal_service.learned_charge_power_sample_count, 0)
        self.assertIsNone(minimal_service.learned_charge_power_phase)
        self.assertIsNone(minimal_service.learned_charge_power_voltage)
        self.assertEqual(minimal_service.learned_charge_power_signature_mismatch_sessions, 0)
        self.assertIsNone(minimal_service.learned_charge_power_signature_checked_session_started_at)

    def test_phase_switch_restore_preserves_complete_pending_transition(self) -> None:
        service = make_runtime_state_service()
        controller = _restorer(service)
        state: dict[str, object] = {
            "supported_phase_selections": ["P1", "P1_P2"],
            "requested_phase_selection": "P1_P2",
            "active_phase_selection": "P1",
            "phase_switch_pending_selection": "P1_P2",
            "phase_switch_state": "stabilizing",
            "phase_switch_requested_at": 900.0,
            "phase_switch_stable_until": 1015.0,
            "phase_switch_resume_relay": 1,
            "phase_switch_mismatch_counts": {"P1": "2", "P1_P2": "4"},
            "phase_switch_last_mismatch_selection": "P1",
            "phase_switch_last_mismatch_at": 910.0,
            "phase_switch_lockout_selection": "P1_P2",
            "phase_switch_lockout_reason": "relay-feedback",
            "phase_switch_lockout_at": 920.0,
            "phase_switch_lockout_until": 1200.0,
        }

        controller._restore_phase_switch_runtime_state(service, state, 1000.0)

        self.assertEqual(service.supported_phase_selections, ("P1", "P1_P2"))
        self.assertEqual(service.requested_phase_selection, "P1_P2")
        self.assertEqual(service.active_phase_selection, "P1")
        self.assertEqual(service._phase_switch_pending_selection, "P1_P2")
        self.assertEqual(service._phase_switch_state, "stabilizing")
        self.assertEqual(service._phase_switch_requested_at, 900.0)
        self.assertEqual(service._phase_switch_stable_until, 1015.0)
        self.assertIs(service._phase_switch_resume_relay, True)
        self.assertEqual(service._phase_switch_mismatch_counts, {"P1": 2, "P1_P2": 4})
        self.assertEqual(service._phase_switch_last_mismatch_selection, "P1")
        self.assertEqual(service._phase_switch_last_mismatch_at, 910.0)
        self.assertEqual(service._phase_switch_lockout_selection, "P1_P2")
        self.assertEqual(service._phase_switch_lockout_reason, "relay-feedback")
        self.assertEqual(service._phase_switch_lockout_at, 920.0)
        self.assertEqual(service._phase_switch_lockout_until, 1200.0)

    def test_phase_switch_restore_preserves_missing_payload_fields(self) -> None:
        service = make_runtime_state_service(
            supported_phase_selections=("P1", "P1_P2"),
            requested_phase_selection="P1_P2",
            active_phase_selection="P1",
            _phase_switch_pending_selection="P1_P2",
            _phase_switch_state="stabilizing",
            _phase_switch_requested_at=700.0,
            _phase_switch_stable_until=1010.0,
            _phase_switch_resume_relay=True,
            _phase_switch_mismatch_counts={"P1_P2": 5},
            _phase_switch_last_mismatch_selection="P1",
            _phase_switch_last_mismatch_at=710.0,
            _phase_switch_lockout_selection="P1_P2",
            _phase_switch_lockout_reason="preserved-reason",
            _phase_switch_lockout_at=720.0,
            _phase_switch_lockout_until=1300.0,
        )
        controller = _restorer(service)

        controller._restore_phase_switch_runtime_state(service, {}, 1000.0)

        self.assertEqual(service.supported_phase_selections, ("P1", "P1_P2"))
        self.assertEqual(service.requested_phase_selection, "P1_P2")
        self.assertEqual(service.active_phase_selection, "P1")
        self.assertEqual(service._phase_switch_pending_selection, "P1_P2")
        self.assertEqual(service._phase_switch_state, "stabilizing")
        self.assertEqual(service._phase_switch_requested_at, 700.0)
        self.assertEqual(service._phase_switch_stable_until, 1010.0)
        self.assertIs(service._phase_switch_resume_relay, True)
        self.assertEqual(service._phase_switch_mismatch_counts, {"P1_P2": 5})
        self.assertEqual(service._phase_switch_last_mismatch_selection, "P1")
        self.assertEqual(service._phase_switch_last_mismatch_at, 710.0)
        self.assertEqual(service._phase_switch_lockout_selection, "P1_P2")
        self.assertEqual(service._phase_switch_lockout_reason, "preserved-reason")
        self.assertEqual(service._phase_switch_lockout_at, 720.0)
        self.assertEqual(service._phase_switch_lockout_until, 1300.0)

        minimal_service = SimpleNamespace()
        controller._restore_phase_switch_runtime_state(minimal_service, {}, 1000.0)
        self.assertEqual(minimal_service.supported_phase_selections, ("P1",))
        self.assertEqual(minimal_service.requested_phase_selection, "P1")
        self.assertEqual(minimal_service.active_phase_selection, "P1")
        self.assertIsNone(minimal_service._phase_switch_pending_selection)
        self.assertIsNone(minimal_service._phase_switch_state)
        self.assertEqual(minimal_service._phase_switch_mismatch_counts, {})
        self.assertEqual(minimal_service._phase_switch_lockout_reason, "")

    def test_invalid_phase_switch_selections_fall_back_to_requested_selection(self) -> None:
        service = make_runtime_state_service()
        controller = _restorer(service)

        controller._restore_phase_switch_runtime_state(
            service,
            {
                "supported_phase_selections": ["P1", "P1_P2"],
                "requested_phase_selection": "P1_P2",
                "active_phase_selection": "invalid",
                "phase_switch_pending_selection": "invalid",
                "phase_switch_state": "stabilizing",
                "phase_switch_last_mismatch_selection": "invalid",
                "phase_switch_lockout_selection": "invalid",
            },
            1000.0,
        )

        self.assertEqual(service.active_phase_selection, "P1_P2")
        self.assertEqual(service._phase_switch_pending_selection, "P1_P2")
        self.assertEqual(service._phase_switch_last_mismatch_selection, "P1_P2")
        self.assertEqual(service._phase_switch_lockout_selection, "P1_P2")

        controller._restore_phase_switch_runtime_state(
            service,
            {
                "supported_phase_selections": ["P1_P2"],
                "requested_phase_selection": "invalid",
                "active_phase_selection": "P1_P2",
                "phase_switch_mismatch_counts": {"invalid": 3},
            },
            1000.0,
        )
        self.assertEqual(service.requested_phase_selection, "P1_P2")
        self.assertEqual(service._phase_switch_mismatch_counts, {"P1_P2": 3})

    def test_restore_rejects_timestamps_beyond_supplied_runtime_clock(self) -> None:
        service = make_runtime_state_service()
        controller = _restorer(service)

        controller._restore_phase_switch_runtime_state(
            service,
            {
                "phase_switch_pending_selection": "P1",
                "phase_switch_state": "stabilizing",
                "phase_switch_requested_at": 1001.1,
                "phase_switch_last_mismatch_at": 1001.1,
                "phase_switch_lockout_at": 1001.1,
            },
            1000.0,
        )
        controller._restore_contactor_runtime_state(
            service,
            {
                "contactor_fault_active_since": 1001.1,
                "contactor_lockout_at": 1001.1,
            },
            1000.0,
        )

        self.assertIsNone(service._phase_switch_requested_at)
        self.assertIsNone(service._phase_switch_last_mismatch_at)
        self.assertIsNone(service._phase_switch_lockout_at)
        self.assertIsNone(service._contactor_fault_active_since)
        self.assertIsNone(service._contactor_lockout_at)

    def test_incomplete_phase_switch_restore_clears_transient_transition(self) -> None:
        service = make_runtime_state_service()
        controller = _restorer(service)

        controller._restore_phase_switch_runtime_state(
            service,
            {
                "phase_switch_pending_selection": None,
                "phase_switch_state": "stabilizing",
                "phase_switch_requested_at": 900.0,
                "phase_switch_stable_until": 1015.0,
                "phase_switch_resume_relay": 1,
            },
            1000.0,
        )

        self.assertIsNone(service._phase_switch_pending_selection)
        self.assertIsNone(service._phase_switch_state)
        self.assertIsNone(service._phase_switch_requested_at)
        self.assertIsNone(service._phase_switch_stable_until)
        self.assertIs(service._phase_switch_resume_relay, False)

    def test_phase_switch_resume_defaults_false_when_runtime_field_is_absent(self) -> None:
        service = SimpleNamespace(
            supported_phase_selections=("P1",),
            requested_phase_selection="P1",
            active_phase_selection="P1",
        )
        controller = _restorer(service)

        controller._restore_phase_switch_runtime_state(
            service,
            {
                "phase_switch_pending_selection": "P1",
                "phase_switch_state": "stabilizing",
            },
            1000.0,
        )

        self.assertIs(service._phase_switch_resume_relay, False)

    def test_relay_and_contactor_restore_normalize_exact_payload_contract(self) -> None:
        service = make_runtime_state_service()
        controller = _restorer(service)
        state: dict[str, object] = {
            "relay_last_changed_at": 930.0,
            "relay_last_off_at": 940.0,
            "contactor_fault_counts": {
                "contactor-suspected-open": "2",
                "contactor-suspected-welded": 3,
                "ignored": 99,
            },
            "contactor_fault_active_reason": " contactor-suspected-open ",
            "contactor_fault_active_since": 950.0,
            "contactor_lockout_reason": "contactor-suspected-welded",
            "contactor_lockout_source": " feedback ",
            "contactor_lockout_at": 960.0,
        }

        controller._restore_relay_runtime_state(service, state, 1000.0)
        controller._restore_contactor_runtime_state(service, state, 1000.0)

        self.assertEqual(service.relay_last_changed_at, 930.0)
        self.assertEqual(service.relay_last_off_at, 940.0)
        self.assertEqual(
            service._contactor_fault_counts,
            {"contactor-suspected-open": 2, "contactor-suspected-welded": 3},
        )
        self.assertEqual(service._contactor_fault_active_reason, "contactor-suspected-open")
        self.assertEqual(service._contactor_fault_active_since, 950.0)
        self.assertEqual(service._contactor_lockout_reason, "contactor-suspected-welded")
        self.assertEqual(service._contactor_lockout_source, " feedback ")
        self.assertEqual(service._contactor_lockout_at, 960.0)

    def test_relay_and_contactor_restore_preserve_missing_payload_fields(self) -> None:
        service = make_runtime_state_service(
            relay_last_changed_at=600.0,
            relay_last_off_at=610.0,
            _contactor_fault_counts={"contactor-suspected-open": 7},
            _contactor_fault_active_reason="contactor-suspected-open",
            _contactor_fault_active_since=620.0,
            _contactor_lockout_reason="contactor-suspected-welded",
            _contactor_lockout_source="existing-source",
            _contactor_lockout_at=630.0,
        )
        controller = _restorer(service)

        controller._restore_relay_runtime_state(service, {}, 1000.0)
        controller._restore_contactor_runtime_state(service, {}, 1000.0)

        self.assertEqual(service.relay_last_changed_at, 600.0)
        self.assertEqual(service.relay_last_off_at, 610.0)
        self.assertEqual(service._contactor_fault_counts, {"contactor-suspected-open": 7})
        self.assertEqual(service._contactor_fault_active_reason, "contactor-suspected-open")
        self.assertEqual(service._contactor_fault_active_since, 620.0)
        self.assertEqual(service._contactor_lockout_reason, "contactor-suspected-welded")
        self.assertEqual(service._contactor_lockout_source, "existing-source")
        self.assertEqual(service._contactor_lockout_at, 630.0)

        minimal_service = SimpleNamespace()
        controller._restore_contactor_runtime_state(minimal_service, {}, 1000.0)
        self.assertEqual(minimal_service._contactor_fault_counts, {})
        self.assertIsNone(minimal_service._contactor_fault_active_reason)
        self.assertIsNone(minimal_service._contactor_fault_active_since)
        self.assertEqual(minimal_service._contactor_lockout_reason, "")
        self.assertEqual(minimal_service._contactor_lockout_source, "")
        self.assertIsNone(minimal_service._contactor_lockout_at)

    def test_contactor_normalizers_reject_wrong_shapes_and_reasons(self) -> None:
        controller = _restorer(make_runtime_state_service())

        self.assertEqual(controller._normalized_contactor_fault_counts(None), {})
        self.assertEqual(
            controller._normalized_contactor_fault_counts(
                {" contactor-suspected-open ": "4", "other": 8}
            ),
            {"contactor-suspected-open": 4},
        )
        self.assertEqual(
            controller._normalized_phase_switch_mismatch_counts({"P1": "5", "P1_P2": -2}, "P1"),
            {"P1": 5, "P1_P2": 0},
        )
        self.assertEqual(
            controller._normalized_phase_switch_mismatch_counts({"invalid": "bad"}, "P1_P2"),
            {"P1_P2": 0},
        )
        self.assertEqual(
            controller._normalized_contactor_fault_counts({"contactor-suspected-open": "bad"}),
            {"contactor-suspected-open": 0},
        )
        self.assertEqual(controller._normalized_contactor_fault_reason(" contactor-suspected-welded "), "contactor-suspected-welded")
        self.assertIsNone(controller._normalized_contactor_fault_reason("other"))
        self.assertIsNone(controller._normalized_contactor_fault_reason(None))

if __name__ == "__main__":  # pragma: no cover
    unittest.main()

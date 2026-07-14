# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact contracts for the compact operational state summary."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from venus_evcharger.controllers import state_summary as summary_module
from venus_evcharger.controllers.state import ServiceStateController


class TestStateSummaryContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.service = SimpleNamespace()
        self.controller = ServiceStateController(self.service, int)

    def test_scalar_formatters_preserve_boolean_text_and_finite_float_contracts(self) -> None:
        self.assertEqual(self.controller._summary_flag(0), "0")
        self.assertEqual(self.controller._summary_flag("yes"), "1")
        self.assertEqual(self.controller._summary_text(None, "fallback"), "fallback")
        self.assertEqual(self.controller._summary_text("  value  ", "fallback"), "value")
        self.assertEqual(self.controller._summary_text("   ", "fallback"), "fallback")
        self.assertEqual(self.controller._summary_float("12.34"), "12.3")
        self.assertEqual(self.controller._summary_float(float("inf")), "na")
        self.assertEqual(self.controller._summary_float(None, "missing"), "missing")

    def test_observed_phase_prefers_confirmed_snapshot_then_charger_state(self) -> None:
        self.assertEqual(
            self.controller._summary_observed_phase(
                SimpleNamespace(
                    _last_confirmed_pm_status={"_phase_selection": " P1_P2 "},
                    _last_charger_state_phase_selection="P1",
                )
            ),
            "P1_P2",
        )
        self.assertEqual(
            self.controller._summary_observed_phase(
                SimpleNamespace(
                    _last_confirmed_pm_status={"_phase_selection": ""},
                    _last_charger_state_phase_selection=" P1_P2_P3 ",
                )
            ),
            "P1_P2_P3",
        )
        self.assertEqual(self.controller._summary_observed_phase(SimpleNamespace()), "na")
        self.assertEqual(
            self.controller._summary_observed_phase(
                SimpleNamespace(_last_confirmed_pm_status={}, _last_charger_state_phase_selection="P1")
            ),
            "P1",
        )

    def test_phase_mismatch_lockout_and_effective_phase_contracts(self) -> None:
        self.assertEqual(
            self.controller._summary_phase_mismatch_active(SimpleNamespace(_phase_switch_mismatch_active=True)),
            "1",
        )
        self.assertEqual(
            self.controller._summary_phase_mismatch_active(SimpleNamespace(_last_health_reason="phase-switch-mismatch")),
            "1",
        )
        self.assertEqual(self.controller._summary_phase_mismatch_active(SimpleNamespace()), "0")

        svc = SimpleNamespace(_phase_switch_lockout_selection="P1_P2", _phase_switch_lockout_until=101.0)
        with patch.object(summary_module.time, "time", return_value=100.0):
            self.assertEqual(self.controller._summary_phase_lockout_active(svc), "1")
            self.assertEqual(self.controller._summary_phase_lockout_target(svc), "P1_P2")
        with patch.object(summary_module.time, "time", return_value=101.0):
            self.assertEqual(self.controller._summary_phase_lockout_active(svc), "0")
            self.assertEqual(self.controller._summary_phase_lockout_target(svc), "na")
        self.assertEqual(
            self.controller._summary_phase_lockout_active(SimpleNamespace(_phase_switch_lockout_until=200.0)),
            "0",
        )

        phase_svc = SimpleNamespace(
            supported_phase_selections=("P1", "P1_P2"),
            _phase_switch_lockout_selection="P1_P2",
            _phase_switch_lockout_until=110.0,
        )
        with patch.object(summary_module.time, "time", return_value=100.0):
            self.assertEqual(self.controller._summary_phase_supported_effective(phase_svc), "P1")
            self.assertEqual(self.controller._summary_phase_degraded_active(phase_svc), "1")
        phase_svc._phase_switch_lockout_until = 90.0
        with patch.object(summary_module.time, "time", return_value=100.0):
            self.assertEqual(self.controller._summary_phase_supported_effective(phase_svc), "P1,P1_P2")
            self.assertEqual(self.controller._summary_phase_degraded_active(phase_svc), "0")

        empty = SimpleNamespace()
        with (
            patch.object(summary_module.time, "time", return_value=123.0),
            patch.object(summary_module, "effective_supported_phase_selections", return_value=("P1",)) as effective,
        ):
            self.assertEqual(self.controller._summary_phase_supported_effective(empty), "P1")
        effective.assert_called_once_with(None, lockout_selection=None, lockout_until=None, now=123.0)

        with (
            patch.object(summary_module.time, "time", return_value=124.0),
            patch.object(summary_module, "normalize_phase_selection_tuple", return_value=("P1", "P1_P2")) as normalize,
            patch.object(summary_module, "effective_supported_phase_selections", return_value=("P1",)) as effective,
        ):
            self.assertEqual(self.controller._summary_phase_degraded_active(empty), "1")
        normalize.assert_called_once_with(None, ("P1",))
        effective.assert_called_once_with(
            ("P1", "P1_P2"),
            lockout_selection=None,
            lockout_until=None,
            now=124.0,
        )

    def test_switch_feedback_and_contactor_contracts_cover_absent_and_faulted_state(self) -> None:
        self.assertEqual(self.controller._summary_switch_feedback_closed(SimpleNamespace()), "na")
        self.assertEqual(self.controller._summary_switch_feedback_closed(SimpleNamespace(_last_switch_feedback_closed=True)), "1")
        self.assertEqual(self.controller._summary_switch_feedback_closed(SimpleNamespace(_last_switch_feedback_closed=False)), "0")
        self.assertEqual(self.controller._summary_switch_interlock_ok(SimpleNamespace()), "na")
        self.assertEqual(self.controller._summary_switch_interlock_ok(SimpleNamespace(_last_switch_interlock_ok=True)), "1")
        self.assertEqual(
            self.controller._summary_switch_feedback_mismatch(
                SimpleNamespace(_last_switch_feedback_closed=None, _last_health_reason="contactor-feedback-mismatch")
            ),
            "1",
        )
        self.assertEqual(
            self.controller._summary_switch_feedback_mismatch(
                SimpleNamespace(_last_switch_feedback_closed=False, _last_confirmed_pm_status={"output": True})
            ),
            "1",
        )
        self.assertEqual(
            self.controller._summary_switch_feedback_mismatch(
                SimpleNamespace(_last_switch_feedback_closed=False, _last_confirmed_pm_status=None)
            ),
            "0",
        )

        svc = SimpleNamespace(
            _contactor_lockout_reason="welded",
            _contactor_fault_active_reason="open",
            _contactor_fault_counts={"welded": 3, "open": 2},
            _last_health_reason="contactor-suspected-open",
        )
        self.assertEqual(self.controller._summary_contactor_count_reason(svc), "welded")
        self.assertEqual(self.controller._summary_contactor_fault_count(svc), "3")
        self.assertEqual(self.controller._summary_contactor_suspected_open(svc), "1")
        self.assertEqual(self.controller._summary_contactor_suspected_welded(svc), "0")
        self.assertEqual(self.controller._summary_contactor_lockout_active(svc), "1")
        self.assertEqual(self.controller._summary_contactor_lockout_reason(svc), "welded")
        self.assertEqual(self.controller._summary_contactor_fault_count(SimpleNamespace()), "0")
        self.assertEqual(
            self.controller._summary_contactor_fault_count(
                SimpleNamespace(_contactor_fault_counts={}, _contactor_lockout_reason="")
            ),
            "0",
        )
        self.assertEqual(self.controller._summary_contactor_lockout_reason(SimpleNamespace()), "na")
        active_only = SimpleNamespace(
            _contactor_lockout_reason="",
            _contactor_fault_active_reason="open",
            _contactor_fault_counts={"open": 2},
        )
        self.assertEqual(self.controller._summary_contactor_count_reason(active_only), "open")
        self.assertEqual(self.controller._summary_contactor_fault_count(active_only), "2")
        self.assertEqual(self.controller._summary_contactor_count_reason(SimpleNamespace()), "")
        self.assertEqual(
            self.controller._summary_contactor_suspected_welded(
                SimpleNamespace(_last_health_reason="contactor-suspected-welded")
            ),
            "1",
        )

    def test_fault_transport_retry_and_recovery_contracts(self) -> None:
        with patch.object(summary_module, "evse_fault_reason", side_effect=("fault", None, "fault", None)) as fault:
            self.assertEqual(self.controller._summary_fault_active(SimpleNamespace(_last_health_reason="x")), "1")
            self.assertEqual(self.controller._summary_fault_active(SimpleNamespace(_last_health_reason="y")), "0")
            self.assertEqual(self.controller._summary_fault_reason(SimpleNamespace(_last_health_reason="x")), "fault")
            self.assertEqual(self.controller._summary_fault_reason(SimpleNamespace(_last_health_reason="y")), "na")
        self.assertEqual([item.args for item in fault.call_args_list], [("x",), ("y",), ("x",), ("y",)])

        svc = SimpleNamespace()
        helpers = (
            ("_fresh_charger_transport_reason", "_summary_charger_transport_reason"),
            ("_fresh_charger_transport_source", "_summary_charger_transport_source"),
            ("_fresh_charger_retry_reason", "_summary_charger_retry_reason"),
            ("_fresh_charger_retry_source", "_summary_charger_retry_source"),
        )
        for imported_name, method_name in helpers:
            with self.subTest(method=method_name):
                with (
                    patch.object(summary_module.time, "time", return_value=50.0),
                    patch.object(summary_module, imported_name, return_value=" value ") as helper,
                ):
                    self.assertEqual(getattr(self.controller, method_name)(svc), "value")
                helper.assert_called_once_with(svc, 50.0)
                with (
                    patch.object(summary_module.time, "time", return_value=51.0),
                    patch.object(summary_module, imported_name, return_value=None),
                ):
                    self.assertEqual(getattr(self.controller, method_name)(svc), "na")
        self.assertEqual(self.controller._summary_recovery_active(SimpleNamespace(_last_auto_state="recovery")), "1")
        self.assertEqual(self.controller._summary_recovery_active(SimpleNamespace(_last_auto_state="idle")), "0")

    def test_scheduled_snapshot_contract_forwards_exact_policy_inputs(self) -> None:
        self.assertIsNone(self.controller._scheduled_snapshot(SimpleNamespace(virtual_mode=0), 100.0))
        svc = SimpleNamespace(
            virtual_mode=2,
            auto_month_windows={7: ("20:00", "06:00")},
            auto_scheduled_enabled_days=(0, 2),
            auto_scheduled_night_start_delay_seconds=123.0,
            auto_scheduled_latest_end_time="05:45",
        )
        with (
            patch.object(summary_module, "mode_uses_scheduled_logic", return_value=True) as uses,
            patch.object(summary_module, "scheduled_mode_snapshot", return_value="snapshot") as scheduled,
        ):
            self.assertEqual(self.controller._scheduled_snapshot(svc, 100.0), "snapshot")
        uses.assert_called_once_with(2)
        scheduled.assert_called_once_with(
            summary_module.datetime.fromtimestamp(100.0),
            {7: ("20:00", "06:00")},
            (0, 2),
            delay_seconds=123.0,
            latest_end_time="05:45",
        )

        empty = SimpleNamespace(virtual_mode=2)
        with (
            patch.object(summary_module, "mode_uses_scheduled_logic", return_value=True),
            patch.object(summary_module, "scheduled_mode_snapshot", return_value="default") as scheduled,
        ):
            self.assertEqual(self.controller._scheduled_snapshot(empty, 200.0), "default")
        scheduled.assert_called_once_with(
            summary_module.datetime.fromtimestamp(200.0),
            {},
            summary_module.DEFAULT_SCHEDULED_ENABLED_DAYS,
            delay_seconds=3600.0,
            latest_end_time="06:30",
        )

    def test_part_builders_preserve_field_names_order_and_values(self) -> None:
        svc = SimpleNamespace(
            virtual_mode=2,
            virtual_enable=1,
            virtual_startstop=0,
            virtual_autostart=1,
            _auto_mode_cutover_pending=True,
            _ignore_min_offtime_once=False,
        )
        self.assertEqual(
            self.controller._summary_mode_parts(svc),
            ("mode=2", "enable=1", "startstop=0", "autostart=1", "cutover=1", "ignore_offtime=0"),
        )
        self.assertEqual(
            self.controller._summary_mode_parts(SimpleNamespace()),
            ("mode=na", "enable=na", "startstop=na", "autostart=na", "cutover=0", "ignore_offtime=0"),
        )
        with patch.object(ServiceStateController, "_summary_flag", side_effect=("cutover", "offtime")) as flag:
            mode_parts = self.controller._summary_mode_parts(svc)
        self.assertEqual(mode_parts[-2:], ("cutover=cutover", "ignore_offtime=offtime"))
        self.assertEqual(
            flag.call_args_list,
            [unittest.mock.call(True), unittest.mock.call(False)],
        )

        helper_values = {
            "_summary_observed_phase": "observed",
            "_summary_phase_mismatch_active": "mismatch",
            "_summary_phase_lockout_active": "lockout",
            "_summary_phase_lockout_target": "target",
            "_summary_phase_supported_effective": "effective",
            "_summary_phase_degraded_active": "degraded",
        }
        phase_svc = SimpleNamespace(active_phase_selection="P1", requested_phase_selection="P1_P2", _phase_switch_state="stable")
        patchers = [patch.object(ServiceStateController, name, return_value=value) for name, value in helper_values.items()]
        helper_mocks = []
        for item in patchers:
            helper_mocks.append(item.start())
            self.addCleanup(item.stop)
        self.assertEqual(
            self.controller._summary_phase_parts(phase_svc),
            (
                "phase=P1",
                "phase_req=P1_P2",
                "phase_obs=observed",
                "phase_switch=stable",
                "phase_mismatch=mismatch",
                "phase_lockout=lockout",
                "phase_lockout_target=target",
                "phase_effective=effective",
                "phase_degraded=degraded",
            ),
        )
        for mock in helper_mocks:
            mock.assert_called_once_with(phase_svc)
        self.assertEqual(
            self.controller._summary_phase_parts(SimpleNamespace()),
            (
                "phase=na",
                "phase_req=na",
                "phase_obs=observed",
                "phase_switch=na",
                "phase_mismatch=mismatch",
                "phase_lockout=lockout",
                "phase_lockout_target=target",
                "phase_effective=effective",
                "phase_degraded=degraded",
            ),
        )

    def test_backend_status_and_builder_composition_are_exact(self) -> None:
        svc = SimpleNamespace(
            _charger_target_current_amps=7.5,
            _last_charger_state_status="charging",
            _last_charger_state_fault="none",
            _last_status_source="feedback",
            _last_auto_state="active",
        )
        with (
            patch.object(summary_module, "backend_mode_for_service", return_value="split") as backend_mode,
            patch.object(summary_module, "backend_type_for_service", side_effect=("meter", "switch", "charger")) as backend_type,
            patch.object(ServiceStateController, "_summary_charger_transport_reason", return_value="transport") as transport,
            patch.object(ServiceStateController, "_summary_charger_transport_source", return_value="transport-source") as transport_source,
            patch.object(ServiceStateController, "_summary_charger_retry_reason", return_value="retry") as retry,
            patch.object(ServiceStateController, "_summary_charger_retry_source", return_value="retry-source") as retry_source,
            patch.object(summary_module, "_charger_retry_remaining_seconds", return_value=12) as remaining,
        ):
            self.assertEqual(
                self.controller._summary_backend_parts(svc, 100.0),
                (
                    "backend=split",
                    "meter_backend=meter",
                    "switch_backend=switch",
                    "charger_backend=charger",
                    "charger_target=7.5",
                    "charger_status=charging",
                    "charger_fault=none",
                    "charger_transport=transport",
                    "charger_transport_source=transport-source",
                    "charger_retry=retry",
                    "charger_retry_source=retry-source",
                    "charger_retry_remaining=12",
                ),
            )
        backend_mode.assert_called_once_with(svc, "combined")
        self.assertEqual(
            backend_type.call_args_list,
            [
                unittest.mock.call(svc, "meter", "shelly_meter"),
                unittest.mock.call(svc, "switch", "shelly_contactor_switch"),
                unittest.mock.call(svc, "charger", ""),
            ],
        )
        remaining.assert_called_once_with(svc, 100.0)
        transport.assert_called_once_with(svc)
        transport_source.assert_called_once_with(svc)
        retry.assert_called_once_with(svc)
        retry_source.assert_called_once_with(svc)

        with (
            patch.object(summary_module, "backend_mode_for_service", return_value="combined"),
            patch.object(summary_module, "backend_type_for_service", side_effect=("meter", "switch", "")),
            patch.object(ServiceStateController, "_summary_charger_transport_reason", return_value="na"),
            patch.object(ServiceStateController, "_summary_charger_transport_source", return_value="na"),
            patch.object(ServiceStateController, "_summary_charger_retry_reason", return_value="na"),
            patch.object(ServiceStateController, "_summary_charger_retry_source", return_value="na"),
            patch.object(summary_module, "_charger_retry_remaining_seconds", return_value=0),
        ):
            empty_backend = self.controller._summary_backend_parts(SimpleNamespace(), 0.0)
        self.assertEqual(
            empty_backend,
            (
                "backend=combined",
                "meter_backend=meter",
                "switch_backend=switch",
                "charger_backend=na",
                "charger_target=na",
                "charger_status=na",
                "charger_fault=na",
                "charger_transport=na",
                "charger_transport_source=na",
                "charger_retry=na",
                "charger_retry_source=na",
                "charger_retry_remaining=0",
            ),
        )

        with (
            patch.object(ServiceStateController, "_summary_fault_active", return_value="1"),
            patch.object(ServiceStateController, "_summary_fault_reason", return_value="fault"),
        ):
            self.assertEqual(
                self.controller._summary_status_parts(svc),
                ("status_source=feedback", "fault=1", "fault_reason=fault", "auto_state=active"),
            )
        self.assertEqual(
            self.controller._summary_status_parts(SimpleNamespace()),
            ("status_source=unknown", "fault=0", "fault_reason=na", "auto_state=na"),
        )

        builder_names = (
            "_summary_mode_parts",
            "_summary_phase_parts",
            "_summary_contactor_parts",
            "_summary_backend_parts",
            "_summary_status_parts",
            "_summary_scheduled_parts",
            "_summary_tail_parts",
        )
        patchers = [patch.object(ServiceStateController, name, return_value=(str(index),)) for index, name in enumerate(builder_names)]
        mocks = [item.start() for item in patchers]
        for item in patchers:
            self.addCleanup(item.stop)
        self.assertEqual(self.controller._summary_parts(svc, "scheduled", 100.0), tuple(str(i) for i in range(7)))
        expected_args = (
            (svc,),
            (svc,),
            (svc,),
            (svc, 100.0),
            (svc,),
            ("scheduled",),
            (svc,),
        )
        for mock, args in zip(mocks, expected_args):
            mock.assert_called_once_with(*args)

    def test_scheduled_tail_composition_and_public_summary_are_exact(self) -> None:
        self.assertEqual(
            self.controller._summary_scheduled_snapshot_values(None),
            ("na", "na", "na", "0", "na"),
        )
        empty_snapshot = SimpleNamespace(
            state=None,
            reason="",
            target_day_label=None,
            night_boost_active=False,
            boost_until_text="",
        )
        self.assertEqual(
            self.controller._summary_scheduled_snapshot_values(empty_snapshot),
            ("na", "na", "na", "0", "na"),
        )
        snapshot = SimpleNamespace(
            state="active",
            reason="window",
            target_day_label="Monday",
            night_boost_active=True,
            boost_until_text="05:00",
        )
        self.assertEqual(
            self.controller._summary_scheduled_parts(snapshot),
            (
                "scheduled_state=active",
                "scheduled_reason=window",
                "scheduled_target_day=Monday",
                "scheduled_boost=1",
                "scheduled_boost_until=05:00",
            ),
        )
        self.assertEqual(
            self.controller._summary_tail_parts(SimpleNamespace(_last_auto_state="recovery", _last_health_reason="ok")),
            ("recovery=1", "health=ok"),
        )
        self.assertEqual(
            self.controller._summary_tail_parts(SimpleNamespace()),
            ("recovery=0", "health=na"),
        )
        with (
            patch.object(summary_module.time, "time", return_value=100.0),
            patch.object(self.controller, "_scheduled_snapshot", return_value="scheduled") as scheduled,
            patch.object(self.controller, "_summary_parts", return_value=("a=1", "b=2")) as parts,
        ):
            self.assertEqual(self.controller.state_summary(), "a=1 b=2")
        scheduled.assert_called_once_with(self.service, 100.0)
        parts.assert_called_once_with(self.service, "scheduled", 100.0)


if __name__ == "__main__":
    unittest.main()

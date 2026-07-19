# SPDX-License-Identifier: GPL-3.0-or-later
"""Behavioral edge contracts for update-layer runtime decisions."""

from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from venus_evcharger.update.offline_publish import OfflinePublisher
from venus_evcharger.update.relay_charger_current_targets import ChargerCurrentTargetPolicy
from venus_evcharger.update.relay_charger_transport import ChargerTransportTracker
from venus_evcharger.update.state import UpdateStateController


class UpdateEdgeContractTests(unittest.TestCase):
    def test_malformed_month_windows_fail_closed_before_schedule_evaluation(self) -> None:
        malformed_windows: tuple[object, ...] = (
            [],
            {1: "00:00-04:30"},
            {1: [(0, 0), (4, 30)]},
            {1: ((0,), (4, 30))},
            {1: (("00", 0), (4, 30))},
        )
        service = SimpleNamespace(
            virtual_mode=2,
            auto_schedule_timezone="UTC",
            auto_scheduled_enabled_days="Mon,Tue,Wed,Thu,Fri,Sat,Sun",
            auto_scheduled_night_start_delay_seconds=0.0,
            auto_scheduled_latest_end_time="04:30",
        )

        for month_windows in malformed_windows:
            with self.subTest(month_windows=month_windows):
                service.auto_month_windows = month_windows
                with patch(
                    "venus_evcharger.update.relay_charger_current_targets.scheduled_mode_snapshot",
                    return_value=SimpleNamespace(night_boost_active=False),
                ) as scheduled_snapshot:
                    self.assertFalse(ChargerCurrentTargetPolicy.scheduled_night_active(service, 0.0))
                self.assertEqual(scheduled_snapshot.call_args.args[1], {})

    def test_learned_target_rejects_non_policy_runtime_configuration(self) -> None:
        service = SimpleNamespace(
            learned_charge_power_state="stable",
            learned_charge_power_watts=2300.0,
            learned_charge_power_voltage=230.0,
            learned_charge_power_phase="L1",
            learned_charge_power_updated_at=90.0,
            auto_policy=object(),
        )

        with self.assertRaisesRegex(TypeError, "^auto_policy must be AutoPolicy$"):
            ChargerCurrentTargetPolicy.derived_learned_target(service, 100.0)

    def test_offline_health_defaults_to_configured_when_metadata_is_unavailable(self) -> None:
        self.assertEqual(OfflinePublisher._offline_health_reason(SimpleNamespace()), "shelly-offline")

    def test_clear_retry_tolerates_runtime_without_legacy_retry_mapping(self) -> None:
        service = SimpleNamespace(
            _charger_retry_reason="offline",
            _charger_retry_source="read",
            _charger_retry_until=30.0,
            _source_retry_after=(),
        )

        ChargerTransportTracker.clear_retry(service)

        self.assertIsNone(service._charger_retry_reason)
        self.assertIsNone(service._charger_retry_source)
        self.assertIsNone(service._charger_retry_until)
        self.assertEqual(service._source_retry_after, ())

    def test_virtual_state_defaults_initialize_missing_health_contract(self) -> None:
        ensure_observability_state = MagicMock()
        service = SimpleNamespace(
            runtime=SimpleNamespace(ensure_observability_state=ensure_observability_state),
        )
        controller = UpdateStateController(
            service,
            MagicMock(),
            MagicMock(),
            lambda reason: {"init": 17}[reason],
        )

        controller.ensure_virtual_state_defaults()

        ensure_observability_state.assert_called_once_with()
        self.assertEqual(service._last_health_reason, "init")
        self.assertEqual(service._last_health_code, 17)


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: GPL-3.0-or-later
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, mock_open, patch

sys.modules["vedbus"] = MagicMock()

from venus_evcharger.backend.shelly_io_split import ShellyBackendReadback
from venus_evcharger.controllers.state_summary import StateSummaryBuilder
from venus_evcharger.core.common_auto import _charger_transport_now
from venus_evcharger.publish.dbus_core import DbusPublishCore
from venus_evcharger.publish.dbus_shared import DbusPublishContext
from venus_evcharger.runtime.audit_fields import RuntimeAuditFields
from venus_evcharger.update.input_cache import InputCacheResolver
from venus_evcharger.update.offline_publish import OfflinePublisher
from venus_evcharger.update.readback_resolver import ReadbackResolver
from venus_evcharger.update.software_update_controller import SoftwareUpdateController


class TestShellyWallboxBranchMisc(unittest.TestCase):
    def test_summary_and_audit_helpers_cover_confirmed_phase_fallbacks(self) -> None:
        service = SimpleNamespace(
            _last_confirmed_pm_status={"_phase_selection": "   "},
            _last_charger_state_phase_selection="P1_P2",
            _contactor_lockout_reason="",
            _contactor_fault_active_reason="contactor-suspected-open",
            _contactor_fault_counts={"contactor-suspected-open": 2},
        )

        self.assertEqual(StateSummaryBuilder._summary_observed_phase(service), "P1_P2")
        self.assertEqual(RuntimeAuditFields.observed_phase(service), "P1_P2")
        self.assertEqual(RuntimeAuditFields.contactor_fault_count(service), 2)
        service._contactor_lockout_reason = "contactor-suspected-open"
        self.assertEqual(RuntimeAuditFields.contactor_fault_count(service), 2)

    def test_common_auto_and_update_state_helpers_cover_fallback_time_and_soft_fail_edges(self) -> None:
        service = SimpleNamespace(time_now=lambda: "bad")
        self.assertIsInstance(_charger_transport_now(service), float)

        update_service = SimpleNamespace(
            _worker_poll_interval_seconds=None,
            auto_shelly_soft_fail_seconds=0.0,
        )
        self.assertEqual(ReadbackResolver(SimpleNamespace(snapshot=MagicMock()), update_service).max_age_seconds(), 2.0)

    def test_dbus_core_group_failure_uses_runtime_port(self) -> None:
        runtime = SimpleNamespace(
            mark_failure=MagicMock(),
            warning_throttled=MagicMock(),
        )
        service = SimpleNamespace(
            _dbusservice={},
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
            runtime=runtime,
        )
        harness = DbusPublishCore(DbusPublishContext(service=service, age_seconds=lambda *_args: 0))

        harness._publish_group_failure("diag", ["/Path"])

        runtime.mark_failure.assert_called_once_with("dbus")
        runtime.warning_throttled.assert_called_once_with(
            "dbus-publish-diag-failed",
            1.0,
            "DBus publish group %s failed for paths %s",
            "diag",
            "/Path",
        )

    def test_input_cache_and_offline_publish_cover_remaining_age_fallbacks(self) -> None:
        self.assertFalse(InputCacheResolver._snapshot_input_too_old(10.0, 20.0, None))
        self.assertEqual(
            InputCacheResolver._discard_invalid_snapshot_input(5.0, 10.0, 20.0, None),
            (5.0, 10.0),
        )
        cached_service = SimpleNamespace(
            auto_input_cache_seconds=30.0,
            _last_value=None,
            _last_at=None,
        )
        self.assertEqual(
            InputCacheResolver.resolve_cached_input_value(
                cached_service,
                7.5,
                10.0,
                "_last_value",
                "_last_at",
                20.0,
            ),
            (7.5, False),
        )

        service = SimpleNamespace(
            _worker_poll_interval_seconds=0.0,
            relay_sync_timeout_seconds=0.0,
        )
        self.assertEqual(OfflinePublisher._offline_confirmed_relay_max_age_seconds(service), 2.0)

        service_for_cache = SimpleNamespace(
            auto_input_cache_seconds=30.0,
            auto_pv_poll_interval_seconds=10.0,
            auto_grid_poll_interval_seconds=10.0,
            auto_battery_poll_interval_seconds=10.0,
            _last_pv_value=None,
            _last_pv_at=None,
            _last_grid_value=None,
            _last_grid_at=None,
            _last_battery_soc_value=None,
            _last_battery_soc_at=None,
            _last_combined_battery_charge_power_w=None,
            _last_combined_battery_charge_power_at=None,
            _last_combined_battery_discharge_power_w=None,
            _last_combined_battery_discharge_power_at=None,
            _last_combined_battery_net_power_w=None,
            _last_combined_battery_net_power_at=None,
            _last_combined_battery_ac_power_w=None,
            _last_combined_battery_ac_power_at=None,
            _last_energy_learning_profiles={"old": 1},
            _auto_cached_inputs_used=False,
            _error_state={"cache_hits": 0},
        )
        harness = InputCacheResolver(service_for_cache)
        harness.resolve_auto_inputs(
            {
                "pv_power": 7.5,
                "pv_captured_at": 10.0,
                "battery_soc": 55.0,
                "battery_captured_at": 10.0,
                "grid_power": -10.0,
                "grid_captured_at": 10.0,
                "battery_learning_profiles": ["not-a-dict"],
            },
            now=20.0,
            auto_mode_active=True,
        )
        self.assertEqual(service_for_cache._last_energy_learning_profiles, {"old": 1})

        pm_status = {"apower": 1000.0}
        ShellyBackendReadback._apply_optional_pm_voltage(pm_status, None)
        self.assertEqual(pm_status, {"apower": 1000.0})

    def test_software_update_log_handle_skips_directory_creation_for_flat_path(self) -> None:
        opener = mock_open()
        with (
            patch("builtins.open", opener),
            patch("venus_evcharger.update.software_update_run.os.makedirs") as makedirs,
        ):
            handle = SoftwareUpdateController._software_update_log_handle("software-update.log")

        makedirs.assert_not_called()
        opener.assert_called_once_with("software-update.log", "ab")
        self.assertIs(handle, opener())

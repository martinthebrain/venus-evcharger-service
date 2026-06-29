# SPDX-License-Identifier: GPL-3.0-or-later
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.modules["vedbus"] = MagicMock()

from venus_evcharger.bootstrap.runtime import _ServiceBootstrapRuntimeMixin
from venus_evcharger.backend.shelly_io_split import ShellyIoSplit
from venus_evcharger.controllers.state_summary import _StateSummaryMixin
from venus_evcharger.core.common_auto import _charger_transport_now
from venus_evcharger.publish.dbus_core import _DbusPublishCore
from venus_evcharger.runtime.audit_fields import _RuntimeAuditFields
from venus_evcharger.update.input_cache import _UpdateCycleInputCacheMixin
from venus_evcharger.update.offline_publish import _UpdateCycleOfflineMixin
from venus_evcharger.update.software_update_support import _UpdateCycleSoftwareUpdateMixin
from venus_evcharger.update.state import _UpdateCycleStateMixin


class _BootstrapRuntimeHarness(_ServiceBootstrapRuntimeMixin):
    def __init__(self, service: object) -> None:
        self.service = service
        self._age_seconds = lambda *_args, **_kwargs: 0
        self._health_code = lambda _reason: 0
        self._normalize_mode = lambda value: int(value)
        self._mode_uses_auto_logic = lambda mode: bool(mode)
        self._phase_values = lambda *_args, **_kwargs: {}


class _DbusCoreHarness(_DbusPublishCore):
    def __init__(self, service: object) -> None:
        self.service = service


class _InputCacheHarness(_UpdateCycleInputCacheMixin):
    FUTURE_INPUT_TIMESTAMP_TOLERANCE_SECONDS = 5.0


class TestShellyWallboxBranchMisc(unittest.TestCase):
    def test_bootstrap_runtime_reuses_existing_state_controller(self) -> None:
        existing_state_controller = object()
        service = SimpleNamespace(_state_controller=existing_state_controller)
        resolved = SimpleNamespace(
            runtime=SimpleNamespace(topology_configured=False, primary_rpc_configured=False),
            selection="sel",
            meter="meter",
            switch="switch",
            charger="charger",
        )
        harness = _BootstrapRuntimeHarness(service)

        with (
            patch("venus_evcharger.bootstrap.runtime.RuntimeSupportController") as runtime_controller,
            patch("venus_evcharger.bootstrap.runtime.AutoDecisionController"),
            patch("venus_evcharger.bootstrap.runtime.DbusPublishController"),
            patch("venus_evcharger.bootstrap.runtime.ShellyIoController"),
            patch("venus_evcharger.bootstrap.runtime.build_service_backends", return_value=resolved),
            patch("venus_evcharger.bootstrap.runtime.ServiceStateController") as state_controller_cls,
            patch("venus_evcharger.bootstrap.runtime.DbusWriteController"),
            patch("venus_evcharger.bootstrap.runtime.AutoInputSupervisor"),
            patch("venus_evcharger.bootstrap.runtime.UpdateCycleController"),
        ):
            runtime_controller.return_value.initialize_runtime_support = MagicMock()
            harness.initialize_controllers()

        self.assertIs(service._state_controller, existing_state_controller)
        state_controller_cls.assert_not_called()

    def test_summary_and_audit_helpers_cover_confirmed_phase_fallbacks(self) -> None:
        service = SimpleNamespace(
            _last_confirmed_pm_status={"_phase_selection": "   "},
            _last_charger_state_phase_selection="P1_P2",
            _contactor_lockout_reason="",
            _contactor_fault_active_reason="contactor-suspected-open",
            _contactor_fault_counts={"contactor-suspected-open": 2},
        )

        self.assertEqual(_StateSummaryMixin._summary_observed_phase(service), "P1_P2")
        self.assertEqual(_RuntimeAuditFields._observed_phase_for_audit(service), "P1_P2")
        self.assertEqual(_RuntimeAuditFields._contactor_fault_count_for_audit(service), 2)
        service._contactor_lockout_reason = "contactor-suspected-open"
        self.assertEqual(_RuntimeAuditFields._contactor_fault_count_for_audit(service), 2)

    def test_common_auto_and_update_state_helpers_cover_fallback_time_and_soft_fail_edges(self) -> None:
        service = SimpleNamespace(_time_now=lambda: "bad")
        self.assertIsInstance(_charger_transport_now(service), float)

        update_service = SimpleNamespace(
            _worker_poll_interval_seconds=None,
            auto_shelly_soft_fail_seconds=0.0,
        )
        self.assertEqual(_UpdateCycleStateMixin._charger_state_max_age_seconds(update_service), 2.0)

    def test_dbus_core_group_failure_logs_without_mark_failure_hook(self) -> None:
        service = SimpleNamespace(
            _dbusservice={},
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
            _warning_throttled=MagicMock(),
        )
        harness = _DbusCoreHarness(service)

        harness._publish_group_failure("diag", ["/Path"], 100.0)

        service._warning_throttled.assert_called_once()

    def test_input_cache_and_offline_publish_cover_remaining_age_fallbacks(self) -> None:
        self.assertFalse(_InputCacheHarness._snapshot_input_too_old(10.0, 20.0, None))
        self.assertEqual(
            _InputCacheHarness._discard_invalid_snapshot_input(5.0, 10.0, 20.0, None),
            (5.0, 10.0),
        )
        cached_service = SimpleNamespace(
            auto_input_cache_seconds=30.0,
            _last_value=None,
            _last_at=None,
        )
        self.assertEqual(
            _InputCacheHarness.resolve_cached_input_value(
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
        self.assertEqual(_UpdateCycleOfflineMixin._offline_confirmed_relay_max_age_seconds(service), 2.0)

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
            _auto_cached_inputs_used=False,
            _error_state={"cache_hits": 0},
        )
        cache_owner = SimpleNamespace(service=service_for_cache)
        service_for_cache._service = SimpleNamespace(_last_energy_learning_profiles={"old": 1})
        harness = _InputCacheHarness()
        harness.service = service_for_cache
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
        self.assertEqual(service_for_cache._service._last_energy_learning_profiles, {"old": 1})

        pm_status = {"apower": 1000.0}
        ShellyIoSplit._apply_optional_pm_voltage(pm_status, None)
        self.assertEqual(pm_status, {"apower": 1000.0})

    def test_software_update_log_handle_skips_directory_creation_for_flat_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            old_cwd = os.getcwd()
            try:
                os.chdir(temp_dir)
                handle = _UpdateCycleSoftwareUpdateMixin._software_update_log_handle("software-update.log")
                handle.close()
                self.assertTrue(os.path.exists("software-update.log"))
            finally:
                os.chdir(old_cwd)

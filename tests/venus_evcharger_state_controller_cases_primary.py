# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
import os
import tempfile
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from venus_evcharger.auto.policy import AutoPolicy
from venus_evcharger.controllers.state import ServiceStateController
from venus_evcharger.controllers.state_runtime_normalize import RuntimeStateNormalizer
from venus_evcharger.controllers.state_runtime_overrides import RuntimeOverrideStore
from venus_evcharger.controllers.state_runtime_snapshot import RuntimeStateSnapshotBuilder
from venus_evcharger.controllers.state_validation import RuntimeConfigValidator
from tests.venus_evcharger_state_controller_support import (
    STATE_RUNTIME_PARSER_READ,
    STATE_SUMMARY_TIME,
    ServiceStateControllerTestBase,
)


def _with_backends_config(
    service: SimpleNamespace,
    *,
    mode: str,
    meter_type: str,
    switch_type: str,
    charger_type: str | None,
    host: str = "192.168.1.20",
) -> SimpleNamespace:
    parser = configparser.ConfigParser()
    parser.read_string(
        f"""
[DEFAULT]
Host={host}

[Backends]
Mode={mode}
MeterType={meter_type}
SwitchType={switch_type}
ChargerType={charger_type or ""}
"""
    )
    service.config = parser
    return service


class TestServiceStateControllerPrimary(ServiceStateControllerTestBase):
    def test_config_path_and_coercion_helpers_and_state_summary(self) -> None:
        service = _with_backends_config(SimpleNamespace(
            virtual_mode=1,
            virtual_enable=0,
            virtual_startstop=1,
            virtual_autostart=1,
            _auto_mode_cutover_pending=True,
            _ignore_min_offtime_once=False,
            auto_month_windows={4: ((7, 30), (19, 30))},
            auto_scheduled_enabled_days="Mon,Tue,Wed,Thu,Fri",
            auto_scheduled_night_start_delay_seconds=3600.0,
            auto_scheduled_latest_end_time="06:30",
            active_phase_selection="P1_P2",
            requested_phase_selection="P1_P2_P3",
            supported_phase_selections=("P1", "P1_P2", "P1_P2_P3"),
            _charger_target_current_amps=13.0,
            _last_charger_transport_reason="offline",
            _last_charger_transport_source="read",
            _last_charger_transport_at=100.0,
            _charger_retry_reason="offline",
            _charger_retry_source="read",
            _charger_retry_until=105.0,
            _last_confirmed_pm_status={"_phase_selection": "P1", "output": True},
            _phase_switch_mismatch_active=True,
            _phase_switch_lockout_selection="P1_P2_P3",
            _phase_switch_lockout_until=130.0,
            _last_switch_feedback_closed=False,
            _last_switch_interlock_ok=True,
            _contactor_fault_counts={"contactor-suspected-open": 2},
            _contactor_lockout_reason="contactor-suspected-open",
            _contactor_lockout_source="count-threshold",
            _contactor_lockout_at=90.0,
            _last_charger_state_status="charging",
            _last_charger_state_fault="none",
            _last_status_source="charger-fault",
            _last_auto_state="waiting",
            _last_health_reason="running",
        ), mode="split", meter_type="template_meter", switch_type="template_switch", charger_type="template_charger")
        controller = ServiceStateController(service, self._normalize_mode)
        with patch(STATE_SUMMARY_TIME, return_value=100.0):
            summary = controller.state_summary()

        self.assertTrue(controller.config_path().endswith("config.venus_evcharger.ini"))
        self.assertEqual(controller.coerce_runtime_int("7"), 7)
        self.assertEqual(controller.coerce_runtime_int(True, 3), 3)
        self.assertEqual(controller.coerce_runtime_int("bad", 3), 3)
        self.assertEqual(controller.coerce_runtime_float("7.5"), 7.5)
        self.assertEqual(controller.coerce_runtime_float(True, 3.5), 3.5)
        self.assertEqual(controller.coerce_runtime_float("bad", 3.5), 3.5)
        self.assertEqual(controller.coerce_runtime_float(float("nan"), 3.5), 3.5)
        self.assertEqual(controller.coerce_runtime_float(float("inf"), 3.5), 3.5)
        normalizer = RuntimeStateNormalizer()
        self.assertIsNone(normalizer.optional_float(None))
        self.assertEqual(normalizer.optional_float("7.5"), 7.5)
        self.assertIsNone(normalizer.optional_past_time(110.0, 100.0))
        self.assertEqual(normalizer.optional_past_time(100.5, 100.0), 100.5)
        self.assertIn("mode=1", summary)
        self.assertIn("phase=P1_P2", summary)
        self.assertIn("phase_req=P1_P2_P3", summary)
        self.assertIn("phase_obs=P1", summary)
        self.assertIn("phase_mismatch=1", summary)
        self.assertIn("phase_lockout=1", summary)
        self.assertIn("phase_lockout_target=P1_P2_P3", summary)
        self.assertIn("phase_effective=P1,P1_P2", summary)
        self.assertIn("phase_degraded=1", summary)
        self.assertIn("switch_feedback=0", summary)
        self.assertIn("switch_interlock=1", summary)
        self.assertIn("switch_feedback_mismatch=1", summary)
        self.assertIn("contactor_fault_count=2", summary)
        self.assertIn("contactor_suspected_open=0", summary)
        self.assertIn("contactor_suspected_welded=0", summary)
        self.assertIn("contactor_lockout=1", summary)
        self.assertIn("contactor_lockout_reason=contactor-suspected-open", summary)
        self.assertIn("backend=split", summary)
        self.assertIn("meter_backend=template_meter", summary)
        self.assertIn("switch_backend=template_switch", summary)
        self.assertIn("charger_backend=template_charger", summary)
        self.assertIn("charger_target=13.0", summary)
        self.assertIn("charger_status=charging", summary)
        self.assertIn("charger_fault=none", summary)
        self.assertIn("charger_transport=offline", summary)
        self.assertIn("charger_transport_source=read", summary)
        self.assertIn("charger_retry=offline", summary)
        self.assertIn("charger_retry_source=read", summary)
        self.assertIn("charger_retry_remaining=5", summary)
        self.assertIn("status_source=charger-fault", summary)
        self.assertIn("fault=0", summary)
        self.assertIn("fault_reason=na", summary)
        self.assertIn("auto_state=waiting", summary)
        self.assertIn("recovery=0", summary)
        self.assertIn("health=running", summary)

    def test_state_summary_marks_contactor_suspicions_from_health_reason(self) -> None:
        service = _with_backends_config(SimpleNamespace(
            virtual_mode=1,
            virtual_enable=1,
            virtual_startstop=1,
            virtual_autostart=1,
            _auto_mode_cutover_pending=False,
            _ignore_min_offtime_once=False,
            active_phase_selection="P1",
            requested_phase_selection="P1",
            supported_phase_selections=("P1",),
            _charger_target_current_amps=None,
            _last_confirmed_pm_status={"_phase_selection": "P1", "output": False},
            _phase_switch_mismatch_active=False,
            _phase_switch_lockout_selection=None,
            _phase_switch_lockout_until=None,
            _last_switch_feedback_closed=None,
            _last_switch_interlock_ok=None,
            _last_charger_state_status="",
            _last_charger_state_fault="",
            _last_status_source="switch-feedback",
            _last_auto_state="waiting",
            _last_health_reason="contactor-suspected-open",
            _contactor_fault_counts={},
            _contactor_lockout_reason="",
        ), mode="split", meter_type="template_meter", switch_type="template_switch", charger_type=None)
        controller = ServiceStateController(service, self._normalize_mode)
        with patch(STATE_SUMMARY_TIME, return_value=100.0):
            open_summary = controller.state_summary()

        self.assertIn("contactor_suspected_open=1", open_summary)
        self.assertIn("contactor_suspected_welded=0", open_summary)
        self.assertIn("contactor_lockout=0", open_summary)
        self.assertIn("fault=0", open_summary)
        self.assertIn("recovery=0", open_summary)

        service._last_health_reason = "contactor-suspected-welded"
        with patch(STATE_SUMMARY_TIME, return_value=100.0):
            welded_summary = controller.state_summary()

        self.assertIn("contactor_suspected_open=0", welded_summary)
        self.assertIn("contactor_suspected_welded=1", welded_summary)

        service._last_health_reason = "contactor-lockout-open"
        service._last_auto_state = "recovery"
        with patch(STATE_SUMMARY_TIME, return_value=100.0):
            lockout_summary = controller.state_summary()

        self.assertIn("fault=1", lockout_summary)
        self.assertIn("fault_reason=contactor-lockout-open", lockout_summary)
        self.assertIn("recovery=1", lockout_summary)

    def test_energy_runtime_state_ignores_non_mapping_worker_snapshot(self) -> None:
        service = SimpleNamespace(_get_worker_snapshot=lambda: ["not-a-mapping"])
        state = RuntimeStateSnapshotBuilder._energy_runtime_state(service)

        self.assertEqual(state["combined_battery_soc"], None)
        self.assertEqual(state["combined_battery_source_count"], 0)

    def test_runtime_override_load_and_validation_helpers_cover_error_paths(self) -> None:
        service = SimpleNamespace(
            _contactor_fault_counts={},
            auto_start_delay_seconds=-1.0,
            auto_policy=AutoPolicy(),
        )
        overrides = RuntimeOverrideStore(service, RuntimeStateNormalizer())

        self.assertEqual(overrides._read_runtime_override_values(""), {})
        with patch(STATE_RUNTIME_PARSER_READ, side_effect=RuntimeError("boom")):
            self.assertEqual(overrides._read_runtime_override_values("/tmp/runtime.ini"), {})
        self.assertIn("AutoDbusBackoffBaseSeconds", overrides.serialized())
        self.runtime_restorer(service)._restore_contactor_runtime_state(
            service,
            {"contactor_fault_counts": {"contactor-suspected-open": 2, "ignored": 9}},
            100.0,
        )
        self.assertEqual(service._contactor_fault_counts, {"contactor-suspected-open": 2})
        RuntimeConfigValidator._clamp_non_negative_float(
            service,
            "auto_start_delay_seconds",
        )
        self.assertEqual(service.auto_start_delay_seconds, 0.0)

    def test_flush_runtime_overrides_keeps_pending_snapshot_after_write_failure(self) -> None:
        service = SimpleNamespace(
            runtime_overrides_path="/tmp/runtime.ini",
            _runtime_overrides_pending_serialized='{"Mode":"1"}',
            _runtime_overrides_pending_values={"Mode": "1"},
            _runtime_overrides_pending_text="[RuntimeOverrides]\nMode=1\n",
            _runtime_overrides_pending_due_at=0.0,
            runtime_overrides_write_min_interval_seconds=2.0,
            time_now=lambda: 100.0,
        )
        controller = ServiceStateController(service, self._normalize_mode)
        overrides = controller.components.overrides
        self.assertIsInstance(overrides, RuntimeOverrideStore)

        with patch.object(
            overrides,
            "_write_runtime_overrides_payload",
            side_effect=RuntimeError("boom"),
        ):
            controller.flush_runtime_overrides(100.0)

        self.assertEqual(service._runtime_overrides_pending_due_at, 102.0)

    def test_state_summary_includes_scheduled_v2_diagnostics(self) -> None:
        service = _with_backends_config(SimpleNamespace(
            virtual_mode=2,
            virtual_enable=1,
            virtual_startstop=1,
            virtual_autostart=1,
            _auto_mode_cutover_pending=False,
            _ignore_min_offtime_once=False,
            active_phase_selection="P1",
            requested_phase_selection="P1",
            supported_phase_selections=("P1",),
            auto_month_windows={4: ((7, 30), (19, 30))},
            auto_scheduled_enabled_days="Mon,Tue,Wed,Thu,Fri",
            auto_scheduled_night_start_delay_seconds=3600.0,
            auto_scheduled_latest_end_time="06:30",
            _charger_target_current_amps=None,
            _last_confirmed_pm_status={"_phase_selection": "P1", "output": False},
            _phase_switch_mismatch_active=False,
            _phase_switch_lockout_selection=None,
            _phase_switch_lockout_until=None,
            _last_switch_feedback_closed=None,
            _last_switch_interlock_ok=None,
            _last_charger_state_status="",
            _last_charger_state_fault="",
            _last_status_source="scheduled",
            _last_auto_state="charging",
            _last_health_reason="scheduled-night-charge",
            _contactor_fault_counts={},
            _contactor_lockout_reason="",
        ), mode="split", meter_type="template_meter", switch_type="template_switch", charger_type=None)
        controller = ServiceStateController(service, self._normalize_mode)
        now = datetime(2026, 4, 20, 21, 0).timestamp()
        with patch(STATE_SUMMARY_TIME, return_value=now):
            summary = controller.state_summary()

        self.assertIn("scheduled_state=night-boost", summary)
        self.assertIn("scheduled_reason=night-boost-window", summary)
        self.assertIn("scheduled_target_day=Tue", summary)
        self.assertIn("scheduled_boost=1", summary)
        self.assertIn("scheduled_boost_until=2026-04-21 06:30", summary)

    def test_state_summary_fault_hierarchy_keeps_lockout_fault_visible_over_scheduled_and_retry_diagnostics(self) -> None:
        service = _with_backends_config(SimpleNamespace(
            virtual_mode=2,
            virtual_enable=1,
            virtual_startstop=1,
            virtual_autostart=1,
            _auto_mode_cutover_pending=False,
            _ignore_min_offtime_once=False,
            active_phase_selection="P1",
            requested_phase_selection="P1",
            supported_phase_selections=("P1", "P1_P2"),
            auto_month_windows={4: ((7, 30), (19, 30))},
            auto_scheduled_enabled_days="Mon,Tue,Wed,Thu,Fri",
            auto_scheduled_night_start_delay_seconds=3600.0,
            auto_scheduled_latest_end_time="06:30",
            _charger_target_current_amps=16.0,
            _last_confirmed_pm_status={"_phase_selection": "P1", "output": False},
            _phase_switch_mismatch_active=False,
            _phase_switch_lockout_selection="P1_P2",
            _phase_switch_lockout_until=datetime(2026, 4, 20, 21, 10).timestamp(),
            _last_switch_feedback_closed=False,
            _last_switch_interlock_ok=True,
            _last_charger_state_status="ready",
            _last_charger_state_fault="none",
            _last_status_source="contactor-lockout-open",
            _last_auto_state="recovery",
            _last_health_reason="contactor-lockout-open",
            _contactor_fault_counts={"contactor-suspected-open": 3},
            _contactor_lockout_reason="contactor-suspected-open",
            _contactor_lockout_source="count-threshold",
            _contactor_lockout_at=90.0,
            _charger_retry_reason="offline",
            _charger_retry_source="enable",
            _charger_retry_until=datetime(2026, 4, 20, 21, 5).timestamp(),
            _last_charger_transport_reason="offline",
            _last_charger_transport_source="enable",
            _last_charger_transport_at=datetime(2026, 4, 20, 20, 59).timestamp(),
        ), mode="split", meter_type="template_meter", switch_type="template_switch", charger_type="simpleevse_charger")
        controller = ServiceStateController(service, self._normalize_mode)
        now = datetime(2026, 4, 20, 21, 0).timestamp()
        with patch(STATE_SUMMARY_TIME, return_value=now):
            summary = controller.state_summary()

        self.assertIn("fault=1", summary)
        self.assertIn("fault_reason=contactor-lockout-open", summary)
        self.assertIn("recovery=1", summary)
        self.assertIn("scheduled_state=night-boost", summary)
        self.assertIn("scheduled_boost=1", summary)
        self.assertIn("charger_retry=offline", summary)
        self.assertIn("phase_lockout=1", summary)

    def test_state_summary_keeps_retry_and_phase_lockout_visible_without_promoting_to_fault(self) -> None:
        service = _with_backends_config(SimpleNamespace(
            virtual_mode=1,
            virtual_enable=1,
            virtual_startstop=1,
            virtual_autostart=1,
            _auto_mode_cutover_pending=False,
            _ignore_min_offtime_once=False,
            active_phase_selection="P1",
            requested_phase_selection="P1_P2",
            supported_phase_selections=("P1", "P1_P2"),
            _charger_target_current_amps=11.0,
            _last_confirmed_pm_status={"_phase_selection": "P1", "output": False},
            _phase_switch_mismatch_active=False,
            _phase_switch_lockout_selection="P1_P2",
            _phase_switch_lockout_until=250.0,
            _last_switch_feedback_closed=None,
            _last_switch_interlock_ok=None,
            _last_charger_state_status="ready",
            _last_charger_state_fault="none",
            _last_status_source="charger-status-ready",
            _last_auto_state="blocked",
            _last_health_reason="charger-transport-offline",
            _contactor_fault_counts={},
            _contactor_lockout_reason="",
            _charger_retry_reason="offline",
            _charger_retry_source="read",
            _charger_retry_until=240.0,
            _last_charger_transport_reason="offline",
            _last_charger_transport_source="read",
            _last_charger_transport_at=199.0,
        ), mode="split", meter_type="template_meter", switch_type="switch_group", charger_type="smartevse_charger")
        controller = ServiceStateController(service, self._normalize_mode)
        with patch(STATE_SUMMARY_TIME, return_value=200.0):
            summary = controller.state_summary()

        self.assertIn("fault=0", summary)
        self.assertIn("fault_reason=na", summary)
        self.assertIn("charger_transport=offline", summary)
        self.assertIn("charger_retry=offline", summary)
        self.assertIn("phase_lockout=1", summary)
        self.assertIn("phase_degraded=1", summary)
        self.assertIn("status_source=charger-status-ready", summary)

    def test_load_runtime_state_missing_file_and_load_config_success(self) -> None:
        service = SimpleNamespace(runtime_state_path="/tmp/does-not-exist.json")
        controller = ServiceStateController(service, self._normalize_mode)
        controller.load_runtime_state()

        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write("[DEFAULT]\nHost=192.168.1.20\n")
            config_path = handle.name
        def _cleanup_config() -> None:
            if os.path.exists(config_path):
                os.unlink(config_path)

        self.addCleanup(_cleanup_config)

        with patch.object(ServiceStateController, "config_path", return_value=config_path):
            config = controller.load_config()

        self.assertEqual(config["DEFAULT"]["Host"], "192.168.1.20")

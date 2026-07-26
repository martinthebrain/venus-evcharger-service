# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
import json
import tempfile
from pathlib import Path
from unittest.mock import mock_open, patch

from venus_evcharger.runtime.audit import RuntimeAuditLogger
from venus_evcharger.runtime.audit_fields import RuntimeAuditFields
from tests.venus_evcharger_runtime_support_support import RuntimeSupportController, RuntimeSupportTestCaseBase
from tests.venus_evcharger_test_fixtures import make_auto_metrics, make_runtime_support_service
from venus_evcharger.ipc.gateway_pressure import CachedGatewayPressurePolicy


def _with_backends_config(
    service,
    *,
    mode: str,
    meter_type: str,
    switch_type: str,
    charger_type: str | None,
    host: str = "192.168.1.20",
):
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


class TestRuntimeSupportControllerAudit(RuntimeSupportTestCaseBase):
    def test_warning_throttled_logs_once_per_interval(self) -> None:
        service = type("Service", (), {"_ensure_observability_state": lambda _self: None, "_warning_state": {}})()
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        controller.ensure_observability_state()
        with patch("venus_evcharger.runtime.health.time.time", side_effect=[100.0, 105.0, 131.0]), patch("venus_evcharger.runtime.health.logging.warning") as warning_mock:
            controller.warning_throttled("worker-failed", 30.0, "worker failed: %s", "boom")
            controller.warning_throttled("worker-failed", 30.0, "worker failed: %s", "boom")
            controller.warning_throttled("worker-failed", 30.0, "worker failed: %s", "boom")
        self.assertEqual(warning_mock.call_count, 2)

    def test_bucket_metric_returns_raw_value_when_step_is_not_positive(self) -> None:
        self.assertEqual(RuntimeAuditLogger._bucket_metric(1.23, step=0.0), 1.23)
        self.assertIsNone(RuntimeAuditLogger._bucket_metric(None, step=50.0))

    def test_write_auto_audit_event_variants_cover_repeat_pruning_and_details(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/auto-reasons.log"
            current_time = [1000.0]
            service = make_runtime_support_service(time_now=lambda: current_time[0], auto_audit_log_path=path, _last_auto_metrics=make_auto_metrics())
            controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
            controller.write_auto_audit_event("waiting-surplus", cached=False)
            current_time[0] = 1031.0
            controller.write_auto_audit_event("waiting-surplus", cached=False)
            with open(path, "r", encoding="utf-8") as handle:
                lines = handle.readlines()
            self.assertEqual(len(lines), 2)

    def test_auto_audit_repeat_interval_follows_gateway_backpressure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/auto-reasons.log"
            health_path = f"{temp_dir}/dbus-health.json"
            Path(health_path).write_text(
                json.dumps(
                    {
                        "captured_at": 1000.0,
                        "dbus_health": {"backpressure": {"state": "protective"}},
                    }
                ),
                encoding="utf-8",
            )
            current_time = [1000.0]
            service = make_runtime_support_service(
                time_now=lambda: current_time[0],
                auto_audit_log_path=path,
                gateway_health_path=health_path,
                gateway_pressure_policy=CachedGatewayPressurePolicy(
                    health_path,
                    now=lambda: current_time[0],
                    cache_seconds=0.0,
                ),
                _last_auto_metrics=make_auto_metrics(),
            )
            controller = RuntimeSupportController(service, self._age_zero, self._health_zero)

            controller.write_auto_audit_event("waiting-surplus", cached=False)
            current_time[0] = 1100.0
            controller.write_auto_audit_event("waiting-surplus", cached=False)
            current_time[0] = 1241.0
            controller.write_auto_audit_event("waiting-surplus", cached=False)

            with open(path, "r", encoding="utf-8") as handle:
                lines = handle.readlines()
            self.assertEqual(len(lines), 2)

    def test_write_auto_audit_event_includes_stop_reason_detail(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/auto-reasons.log"
            service = _with_backends_config(
                make_runtime_support_service(time_now=lambda: 1000.0, auto_audit_log_path=path, _last_pm_status={"output": True}, virtual_startstop=1, _charger_target_current_amps=13.0, _last_charger_transport_reason="offline", _last_charger_transport_source="read", _last_charger_transport_detail="Modbus slave 1 did not respond", _last_charger_transport_at=1000.0, _charger_retry_reason="offline", _charger_retry_source="read", _charger_retry_until=1005.0, _last_confirmed_pm_status={"_phase_selection": "P1_P2", "output": True}, _last_confirmed_pm_status_at=1000.0, _phase_switch_mismatch_active=True, _last_switch_feedback_closed=False, _last_switch_interlock_ok=True, auto_stop_condition_reason="auto-stop-surplus", _last_auto_metrics=make_auto_metrics(surplus=900.0, grid=-100.0, soc=61.0, profile="high-soc", start_threshold=1650.0, stop_threshold=800.0, learned_charge_power=2280.0, learned_charge_power_state="stable", threshold_scale=1.2, threshold_mode="adaptive", stop_alpha=0.15, stop_alpha_stage="volatile", surplus_volatility=520.0)),
                mode="split",
                meter_type="template_meter",
                switch_type="template_switch",
                charger_type="template_charger",
            )
            service.learned_charge_power_confidence = 0.92
            service.learned_charge_power_stability_score = 0.88
            service.learned_charge_power_reason = "learning-stable"
            controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
            controller.write_auto_audit_event("auto-stop", cached=False)
            with open(path, "r", encoding="utf-8") as handle:
                payload = handle.read()
            self.assertIn("detail=surplus", payload)
            self.assertIn("charger_backend=template_charger", payload)
            self.assertIn("learned_charge_power_confidence=0.920", payload)
            self.assertIn("learned_charge_power_stability=0.880", payload)
            self.assertIn("learned_charge_power_reason=learning-stable", payload)

    def test_write_auto_audit_event_retry_and_state_change_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/auto-reasons.log"
            current_time = [1000.0]
            service = make_runtime_support_service(time_now=lambda: current_time[0], auto_audit_log_path=path, _last_auto_metrics=make_auto_metrics())
            controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
            with patch("venus_evcharger.runtime.audit.os.makedirs", side_effect=PermissionError("nope")):
                controller.write_auto_audit_event("waiting-surplus", cached=False)
            self.assertIsNone(service._last_auto_audit_key)
            current_time[0] = 1005.0
            controller.write_auto_audit_event("waiting-surplus", cached=False)
            with open(path, "r", encoding="utf-8") as handle:
                payload = handle.read()
            self.assertIn("reason=waiting-surplus", payload)

    def test_auto_audit_cleanup_helpers_cover_remaining_branches(self) -> None:
        service = make_runtime_support_service(auto_audit_log_max_age_hours=0.0)
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)

        self.assertIsNone(controller.audit._auto_audit_cutoff_epoch(service, 1000.0))
        self.assertFalse(controller.audit._auto_audit_cleanup_due("", 1000.0))
        self.assertTrue(controller.audit._auto_audit_repeat_suppressed(("same",), ("same",), 0.0, 995.0, 1000.0))
        self.assertFalse(controller.audit._auto_audit_repeat_suppressed(("same",), ("other",), 30.0, 995.0, 1000.0))
        self.assertIsNone(controller.audit._auto_audit_reason_detail(service, "waiting"))

        with patch("builtins.open", side_effect=RuntimeError("boom")):
            self.assertIsNone(controller.audit._load_auto_audit_lines("/tmp/missing.log"))
        with patch("venus_evcharger.runtime.audit.write_text_atomically", side_effect=RuntimeError("boom")):
            controller.audit._write_pruned_auto_audit_lines("/tmp/prune.log", ["x"])

        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/auto-reasons.log"
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("100\told\n")
                handle.write("bad-line\n")
                handle.write("400\tnew\n")
            service = make_runtime_support_service(
                time_now=lambda: 1000.0,
                auto_audit_log=True,
                auto_audit_log_path=path,
                auto_audit_log_max_age_hours=0.1,
            )
            controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
            controller.audit._cleanup_auto_audit_log(1000.0)
            with open(path, "r", encoding="utf-8") as handle:
                payload = handle.read()
            self.assertIn("bad-line", payload)
            self.assertNotIn("100\told", payload)

    def test_auto_audit_event_covers_repeat_cleanup_reason_detail_and_empty_path(self) -> None:
        service = make_runtime_support_service(
            time_now=lambda: 1000.0,
            auto_audit_log=True,
            auto_audit_log_path="",
            auto_stop_condition_reason=object(),
            _last_auto_metrics=make_auto_metrics(),
        )
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)

        self.assertIsNone(controller.audit._auto_audit_reason_detail(service, "auto-stop"))

        with patch.object(controller.audit, "_cleanup_auto_audit_log") as cleanup_mock:
            service._last_auto_audit_key = controller.audit._auto_audit_key(service, "waiting-surplus", False)
            service._last_auto_audit_event_at = 995.0
            controller.write_auto_audit_event("waiting-surplus", cached=False)
        cleanup_mock.assert_called_once_with(1000.0)

        service._last_auto_audit_key = None
        service._last_auto_audit_event_at = None
        with patch.object(controller.audit, "_cleanup_auto_audit_log") as cleanup_mock:
            controller.write_auto_audit_event("waiting-surplus", cached=False)
        cleanup_mock.assert_called_once_with(1000.0)

    def test_auto_audit_cleanup_and_write_helpers_cover_cutoff_none_and_flat_paths(self) -> None:
        service = make_runtime_support_service(auto_audit_log_max_age_hours=0.0)
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)

        with patch.object(controller.audit, "_auto_audit_cleanup_due", return_value=True), patch.object(
            controller.audit,
            "_auto_audit_cutoff_epoch",
            return_value=None,
        ):
            controller.audit._cleanup_auto_audit_log(1000.0)

        with patch("venus_evcharger.runtime.audit.open", mock_open()) as open_mock:
            controller.audit._write_auto_audit_line("auto-reasons.log", "payload\n")
        open_mock.assert_called_once_with("auto-reasons.log", "a", encoding="utf-8")

    def test_runtime_audit_field_helpers_cover_lockout_feedback_and_fault_edges(self) -> None:
        service = make_runtime_support_service(
            time_now=lambda: 100.0,
            supported_phase_selections=("P1", "P1_P2"),
            _phase_switch_lockout_selection="",
            _phase_switch_lockout_until=120.0,
            _last_switch_feedback_closed=None,
            _last_switch_interlock_ok=None,
            _last_health_reason="contactor-feedback-mismatch",
            _contactor_fault_counts={"contactor-suspected-open": 2},
            _contactor_fault_active_reason="contactor-suspected-open",
        )
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)

        self.assertIsNone(controller.audit_fields.phase_lockout_target(service))
        self.assertFalse(controller.audit_fields.phase_degraded_active(service))
        self.assertTrue(controller.audit_fields.switch_feedback_mismatch(service))
        self.assertEqual(controller.audit_fields.contactor_fault_count(service), 2)

        service._phase_switch_lockout_selection = None
        with patch.object(RuntimeAuditFields, "phase_lockout_active", return_value=True):
            self.assertIsNone(controller.audit_fields.phase_lockout_target(service))

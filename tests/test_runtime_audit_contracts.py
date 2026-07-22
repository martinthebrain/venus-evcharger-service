# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from contextlib import ExitStack
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, call, mock_open, patch

from venus_evcharger.runtime.audit import RuntimeAuditLogger
from venus_evcharger.runtime.audit_fields import RuntimeAuditFields
from venus_evcharger.runtime.state_store import RuntimeStateStore


DISPLAY_FIELDS = (
    "profile",
    "start_threshold",
    "stop_threshold",
    "learned_charge_power",
    "learned_charge_power_state",
    "learned_charge_power_confidence",
    "learned_charge_power_stability",
    "learned_charge_power_reason",
    "threshold_scale",
    "threshold_mode",
    "backend_mode",
    "meter_backend",
    "switch_backend",
    "charger_backend",
    "charger_target",
    "charger_transport_reason",
    "charger_transport_source",
    "charger_retry_reason",
    "charger_retry_source",
    "phase_observed",
    "phase_mismatch",
    "phase_lockout_target",
    "phase_lockout",
    "phase_effective",
    "phase_degraded",
    "switch_feedback",
    "switch_interlock",
    "switch_feedback_mismatch",
    "contactor_fault_count",
    "contactor_lockout_reason",
    "contactor_lockout",
    "fault",
    "fault_reason",
    "recovery",
    "stop_alpha",
    "stop_alpha_stage",
    "surplus_volatility",
    "surplus",
    "grid",
    "soc",
)


def _audit(service: SimpleNamespace) -> RuntimeAuditLogger:
    state_store = RuntimeStateStore(service)
    return RuntimeAuditLogger(service, RuntimeAuditFields(), state_store)


class RuntimeAuditContractTests(unittest.TestCase):
    def test_auto_audit_key_has_exact_order_sources_and_buckets(self) -> None:
        service = SimpleNamespace(
            virtual_mode=2,
            virtual_enable=1,
            virtual_autostart=1,
            _last_auto_state="raw-state",
            _last_auto_state_code=99,
        )
        metrics = {
            "profile": "profile-value",
            "stop_alpha_stage": "stage-value",
            "threshold_mode": "threshold-value",
            "learned_charge_power_state": "learning-state",
            "learned_charge_power_confidence": 0.91,
            "learned_charge_power_stability_score": 0.82,
            "learned_charge_power_reason": "learning-reason",
            "start_threshold": 1201.0,
            "stop_threshold": 801.0,
            "threshold_scale": 1.12,
            "switch_feedback": 1,
            "switch_interlock": 0,
            "switch_feedback_mismatch": 1,
            "contactor_fault_count": 3,
            "contactor_lockout_reason": "lockout-reason",
            "contactor_lockout": 1,
            "fault": 1,
            "fault_reason": "fault-reason",
            "recovery": 1,
        }
        bucket = MagicMock(side_effect=lambda value, *, step: (value, step))
        backend = MagicMock(
            side_effect=lambda _svc, attribute, default: f"{attribute}:{default}"
        )
        with (
            patch.object(RuntimeAuditLogger, "_normalized_auto_audit_metrics", return_value=metrics) as normalized,
            patch("venus_evcharger.runtime.audit.normalized_auto_state_pair", return_value=("auto-state", 7)) as state,
            patch.object(RuntimeAuditLogger, "_auto_audit_reason_detail", return_value="detail") as detail,
            patch.object(RuntimeAuditLogger, "_relay_state_for_audit", return_value=1) as relay,
            patch.object(RuntimeAuditLogger, "_bucket_metric", bucket),
            patch.object(RuntimeAuditFields, "backend_value", backend),
            patch.object(RuntimeAuditFields, "charger_target", return_value=13.0) as charger_target,
            patch.object(RuntimeAuditFields, "charger_transport_reason", return_value="transport-reason") as transport_reason,
            patch.object(RuntimeAuditFields, "charger_transport_source", return_value="transport-source") as transport_source,
            patch.object(RuntimeAuditFields, "charger_retry_reason", return_value="retry-reason") as retry_reason,
            patch.object(RuntimeAuditFields, "charger_retry_source", return_value="retry-source") as retry_source,
            patch.object(RuntimeAuditFields, "observed_phase", return_value="P1_P2") as observed_phase,
            patch.object(RuntimeAuditFields, "phase_mismatch_active", return_value=True) as mismatch,
            patch.object(RuntimeAuditFields, "phase_lockout_target", return_value="P1") as lockout_target,
            patch.object(RuntimeAuditFields, "phase_lockout_active", return_value=True) as lockout,
            patch.object(RuntimeAuditFields, "phase_supported_effective", return_value="P1,P1_P2") as effective,
            patch.object(RuntimeAuditFields, "phase_degraded_active", return_value=False) as degraded,
        ):
            result = _audit(service)._auto_audit_key(service, "reason", True)

        self.assertEqual(
            result,
            (
                "reason",
                "detail",
                1,
                1,
                2,
                1,
                1,
                "auto-state",
                "profile-value",
                "stage-value",
                "threshold-value",
                "learning-state",
                (0.91, 0.05),
                (0.82, 0.05),
                "learning-reason",
                (1201.0, 50.0),
                (801.0, 50.0),
                (1.12, 0.02),
                "backend_mode:combined",
                "meter_backend_type:shelly_meter",
                "switch_backend_type:shelly_contactor_switch",
                "charger_backend_type:na",
                (13.0, 1.0),
                "transport-reason",
                "transport-source",
                "retry-reason",
                "retry-source",
                "P1_P2",
                1,
                "P1",
                1,
                "P1,P1_P2",
                0,
                1,
                0,
                1,
                3,
                "lockout-reason",
                1,
                1,
                "fault-reason",
                1,
            ),
        )
        normalized.assert_called_once_with(service)
        state.assert_called_once_with("raw-state", 99)
        detail.assert_called_once_with(service, "reason")
        relay.assert_called_once_with(service)
        self.assertEqual(
            backend.call_args_list,
            [
                call(service, "backend_mode", "combined"),
                call(service, "meter_backend_type", "shelly_meter"),
                call(service, "switch_backend_type", "shelly_contactor_switch"),
                call(service, "charger_backend_type", "na"),
            ],
        )
        for helper in (
            charger_target,
            transport_reason,
            transport_source,
            retry_reason,
            retry_source,
            observed_phase,
            mismatch,
            lockout_target,
            lockout,
            effective,
            degraded,
        ):
            helper.assert_called_once_with(service)

    def test_auto_audit_key_uses_zero_defaults_for_missing_optional_metrics(self) -> None:
        service = SimpleNamespace(
            virtual_mode=0,
            virtual_enable=0,
            virtual_autostart=0,
            _last_auto_state="idle",
            _last_auto_state_code=0,
        )
        with (
            patch.object(RuntimeAuditLogger, "_normalized_auto_audit_metrics", return_value={}),
            patch("venus_evcharger.runtime.audit.normalized_auto_state_pair", return_value=("idle", 0)),
            patch.object(RuntimeAuditLogger, "_auto_audit_reason_detail", return_value=None),
            patch.object(RuntimeAuditLogger, "_relay_state_for_audit", return_value=0),
            patch.object(RuntimeAuditFields, "backend_value", return_value="backend"),
            patch.object(RuntimeAuditFields, "charger_target", return_value=None),
            patch.object(RuntimeAuditFields, "charger_transport_reason", return_value=None),
            patch.object(RuntimeAuditFields, "charger_transport_source", return_value=None),
            patch.object(RuntimeAuditFields, "charger_retry_reason", return_value=None),
            patch.object(RuntimeAuditFields, "charger_retry_source", return_value=None),
            patch.object(RuntimeAuditFields, "observed_phase", return_value=None),
            patch.object(RuntimeAuditFields, "phase_mismatch_active", return_value=False),
            patch.object(RuntimeAuditFields, "phase_lockout_target", return_value=None),
            patch.object(RuntimeAuditFields, "phase_lockout_active", return_value=False),
            patch.object(RuntimeAuditFields, "phase_supported_effective", return_value="P1"),
            patch.object(RuntimeAuditFields, "phase_degraded_active", return_value=False),
        ):
            result = _audit(service)._auto_audit_key(service, "reason", False)

        self.assertEqual(result[4:7], (0, 0, 0))
        self.assertEqual(result[33:42], (None, None, 0, 0, None, 0, 0, None, 0))

    def test_normalized_metrics_have_exact_outward_schema(self) -> None:
        service = SimpleNamespace(_last_auto_metrics={"base": "value"})
        helper_values: dict[str, object] = {
            "charger_target": 11.0,
            "charger_transport_reason": "transport-reason",
            "charger_transport_source": "transport-source",
            "charger_retry_reason": "retry-reason",
            "charger_retry_source": "retry-source",
            "observed_phase": "P1",
            "phase_mismatch_active": True,
            "phase_lockout_target": "P1_P2",
            "phase_lockout_active": False,
            "phase_supported_effective": "P1,P1_P2",
            "phase_degraded_active": True,
            "switch_feedback_closed": True,
            "switch_interlock_ok": False,
            "switch_feedback_mismatch": True,
            "contactor_fault_count": 4,
            "contactor_lockout_reason": "locked",
            "contactor_lockout_active": True,
            "evse_fault_active": True,
            "evse_fault_reason": "faulted",
            "recovery_active": False,
        }
        backend_values = iter(("combined", "meter", "switch", "charger"))
        helper_mocks: dict[str, MagicMock] = {}
        with ExitStack() as stack:
            sanitize = stack.enter_context(
                patch("venus_evcharger.runtime.audit.sanitized_auto_metrics", return_value={"base": "value"})
            )
            backend = stack.enter_context(
                patch.object(RuntimeAuditFields, "backend_value", side_effect=lambda *_args: next(backend_values))
            )
            for name, value in helper_values.items():
                helper_mocks[name] = stack.enter_context(patch.object(RuntimeAuditFields, name, return_value=value))
            result = _audit(service)._normalized_auto_audit_metrics(service)

        sanitize.assert_called_once_with({"base": "value"})
        self.assertEqual(
            backend.call_args_list,
            [
                call(service, "backend_mode", "combined"),
                call(service, "meter_backend_type", "shelly_meter"),
                call(service, "switch_backend_type", "shelly_contactor_switch"),
                call(service, "charger_backend_type", "na"),
            ],
        )
        for helper in helper_mocks.values():
            helper.assert_called_once_with(service)
        self.assertEqual(
            result,
            {
                "base": "value",
                "backend_mode": "combined",
                "meter_backend": "meter",
                "switch_backend": "switch",
                "charger_backend": "charger",
                "charger_target": 11.0,
                "charger_transport_reason": "transport-reason",
                "charger_transport_source": "transport-source",
                "charger_retry_reason": "retry-reason",
                "charger_retry_source": "retry-source",
                "learned_charge_power_confidence": None,
                "learned_charge_power_stability_score": None,
                "learned_charge_power_reason": None,
                "phase_observed": "P1",
                "phase_mismatch": 1,
                "phase_lockout_target": "P1_P2",
                "phase_lockout": 0,
                "phase_effective": "P1,P1_P2",
                "phase_degraded": 1,
                "switch_feedback": 1,
                "switch_interlock": 0,
                "switch_feedback_mismatch": 1,
                "contactor_fault_count": 4,
                "contactor_lockout_reason": "locked",
                "contactor_lockout": 1,
                "fault": 1,
                "fault_reason": "faulted",
                "recovery": 0,
            },
        )

    def test_normalized_metrics_default_to_empty_input_payload(self) -> None:
        service = SimpleNamespace(_last_auto_metrics={})
        helper_values: dict[str, object] = {
            "charger_target": None,
            "charger_transport_reason": None,
            "charger_transport_source": None,
            "charger_retry_reason": None,
            "charger_retry_source": None,
            "observed_phase": None,
            "phase_mismatch_active": False,
            "phase_lockout_target": None,
            "phase_lockout_active": False,
            "phase_supported_effective": "P1",
            "phase_degraded_active": False,
            "switch_feedback_closed": None,
            "switch_interlock_ok": None,
            "switch_feedback_mismatch": False,
            "contactor_fault_count": 0,
            "contactor_lockout_reason": None,
            "contactor_lockout_active": False,
            "evse_fault_active": False,
            "evse_fault_reason": None,
            "recovery_active": False,
        }
        with ExitStack() as stack:
            sanitize = stack.enter_context(
                patch("venus_evcharger.runtime.audit.sanitized_auto_metrics", return_value={})
            )
            stack.enter_context(patch.object(RuntimeAuditFields, "backend_value", return_value="backend"))
            for name, value in helper_values.items():
                stack.enter_context(patch.object(RuntimeAuditFields, name, return_value=value))
            _audit(service)._normalized_auto_audit_metrics(service)
        sanitize.assert_called_once_with({})

    def test_display_fields_have_exact_names_sources_and_formatting(self) -> None:
        metrics = {
            "surplus": 1250.4,
            "grid": -20.6,
            "soc": 61.25,
            "profile": "profile",
            "start_threshold": 1600.4,
            "stop_threshold": 799.6,
            "learned_charge_power": 2299.6,
            "learned_charge_power_state": "stable",
            "learned_charge_power_confidence": 0.9236,
            "learned_charge_power_stability_score": 0.8126,
            "learned_charge_power_reason": "learned",
            "threshold_scale": 1.2346,
            "threshold_mode": "adaptive",
            "backend_mode": "split",
            "meter_backend": "meter",
            "switch_backend": "switch",
            "charger_backend": "charger",
            "charger_target": 12.25,
            "charger_transport_reason": "tr",
            "charger_transport_source": "ts",
            "charger_retry_reason": "rr",
            "charger_retry_source": "rs",
            "phase_observed": "P1",
            "phase_mismatch": 1,
            "phase_lockout_target": "P1_P2",
            "phase_lockout": 0,
            "phase_effective": "P1,P1_P2",
            "phase_degraded": 0,
            "switch_feedback": 1,
            "switch_interlock": 1,
            "switch_feedback_mismatch": 0,
            "contactor_fault_count": 2,
            "contactor_lockout_reason": "lockout",
            "contactor_lockout": 0,
            "fault": 0,
            "fault_reason": "fault-reason",
            "recovery": 0,
            "stop_alpha": 0.126,
            "stop_alpha_stage": "calm",
            "surplus_volatility": 501.2,
        }
        with patch.object(RuntimeAuditLogger, "_normalized_auto_audit_metrics", return_value=metrics):
            service = SimpleNamespace()
            result = _audit(service)._auto_audit_display_fields(service)

        self.assertEqual(set(result), set(DISPLAY_FIELDS))
        self.assertEqual(
            result,
            {
                "surplus": "1250W",
                "grid": "-21W",
                "soc": "61.2%",
                "profile": "profile",
                "start_threshold": "1600W",
                "stop_threshold": "800W",
                "learned_charge_power": "2300W",
                "learned_charge_power_state": "stable",
                "learned_charge_power_confidence": "0.924",
                "learned_charge_power_stability": "0.813",
                "learned_charge_power_reason": "learned",
                "threshold_scale": "1.235",
                "threshold_mode": "adaptive",
                "backend_mode": "split",
                "meter_backend": "meter",
                "switch_backend": "switch",
                "charger_backend": "charger",
                "charger_target": "12.2A",
                "charger_transport_reason": "tr",
                "charger_transport_source": "ts",
                "charger_retry_reason": "rr",
                "charger_retry_source": "rs",
                "phase_observed": "P1",
                "phase_mismatch": "1",
                "phase_lockout_target": "P1_P2",
                "phase_lockout": "0",
                "phase_effective": "P1,P1_P2",
                "phase_degraded": "0",
                "switch_feedback": "1",
                "switch_interlock": "1",
                "switch_feedback_mismatch": "0",
                "contactor_fault_count": "2",
                "contactor_lockout_reason": "lockout",
                "contactor_lockout": "0",
                "fault": "0",
                "fault_reason": "fault-reason",
                "recovery": "0",
                "stop_alpha": "0.13",
                "stop_alpha_stage": "calm",
                "surplus_volatility": "501W",
            },
        )

    def test_display_fields_preserve_lowercase_non_finite_format_marker(self) -> None:
        numeric_keys = {
            "surplus",
            "grid",
            "soc",
            "start_threshold",
            "stop_threshold",
            "learned_charge_power",
            "learned_charge_power_confidence",
            "learned_charge_power_stability_score",
            "threshold_scale",
            "charger_target",
            "stop_alpha",
            "surplus_volatility",
        }
        metrics = {key: float("inf") for key in numeric_keys}
        with patch.object(RuntimeAuditLogger, "_normalized_auto_audit_metrics", return_value=metrics):
            service = SimpleNamespace()
            result = _audit(service)._auto_audit_display_fields(service)
        formatted_names = (
            "surplus",
            "grid",
            "soc",
            "start_threshold",
            "stop_threshold",
            "learned_charge_power",
            "learned_charge_power_confidence",
            "learned_charge_power_stability",
            "threshold_scale",
            "charger_target",
            "stop_alpha",
            "surplus_volatility",
        )
        for name in formatted_names:
            self.assertIn("inf", result[name])
            self.assertNotIn("INF", result[name])

    def test_formatted_line_contains_every_field_in_exact_order(self) -> None:
        service = SimpleNamespace(
            virtual_mode=2,
            virtual_enable=1,
            virtual_autostart=1,
            _last_auto_state="raw-state",
            _last_auto_state_code=99,
        )
        fields = {name: f"<{name}>" for name in DISPLAY_FIELDS}
        with (
            patch.object(RuntimeAuditLogger, "_auto_audit_display_fields", return_value=fields) as display,
            patch.object(RuntimeAuditLogger, "_auto_audit_reason_detail", return_value="detail") as detail,
            patch.object(RuntimeAuditLogger, "_relay_state_for_audit", return_value=1) as relay,
            patch("venus_evcharger.runtime.audit.normalized_auto_state_pair", return_value=("scheduled", 2)) as state,
            patch("venus_evcharger.runtime.audit.time.localtime", return_value=object()) as localtime,
            patch("venus_evcharger.runtime.audit.time.strftime", return_value="LOCAL") as strftime,
        ):
            result = _audit(service)._format_auto_audit_line(service, "reason", True, 123.75)

        localtime.assert_called_once_with(123.75)
        strftime.assert_called_once_with("%Y-%m-%d %H:%M:%S", localtime.return_value)
        display.assert_called_once_with(service)
        detail.assert_called_once_with(service, "reason")
        relay.assert_called_once_with(service)
        state.assert_called_once_with("raw-state", 99)
        expected_names = (
            "reason",
            "detail",
            "cached",
            "state",
            "relay",
            "mode",
            "enable",
            "autostart",
            *DISPLAY_FIELDS,
        )
        values = result.rstrip("\n").split("\t")
        self.assertEqual(values[:2], ["123", "LOCAL"])
        self.assertEqual([item.split("=", 1)[0] for item in values[2:]], list(expected_names))
        self.assertEqual(values[2:10], [
            "reason=reason",
            "detail=detail",
            "cached=1",
            "state=scheduled",
            "relay=1",
            "mode=2",
            "enable=1",
            "autostart=1",
        ])
        self.assertEqual(values[10:], [f"{name}=<{name}>" for name in DISPLAY_FIELDS])

    def test_formatted_line_uses_explicit_zero_value_contracts(self) -> None:
        fields = {name: "value" for name in DISPLAY_FIELDS}
        with (
            patch.object(RuntimeAuditLogger, "_auto_audit_display_fields", return_value=fields),
            patch.object(RuntimeAuditLogger, "_auto_audit_reason_detail", return_value=None),
            patch.object(RuntimeAuditLogger, "_relay_state_for_audit", return_value=0),
            patch("venus_evcharger.runtime.audit.normalized_auto_state_pair", return_value=("idle", 0)) as state,
            patch("venus_evcharger.runtime.audit.time.strftime", return_value="LOCAL"),
            patch("venus_evcharger.runtime.audit.time.localtime"),
        ):
            service = SimpleNamespace(
                virtual_mode=0,
                virtual_enable=0,
                virtual_autostart=0,
                _last_auto_state="idle",
                _last_auto_state_code=0,
            )
            result = _audit(service)._format_auto_audit_line(service, "reason", False, 10.0)
        state.assert_called_once_with("idle", 0)
        self.assertIn("\tdetail=na\t", result)
        self.assertIn("\tmode=0\t", result)
        self.assertIn("\tenable=0\t", result)
        self.assertIn("\tautostart=0\t", result)

    def test_scalar_and_retention_helpers_enforce_boundaries(self) -> None:
        service = SimpleNamespace(auto_stop_condition_reason="auto-stop-grid")
        self.assertEqual(RuntimeAuditLogger._auto_audit_reason_detail(service, "auto-stop"), "grid")
        service.auto_stop_condition_reason = "auto-stop-soc"
        self.assertEqual(RuntimeAuditLogger._auto_audit_reason_detail(service, "auto-stop"), "soc")
        service.auto_stop_condition_reason = "auto-stop-surplus"
        self.assertEqual(RuntimeAuditLogger._auto_audit_reason_detail(service, "auto-stop"), "surplus")
        self.assertIsNone(RuntimeAuditLogger._auto_audit_reason_detail(service, "other"))
        self.assertIsNone(RuntimeAuditLogger._auto_audit_reason_detail(SimpleNamespace(), "auto-stop"))

        self.assertEqual(RuntimeAuditLogger._bucket_metric(1.24, step=0.5), 1.0)
        self.assertEqual(RuntimeAuditLogger._bucket_metric(1.26, step=0.5), 1.5)
        self.assertEqual(RuntimeAuditLogger._bucket_metric(1.25, step=0.0), 1.25)
        self.assertIsNone(RuntimeAuditLogger._bucket_metric(None, step=0.5))
        self.assertIsNone(RuntimeAuditLogger._string_metric(None))
        self.assertEqual(RuntimeAuditLogger._string_metric(0), "0")
        self.assertEqual(RuntimeAuditLogger._auto_audit_value_text(None, "{:.1f}"), "na")
        self.assertEqual(RuntimeAuditLogger._auto_audit_value_text("raw", None), "raw")
        self.assertEqual(RuntimeAuditLogger._auto_audit_value_text(1.25, "{:.1f}"), "1.2")

        lines = [
            "\n",
            "99\told\n",
            "99\tmiddle\ttail\n",
            "99 separated-by-space\n",
            "100\tboundary\n",
            "invalid\n",
            "101\tnew\n",
        ]
        self.assertEqual(
            RuntimeAuditLogger._prune_auto_audit_payload(lines, 100.0),
            ["99 separated-by-space\n", "100\tboundary\n", "invalid\n", "101\tnew\n"],
        )

    def test_relay_state_uses_normalized_service_clock_and_confirmed_output(self) -> None:
        service = SimpleNamespace(time_now=lambda: 12.0)
        with (
            patch.object(RuntimeAuditFields, "callable_time_or_none", return_value=12.0) as normalize,
            patch("venus_evcharger.runtime.audit._fresh_confirmed_relay_output", return_value=True) as relay,
        ):
            self.assertEqual(_audit(service)._relay_state_for_audit(service), 1)
        normalize.assert_called_once_with(service.time_now)
        relay.assert_called_once_with(service, 12.0)

        service = SimpleNamespace()
        with (
            patch.object(RuntimeAuditFields, "callable_time_or_none", return_value=None) as normalize,
            patch("venus_evcharger.runtime.audit._fresh_confirmed_relay_output", return_value=False) as relay,
        ):
            self.assertEqual(_audit(service)._relay_state_for_audit(service), 0)
        normalize.assert_called_once_with(None)
        relay.assert_called_once_with(service, None)

    def test_file_helpers_use_exact_io_contracts(self) -> None:
        opened = mock_open(read_data="a\nb\n")
        with patch("venus_evcharger.runtime.audit.open", opened):
            self.assertEqual(RuntimeAuditLogger._load_auto_audit_lines("audit.log"), ["a\n", "b\n"])
        opened.assert_called_once_with("audit.log", "r", encoding="utf-8")

        with patch("venus_evcharger.runtime.audit.write_text_atomically") as write:
            RuntimeAuditLogger._write_pruned_auto_audit_lines("audit.log", ["a\n", "b\n"])
        write.assert_called_once_with("audit.log", "a\nb\n")

        opened = mock_open()
        with (
            patch("venus_evcharger.runtime.audit.os.makedirs") as makedirs,
            patch("venus_evcharger.runtime.audit.open", opened),
        ):
            RuntimeAuditLogger._write_auto_audit_line("logs/audit.log", "payload\n")
        makedirs.assert_called_once_with("logs", exist_ok=True)
        opened.assert_called_once_with("logs/audit.log", "a", encoding="utf-8")
        opened().write.assert_called_once_with("payload\n")

    def test_file_helper_failures_log_exact_context_without_escaping(self) -> None:
        for error in (OSError("read"), RuntimeError("runtime")):
            with (
                self.subTest(error=type(error).__name__),
                patch("venus_evcharger.runtime.audit.open", side_effect=error),
                patch("venus_evcharger.runtime.audit.logging.debug") as debug,
            ):
                self.assertIsNone(RuntimeAuditLogger._load_auto_audit_lines("audit.log"))
                debug.assert_called_once_with("Auto audit cleanup skipped for %s: %s", "audit.log", error)

        error = TypeError("write")
        with (
            patch("venus_evcharger.runtime.audit.write_text_atomically", side_effect=error),
            patch("venus_evcharger.runtime.audit.logging.debug") as debug,
        ):
            RuntimeAuditLogger._write_pruned_auto_audit_lines("audit.log", ["payload\n"])
        debug.assert_called_once_with("Unable to prune auto audit log %s: %s", "audit.log", error)

    def test_cleanup_due_and_cutoff_obey_exact_defaults_and_boundaries(self) -> None:
        service = SimpleNamespace(
            _last_auto_audit_cleanup_at=100.0,
            auto_audit_log_max_age_hours=168.0,
        )
        audit = _audit(service)
        policy = SimpleNamespace(audit_cleanup_interval_seconds=MagicMock(return_value=300.0))
        with patch("venus_evcharger.runtime.audit.service_gateway_pressure_policy", return_value=policy) as resolve:
            self.assertFalse(audit._auto_audit_cleanup_due("", 300.0))
            self.assertFalse(audit._auto_audit_cleanup_due("audit.log", 399.999))
            self.assertTrue(audit._auto_audit_cleanup_due("audit.log", 400.0))
        self.assertEqual(resolve.call_count, 2)
        self.assertEqual(resolve.call_args_list, [call(service), call(service)])
        policy.audit_cleanup_interval_seconds.assert_has_calls([call(300.0), call(300.0)])

        self.assertEqual(audit._auto_audit_cutoff_epoch(service, 1_000_000.0), 395_200.0)
        service.auto_audit_log_max_age_hours = 2.0
        self.assertEqual(audit._auto_audit_cutoff_epoch(service, 10_000.0), 2_800.0)
        service.auto_audit_log_max_age_hours = 0.0
        self.assertIsNone(audit._auto_audit_cutoff_epoch(service, 10_000.0))

    def test_cleanup_with_empty_path_stops_before_due_check(self) -> None:
        service = SimpleNamespace(auto_audit_log_path="")
        audit = _audit(service)
        audit._auto_audit_cleanup_due = MagicMock(return_value=False)
        audit._cleanup_auto_audit_log(100.0)
        audit._auto_audit_cleanup_due.assert_called_once_with("", 100.0)

    def test_cleanup_orchestration_commits_cadence_before_optional_work(self) -> None:
        service = SimpleNamespace(auto_audit_log_path=" audit.log ")
        audit = _audit(service)
        audit._auto_audit_cleanup_due = MagicMock(return_value=True)
        audit._auto_audit_cutoff_epoch = MagicMock(return_value=100.0)
        audit._load_auto_audit_lines = MagicMock(return_value=["99\n", "100\n"])
        audit._prune_auto_audit_payload = MagicMock(return_value=["100\n"])
        audit._write_pruned_auto_audit_lines = MagicMock()

        audit._cleanup_auto_audit_log(200.0)

        audit._auto_audit_cleanup_due.assert_called_once_with("audit.log", 200.0)
        self.assertEqual(service._last_auto_audit_cleanup_at, 200.0)
        audit._auto_audit_cutoff_epoch.assert_called_once_with(service, 200.0)
        audit._load_auto_audit_lines.assert_called_once_with("audit.log")
        audit._prune_auto_audit_payload.assert_called_once_with(["99\n", "100\n"], 100.0)
        audit._write_pruned_auto_audit_lines.assert_called_once_with("audit.log", ["100\n"])

    def test_repeat_suppression_has_exact_time_boundaries(self) -> None:
        key = ("same",)
        self.assertFalse(RuntimeAuditLogger._auto_audit_repeat_suppressed(key, ("other",), 10.0, 99.0, 100.0))
        self.assertTrue(RuntimeAuditLogger._auto_audit_repeat_suppressed(key, key, 0.0, None, 100.0))
        self.assertTrue(RuntimeAuditLogger._auto_audit_repeat_suppressed(key, key, -1.0, None, 100.0))
        self.assertFalse(RuntimeAuditLogger._auto_audit_repeat_suppressed(key, key, 0.5, 0.0, 100.0))
        self.assertFalse(RuntimeAuditLogger._auto_audit_repeat_suppressed(key, key, 10.0, None, 100.0))
        self.assertTrue(RuntimeAuditLogger._auto_audit_repeat_suppressed(key, key, 10.0, 90.01, 100.0))
        self.assertFalse(RuntimeAuditLogger._auto_audit_repeat_suppressed(key, key, 10.0, 90.0, 100.0))

    def test_write_event_orchestrates_policy_cleanup_write_and_commit(self) -> None:
        service = SimpleNamespace(
            auto_audit_log=True,
            auto_audit_log_path=" audit.log ",
            auto_audit_log_repeat_seconds=31.0,
            time_now=MagicMock(return_value=100.0),
            _last_auto_audit_key=("old",),
            _last_auto_audit_event_at=80.0,
        )
        audit = _audit(service)
        audit.state_store.ensure_observability_state = MagicMock()
        audit._auto_audit_key = MagicMock(return_value=("new",))
        audit._auto_audit_repeat_suppressed = MagicMock(return_value=False)
        audit._cleanup_auto_audit_log = MagicMock()
        audit._format_auto_audit_line = MagicMock(return_value="line\n")
        audit._write_auto_audit_line = MagicMock()
        policy = SimpleNamespace(audit_repeat_seconds=MagicMock(return_value=41.0))

        with patch("venus_evcharger.runtime.audit.service_gateway_pressure_policy", return_value=policy) as resolve:
            audit.write_auto_audit_event("reason", cached=True)

        audit.state_store.ensure_observability_state.assert_called_once_with()
        service.time_now.assert_called_once_with()
        audit._auto_audit_key.assert_called_once_with(service, "reason", True)
        resolve.assert_called_once_with(service)
        policy.audit_repeat_seconds.assert_called_once_with(31.0)
        audit._auto_audit_repeat_suppressed.assert_called_once_with(
            ("new",),
            ("old",),
            41.0,
            80.0,
            100.0,
        )
        audit._cleanup_auto_audit_log.assert_called_once_with(100.0)
        audit._format_auto_audit_line.assert_called_once_with(service, "reason", True, 100.0)
        audit._write_auto_audit_line.assert_called_once_with("audit.log", "line\n")
        self.assertEqual(service._last_auto_audit_key, ("new",))
        self.assertEqual(service._last_auto_audit_event_at, 100.0)

    def test_write_event_suppression_cleans_up_without_writing(self) -> None:
        service = SimpleNamespace(
            auto_audit_log=True,
            auto_audit_log_repeat_seconds=30.0,
            time_now=MagicMock(return_value=100.0),
            _last_auto_audit_key=None,
            _last_auto_audit_event_at=None,
        )
        audit = _audit(service)
        audit.state_store.ensure_observability_state = MagicMock()
        audit._auto_audit_key = MagicMock(return_value=("key",))
        audit._auto_audit_repeat_suppressed = MagicMock(return_value=True)
        audit._cleanup_auto_audit_log = MagicMock()
        audit._write_auto_audit_line = MagicMock()
        policy = SimpleNamespace(audit_repeat_seconds=MagicMock(return_value=30.0))

        with patch("venus_evcharger.runtime.audit.service_gateway_pressure_policy", return_value=policy):
            audit.write_auto_audit_event("reason")

        audit._cleanup_auto_audit_log.assert_called_once_with(100.0)
        audit._write_auto_audit_line.assert_not_called()

    def test_write_event_disabled_returns_before_clock_and_policy(self) -> None:
        service = SimpleNamespace(auto_audit_log=False, time_now=MagicMock())
        audit = _audit(service)
        audit.state_store.ensure_observability_state = MagicMock()
        with patch("venus_evcharger.runtime.audit.service_gateway_pressure_policy") as policy:
            audit.write_auto_audit_event("reason")
        audit.state_store.ensure_observability_state.assert_called_once_with()
        service.time_now.assert_not_called()
        policy.assert_not_called()

    def test_write_event_empty_path_uses_initialized_runtime_state(self) -> None:
        service = SimpleNamespace(
            auto_audit_log=True,
            auto_audit_log_path="",
            auto_audit_log_repeat_seconds=30.0,
            time_now=MagicMock(return_value=100.0),
            _last_auto_audit_key=None,
            _last_auto_audit_event_at=None,
        )
        audit = _audit(service)
        audit.state_store.ensure_observability_state = MagicMock()
        audit._auto_audit_key = MagicMock(return_value=("key",))
        audit._auto_audit_repeat_suppressed = MagicMock(return_value=False)
        audit._cleanup_auto_audit_log = MagicMock()
        audit._write_auto_audit_line = MagicMock()
        policy = SimpleNamespace(audit_repeat_seconds=MagicMock(return_value=30.0))
        with patch("venus_evcharger.runtime.audit.service_gateway_pressure_policy", return_value=policy):
            audit.write_auto_audit_event("reason")
        policy.audit_repeat_seconds.assert_called_once_with(30.0)
        audit._auto_audit_key.assert_called_once_with(service, "reason", False)
        audit._auto_audit_repeat_suppressed.assert_called_once_with(
            ("key",),
            None,
            30.0,
            None,
            100.0,
        )
        audit._cleanup_auto_audit_log.assert_called_once_with(100.0)
        audit._write_auto_audit_line.assert_not_called()

    def test_write_event_logs_exact_append_failure_and_does_not_commit(self) -> None:
        service = SimpleNamespace(
            auto_audit_log=True,
            auto_audit_log_path="audit.log",
            auto_audit_log_repeat_seconds=30.0,
            time_now=MagicMock(return_value=100.0),
            _last_auto_audit_key=None,
            _last_auto_audit_event_at=None,
        )
        audit = _audit(service)
        audit.state_store.ensure_observability_state = MagicMock()
        audit._auto_audit_key = MagicMock(return_value=("key",))
        audit._auto_audit_repeat_suppressed = MagicMock(return_value=False)
        audit._cleanup_auto_audit_log = MagicMock()
        audit._format_auto_audit_line = MagicMock(return_value="line\n")
        error = OSError("full")
        audit._write_auto_audit_line = MagicMock(side_effect=error)
        policy = SimpleNamespace(audit_repeat_seconds=MagicMock(return_value=30.0))
        with (
            patch("venus_evcharger.runtime.audit.service_gateway_pressure_policy", return_value=policy),
            patch("venus_evcharger.runtime.audit.logging.debug") as debug,
        ):
            audit.write_auto_audit_event("reason")
        debug.assert_called_once_with("Unable to write auto audit log %s: %s", "audit.log", error)
        self.assertIsNone(service._last_auto_audit_key)
        self.assertIsNone(service._last_auto_audit_event_at)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

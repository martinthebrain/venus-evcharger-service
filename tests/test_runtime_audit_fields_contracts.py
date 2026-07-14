# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact contracts for runtime audit field derivation."""

from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from venus_evcharger.runtime.audit_fields import (
    _RuntimeAuditFields,
    _backend_role_name,
    _normalized_optional_audit_text,
    _resolved_backend_value,
)


class _AuditFieldsHarness(_RuntimeAuditFields):
    def __init__(self, service: object) -> None:
        self.service = service

    @staticmethod
    def clone_worker_snapshot(snapshot: dict[str, object]) -> dict[str, object]:
        return dict(snapshot)


def _service(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "_time_now": lambda: 100.0,
        "_last_confirmed_pm_status": None,
        "_last_charger_state_phase_selection": None,
        "_phase_switch_mismatch_active": False,
        "_last_health_reason": "ok",
        "_phase_switch_lockout_selection": None,
        "_phase_switch_lockout_until": None,
        "supported_phase_selections": ("P1", "P3"),
        "_last_switch_feedback_closed": None,
        "_last_switch_interlock_ok": None,
        "_contactor_lockout_reason": "",
        "_contactor_fault_active_reason": "",
        "_contactor_fault_counts": {},
        "_last_auto_state": "idle",
        "_last_auto_state_code": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class RuntimeAuditFieldsContractTests(unittest.TestCase):
    def test_optional_text_normalizes_none_blank_and_scalar_values(self) -> None:
        self.assertIsNone(_normalized_optional_audit_text(None))
        self.assertIsNone(_normalized_optional_audit_text(" \t "))
        self.assertEqual(_normalized_optional_audit_text("  P1  "), "P1")
        self.assertEqual(_normalized_optional_audit_text(7), "7")

    def test_backend_role_and_resolution_contracts_are_exact(self) -> None:
        self.assertEqual(_backend_role_name("meter_backend_type"), "meter")
        self.assertEqual(_backend_role_name("switch_backend_type"), "switch")
        self.assertEqual(_backend_role_name("charger_backend_type"), "charger")
        self.assertIsNone(_backend_role_name("other"))
        service = object()
        with (
            patch("venus_evcharger.runtime.audit_fields.backend_mode_for_service", return_value="combined") as mode,
            patch("venus_evcharger.runtime.audit_fields.backend_type_for_service", return_value="shelly") as backend,
        ):
            self.assertEqual(_resolved_backend_value(service, "backend_mode", "fallback"), "combined")
            self.assertEqual(_resolved_backend_value(service, "meter_backend_type", "fallback"), "shelly")
            self.assertIsNone(_resolved_backend_value(service, "unknown", "fallback"))
        mode.assert_called_once_with(service, "fallback")
        backend.assert_called_once_with(service, "meter", "fallback")

    def test_backend_value_uses_resolver_then_normalized_attribute_fallback(self) -> None:
        service = SimpleNamespace(custom="  custom  ", empty=None)
        with patch("venus_evcharger.runtime.audit_fields._resolved_backend_value", return_value="resolved") as resolved:
            self.assertEqual(_RuntimeAuditFields._backend_value(service, "custom", "fallback"), "resolved")
        resolved.assert_called_once_with(service, "custom", "fallback")
        with patch("venus_evcharger.runtime.audit_fields._resolved_backend_value", return_value=None):
            self.assertEqual(_RuntimeAuditFields._backend_value(service, "custom", "fallback"), "custom")
            self.assertEqual(_RuntimeAuditFields._backend_value(service, "empty", "fallback"), "fallback")
            self.assertEqual(_RuntimeAuditFields._backend_value(service, "missing", "fallback"), "fallback")

    def test_charger_scalar_and_fresh_transport_helpers_delegate_exactly(self) -> None:
        service = _service(_charger_target_current_amps="12.5")
        self.assertEqual(_RuntimeAuditFields._charger_target_for_audit(service), 12.5)
        self.assertIsNone(_RuntimeAuditFields._charger_target_for_audit(SimpleNamespace()))
        helper_names = (
            ("_fresh_charger_transport_reason", "_charger_transport_reason_for_audit"),
            ("_fresh_charger_transport_source", "_charger_transport_source_for_audit"),
            ("_fresh_charger_retry_reason", "_charger_retry_reason_for_audit"),
            ("_fresh_charger_retry_source", "_charger_retry_source_for_audit"),
        )
        for helper_name, method_name in helper_names:
            with self.subTest(helper=helper_name):
                with patch(f"venus_evcharger.runtime.audit_fields.{helper_name}", return_value="value") as helper:
                    self.assertEqual(getattr(_RuntimeAuditFields, method_name)(service), "value")
                helper.assert_called_once_with(service)

    def test_observed_phase_prefers_confirmed_then_charger_and_normalizes_both(self) -> None:
        self.assertEqual(
            _RuntimeAuditFields._observed_phase_for_audit(
                _service(
                    _last_confirmed_pm_status={"_phase_selection": " P3 "},
                    _last_charger_state_phase_selection="P1",
                )
            ),
            "P3",
        )
        self.assertEqual(
            _RuntimeAuditFields._observed_phase_for_audit(
                _service(
                    _last_confirmed_pm_status={"_phase_selection": " "},
                    _last_charger_state_phase_selection=" P1 ",
                )
            ),
            "P1",
        )
        self.assertIsNone(_RuntimeAuditFields._observed_phase_for_audit(_service()))
        self.assertIsNone(_RuntimeAuditFields._observed_phase_for_audit(SimpleNamespace()))
        self.assertIsNone(
            _RuntimeAuditFields._observed_phase_for_audit(
                SimpleNamespace(_last_confirmed_pm_status={}, _last_charger_state_phase_selection=None)
            )
        )

    def test_phase_mismatch_accepts_explicit_flag_or_health_reason(self) -> None:
        self.assertTrue(_RuntimeAuditFields._phase_mismatch_active_for_audit(_service(_phase_switch_mismatch_active=True)))
        self.assertTrue(_RuntimeAuditFields._phase_mismatch_active_for_audit(_service(_last_health_reason="phase-switch-mismatch")))
        self.assertFalse(_RuntimeAuditFields._phase_mismatch_active_for_audit(_service()))
        self.assertFalse(_RuntimeAuditFields._phase_mismatch_active_for_audit(SimpleNamespace()))
        self.assertTrue(
            _RuntimeAuditFields._phase_mismatch_active_for_audit(
                SimpleNamespace(_last_health_reason="phase-switch-mismatch")
            )
        )

    def test_callable_time_accepts_numbers_and_rejects_other_values(self) -> None:
        self.assertIsNone(_RuntimeAuditFields._callable_time_or_none(None))
        self.assertIsNone(_RuntimeAuditFields._callable_time_or_none(lambda: "100"))
        self.assertEqual(_RuntimeAuditFields._callable_time_or_none(lambda: 12), 12.0)
        self.assertEqual(_RuntimeAuditFields._callable_time_or_none(lambda: 12.5), 12.5)

    def test_phase_lockout_requires_selection_future_deadline_and_valid_clock(self) -> None:
        active = _service(_phase_switch_lockout_selection=" P3 ", _phase_switch_lockout_until=101.0)
        self.assertTrue(_RuntimeAuditFields._phase_lockout_active_for_audit(active))
        self.assertEqual(_RuntimeAuditFields._phase_lockout_target_for_audit(active), "P3")
        self.assertFalse(
            _RuntimeAuditFields._phase_lockout_active_for_audit(
                _service(_phase_switch_lockout_selection="P3", _phase_switch_lockout_until=100.0)
            )
        )
        self.assertFalse(
            _RuntimeAuditFields._phase_lockout_active_for_audit(
                _service(_phase_switch_lockout_selection=None, _phase_switch_lockout_until=101.0)
            )
        )
        self.assertFalse(
            _RuntimeAuditFields._phase_lockout_active_for_audit(
                _service(_phase_switch_lockout_selection="P3", _phase_switch_lockout_until="bad")
            )
        )
        blank = _service(_phase_switch_lockout_selection=" ", _phase_switch_lockout_until=101.0)
        self.assertIsNone(_RuntimeAuditFields._phase_lockout_target_for_audit(blank))
        fallback = _service(
            _time_now=lambda: "bad",
            _phase_switch_lockout_selection="P3",
            _phase_switch_lockout_until=51.0,
        )
        with patch("venus_evcharger.runtime.audit_fields.time.time", return_value=50.0) as clock:
            self.assertTrue(_RuntimeAuditFields._phase_lockout_active_for_audit(fallback))
        clock.assert_called_once_with()
        with patch("venus_evcharger.runtime.audit_fields.time.time", return_value=50.0):
            self.assertFalse(_RuntimeAuditFields._phase_lockout_active_for_audit(SimpleNamespace()))
        self.assertIsNone(_RuntimeAuditFields._phase_lockout_target_for_audit(SimpleNamespace()))
        with patch.object(_RuntimeAuditFields, "_phase_lockout_active_for_audit", return_value=True):
            self.assertIsNone(_RuntimeAuditFields._phase_lockout_target_for_audit(SimpleNamespace()))

    def test_effective_phase_support_delegates_exact_inputs_and_detects_degradation(self) -> None:
        service = _service(_phase_switch_lockout_selection="P3", _phase_switch_lockout_until=120.0)
        with patch(
            "venus_evcharger.runtime.audit_fields.effective_supported_phase_selections",
            return_value=("P1",),
        ) as effective:
            self.assertEqual(_RuntimeAuditFields._phase_supported_effective_for_audit(service), "P1")
        effective.assert_called_once_with(
            ("P1", "P3"),
            lockout_selection="P3",
            lockout_until=120.0,
            now=100.0,
        )
        with patch.object(_RuntimeAuditFields, "_phase_supported_effective_for_audit", return_value="P1"):
            self.assertTrue(_RuntimeAuditFields._phase_degraded_active_for_audit(service))
        with patch.object(_RuntimeAuditFields, "_phase_supported_effective_for_audit", return_value="P1,P3"):
            self.assertFalse(_RuntimeAuditFields._phase_degraded_active_for_audit(service))
        missing = SimpleNamespace()
        with patch(
            "venus_evcharger.runtime.audit_fields.effective_supported_phase_selections",
            return_value=("P1",),
        ) as effective:
            self.assertEqual(_RuntimeAuditFields._phase_supported_effective_for_audit(missing), "P1")
        effective.assert_called_once_with(
            ("P1",),
            lockout_selection=None,
            lockout_until=None,
            now=None,
        )
        with patch(
            "venus_evcharger.runtime.audit_fields.effective_supported_phase_selections",
            return_value=("P1", "P3"),
        ):
            self.assertEqual(_RuntimeAuditFields._phase_supported_effective_for_audit(missing), "P1,P3")
        with patch.object(_RuntimeAuditFields, "_phase_supported_effective_for_audit", return_value="P1"):
            self.assertFalse(_RuntimeAuditFields._phase_degraded_active_for_audit(missing))
        with patch.object(_RuntimeAuditFields, "_phase_supported_effective_for_audit", return_value="P1") as effective:
            self.assertTrue(_RuntimeAuditFields._phase_degraded_active_for_audit(service))
        effective.assert_called_once_with(service)

    def test_switch_feedback_and_interlock_preserve_unknown_and_coerce_known_values(self) -> None:
        self.assertIsNone(_RuntimeAuditFields._switch_feedback_closed_for_audit(_service()))
        self.assertIsNone(_RuntimeAuditFields._switch_interlock_ok_for_audit(_service()))
        service = _service(_last_switch_feedback_closed=1, _last_switch_interlock_ok=0)
        self.assertTrue(_RuntimeAuditFields._switch_feedback_closed_for_audit(service))
        self.assertFalse(_RuntimeAuditFields._switch_interlock_ok_for_audit(service))
        self.assertTrue(
            _RuntimeAuditFields._switch_interlock_ok_for_audit(
                _service(_last_switch_interlock_ok=1)
            )
        )
        self.assertIsNone(_RuntimeAuditFields._switch_feedback_closed_for_audit(SimpleNamespace()))
        self.assertIsNone(_RuntimeAuditFields._switch_interlock_ok_for_audit(SimpleNamespace()))

    def test_switch_feedback_mismatch_uses_health_fallback_or_model_contract(self) -> None:
        unknown = _service(_last_health_reason="contactor-feedback-mismatch")
        with patch("venus_evcharger.runtime.audit_fields._fresh_confirmed_relay_output", return_value=None) as relay:
            self.assertTrue(_RuntimeAuditFields._switch_feedback_mismatch_for_audit(unknown))
        relay.assert_called_once_with(unknown, 100.0)
        known = _service(_last_switch_feedback_closed=True)
        with (
            patch("venus_evcharger.runtime.audit_fields._fresh_confirmed_relay_output", return_value=False) as relay,
            patch("venus_evcharger.runtime.audit_fields.switch_feedback_mismatch", return_value=True) as mismatch,
        ):
            self.assertTrue(_RuntimeAuditFields._switch_feedback_mismatch_for_audit(known))
        relay.assert_called_once_with(known, 100.0)
        mismatch.assert_called_once_with(False, True)
        missing = SimpleNamespace()
        with patch("venus_evcharger.runtime.audit_fields._fresh_confirmed_relay_output", return_value=None) as relay:
            self.assertFalse(_RuntimeAuditFields._switch_feedback_mismatch_for_audit(missing))
        relay.assert_called_once_with(missing, None)

    def test_contactor_and_evse_fault_contracts_are_exact(self) -> None:
        service = _service(
            _contactor_lockout_reason=" open ",
            _contactor_fault_counts={"open": 3},
            _last_health_reason="contactor-lockout-welded-cached",
        )
        self.assertEqual(_RuntimeAuditFields._contactor_lockout_reason_for_audit(service), "open")
        self.assertTrue(_RuntimeAuditFields._contactor_lockout_active_for_audit(service))
        self.assertEqual(_RuntimeAuditFields._contactor_fault_count_for_audit(service), 3)
        self.assertEqual(_RuntimeAuditFields._evse_fault_reason_for_audit(service), "contactor-lockout-welded")
        self.assertTrue(_RuntimeAuditFields._evse_fault_active_for_audit(service))
        inactive = _service(_contactor_fault_counts="bad")
        self.assertEqual(_RuntimeAuditFields._contactor_fault_count_for_audit(inactive), 0)
        self.assertFalse(_RuntimeAuditFields._contactor_lockout_active_for_audit(inactive))
        self.assertFalse(_RuntimeAuditFields._evse_fault_active_for_audit(inactive))
        fallback = _service(
            _contactor_fault_active_reason=" active ",
            _contactor_fault_counts={"active": 2},
        )
        self.assertEqual(_RuntimeAuditFields._contactor_fault_count_for_audit(fallback), 2)
        missing = SimpleNamespace()
        self.assertIsNone(_RuntimeAuditFields._contactor_lockout_reason_for_audit(missing))
        self.assertEqual(_RuntimeAuditFields._contactor_fault_count_for_audit(missing), 0)
        with patch("venus_evcharger.runtime.audit_fields.evse_fault_reason", return_value=None) as fault_reason:
            self.assertIsNone(_RuntimeAuditFields._evse_fault_reason_for_audit(missing))
        fault_reason.assert_called_once_with(None)
        self.assertEqual(
            _RuntimeAuditFields._contactor_fault_count_for_audit(
                SimpleNamespace(_contactor_fault_counts={"XXXX": 4})
            ),
            0,
        )
        self.assertEqual(
            _RuntimeAuditFields._contactor_fault_count_for_audit(
                SimpleNamespace(
                    _contactor_fault_counts={},
                    _contactor_fault_active_reason="unknown",
                )
            ),
            0,
        )

    def test_recovery_state_uses_normalized_state_contract(self) -> None:
        service = _service(_last_auto_state="recovery", _last_auto_state_code=5)
        with patch("venus_evcharger.runtime.audit_fields.normalized_auto_state_pair", return_value=("recovery", 5)) as normalized:
            self.assertTrue(_RuntimeAuditFields._recovery_active_for_audit(service))
        normalized.assert_called_once_with("recovery", 5)
        with patch("venus_evcharger.runtime.audit_fields.normalized_auto_state_pair", return_value=("idle", 0)):
            self.assertFalse(_RuntimeAuditFields._recovery_active_for_audit(service))
        missing = SimpleNamespace()
        with patch("venus_evcharger.runtime.audit_fields.normalized_auto_state_pair", return_value=("idle", 0)) as normalized:
            self.assertFalse(_RuntimeAuditFields._recovery_active_for_audit(missing))
        normalized.assert_called_once_with("idle", 0)

    def test_worker_snapshot_methods_normalize_clone_and_lock(self) -> None:
        service = _service()
        service._worker_snapshot_lock = threading.Lock()
        service._worker_snapshot = {"state": "old"}
        service._ensure_worker_state = MagicMock()
        harness = _AuditFieldsHarness(service)
        with patch("venus_evcharger.runtime.audit_fields.normalized_worker_snapshot", side_effect=lambda payload, now: {**payload, "now": now}) as normalized:
            harness.set_worker_snapshot({"state": "new"})
            self.assertEqual(service._worker_snapshot, {"state": "new", "now": 100.0})
            harness.update_worker_snapshot(extra=7)
            self.assertEqual(service._worker_snapshot, {"state": "new", "now": 100.0, "extra": 7})
            result = harness.get_worker_snapshot()
        self.assertEqual(result, {"state": "new", "now": 100.0, "extra": 7})
        self.assertIsNot(result, service._worker_snapshot)
        self.assertEqual(service._ensure_worker_state.call_count, 3)
        self.assertEqual(normalized.call_count, 2)
        service_without_clock = SimpleNamespace()
        harness_without_clock = _AuditFieldsHarness(service_without_clock)
        with patch("venus_evcharger.runtime.audit_fields.normalized_worker_snapshot", return_value={}) as normalized:
            self.assertEqual(harness_without_clock._normalized_worker_snapshot({"state": "idle"}), {})
        normalized.assert_called_once_with({"state": "idle"}, now=None)

    def test_state_initializers_delegate_exact_default_maps(self) -> None:
        service = _service()
        harness = _AuditFieldsHarness(service)
        harness.ensure_missing_attributes = MagicMock()
        harness.worker_state_defaults = MagicMock(return_value={"worker": MagicMock()})
        harness.observability_state_defaults = MagicMock(return_value={"audit": MagicMock()})
        harness.ensure_worker_state()
        harness.ensure_observability_state()
        self.assertEqual(harness.ensure_missing_attributes.call_count, 2)
        self.assertIs(harness.ensure_missing_attributes.call_args_list[0].args[0], service)
        self.assertEqual(tuple(harness.ensure_missing_attributes.call_args_list[0].args[1]), ("worker",))
        self.assertIs(harness.ensure_missing_attributes.call_args_list[1].args[0], service)
        self.assertEqual(tuple(harness.ensure_missing_attributes.call_args_list[1].args[1]), ("audit",))


if __name__ == "__main__":
    unittest.main()

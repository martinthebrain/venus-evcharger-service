# SPDX-License-Identifier: GPL-3.0-or-later
"""Direct contracts for lazy runtime-support state factories."""

from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from tests.runtime_contract_assertions import assert_typed_mapping, typed_values
from venus_evcharger.runtime.support import DefaultFactory, RuntimeSupportController


OBS_NONE = (
    "_last_dbus_ok_at", "_last_successful_update_at", "_last_recovery_attempt_at",
    "_last_pm_status_at", "_last_confirmed_pm_status", "_last_confirmed_pm_status_at",
    "_shelly_last_error_at", "_shelly_last_ok_at", "_shelly_offline_since",
    "_last_charger_state_enabled", "_last_charger_state_current_amps",
    "_last_charger_state_phase_selection", "_last_charger_state_actual_current_amps",
    "_last_charger_state_power_w", "_last_charger_state_energy_kwh",
    "_last_charger_state_status", "_last_charger_state_fault", "_last_charger_state_at",
    "_last_charger_transport_reason", "_last_charger_transport_source",
    "_last_charger_transport_detail", "_last_charger_transport_at", "_charger_retry_reason",
    "_charger_retry_source", "_charger_retry_until", "_last_switch_feedback_closed",
    "_last_switch_interlock_ok", "_last_switch_feedback_at", "_contactor_suspected_open_since",
    "_contactor_suspected_welded_since", "_contactor_fault_active_reason",
    "_contactor_fault_active_since", "_contactor_lockout_at", "_charger_target_current_amps",
    "_charger_target_current_applied_at", "_phase_switch_pending_selection",
    "_phase_switch_state", "_phase_switch_requested_at", "_phase_switch_stable_until",
    "_phase_switch_last_mismatch_selection", "_phase_switch_last_mismatch_at",
    "_phase_switch_lockout_selection", "_phase_switch_lockout_at", "_phase_switch_lockout_until",
    "_auto_phase_target_candidate", "_auto_phase_target_since", "_last_pv_at",
    "_last_battery_soc_at", "_last_grid_at", "_grid_recovery_since", "_last_auto_audit_key",
    "_last_auto_audit_event_at", "_auto_high_soc_profile_active",
)
OBS_EXPECTED = typed_values(
    none=OBS_NONE,
    false=(
        "_last_pm_status_confirmed", "_phase_switch_resume_relay",
        "_phase_switch_mismatch_active", "_grid_recovery_required", "auto_audit_log",
    ),
    integers=(
        "_recovery_attempts", "_last_auto_state_code", "_last_charger_fault_active",
        "_shelly_consecutive_errors", "_shelly_session_reset_count",
    ),
    floats=("_shelly_retry_after", "_last_auto_audit_cleanup_at"),
    empty_text=(
        "_shelly_last_error_reason", "_shelly_last_error_detail", "_contactor_lockout_reason",
        "_contactor_lockout_source", "_phase_switch_lockout_reason",
    ),
    empty_dicts=(
        "_warning_state", "_contactor_fault_counts", "_phase_switch_mismatch_counts",
        "_source_retry_after",
    ),
    text={
        "_last_auto_state": "idle", "_last_status_source": "unknown", "_shelly_state": "unknown",
        "active_phase_selection": "phase:P1", "requested_phase_selection": "phase:P1",
        "auto_audit_log_path": "/var/volatile/log/dbus-venus-evcharger/auto-reasons.log",
    },
)
OBS_EXPECTED.update(
    {
        "_error_state": {
            "dbus": 0, "shelly": 0, "charger": 0, "pv": 0,
            "battery": 0, "grid": 0, "cache_hits": 0,
        },
        "_failure_active": {
            "dbus": False, "shelly": False, "charger": False,
            "pv": False, "battery": False, "grid": False,
        },
        "supported_phase_selections": ("supported",),
        "started_at": 123.0,
        "auto_watchdog_stale_seconds": 180.0,
        "auto_watchdog_recovery_seconds": 60.0,
        "auto_watchdog_restart_attempts": 5,
        "auto_audit_log_max_age_hours": 168.0,
        "auto_audit_log_repeat_seconds": 30.0,
    }
)


def _evaluate(defaults: dict[str, DefaultFactory]) -> dict[str, object]:
    return {name: factory() for name, factory in defaults.items()}


class RuntimeSupportContractTests(unittest.TestCase):
    def test_constructor_and_source_state_factories_preserve_contracts(self) -> None:
        service = object()
        age = MagicMock(return_value=2)
        health_code = MagicMock(return_value=3)
        controller = RuntimeSupportController(service, age, health_code)
        self.assertIs(controller.service, service)
        self.assertIs(controller._age_seconds, age)
        self.assertIs(controller._health_code, health_code)
        self.assertEqual(
            controller.new_error_state(),
            {"dbus": 0, "shelly": 0, "charger": 0, "pv": 0, "battery": 0, "grid": 0, "cache_hits": 0},
        )
        self.assertEqual(
            controller.new_failure_state(),
            {"dbus": False, "shelly": False, "charger": False, "pv": False, "battery": False, "grid": False},
        )

        class OneSourceController(RuntimeSupportController):
            SOURCE_ERROR_KEYS = ("only",)

        self.assertEqual(OneSourceController.new_error_state(), {"only": 0, "cache_hits": 0})
        self.assertEqual(OneSourceController.new_failure_state(), {"only": False})

    def test_worker_defaults_are_complete_typed_lazy_and_independent(self) -> None:
        service = SimpleNamespace(poll_interval_ms=1000, deviceinstance=61)
        controller = RuntimeSupportController(service, MagicMock(), MagicMock())
        snapshot = {"snapshot": True}
        session = object()
        with (
            patch.object(controller, "empty_worker_snapshot", return_value=snapshot) as empty,
            patch("venus_evcharger.runtime.support.requests.Session", return_value=session),
            patch("venus_evcharger.runtime.support.uuid.uuid4") as uuid4,
        ):
            uuid4.return_value.hex = "runtime-id"
            defaults = controller.worker_state_defaults()
            values = _evaluate(defaults)
        empty.assert_called_once_with()
        uuid4.assert_called_once_with()
        self.assertIs(values.pop("_worker_snapshot"), snapshot)
        self.assertIs(values.pop("_worker_session"), session)
        worker_lock = values.pop("_worker_snapshot_lock")
        relay_lock = values.pop("_relay_command_lock")
        stop_event = values.pop("_worker_stop_event")
        self.assertTrue(hasattr(worker_lock, "acquire"))
        self.assertTrue(hasattr(relay_lock, "acquire"))
        self.assertIsNot(worker_lock, relay_lock)
        self.assertIsInstance(stop_event, threading.Event)
        expected = typed_values(
            none=(
                "_worker_thread", "_pending_relay_state", "_pending_relay_requested_at",
                "_relay_sync_expected_state", "_relay_sync_requested_at", "_relay_sync_deadline_at",
                "_auto_input_helper_process", "_auto_input_helper_restart_requested_at",
                "_auto_input_snapshot_last_seen", "_auto_input_snapshot_mtime_ns",
                "_auto_input_snapshot_last_captured_at", "_auto_input_snapshot_version",
                "_auto_input_snapshot_writer_pid", "_auto_input_snapshot_generation",
                "_auto_input_snapshot_runtime_instance_id",
            ),
            false=(
                "_relay_sync_failure_reported", "_auto_input_snapshot_seen_for_current_helper",
                "_auto_mode_cutover_pending", "_ignore_min_offtime_once",
            ),
            true=("dbus_introspection_enabled",),
            integers=("_auto_input_helper_generation",),
            floats=("_auto_input_helper_last_start_at",),
            fives=("auto_input_helper_restart_seconds",),
            text={
                "_auto_input_runtime_instance_id": "runtime-id",
                "auto_input_snapshot_path": "/run/dbus-venus-evcharger-auto-61.json",
                "dbus_introspection_snapshot_path": "/run/dbus-venus-evcharger-dbus-map-61.json",
                "dbus_introspection_request_path": "/run/dbus-venus-evcharger-dbus-map-requests-61.json",
            },
        )
        expected.update(
            {
                "_worker_poll_interval_seconds": 1.0,
                "relay_sync_timeout_seconds": 3.0,
                "auto_input_helper_stale_seconds": 15.0,
                "dbus_introspection_max_age_seconds": 900.0,
            }
        )
        assert_typed_mapping(self, values, expected)

        short_service = SimpleNamespace(poll_interval_ms=500)
        short = RuntimeSupportController(short_service, MagicMock(), MagicMock())
        short_defaults = short.worker_state_defaults()
        self.assertEqual(short_defaults["_worker_poll_interval_seconds"](), 0.5)
        self.assertEqual(short_defaults["relay_sync_timeout_seconds"](), 2.0)
        self.assertEqual(short_defaults["auto_input_snapshot_path"](), "/run/dbus-venus-evcharger-auto-0.json")

        partial = RuntimeSupportController(SimpleNamespace(), MagicMock(), MagicMock())
        partial_defaults = partial.worker_state_defaults()
        self.assertEqual(partial_defaults["_worker_poll_interval_seconds"](), 1.0)
        self.assertEqual(partial_defaults["relay_sync_timeout_seconds"](), 3.0)
        self.assertEqual(
            partial_defaults["dbus_introspection_snapshot_path"](),
            "/run/dbus-venus-evcharger-dbus-map-0.json",
        )
        self.assertEqual(
            partial_defaults["dbus_introspection_request_path"](),
            "/run/dbus-venus-evcharger-dbus-map-requests-0.json",
        )

    def test_observability_defaults_are_complete_typed_and_delegate_normalizers(self) -> None:
        controller = RuntimeSupportController(SimpleNamespace(), MagicMock(), MagicMock())
        with (
            patch("venus_evcharger.runtime.support.time.time", return_value=123.0),
            patch(
                "venus_evcharger.runtime.support.normalize_phase_selection",
                side_effect=lambda value: f"phase:{value}",
            ) as normalize,
            patch(
                "venus_evcharger.runtime.support.normalize_phase_selection_tuple",
                return_value=("supported",),
            ) as normalize_tuple,
        ):
            values = _evaluate(controller.observability_state_defaults())
        assert_typed_mapping(self, values, OBS_EXPECTED)
        self.assertEqual(normalize.call_args_list, [call("P1"), call("P1")])
        normalize_tuple.assert_called_once_with(("P1",), ("P1",))

        second = _evaluate(controller.observability_state_defaults())
        self.assertIsNot(values["_warning_state"], second["_warning_state"])
        self.assertIsNot(values["_source_retry_after"], second["_source_retry_after"])

    def test_ensure_missing_attributes_is_lazy_and_preserves_existing_values(self) -> None:
        existing = object()
        service = SimpleNamespace(existing=existing)
        existing_factory = MagicMock(return_value="replacement")
        missing_factory = MagicMock(return_value={"created": True})
        RuntimeSupportController.ensure_missing_attributes(
            service,
            {"existing": existing_factory, "missing": missing_factory},
        )
        self.assertIs(service.existing, existing)
        existing_factory.assert_not_called()
        missing_factory.assert_called_once_with()
        self.assertEqual(service.missing, {"created": True})


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact contracts for the update entry point and charger retry transport."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from venus_evcharger.update import controller as controller_module
from venus_evcharger.update import relay_charger_transport as transport_module
from venus_evcharger.update.controller import UpdateCycleController
from venus_evcharger.update.relay_charger_transport import _RelayChargerTransport


class _TransportHarness(_RelayChargerTransport):
    @classmethod
    def _charger_readback_now(cls, svc: object, now: float | None = None) -> float:
        del cls, svc
        return 50.0 if now is None else float(now)


class _RejectingAttributes:
    def __setattr__(self, name: str, value: object) -> None:
        del name, value
        raise AttributeError("read only")


class _SlotOnly:
    __slots__ = ()


class _ObservedAttributes:
    def __init__(self) -> None:
        object.__setattr__(self, "assignments", [])

    def __setattr__(self, name: str, value: object) -> None:
        self.assignments.append((name, value))
        object.__setattr__(self, name, value)


class UpdateControllerContractTests(unittest.TestCase):
    def test_constructor_preserves_dependencies_and_constants(self) -> None:
        service = object()
        phase_values = object()
        health_code = object()
        controller = UpdateCycleController(service, phase_values, health_code)
        self.assertIs(controller.service, service)
        self.assertIs(controller._phase_values, phase_values)
        self.assertIs(controller._health_code, health_code)
        self.assertEqual(controller.LEARNED_POWER_STABLE_MIN_SAMPLES, 3)
        self.assertEqual(controller.LEARNED_POWER_STABLE_MIN_SECONDS, 15.0)
        self.assertEqual(controller.LEARNED_POWER_STABLE_TOLERANCE_WATTS, 150.0)
        self.assertEqual(controller.LEARNED_POWER_STABLE_TOLERANCE_RATIO, 0.08)
        self.assertEqual(controller.LEARNED_POWER_SIGNATURE_MISMATCH_SESSIONS, 2)
        self.assertEqual(controller.LEARNED_POWER_VOLTAGE_TOLERANCE_VOLTS, 10.0)
        self.assertEqual(controller.FUTURE_INPUT_TIMESTAMP_TOLERANCE_SECONDS, 1.0)
        self.assertEqual(controller.SOFTWARE_UPDATE_CHECK_INTERVAL_SECONDS, 604800.0)
        self.assertEqual(controller.SOFTWARE_UPDATE_REQUEST_TIMEOUT_SECONDS, 5.0)

    def test_manifest_result_validates_response_and_semantics(self) -> None:
        response = MagicMock()
        response.json.return_value = {"version": "2.0", "bundle_sha256": "abc"}
        with (
            patch.object(controller_module.requests, "get", return_value=response) as get,
            patch.object(
                UpdateCycleController,
                "_software_update_payload_value",
                side_effect=("2.0", "abc"),
            ) as payload_value,
            patch.object(UpdateCycleController, "_software_update_manifest_available", return_value=True) as available,
        ):
            result = UpdateCycleController._software_update_manifest_result("https://manifest", "1.0", "old")
        self.assertEqual(result, ("2.0", True, "manifest"))
        get.assert_called_once_with("https://manifest", timeout=5.0)
        response.raise_for_status.assert_called_once_with()
        self.assertEqual(payload_value.call_args_list, [call(response.json.return_value, "version"), call(response.json.return_value, "bundle_sha256")])
        available.assert_called_once_with("2.0", "abc", "1.0", "old")

    def test_manifest_result_rejects_non_mapping_payload(self) -> None:
        response = MagicMock()
        response.json.return_value = ["invalid"]
        with (
            patch.object(controller_module.requests, "get", return_value=response) as get,
            patch.object(UpdateCycleController, "_software_update_payload_value") as payload_value,
        ):
            result = UpdateCycleController._software_update_manifest_result("source", "1", "hash")
        self.assertEqual(result, ("", False, ""))
        get.assert_called_once_with("source", timeout=5.0)
        response.raise_for_status.assert_called_once_with()
        payload_value.assert_not_called()

    def test_version_result_uses_first_trimmed_line_and_empty_contract(self) -> None:
        response = MagicMock(text=" 2.0 \nignored")
        with patch.object(controller_module.requests, "get", return_value=response) as get:
            self.assertEqual(UpdateCycleController._software_update_version_result("source", "1.0"), ("2.0", True, "version-file"))
        get.assert_called_once_with("source", timeout=5.0)
        response.raise_for_status.assert_called_once_with()

        response = MagicMock(text="")
        with patch.object(controller_module.requests, "get", return_value=response):
            self.assertEqual(UpdateCycleController._software_update_version_result("source", "1.0"), ("", False, "version-file"))

    def test_spawn_process_preserves_command_and_closes_log_on_expected_error(self) -> None:
        log_handle = MagicMock()
        process = MagicMock()
        command = ["/bin/sh", "restart.sh"]
        with (
            patch.object(UpdateCycleController, "_software_update_log_handle", return_value=log_handle) as open_log,
            patch.object(UpdateCycleController, "_software_update_command", return_value=command) as build,
            patch.object(controller_module.subprocess, "Popen", return_value=process) as popen,
        ):
            self.assertEqual(
                UpdateCycleController._spawn_software_update_process("update.log", "/repo", "restart.sh"),
                (process, log_handle),
            )
        open_log.assert_called_once_with("update.log")
        build.assert_called_once_with("/repo", "restart.sh")
        popen.assert_called_once_with(
            command,
            cwd="/repo",
            stdout=log_handle,
            stderr=controller_module.subprocess.STDOUT,
            start_new_session=True,
        )

        with (
            patch.object(UpdateCycleController, "_software_update_log_handle", return_value=log_handle),
            patch.object(UpdateCycleController, "_software_update_command", return_value=command),
            patch.object(controller_module.subprocess, "Popen", side_effect=OSError("failed")),
            patch.object(UpdateCycleController, "_close_open_log_handle") as close,
        ):
            with self.assertRaisesRegex(OSError, "failed"):
                UpdateCycleController._spawn_software_update_process("update.log", "/repo", "restart.sh")
        close.assert_called_once_with(log_handle)

    def test_sign_of_life_logs_exact_service_and_power(self) -> None:
        service = SimpleNamespace(service_name="EVCS", _dbusservice={"/Ac/Power": 123.0})
        controller = UpdateCycleController(service, object(), object())
        with patch.object(controller_module.logging, "info") as info:
            self.assertIs(controller.sign_of_life(), True)
        info.assert_called_once_with("[%s] Last '/Ac/Power': %s", "EVCS", 123.0)

    def test_update_runs_flush_and_housekeeping_or_reports_failure(self) -> None:
        service = SimpleNamespace(
            _time_now=MagicMock(return_value=42.0),
            _flush_runtime_overrides=MagicMock(),
            _state_summary=MagicMock(return_value="state"),
        )
        controller = UpdateCycleController(service, object(), object())
        with (
            patch.object(controller, "_run_update_cycle", return_value=False) as cycle,
            patch.object(controller, "_software_update_housekeeping") as housekeeping,
        ):
            self.assertIs(controller.update(), False)
        cycle.assert_called_once_with()
        service._time_now.assert_called_once_with()
        service._flush_runtime_overrides.assert_called_once_with(42.0)
        housekeeping.assert_called_once_with(service, 42.0)

        service._flush_runtime_overrides = None
        with (
            patch.object(controller, "_run_update_cycle", return_value=True),
            patch.object(controller, "_software_update_housekeeping") as housekeeping,
        ):
            self.assertIs(controller.update(), True)
        housekeeping.assert_called_once_with(service, 42.0)

        service_without_flush = SimpleNamespace(
            _time_now=MagicMock(return_value=43.0),
            _state_summary=MagicMock(return_value="state"),
        )
        controller_without_flush = UpdateCycleController(service_without_flush, object(), object())
        with (
            patch.object(controller_without_flush, "_run_update_cycle", return_value=False),
            patch.object(controller_without_flush, "_software_update_housekeeping") as housekeeping,
        ):
            self.assertIs(controller_without_flush.update(), False)
        housekeeping.assert_called_once_with(service_without_flush, 43.0)

        error = ValueError("broken")
        with (
            patch.object(controller, "_run_update_cycle", side_effect=error),
            patch.object(controller_module.logging, "warning") as warning,
        ):
            self.assertIs(controller.update(), True)
        service._state_summary.assert_called_once_with()
        warning.assert_called_once_with(
            "Error updating Venus EV charger data: %s (%s)",
            error,
            "state",
            exc_info=error,
        )


class RelayTransportContractTests(unittest.TestCase):
    def test_runtime_attribute_fallback_and_failure_contract(self) -> None:
        regular = _ObservedAttributes()
        self.assertIsNone(_TransportHarness._set_runtime_attr(regular, "value", 1))
        self.assertEqual(regular.value, 1)
        self.assertEqual(regular.assignments, [("value", 1)])

        rejecting = _RejectingAttributes()
        self.assertIsNone(_TransportHarness._set_runtime_attr(rejecting, "value", 2))
        self.assertEqual(rejecting.__dict__["value"], 2)

        with self.assertRaises(AttributeError):
            _TransportHarness._set_runtime_attr(_SlotOnly(), "value", 3)

    def test_transport_detail_and_issue_lifecycle_are_exact(self) -> None:
        error = RuntimeError("offline")
        with patch.object(transport_module, "exception_detail", return_value="RuntimeError: offline") as detail:
            self.assertEqual(_TransportHarness._charger_transport_detail(error), "RuntimeError: offline")
        detail.assert_called_once_with(error)

        svc = SimpleNamespace()
        with (
            patch.object(_TransportHarness, "_charger_readback_now", return_value=10.0) as readback_now,
            patch.object(_TransportHarness, "_charger_transport_detail", return_value="detail") as detail,
        ):
            _TransportHarness._remember_charger_transport_issue(svc, " timeout ", " rpc ", error, 10.0)
        readback_now.assert_called_once_with(svc, 10.0)
        detail.assert_called_once_with(error)
        self.assertEqual(
            vars(svc),
            {
                "_last_charger_transport_reason": "timeout",
                "_last_charger_transport_source": "rpc",
                "_last_charger_transport_detail": "detail",
                "_last_charger_transport_at": 10.0,
            },
        )
        _TransportHarness._clear_charger_transport_issue(svc)
        self.assertTrue(all(value is None for value in vars(svc).values()))

    def test_retry_lifecycle_uses_delay_hook_or_legacy_mapping(self) -> None:
        delay = MagicMock()
        svc = SimpleNamespace(_delay_source_retry=delay, _source_retry_after={"charger": 1.0})
        with (
            patch.object(_TransportHarness, "_charger_readback_now", return_value=10.0) as readback_now,
            patch.object(transport_module, "_charger_transport_retry_delay_seconds", return_value=7.0) as retry_delay,
        ):
            _TransportHarness._remember_charger_retry(svc, " timeout ", " rpc ", 10.0)
        readback_now.assert_called_once_with(svc, 10.0)
        retry_delay.assert_called_once_with(svc, " timeout ")
        delay.assert_called_once_with("charger", 10.0, 7.0)
        self.assertEqual(svc._charger_retry_reason, "timeout")
        self.assertEqual(svc._charger_retry_source, "rpc")
        self.assertEqual(svc._charger_retry_until, 17.0)

        legacy = SimpleNamespace(_delay_source_retry=None, _source_retry_after={})
        with patch.object(transport_module, "_charger_transport_retry_delay_seconds", return_value=5.0):
            _TransportHarness._remember_charger_retry(legacy, "timeout", "rpc", 20.0)
        self.assertEqual(legacy._source_retry_after, {"charger": 25.0})
        self.assertEqual(legacy._charger_retry_reason, "timeout")
        self.assertEqual(legacy._charger_retry_source, "rpc")
        self.assertEqual(legacy._charger_retry_until, 25.0)

        without_delay_hook = SimpleNamespace(_source_retry_after={})
        with patch.object(transport_module, "_charger_transport_retry_delay_seconds", return_value=2.0):
            _TransportHarness._remember_charger_retry(without_delay_hook, "offline", "http", 30.0)
        self.assertEqual(without_delay_hook._source_retry_after, {"charger": 32.0})

        _TransportHarness._clear_charger_retry(legacy)
        self.assertEqual(legacy._source_retry_after, {"charger": 0.0})
        self.assertIsNone(legacy._charger_retry_reason)
        self.assertIsNone(legacy._charger_retry_source)
        self.assertIsNone(legacy._charger_retry_until)

    def test_retry_active_delegates_freshness_at_exact_time(self) -> None:
        svc = object()
        with patch.object(transport_module, "_fresh_charger_retry_until", return_value=12.0) as fresh:
            self.assertIs(_TransportHarness._charger_retry_active(svc, 10.0), True)
        fresh.assert_called_once_with(svc, 10.0)
        with (
            patch.object(_TransportHarness, "_charger_readback_now", return_value=50.0) as readback_now,
            patch.object(transport_module, "_fresh_charger_retry_until", return_value=None) as fresh,
        ):
            self.assertIs(_TransportHarness._charger_retry_active(svc), False)
        readback_now.assert_called_once_with(svc, None)
        fresh.assert_called_once_with(svc, 50.0)


if __name__ == "__main__":
    unittest.main()

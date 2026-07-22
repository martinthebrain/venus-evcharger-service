# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact contracts for the update entry point and charger retry transport."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from venus_evcharger.update import controller as controller_module
from venus_evcharger.update import relay_charger_transport as transport_module
from venus_evcharger.update import software_update_controller as software_update_module
from venus_evcharger.update.controller import UpdateCycleController
from venus_evcharger.update.relay_charger_transport import ChargerTransportTracker
from venus_evcharger.update.software_update_controller import SoftwareUpdateController
from venus_evcharger.readback_store import InMemoryReadbackStore


def _controller_service(**values: object) -> SimpleNamespace:
    time_now = values.pop("time_now", MagicMock(return_value=0.0))
    flush = values.pop("flush_runtime_overrides", MagicMock())
    summary = values.pop("state_summary", MagicMock(return_value="state"))
    last_accepted = values.pop("last_accepted_field", MagicMock(return_value=None))
    return SimpleNamespace(
        _readback_store=InMemoryReadbackStore(),
        _worker_poll_interval_seconds=1.0,
        auto_shelly_soft_fail_seconds=10.0,
        time_now=time_now,
        state=SimpleNamespace(
            flush_runtime_overrides=flush,
            summary=summary,
            last_accepted_field=last_accepted,
        ),
        **values,
    )


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
    def test_constructor_wires_the_exact_component_graph(self) -> None:
        service = _controller_service()
        phase_values = MagicMock(name="phase_values")
        health_code = MagicMock(name="health_code")
        gateway_operations = object()
        readbacks = object()
        targets = object()
        health = object()
        telemetry = object()
        relay_foundation = SimpleNamespace(targets=targets, health=health, telemetry=telemetry)
        state = object()
        relay = object()
        pm_snapshots = object()
        inputs = object()
        offline = object()
        learning = object()
        balance = object()
        runtime_cycle = object()
        software_update = object()

        with (
            patch.object(controller_module, "ReadbackResolver", return_value=readbacks) as readback_factory,
            patch.object(controller_module, "build_relay_foundation", return_value=relay_foundation) as foundation_factory,
            patch.object(controller_module, "UpdateStateController", return_value=state) as state_factory,
            patch.object(controller_module, "complete_relay_components", return_value=relay) as relay_factory,
            patch.object(controller_module, "PmSnapshotResolver", return_value=pm_snapshots) as pm_factory,
            patch.object(controller_module, "InputCacheResolver", return_value=inputs) as input_factory,
            patch.object(controller_module, "OfflinePublisher", return_value=offline) as offline_factory,
            patch.object(controller_module, "LearningController", return_value=learning) as learning_factory,
            patch.object(controller_module, "VictronEssBalanceController", return_value=balance) as balance_factory,
            patch.object(controller_module, "RuntimeCycleCoordinator", return_value=runtime_cycle) as cycle_factory,
            patch.object(controller_module, "SoftwareUpdateController", return_value=software_update) as update_factory,
        ):
            controller = UpdateCycleController(
                service,
                phase_values,
                health_code,
                gateway_operations=gateway_operations,
            )

        self.assertIs(controller.service, service)
        readback_factory.assert_called_once_with(service._readback_store, service, service.time_now)
        self.assertIs(service._readback_resolver, readbacks)
        foundation_factory.assert_called_once_with(phase_values)
        state_factory.assert_called_once_with(service, targets, health, health_code)
        relay_factory.assert_called_once_with(relay_foundation, state)
        pm_factory.assert_called_once_with()
        input_factory.assert_called_once_with(service)
        offline_factory.assert_called_once_with(service, telemetry, state)
        learning_factory.assert_called_once_with(service)
        balance_factory.assert_called_once_with(gateway_operations)
        cycle_factory.assert_called_once_with(
            service,
            state,
            pm_snapshots,
            inputs,
            offline,
            relay,
            learning,
            balance,
        )
        update_factory.assert_called_once_with()
        components = controller.components
        self.assertEqual(
            (
                components.readbacks,
                components.state,
                components.pm_snapshots,
                components.inputs,
                components.offline,
                components.relay,
                components.learning,
                components.victron_ess_balance,
                components.runtime_cycle,
                components.software_update,
            ),
            (
                readbacks,
                state,
                pm_snapshots,
                inputs,
                offline,
                relay,
                learning,
                balance,
                runtime_cycle,
                software_update,
            ),
        )

    def test_constructor_builds_unavailable_gateway_fallback_once(self) -> None:
        service = _controller_service()
        unavailable = object()
        balance = object()

        with (
            patch.object(controller_module, "UnavailableGatewayOperations", return_value=unavailable) as unavailable_factory,
            patch.object(controller_module, "VictronEssBalanceController", return_value=balance) as balance_factory,
        ):
            controller = UpdateCycleController(service, MagicMock(), MagicMock(return_value=0))

        unavailable_factory.assert_called_once_with()
        balance_factory.assert_called_once_with(unavailable)
        self.assertIs(controller.components.victron_ess_balance, balance)

    def test_constructor_preserves_dependencies_and_constants(self) -> None:
        service = _controller_service()
        phase_values = MagicMock()
        health_code = MagicMock(return_value=0)
        controller = UpdateCycleController(service, phase_values, health_code)
        self.assertIs(controller.service, service)
        components = controller.components
        learning = components.learning
        self.assertEqual(learning.LEARNED_POWER_STABLE_MIN_SAMPLES, 3)
        self.assertEqual(learning.LEARNED_POWER_STABLE_MIN_SECONDS, 15.0)
        self.assertEqual(learning.LEARNED_POWER_STABLE_TOLERANCE_WATTS, 150.0)
        self.assertEqual(learning.LEARNED_POWER_STABLE_TOLERANCE_RATIO, 0.08)
        self.assertEqual(learning.LEARNED_POWER_SIGNATURE_MISMATCH_SESSIONS, 2)
        self.assertEqual(learning.LEARNED_POWER_VOLTAGE_TOLERANCE_VOLTS, 10.0)
        self.assertEqual(components.inputs.FUTURE_INPUT_TIMESTAMP_TOLERANCE_SECONDS, 1.0)
        self.assertIsInstance(components.software_update, SoftwareUpdateController)
        self.assertEqual(components.software_update.CHECK_INTERVAL_SECONDS, 604800.0)
        self.assertEqual(components.software_update.REQUEST_TIMEOUT_SECONDS, 5.0)

    def test_manifest_result_validates_response_and_semantics(self) -> None:
        response = MagicMock()
        response.json.return_value = {"version": "2.0", "bundle_sha256": "abc"}
        with (
            patch.object(software_update_module.requests, "get", return_value=response) as get,
            patch.object(
                SoftwareUpdateController,
                "_software_update_payload_value",
                side_effect=("2.0", "abc"),
            ) as payload_value,
            patch.object(SoftwareUpdateController, "_software_update_manifest_available", return_value=True) as available,
        ):
            result = SoftwareUpdateController._software_update_manifest_result("https://manifest", "1.0", "old")
        self.assertEqual(result, ("2.0", True, "manifest"))
        get.assert_called_once_with("https://manifest", timeout=5.0)
        response.raise_for_status.assert_called_once_with()
        self.assertEqual(payload_value.call_args_list, [call(response.json.return_value, "version"), call(response.json.return_value, "bundle_sha256")])
        available.assert_called_once_with("2.0", "abc", "1.0", "old")

    def test_manifest_result_rejects_non_mapping_payload(self) -> None:
        response = MagicMock()
        response.json.return_value = ["invalid"]
        with (
            patch.object(software_update_module.requests, "get", return_value=response) as get,
            patch.object(SoftwareUpdateController, "_software_update_payload_value") as payload_value,
        ):
            result = SoftwareUpdateController._software_update_manifest_result("source", "1", "hash")
        self.assertEqual(result, ("", False, ""))
        get.assert_called_once_with("source", timeout=5.0)
        response.raise_for_status.assert_called_once_with()
        payload_value.assert_not_called()

    def test_version_result_uses_first_trimmed_line_and_empty_contract(self) -> None:
        response = MagicMock(text=" 2.0 \nignored")
        with patch.object(software_update_module.requests, "get", return_value=response) as get:
            self.assertEqual(SoftwareUpdateController._software_update_version_result("source", "1.0"), ("2.0", True, "version-file"))
        get.assert_called_once_with("source", timeout=5.0)
        response.raise_for_status.assert_called_once_with()

        response = MagicMock(text="")
        with patch.object(software_update_module.requests, "get", return_value=response):
            self.assertEqual(SoftwareUpdateController._software_update_version_result("source", "1.0"), ("", False, "version-file"))

    def test_spawn_process_preserves_command_and_closes_log_on_expected_error(self) -> None:
        log_handle = MagicMock()
        process = MagicMock()
        command = ["/bin/sh", "restart.sh"]
        with (
            patch.object(SoftwareUpdateController, "_software_update_log_handle", return_value=log_handle) as open_log,
            patch.object(SoftwareUpdateController, "_software_update_command", return_value=command) as build,
            patch.object(software_update_module.subprocess, "Popen", return_value=process) as popen,
        ):
            self.assertEqual(
                SoftwareUpdateController._spawn_software_update_process("update.log", "/repo", "restart.sh"),
                (process, log_handle),
            )
        open_log.assert_called_once_with("update.log")
        build.assert_called_once_with("/repo", "restart.sh")
        popen.assert_called_once_with(
            command,
            cwd="/repo",
            stdout=log_handle,
            stderr=software_update_module.subprocess.STDOUT,
            start_new_session=True,
        )

        with (
            patch.object(SoftwareUpdateController, "_software_update_log_handle", return_value=log_handle),
            patch.object(SoftwareUpdateController, "_software_update_command", return_value=command),
            patch.object(software_update_module.subprocess, "Popen", side_effect=OSError("failed")),
            patch.object(SoftwareUpdateController, "_close_open_log_handle") as close,
        ):
            with self.assertRaisesRegex(OSError, "failed"):
                SoftwareUpdateController._spawn_software_update_process("update.log", "/repo", "restart.sh")
        close.assert_called_once_with(log_handle)

    def test_sign_of_life_logs_power_without_transport_identity(self) -> None:
        last_accepted = MagicMock(return_value=123.0)
        service = _controller_service(last_accepted_field=last_accepted)
        controller = UpdateCycleController(service, MagicMock(), MagicMock(return_value=0))
        with patch.object(controller_module.logging, "info") as info:
            self.assertIs(controller.sign_of_life(), True)
        last_accepted.assert_called_once_with("ac_power_w")
        info.assert_called_once_with("Last accepted AC power publication: %s", 123.0)

    def test_update_runs_flush_and_housekeeping_or_reports_failure(self) -> None:
        service = _controller_service(
            time_now=MagicMock(return_value=42.0),
            flush_runtime_overrides=MagicMock(),
            state_summary=MagicMock(return_value="state"),
        )
        controller = UpdateCycleController(service, MagicMock(), MagicMock(return_value=0))
        with (
            patch.object(controller.components.runtime_cycle, "run", return_value=False) as cycle,
            patch.object(controller.components.software_update, "housekeeping") as housekeeping,
        ):
            self.assertIs(controller.update(), False)
        cycle.assert_called_once_with()
        service.time_now.assert_called_once_with()
        service.state.flush_runtime_overrides.assert_called_once_with(42.0)
        housekeeping.assert_called_once_with(service, 42.0)

        error = ValueError("broken")
        with (
            patch.object(controller.components.runtime_cycle, "run", side_effect=error),
            patch.object(controller_module.logging, "warning") as warning,
        ):
            self.assertIs(controller.update(), True)
        service.state.summary.assert_called_once_with()
        warning.assert_called_once_with(
            "Error updating Venus EV charger data: %s (%s)",
            error,
            "state",
            exc_info=error,
        )


class RelayTransportContractTests(unittest.TestCase):
    def test_runtime_attribute_fallback_and_failure_contract(self) -> None:
        regular = _ObservedAttributes()
        self.assertIsNone(ChargerTransportTracker.set_runtime_attr(regular, "value", 1))
        self.assertEqual(regular.value, 1)
        self.assertEqual(regular.assignments, [("value", 1)])

        rejecting = _RejectingAttributes()
        self.assertIsNone(ChargerTransportTracker.set_runtime_attr(rejecting, "value", 2))
        self.assertEqual(rejecting.__dict__["value"], 2)

        with self.assertRaises(AttributeError):
            ChargerTransportTracker.set_runtime_attr(_SlotOnly(), "value", 3)

    def test_transport_detail_and_issue_lifecycle_are_exact(self) -> None:
        error = RuntimeError("offline")
        with patch.object(transport_module, "exception_detail", return_value="RuntimeError: offline") as detail:
            self.assertEqual(ChargerTransportTracker.transport_detail(error), "RuntimeError: offline")
        detail.assert_called_once_with(error)

        svc = SimpleNamespace()
        with (
            patch.object(ChargerTransportTracker, "now", return_value=10.0) as transport_now,
            patch.object(ChargerTransportTracker, "transport_detail", return_value="detail") as detail,
        ):
            ChargerTransportTracker.remember_issue(svc, " timeout ", " rpc ", error, 10.0)
        transport_now.assert_called_once_with(svc, 10.0)
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
        ChargerTransportTracker.clear_issue(svc)
        self.assertTrue(all(value is None for value in vars(svc).values()))

    def test_retry_lifecycle_uses_delay_hook_or_legacy_mapping(self) -> None:
        delay = MagicMock()
        svc = SimpleNamespace(_delay_source_retry=delay, _source_retry_after={"charger": 1.0})
        with (
            patch.object(ChargerTransportTracker, "now", return_value=10.0) as transport_now,
            patch.object(transport_module, "_charger_transport_retry_delay_seconds", return_value=7.0) as retry_delay,
        ):
            ChargerTransportTracker.remember_retry(svc, " timeout ", " rpc ", 10.0)
        transport_now.assert_called_once_with(svc, 10.0)
        retry_delay.assert_called_once_with(svc, " timeout ")
        delay.assert_called_once_with("charger", 10.0, 7.0)
        self.assertEqual(svc._charger_retry_reason, "timeout")
        self.assertEqual(svc._charger_retry_source, "rpc")
        self.assertEqual(svc._charger_retry_until, 17.0)

        legacy = SimpleNamespace(_delay_source_retry=None, _source_retry_after={})
        with patch.object(transport_module, "_charger_transport_retry_delay_seconds", return_value=5.0):
            ChargerTransportTracker.remember_retry(legacy, "timeout", "rpc", 20.0)
        self.assertEqual(legacy._source_retry_after, {"charger": 25.0})
        self.assertEqual(legacy._charger_retry_reason, "timeout")
        self.assertEqual(legacy._charger_retry_source, "rpc")
        self.assertEqual(legacy._charger_retry_until, 25.0)

        without_delay_hook = SimpleNamespace(_source_retry_after={})
        with patch.object(transport_module, "_charger_transport_retry_delay_seconds", return_value=2.0):
            ChargerTransportTracker.remember_retry(without_delay_hook, "offline", "http", 30.0)
        self.assertEqual(without_delay_hook._source_retry_after, {"charger": 32.0})

        ChargerTransportTracker.clear_retry(legacy)
        self.assertEqual(legacy._source_retry_after, {"charger": 0.0})
        self.assertIsNone(legacy._charger_retry_reason)
        self.assertIsNone(legacy._charger_retry_source)
        self.assertIsNone(legacy._charger_retry_until)

    def test_retry_active_delegates_freshness_at_exact_time(self) -> None:
        svc = object()
        with patch.object(transport_module, "_fresh_charger_retry_until", return_value=12.0) as fresh:
            self.assertIs(ChargerTransportTracker.retry_active(svc, 10.0), True)
        fresh.assert_called_once_with(svc, 10.0)
        with (
            patch.object(ChargerTransportTracker, "now", return_value=50.0) as transport_now,
            patch.object(transport_module, "_fresh_charger_retry_until", return_value=None) as fresh,
        ):
            self.assertIs(ChargerTransportTracker.retry_active(svc), False)
        transport_now.assert_called_once_with(svc, None)
        fresh.assert_called_once_with(svc, 50.0)


if __name__ == "__main__":
    unittest.main()

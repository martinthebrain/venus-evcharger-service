# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact asynchronous introspection and energy-refresh contracts."""

from __future__ import annotations

from unittest.mock import call

from tests.support.dbus_gateway_adapter_harness import (
    DbusOperationDeferred,
    GatewayAdapterContractCase,
    MagicMock,
    install_mock,
    introspection_module,
    patch,
)
from venus_evcharger.dbus_adapter.async_broker import DbusMethodCall
from venus_evcharger.dbus_adapter.contracts import CommandExecution
from venus_evcharger.ipc.energy import EnergyRefreshRequest


class DbusAdapterProcessIntrospectionAsyncContractTests(GatewayAdapterContractCase):
    """Pin adapter-owned scheduling and asynchronous completion semantics."""

    def test_introspection_call_preserves_every_explicit_and_default_field(self) -> None:
        explicit = introspection_module._introspection_call(
            {
                "service": "svc.inspect",
                "path": "/Device/Path",
                "priority": "discovery",
                "timeout": 2.75,
            },
            "command.json",
        )
        self.assertEqual(
            explicit,
            DbusMethodCall(
                service="svc.inspect",
                path="/Device/Path",
                interface="org.freedesktop.DBus.Introspectable",
                method_name="Introspect",
                signature="",
                rate_kind="introspection",
                metric_kind="introspection",
                source="svc.inspect/Device/Path",
                priority="discovery",
                timeout_seconds=2.75,
                owner_path="command.json",
            ),
        )
        self.assertEqual(
            introspection_module._introspection_call(
                {"service": "svc.default"},
                "command.json",
            ),
            DbusMethodCall(
                service="svc.default",
                path="/",
                interface="org.freedesktop.DBus.Introspectable",
                method_name="Introspect",
                signature="",
                rate_kind="introspection",
                metric_kind="introspection",
                source="svc.default/",
                priority="diagnostic",
                timeout_seconds=1.0,
                owner_path="command.json",
            ),
        )
        self.assertIsNone(introspection_module._introspection_call({}, "command.json"))
        self.assertIsNone(
            introspection_module._introspection_call(
                {"service": ""},
                "command.json",
            )
        )

    def test_invalid_introspection_deadline_is_dropped_before_submission(self) -> None:
        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            submit = install_mock(adapter.operation_broker, "submit", MagicMock())
            completion = MagicMock()

            self.assertEqual(
                adapter.introspection_role.schedule_introspection(
                    {"service": "svc.invalid", "timeout": 0.0},
                    "command.json",
                    completion,
                ),
                CommandExecution.immediate("dropped"),
            )

            submit.assert_not_called()
            completion.assert_not_called()

    def test_healthy_introspection_guard_preserves_command_owner_and_completion(self) -> None:
        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            role = adapter.introspection_role
            command = {"service": "svc.inspect"}
            completion = MagicMock()
            state = install_mock(adapter.circuit, "state", MagicMock(return_value="ok"))
            schedule = install_mock(
                role,
                "schedule_introspection",
                MagicMock(return_value=CommandExecution.pending()),
            )

            self.assertEqual(
                role.schedule_introspection_if_healthy(
                    command,
                    "owned-command.json",
                    completion,
                ),
                CommandExecution.pending(),
            )
            schedule.assert_called_once_with(command, "owned-command.json", completion)

            schedule.reset_mock()
            state.return_value = "protective"
            self.assertEqual(
                role.schedule_introspection_if_healthy(
                    command,
                    "owned-command.json",
                    completion,
                ),
                CommandExecution.immediate("deferred"),
            )
            schedule.assert_not_called()
            completion.assert_not_called()

    def test_introspection_schedule_preserves_owner_when_building_async_call(self) -> None:
        with self.adapter_scenario() as scenario:
            role = scenario.adapter.introspection_role
            command = {"service": "svc.inspect"}
            completion = MagicMock()
            method_call = object()
            build_call = MagicMock(return_value=method_call)
            submit = install_mock(
                role,
                "_submit_introspection",
                MagicMock(return_value=CommandExecution.pending()),
            )

            with patch.object(introspection_module, "_introspection_call", build_call):
                self.assertEqual(
                    role.schedule_introspection(
                        command,
                        "owned-command.json",
                        completion,
                    ),
                    CommandExecution.pending(),
                )

            build_call.assert_called_once_with(command, "owned-command.json")
            submit.assert_called_once_with(method_call, completion)

    def test_enqueue_introspection_command_writes_the_complete_durable_payload(self) -> None:
        config = "[DEFAULT]\nDbusIntrospectionTimeoutSeconds=2.75\n"
        with self.adapter_scenario(config) as scenario:
            adapter = scenario.adapter
            enqueue = install_mock(adapter.commands, "enqueue", MagicMock())

            adapter.introspection_role.enqueue_introspection_command(
                "svc.discovery",
                "/Discovery",
                priority=89,
                source="pv",
                reason="source-probe",
            )
            adapter.introspection_role.enqueue_introspection_command(
                "svc.optional",
                "/Optional",
                priority=90,
                source="battery",
                reason="diagnostic-probe",
            )

            self.assertEqual(
                enqueue.call_args_list,
                [
                    call(
                        {
                            "kind": "introspect",
                            "service": "svc.discovery",
                            "path": "/Discovery",
                            "priority": "discovery",
                            "source": "pv",
                            "reason": "source-probe",
                            "timeout": 2.75,
                            "coalesce_key": "introspect:svc.discovery:/Discovery",
                        }
                    ),
                    call(
                        {
                            "kind": "introspect",
                            "service": "svc.optional",
                            "path": "/Optional",
                            "priority": "optional",
                            "source": "battery",
                            "reason": "diagnostic-probe",
                            "timeout": 2.75,
                            "coalesce_key": "introspect:svc.optional:/Optional",
                        }
                    ),
                ],
            )

        with self.adapter_scenario() as scenario:
            enqueue = install_mock(scenario.adapter.commands, "enqueue", MagicMock())
            scenario.adapter.introspection_role.enqueue_introspection_command(
                "svc.default",
                "/",
                priority=90,
                source="diagnostic",
                reason="default-timeout",
            )
            self.assertEqual(enqueue.call_args.args[0]["timeout"], 1.0)

    def test_background_due_uses_interval_boundary_services_and_discovery_policy(self) -> None:
        config = "[DEFAULT]\nDbusIntrospectionFullScanIntervalSeconds=120\n"
        with self.adapter_scenario(config) as scenario:
            adapter = scenario.adapter
            role = adapter.introspection_role
            allowed = install_mock(adapter.circuit, "allows_priority", MagicMock(return_value=True))
            adapter._last_introspection_full_scan_at = 40.0
            adapter.cache.update_services(["svc.present"], now=1.0)

            self.assertFalse(role.background_introspection_due(159.999))
            allowed.assert_not_called()
            self.assertTrue(role.background_introspection_due(160.0))
            allowed.assert_called_once_with("discovery")

            allowed.reset_mock()
            adapter.cache.update_services([], now=2.0)
            self.assertFalse(role.background_introspection_due(200.0))
            allowed.assert_not_called()
            adapter.cache.update_services(["svc.present"], now=3.0)
            adapter.dbus_introspection_enabled = False
            self.assertFalse(role.background_introspection_due(200.0))
            allowed.assert_not_called()
            adapter.dbus_introspection_enabled = True
            allowed.return_value = False
            self.assertFalse(role.background_introspection_due(200.0))
            allowed.assert_called_once_with("discovery")

        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            adapter.cache.update_services(["svc.present"], now=1.0)
            self.assertFalse(adapter.introspection_role.background_introspection_due(21599.999))
            self.assertTrue(adapter.introspection_role.background_introspection_due(21600.0))

    def test_non_write_dispatch_requires_kind_and_preserves_callback_identity(self) -> None:
        with self.adapter_scenario() as scenario:
            role = scenario.adapter.introspection_role
            completion = MagicMock()
            refresh = install_mock(role, "refresh_energy_inputs_command", MagicMock(return_value="applied"))
            schedule = install_mock(
                role,
                "schedule_introspection_if_healthy",
                MagicMock(return_value=CommandExecution.pending()),
            )
            refresh_command = {"kind": "refresh_energy_inputs", "request_id": "refresh"}
            introspection_command = {"kind": "introspect", "service": "svc"}

            self.assertEqual(
                role.schedule_non_write_command(refresh_command, "refresh.json", completion),
                CommandExecution.immediate("applied"),
            )
            self.assertIs(refresh.call_args.args[0], refresh_command)
            self.assertEqual(
                role.schedule_non_write_command(
                    introspection_command,
                    "introspection.json",
                    completion,
                ),
                CommandExecution.pending(),
            )
            self.assertIs(schedule.call_args.args[0], introspection_command)
            self.assertEqual(schedule.call_args.args[1], "introspection.json")
            self.assertIs(schedule.call_args.args[2], completion)
            self.assertEqual(
                role.schedule_non_write_command(
                    {"kind": "unknown"},
                    "unknown.json",
                    completion,
                ),
                CommandExecution.immediate("dropped"),
            )
            self.assertEqual(
                role.schedule_non_write_command({}, "empty.json", completion),
                CommandExecution.immediate("dropped"),
            )
            self.assertEqual(
                role.schedule_non_write_command(
                    {"type": "introspect", "service": "svc"},
                    "type-only.json",
                    completion,
                ),
                CommandExecution.immediate("dropped"),
            )
            completion.assert_not_called()

    def test_refresh_scopes_force_only_stale_reads_and_required_discovery(self) -> None:
        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            role = adapter.introspection_role
            force_reads = install_mock(adapter.read_scheduler, "force_due", MagicMock())
            force_discovery = install_mock(adapter.discovery, "force_due", MagicMock())
            adapter._last_introspection_full_scan_at = 77.0
            adapter.cache.values.update(
                {
                    "grid_power_w": {"confirmed_at": 90.0},
                    "pv_power_w": {"confirmed_at": 89.999},
                    "battery_soc": {},
                }
            )

            all_request = EnergyRefreshRequest("all", "all", 10.0).to_command(source="test")
            with patch.object(introspection_module.time, "time", return_value=100.0):
                self.assertEqual(role.refresh_energy_inputs_command(all_request), "applied")
            force_discovery.assert_called_once_with()
            force_reads.assert_called_once_with(
                (
                    "pv_power_w",
                    "battery_soc",
                    "battery_net_power_w",
                    "battery_capacity_ah",
                    "battery_voltage_v",
                )
            )
            self.assertEqual(adapter._last_introspection_full_scan_at, 77.0)

            force_reads.reset_mock()
            force_discovery.reset_mock()
            adapter._last_introspection_full_scan_at = 88.0
            topology = EnergyRefreshRequest("topology", "topology", 10.0).to_command(source="test")
            self.assertEqual(role.refresh_energy_inputs_command(topology), "applied")
            force_discovery.assert_called_once_with()
            force_reads.assert_called_once_with(())
            self.assertEqual(adapter._last_introspection_full_scan_at, 88.0)

            force_reads.reset_mock()
            force_discovery.reset_mock()
            adapter._last_introspection_full_scan_at = 99.0
            grid = EnergyRefreshRequest("grid", "grid", 0.0).to_command(source="test")
            with patch.object(introspection_module.time, "time", return_value=100.0):
                self.assertEqual(role.refresh_energy_inputs_command(grid), "applied")
            force_reads.assert_called_once_with(("grid_power_w",))
            force_discovery.assert_not_called()
            self.assertEqual(adapter._last_introspection_full_scan_at, 99.0)

    def test_energy_source_refresh_requires_a_known_source_and_forwards_its_keys(self) -> None:
        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            role = adapter.introspection_role
            force_reads = install_mock(adapter.read_scheduler, "force_due", MagicMock())
            source_keys = install_mock(
                adapter.energy_discovery,
                "read_keys_for_source",
                MagicMock(return_value=("pv_power_w",)),
            )
            request = EnergyRefreshRequest(
                "source",
                "energy_source",
                0.0,
                source_id="opaque-source",
            ).to_command(source="test")

            with patch.object(introspection_module.time, "time", return_value=100.0):
                self.assertEqual(role.refresh_energy_inputs_command(request), "applied")
            source_keys.assert_called_once_with("opaque-source")
            force_reads.assert_called_once_with(("pv_power_w",))

            source_keys.return_value = ()
            force_reads.reset_mock()
            self.assertEqual(role.refresh_energy_inputs_command(request), "dropped")
            force_reads.assert_not_called()
            self.assertEqual(role.refresh_energy_inputs_command({"kind": "refresh_energy_inputs"}), "dropped")

    def test_async_submit_preserves_call_and_completes_success_exactly_once(self) -> None:
        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            role = adapter.introspection_role
            completion = MagicMock()
            operation = object()
            submit = install_mock(adapter.operation_broker, "submit", MagicMock(return_value=41))
            factory = MagicMock(return_value=operation)
            adapter._introspection_queue_depth = 2
            proxy_call = DbusMethodCall(
                service="svc.inspect",
                path="/Device",
                interface="org.freedesktop.DBus.Introspectable",
                method_name="Introspect",
                signature="",
                rate_kind="introspection",
                metric_kind="introspection",
                source="svc.inspect/Device",
                priority="diagnostic",
                timeout_seconds=1.5,
                owner_path="command.json",
            )

            with patch.object(introspection_module, "dbus_call_operation", factory):
                execution = role._submit_introspection(proxy_call, completion)

            self.assertEqual(execution, CommandExecution.pending())
            completion.assert_not_called()
            submit.assert_called_once_with(operation)
            self.assertIs(factory.call_args.args[0], adapter.connection)
            self.assertIs(factory.call_args.args[1], proxy_call)
            factory.call_args.kwargs["on_success"]("<node/>")
            completion.assert_called_once_with("applied")
            self.assertEqual(adapter._introspection_queue_depth, 1)
            self.assertEqual(
                adapter.cache.values["introspection:svc.inspect:/Device"]["value"],
                "<node/>",
            )

    def test_async_submit_error_and_immediate_failures_have_distinct_lifecycles(self) -> None:
        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            role = adapter.introspection_role
            completion = MagicMock()
            operation = object()
            factory = MagicMock(return_value=operation)
            proxy_call = introspection_module._introspection_call(
                {"service": "svc.failed", "path": "/Path"},
                "command.json",
            )
            assert proxy_call is not None
            submit = install_mock(adapter.operation_broker, "submit", MagicMock(return_value=1))
            adapter._introspection_queue_depth = 3

            with patch.object(introspection_module, "dbus_call_operation", factory):
                self.assertEqual(role._submit_introspection(proxy_call, completion), CommandExecution.pending())
            error = RuntimeError("callback failure")
            factory.call_args.kwargs["on_error"](error)
            completion.assert_called_once_with("dropped")
            self.assertEqual(adapter._introspection_queue_depth, 2)
            failed = adapter.cache.values["introspection:svc.failed:/Path"]
            self.assertEqual(failed["source"], "svc.failed/Path")
            self.assertEqual(failed["last_error"], "callback failure")

            completion.reset_mock()
            submit.side_effect = DbusOperationDeferred("busy")
            with patch.object(introspection_module, "dbus_call_operation", factory):
                self.assertEqual(
                    role._submit_introspection(proxy_call, completion),
                    CommandExecution.immediate("deferred"),
                )
            completion.assert_not_called()
            self.assertEqual(adapter._introspection_queue_depth, 2)

            submit.side_effect = OSError("submit failure")
            with patch.object(introspection_module, "dbus_call_operation", factory):
                self.assertEqual(
                    role._submit_introspection(proxy_call, completion),
                    CommandExecution.immediate("dropped"),
                )
            completion.assert_not_called()
            self.assertEqual(adapter._introspection_queue_depth, 1)
            self.assertEqual(failed["freshness_kind"], "diagnostic")

    def test_record_outcome_forwards_exact_values_before_reporting_applied(self) -> None:
        with self.adapter_scenario() as scenario:
            role = scenario.adapter.introspection_role
            record = install_mock(role, "record_introspection_xml", MagicMock())
            xml = object()

            self.assertEqual(role._record_introspection_outcome("svc", "/Path", xml), "applied")
            record.assert_called_once_with("svc", "/Path", xml)


if __name__ == "__main__":
    import unittest

    unittest.main()

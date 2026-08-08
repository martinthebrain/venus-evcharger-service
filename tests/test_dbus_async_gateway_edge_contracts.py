# SPDX-License-Identifier: GPL-3.0-or-later
"""Edge contracts for asynchronous gateway scheduling boundaries."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.support.dbus_gateway_adapter_harness import (
    DbusOperationDeferred,
    GatewayAdapterContractCase,
    install_mock,
    install_read_responder,
)
from venus_evcharger.dbus_adapter.contracts import CommandExecution
from venus_evcharger.dbus_adapter.async_request import DbusWireRequest
from venus_evcharger.dbus_adapter.read.transport import (
    DBUS_READ_TIMEOUT_SECONDS,
    BusItemReadCall,
    submit_busitem_read,
)
from venus_evcharger.ipc.gateway_operations import gx_relay_refresh_command


class DbusAsyncGatewayEdgeContracts(GatewayAdapterContractCase):
    """Pin callback timing and immediate submission-failure behavior."""

    def test_read_executor_forwards_completed_outcome_to_caller(self) -> None:
        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            install_read_responder(adapter, MagicMock(return_value=12.5))
            completion = MagicMock()

            outcome = adapter.read_executor.poll_read_spec(
                "power",
                {"service": "svc.read", "path": "/Power"},
                completion=completion,
            )

            self.assertEqual(outcome, "applied")
            completion.assert_called_once_with("applied")

    def test_read_transport_rejects_incomplete_target(self) -> None:
        for service, path in (("", "/Power"), ("svc.read", "")):
            with self.subTest(service=service, path=path):
                with self.assertRaises(ValueError) as raised:
                    submit_busitem_read(
                        MagicMock(),
                        BusItemReadCall(service, path),
                        on_success=MagicMock(),
                        on_error=MagicMock(),
                    )
                self.assertEqual(
                    str(raised.exception),
                    "DBus read target requires service and path",
                )

    def test_read_transport_builds_exact_broker_operation(self) -> None:
        for optional, metric_kind, priority in (
            (False, "read", "read"),
            (True, "optional_read", "optional"),
        ):
            with self.subTest(optional=optional):
                adapter = MagicMock()
                on_success = MagicMock()
                on_error = MagicMock()

                submit_busitem_read(
                    adapter,
                    BusItemReadCall("svc.read", "/Power", optional=optional),
                    on_success=on_success,
                    on_error=on_error,
                )

                operation = adapter.operation_broker.submit.call_args.args[0]
                self.assertEqual(operation.rate_kind, "read")
                self.assertEqual(operation.metric_kind, metric_kind)
                self.assertEqual(operation.source, "svc.read/Power")
                self.assertEqual(operation.priority, priority)
                self.assertEqual(operation.timeout_seconds, DBUS_READ_TIMEOUT_SECONDS)
                self.assertIs(operation.on_error, on_error)
                self.assertIs(operation.optional_failure, optional)

                operation.on_success("12.5")
                on_success.assert_called_once_with(12.5)
                error = RuntimeError("read failed")
                operation.on_error(error)
                on_error.assert_called_once_with(error)

    def test_read_transport_starter_uses_non_introspecting_busitem_call(self) -> None:
        adapter = MagicMock()
        pending = object()
        adapter.connection.send_async.return_value = pending
        submit_busitem_read(
            adapter,
            BusItemReadCall("svc.read", "/Power"),
            on_success=MagicMock(),
            on_error=MagicMock(),
        )
        operation = adapter.operation_broker.submit.call_args.args[0]
        reply = MagicMock()
        error = MagicMock()

        returned = operation.starter(reply, error)

        self.assertIs(returned, pending)
        call = adapter.connection.send_async.call_args
        self.assertEqual(
            call.args[0],
            DbusWireRequest(
                service="svc.read",
                path="/Power",
                interface="com.victronenergy.BusItem",
                method_name="GetValue",
                signature="",
                timeout_seconds=DBUS_READ_TIMEOUT_SECONDS,
            ),
        )
        self.assertEqual(call.kwargs, {})
        self.assertEqual(len(call.args), 3)
        reply_handler = call.args[1]
        error_handler = call.args[2]
        self.assertTrue(callable(reply_handler))
        self.assertTrue(callable(error_handler))
        reply_handler(17)
        reply.assert_called_once_with(17)
        failure = RuntimeError("read failed")
        error_handler(failure)
        error.assert_called_once_with(failure)

    def test_read_scheduler_records_callback_failure(self) -> None:
        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            spec = {"service": "svc.read", "path": "/Power"}
            install_mock(
                adapter.read_scheduler,
                "next_due",
                MagicMock(return_value=("power", spec, 2.0)),
            )
            record_error = install_mock(adapter.read_scheduler, "record_error", MagicMock())

            def fail_read(
                _key: str,
                _spec: object,
                *,
                completion: object,
            ) -> str:
                assert callable(completion)
                completion("dropped")
                return "dropped"

            install_mock(adapter.read_executor, "poll_read_spec", MagicMock(side_effect=fail_read))

            self.assertTrue(adapter.io_role.poll_one_due_read_once())
            record_error.assert_called_once_with(
                "power",
                monotonic_at=unittest.mock.ANY,
                interval=2.0,
            )

    def test_read_scheduler_leaves_partial_aggregate_due(self) -> None:
        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            spec = {"service": "svc.read", "path": "/Power"}
            install_mock(
                adapter.read_scheduler,
                "next_due",
                MagicMock(return_value=("power", spec, 2.0)),
            )
            record_success = install_mock(adapter.read_scheduler, "record_success", MagicMock())
            record_error = install_mock(adapter.read_scheduler, "record_error", MagicMock())

            def defer_read(
                _key: str,
                _spec: object,
                *,
                completion: object,
            ) -> str:
                assert callable(completion)
                completion("deferred")
                return "deferred"

            install_mock(adapter.read_executor, "poll_read_spec", MagicMock(side_effect=defer_read))
            adapter.read_executor.last_operation_performed = True

            self.assertTrue(adapter.io_role.poll_one_due_read_once())
            record_success.assert_not_called()
            record_error.assert_not_called()

    def test_discovery_classifies_immediate_submission_failure(self) -> None:
        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            install_mock(adapter.discovery, "due", MagicMock(return_value=True))
            record_error = install_mock(adapter.discovery, "record_error", MagicMock())
            error = RuntimeError("submit failed")

            with patch.object(adapter.operation_broker, "submit", side_effect=error):
                self.assertTrue(adapter.io_role.refresh_services_if_due_once())

            self.assertIs(record_error.call_args.args[0], error)

    def test_introspection_classifies_immediate_submission_failures(self) -> None:
        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            command = {"service": "svc.inspect", "path": "/"}
            completion = MagicMock()

            with patch.object(
                adapter.operation_broker,
                "submit",
                side_effect=DbusOperationDeferred("busy"),
            ):
                self.assertEqual(
                    adapter.introspection_role.schedule_introspection(
                        command,
                        "command.json",
                        completion,
                    ),
                    CommandExecution.immediate("deferred"),
                )

            error = OSError("submit failed")
            with patch.object(adapter.operation_broker, "submit", side_effect=error):
                self.assertEqual(
                    adapter.introspection_role.schedule_introspection(
                        command,
                        "command.json",
                        completion,
                    ),
                    CommandExecution.immediate("dropped"),
                )
            self.assertEqual(
                adapter.cache.values["introspection:svc.inspect:/"]["last_error"],
                "submit failed",
            )

    def test_durable_queue_handles_completion_during_dispatch(self) -> None:
        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            queue = adapter.write_scheduler.command_queue
            command = gx_relay_refresh_command(0)
            path = adapter.commands.enqueue(command)
            pending = adapter.commands.load_pending()
            loaded = next(payload for candidate, payload in pending if candidate == path)

            def complete_immediately(
                _path: str,
                _command: object,
                *,
                completion: object,
            ) -> CommandExecution:
                assert callable(completion)
                completion("applied")
                return CommandExecution.pending()

            install_mock(
                queue.dispatcher,
                "execute",
                MagicMock(side_effect=complete_immediately),
            )
            finalize = install_mock(
                queue,
                "_finalize_loaded_command",
                MagicMock(),
            )

            self.assertEqual(
                queue.process_loaded_command(path, loaded, pending_commands=pending),
                "applied",
            )
            finalize.assert_called_once_with(
                path,
                loaded,
                "applied",
                pending_commands=pending,
            )
            self.assertTrue(Path(path).exists())

    def test_durable_queue_preserves_context_for_late_completion(self) -> None:
        with self.adapter_scenario() as scenario:
            queue = scenario.adapter.write_scheduler.command_queue
            command = gx_relay_refresh_command(0)
            pending = [("relay.json", command)]
            completions: list[object] = []

            def remain_pending(
                _path: str,
                _command: object,
                *,
                completion: object,
            ) -> CommandExecution:
                completions.append(completion)
                return CommandExecution.pending()

            install_mock(
                queue.dispatcher,
                "execute",
                MagicMock(side_effect=remain_pending),
            )
            finalize = install_mock(
                queue,
                "_finalize_loaded_command",
                MagicMock(),
            )

            self.assertEqual(
                queue._dispatch_loaded_command(
                    "relay.json",
                    command,
                    pending_commands=pending,
                ),
                "deferred",
            )
            finalize.assert_not_called()
            completion = completions.pop()
            self.assertTrue(callable(completion))
            completion("applied")
            finalize.assert_called_once_with(
                "relay.json",
                command,
                "applied",
                pending_commands=pending,
            )

            completion("dropped")
            finalize.assert_called_once_with(
                "relay.json",
                command,
                "applied",
                pending_commands=pending,
            )

    def test_durable_queue_uses_only_the_first_synchronous_completion(self) -> None:
        with self.adapter_scenario() as scenario:
            queue = scenario.adapter.write_scheduler.command_queue
            command = gx_relay_refresh_command(0)

            def complete_twice(
                _path: str,
                _command: object,
                *,
                completion: object,
            ) -> CommandExecution:
                assert callable(completion)
                completion("applied")
                completion("dropped")
                return CommandExecution.pending()

            install_mock(
                queue.dispatcher,
                "execute",
                MagicMock(side_effect=complete_twice),
            )
            finalize = install_mock(
                queue,
                "_finalize_loaded_command",
                MagicMock(),
            )

            self.assertEqual(
                queue._dispatch_loaded_command(
                    "relay.json",
                    command,
                    pending_commands=[],
                ),
                "applied",
            )
            finalize.assert_called_once_with(
                "relay.json",
                command,
                "applied",
                pending_commands=[],
            )


if __name__ == "__main__":
    unittest.main()

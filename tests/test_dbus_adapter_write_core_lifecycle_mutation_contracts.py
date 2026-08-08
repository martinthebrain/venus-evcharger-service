# SPDX-License-Identifier: GPL-3.0-or-later
"""Mutation-strength lifecycle contracts for the gateway write queue."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from tests.support.dbus_gateway_adapter_harness import (
    CommandFileList,
    GatewayAdapterContractCase,
    MagicMock,
    evcs_publication,
    install_mock,
    patch,
    unittest,
    write_core_module,
    write_dispatch_module,
)
from venus_evcharger.dbus_adapter.contracts import (
    CommandCompletion,
    CommandExecution,
)
from venus_evcharger.dbus_adapter.rate import DbusOperationDeferred
from venus_evcharger.ipc.command_types import CommandMapping
from venus_evcharger.ipc.gateway_operations import gx_relay_refresh_command


class DbusAdapterWriteCoreLifecycleMutationContracts(GatewayAdapterContractCase):
    """Pin dispatch, durable arbitration, retirement, and retry outcomes."""

    def test_completion_phase_contract_is_exhaustive(self) -> None:
        self.assertIs(
            write_core_module._completion_action(write_core_module._CompletionPhase.DISPATCHING),
            write_core_module._CompletionAction.BUFFER,
        )
        self.assertIs(
            write_core_module._completion_action(write_core_module._CompletionPhase.WAITING),
            write_core_module._CompletionAction.FINALIZE,
        )
        self.assertIs(
            write_core_module._completion_action(write_core_module._CompletionPhase.CLOSED),
            write_core_module._CompletionAction.IGNORE,
        )
        invalid = cast(write_core_module._CompletionPhase, object())
        with self.assertRaises(RuntimeError):
            write_core_module._completion_action(invalid)

    def test_next_local_publish_passes_exact_command_and_timestamp_to_budget(self) -> None:
        with self.adapter_scenario() as scenario:
            queue = scenario.adapter.write_scheduler.command_queue
            remote = gx_relay_refresh_command(0)
            local = evcs_publication({"mode": 1})
            pending: CommandFileList = [("remote.json", remote), ("local.json", local)]
            install_mock(scenario.adapter.commands, "load_pending", MagicMock(return_value=pending))
            install_mock(scenario.adapter.commands, "coalesce", MagicMock(return_value=pending))
            install_mock(queue.health, "prioritized_commands", MagicMock(return_value=pending))
            budget = install_mock(queue.health, "budget_available", MagicMock(return_value=True))

            with patch.object(write_core_module.time, "time", return_value=42.5):
                self.assertEqual(queue.next_local_publish_command(), ("local.json", local))

            budget.assert_called_once_with(local, 42.5)

    def test_missing_coalesce_key_returns_before_snapshot_or_stale_scan(self) -> None:
        with self.adapter_scenario() as scenario:
            queue = scenario.adapter.write_scheduler.command_queue
            snapshot = install_mock(queue, "pending_snapshot", MagicMock())
            stale = patch.object(write_core_module, "stale_coalesced_paths")
            with stale as stale_paths:
                queue.drop_stale_coalesced_commands(
                    "plain.json",
                    {"kind": "refresh_energy_inputs"},
                )
            snapshot.assert_not_called()
            stale_paths.assert_not_called()

    def test_process_one_default_includes_local_publications(self) -> None:
        with self.adapter_scenario() as scenario:
            queue = scenario.adapter.write_scheduler.command_queue
            command = evcs_publication({"mode": 1})
            pending: CommandFileList = [("publish.json", command)]
            install_mock(scenario.adapter.commands, "load_pending", MagicMock(return_value=pending))
            install_mock(scenario.adapter.commands, "coalesce", MagicMock(return_value=pending))
            install_mock(queue.health, "prioritized_commands", MagicMock(return_value=pending))
            select = install_mock(queue, "select_next_command", MagicMock(return_value=None))

            self.assertFalse(queue.process_one())
            self.assertIsNone(queue.last_scheduled_outcome)
            select.assert_called_once_with(
                pending,
                include_local_publish=True,
                required_kind=None,
            )

    def test_empty_urgent_scan_resets_previous_outcome_to_none(self) -> None:
        with self.adapter_scenario() as scenario:
            queue = scenario.adapter.write_scheduler.command_queue
            install_mock(scenario.adapter.commands, "load_pending", MagicMock(return_value=[]))
            install_mock(scenario.adapter.commands, "coalesce", MagicMock(return_value=[]))
            install_mock(queue.health, "prioritized_commands", MagicMock(return_value=[]))
            queue.last_scheduled_outcome = "applied"

            self.assertFalse(queue.process_urgent_once())
            self.assertIsNone(queue.last_scheduled_outcome)

    def test_process_command_blocks_before_dispatch(self) -> None:
        with self.adapter_scenario() as scenario:
            dispatcher = scenario.adapter.write_scheduler.command_dispatcher
            command = evcs_publication({"mode": 1})
            blocked = install_mock(dispatcher, "_command_blocked", MagicMock(return_value=True))
            dispatch = install_mock(
                dispatcher,
                "_dispatch",
                MagicMock(return_value=CommandExecution.immediate("applied")),
            )

            self.assertEqual(dispatcher.process(command, command_file="publish.json"), "deferred")
            dispatch.assert_not_called()

            blocked.return_value = False
            self.assertEqual(dispatcher.process(command, command_file="publish.json"), "applied")
            dispatch.assert_called_once_with(
                command,
                command_file="publish.json",
                completion=unittest.mock.ANY,
            )
            direct_completion = dispatch.call_args.kwargs["completion"]
            self.assertIsNotNone(direct_completion)
            self.assertIsNone(direct_completion("applied"))

            dispatch.reset_mock(return_value=True)
            dispatch.return_value = CommandExecution.immediate("dropped")
            self.assertEqual(dispatcher.process(command), "dropped")
            dispatch.assert_called_once_with(
                command,
                command_file="",
                completion=unittest.mock.ANY,
            )
            default_completion = dispatch.call_args.kwargs["completion"]
            self.assertIsNotNone(default_completion)
            self.assertIsNone(default_completion("dropped"))

    def test_dispatch_routes_all_command_families(self) -> None:
        with self.adapter_scenario() as scenario:
            dispatcher = scenario.adapter.write_scheduler.command_dispatcher
            command = evcs_publication({"mode": 1})
            publication = install_mock(dispatcher.publication, "process", MagicMock(return_value="dropped"))

            completion = MagicMock()
            self.assertEqual(
                dispatcher._dispatch(
                    command,
                    command_file="publication.json",
                    completion=completion,
                ),
                CommandExecution.immediate("dropped"),
            )
            publication.assert_called_once_with(command)

            semantic_command = gx_relay_refresh_command(0)
            semantic = install_mock(
                dispatcher.semantic,
                "schedule_semantic_operation",
                MagicMock(return_value=CommandExecution.immediate("applied")),
            )
            self.assertEqual(
                dispatcher._dispatch(
                    semantic_command,
                    command_file="relay.json",
                    completion=completion,
                ),
                CommandExecution.immediate("applied"),
            )
            semantic.assert_called_once_with(
                semantic_command,
                command_file="relay.json",
                completion=completion,
            )

            other: CommandMapping = {"kind": "refresh_energy_inputs"}
            adapter = MagicMock()
            adapter.commands = scenario.adapter.commands
            adapter.schedule_non_write_command.return_value = CommandExecution.immediate("deferred")
            direct_dispatcher = write_dispatch_module.WriteCommandDispatcher(
                adapter,
                publication=dispatcher.publication,
                semantic=dispatcher.semantic,
            )
            self.assertEqual(
                direct_dispatcher._dispatch(
                    other,
                    command_file="other.json",
                    completion=completion,
                ),
                CommandExecution.immediate("deferred"),
            )
            adapter.schedule_non_write_command.assert_called_once_with(
                other,
                "other.json",
                completion,
            )

    def test_process_loaded_expired_retires_before_durable_arbitration(self) -> None:
        with self.adapter_scenario() as scenario:
            queue = scenario.adapter.write_scheduler.command_queue
            command = evcs_publication({"mode": 1})
            pending: CommandFileList = [("expired.json", command)]
            install_mock(queue, "command_expired", MagicMock(return_value=True))
            drop = install_mock(
                queue,
                "_drop_expired_command",
                MagicMock(return_value="dropped"),
            )
            effective = install_mock(queue, "_effective_durable_command", MagicMock())

            self.assertEqual(
                queue.process_loaded_command(
                    "expired.json",
                    command,
                    pending_commands=pending,
                ),
                "dropped",
            )
            drop.assert_called_once_with("expired.json", command, pending_commands=pending)
            effective.assert_not_called()

    def test_process_loaded_order_deferral_records_only_retry_lifecycle(self) -> None:
        with self.adapter_scenario() as scenario:
            queue = scenario.adapter.write_scheduler.command_queue
            command = evcs_publication({"mode": 1})
            install_mock(queue, "command_expired", MagicMock(return_value=False))
            install_mock(
                queue,
                "_effective_durable_command",
                MagicMock(side_effect=write_core_module.PublicationOrderDeferredError()),
            )
            budget = install_mock(queue.health, "record_budget", MagicMock())
            lifecycle = install_mock(queue.health, "record_lifecycle", MagicMock())
            outcome = install_mock(queue.dispatcher, "execute", MagicMock())

            self.assertEqual(queue.process_loaded_command("publish.json", command), "deferred")
            budget.assert_called_once_with(command)
            lifecycle.assert_called_once_with(command, "deferred")
            outcome.assert_not_called()

    def test_process_loaded_superseded_delegates_original_command_and_snapshot(self) -> None:
        with self.adapter_scenario() as scenario:
            queue = scenario.adapter.write_scheduler.command_queue
            command = evcs_publication({"mode": 1})
            pending: CommandFileList = [("publish.json", command)]
            install_mock(queue, "command_expired", MagicMock(return_value=False))
            install_mock(queue, "_effective_durable_command", MagicMock(return_value=None))
            drop = install_mock(
                queue,
                "_drop_superseded_publication",
                MagicMock(return_value="dropped"),
            )

            self.assertEqual(
                queue.process_loaded_command(
                    "publish.json",
                    command,
                    pending_commands=pending,
                ),
                "dropped",
            )
            drop.assert_called_once_with("publish.json", command, pending_commands=pending)

    def test_process_loaded_applies_effective_publication_and_records_exact_lifecycle(self) -> None:
        with self.adapter_scenario() as scenario:
            queue = scenario.adapter.write_scheduler.command_queue
            original = evcs_publication({"mode": 0, "connected": 1})
            effective = evcs_publication({"mode": 2})
            pending: CommandFileList = [("publish.json", original)]
            install_mock(queue, "command_expired", MagicMock(return_value=False))
            install_mock(queue, "_effective_durable_command", MagicMock(return_value=effective))
            outcome = install_mock(
                queue.dispatcher,
                "execute",
                MagicMock(return_value=CommandExecution.immediate("applied")),
            )
            durable = install_mock(
                scenario.adapter.fast_publications,
                "record_durable_outcome",
                MagicMock(),
            )
            apply = install_mock(queue, "_apply_command_result", MagicMock())
            lifecycle = install_mock(queue.health, "record_lifecycle", MagicMock())

            self.assertEqual(
                queue.process_loaded_command(
                    "publish.json",
                    original,
                    pending_commands=pending,
                ),
                "applied",
            )
            outcome.assert_called_once_with(
                "publish.json",
                effective,
                completion=unittest.mock.ANY,
            )
            durable.assert_called_once_with(effective, "applied")
            apply.assert_called_once_with(
                "publish.json",
                effective,
                "applied",
                pending_commands=pending,
            )
            lifecycle.assert_called_once_with(effective, "applied")

    def test_async_completion_owns_durable_file_retirement(self) -> None:
        with self.adapter_scenario() as scenario:
            queue = scenario.adapter.write_scheduler.command_queue
            completions: list[CommandCompletion] = []

            def pending_execution(
                _path: str,
                _command: CommandMapping,
                *,
                completion: CommandCompletion,
            ) -> CommandExecution:
                completions.append(completion)
                return CommandExecution.pending()

            install_mock(queue, "command_expired", MagicMock(return_value=False))
            install_mock(
                queue.dispatcher,
                "execute",
                MagicMock(side_effect=pending_execution),
            )

            applied_command = gx_relay_refresh_command(0)
            applied_path = scenario.adapter.commands.enqueue(applied_command)
            applied_pending = scenario.adapter.commands.load_pending()
            applied_loaded = next(command for path, command in applied_pending if path == applied_path)
            self.assertEqual(
                queue.process_loaded_command(
                    applied_path,
                    applied_loaded,
                    pending_commands=applied_pending,
                ),
                "deferred",
            )
            self.assertTrue(Path(applied_path).exists())

            completions.pop(0)("applied")
            self.assertFalse(Path(applied_path).exists())

            deferred_command = gx_relay_refresh_command(1)
            deferred_path = scenario.adapter.commands.enqueue(deferred_command)
            deferred_pending = scenario.adapter.commands.load_pending()
            deferred_loaded = next(command for path, command in deferred_pending if path == deferred_path)
            self.assertEqual(
                queue.process_loaded_command(
                    deferred_path,
                    deferred_loaded,
                    pending_commands=deferred_pending,
                ),
                "deferred",
            )
            self.assertTrue(Path(deferred_path).exists())

            completions.pop(0)("deferred")
            self.assertTrue(Path(deferred_path).exists())

    def test_command_completion_is_exactly_once_for_every_dispatch_timing(self) -> None:
        with self.adapter_scenario() as scenario:
            queue = scenario.adapter.write_scheduler.command_queue
            command = gx_relay_refresh_command(0)
            pending: CommandFileList = [("command.json", command)]
            finalize = install_mock(queue, "_finalize_loaded_command", MagicMock())
            callbacks: list[CommandCompletion] = []

            def synchronous(
                _path: str,
                _command: CommandMapping,
                *,
                completion: CommandCompletion,
            ) -> CommandExecution:
                callbacks.append(completion)
                completion("applied")
                completion("dropped")
                return CommandExecution.pending()

            install_mock(queue.dispatcher, "execute", MagicMock(side_effect=synchronous))
            self.assertEqual(
                queue._dispatch_loaded_command(
                    "command.json",
                    command,
                    pending_commands=pending,
                ),
                "applied",
            )
            finalize.assert_called_once_with(
                "command.json",
                command,
                "applied",
                pending_commands=pending,
            )
            callbacks.pop()("deferred")
            self.assertEqual(finalize.call_count, 1)

            finalize.reset_mock()
            callbacks.clear()

            def asynchronous(
                _path: str,
                _command: CommandMapping,
                *,
                completion: CommandCompletion,
            ) -> CommandExecution:
                callbacks.append(completion)
                return CommandExecution.pending()

            queue.dispatcher.execute.side_effect = asynchronous
            self.assertEqual(
                queue._dispatch_loaded_command(
                    "command.json",
                    command,
                    pending_commands=pending,
                ),
                "deferred",
            )
            callbacks[0]("applied")
            callbacks[0]("dropped")
            finalize.assert_called_once_with(
                "command.json",
                command,
                "applied",
                pending_commands=pending,
            )

            finalize.reset_mock()
            callbacks.clear()

            def immediate(
                _path: str,
                _command: CommandMapping,
                *,
                completion: CommandCompletion,
            ) -> CommandExecution:
                callbacks.append(completion)
                return CommandExecution.immediate("applied")

            queue.dispatcher.execute.side_effect = immediate
            self.assertEqual(
                queue._dispatch_loaded_command(
                    "command.json",
                    command,
                    pending_commands=pending,
                ),
                "applied",
            )
            callbacks[0]("dropped")
            finalize.assert_called_once_with(
                "command.json",
                command,
                "applied",
                pending_commands=pending,
            )

    def test_old_async_completion_cannot_retire_a_newer_coalesced_generation(
        self,
    ) -> None:
        with self.adapter_scenario() as scenario:
            queue = scenario.adapter.write_scheduler.command_queue
            completions: list[CommandCompletion] = []

            def pending_execution(
                _path: str,
                _command: CommandMapping,
                *,
                completion: CommandCompletion,
            ) -> CommandExecution:
                completions.append(completion)
                return CommandExecution.pending()

            install_mock(queue, "command_expired", MagicMock(return_value=False))
            install_mock(
                queue.dispatcher,
                "execute",
                MagicMock(side_effect=pending_execution),
            )
            original = {
                **gx_relay_refresh_command(0),
                "created_at": 10.0,
                "source": "old",
            }
            path = scenario.adapter.commands.enqueue(original)
            old_pending = scenario.adapter.commands.load_pending()
            old_loaded = old_pending[0][1]

            self.assertEqual(
                queue.process_loaded_command(
                    path,
                    old_loaded,
                    pending_commands=old_pending,
                ),
                "deferred",
            )
            scenario.adapter.commands.enqueue(
                {
                    **gx_relay_refresh_command(0),
                    "created_at": 11.0,
                    "source": "new",
                }
            )
            newer = scenario.adapter.commands.load_pending()[0][1]
            self.assertNotEqual(
                newer["mailbox_revision"],
                old_loaded["mailbox_revision"],
            )

            completions.pop()("applied")

            remaining = scenario.adapter.commands.load_pending()
            self.assertEqual(len(remaining), 1)
            self.assertEqual(remaining[0][0], path)
            self.assertEqual(remaining[0][1]["source"], "new")
            self.assertEqual(
                remaining[0][1]["mailbox_revision"],
                newer["mailbox_revision"],
            )

    def test_drop_helpers_retire_original_and_record_distinct_lifecycles(self) -> None:
        with self.adapter_scenario() as scenario:
            queue = scenario.adapter.write_scheduler.command_queue
            command = evcs_publication({"mode": 1})
            pending: CommandFileList = [("publish.json", command)]
            remove = install_mock(queue, "remove_pending", MagicMock(return_value=True))
            stale = install_mock(queue, "drop_stale_coalesced_commands", MagicMock())
            lifecycle = install_mock(queue.health, "record_lifecycle", MagicMock())
            processed = install_mock(queue.health, "record_processed", MagicMock())
            budget = install_mock(queue.health, "record_budget", MagicMock())

            self.assertEqual(
                queue._drop_superseded_publication(
                    "publish.json",
                    command,
                    pending_commands=pending,
                ),
                "dropped",
            )
            remove.assert_called_once_with("publish.json", command)
            stale.assert_called_once_with("publish.json", command, pending_commands=pending)
            lifecycle.assert_called_once_with(command, "coalesced")
            processed.assert_called_once_with()
            budget.assert_called_once_with(command)

            remove.reset_mock()
            stale.reset_mock()
            lifecycle.reset_mock()
            processed.reset_mock()
            budget.reset_mock()
            self.assertEqual(
                queue._drop_expired_command(
                    "expired.json",
                    command,
                    pending_commands=pending,
                ),
                "dropped",
            )
            remove.assert_called_once_with("expired.json", command)
            stale.assert_called_once_with("expired.json", command, pending_commands=pending)
            lifecycle.assert_called_once_with(command, "expired")
            processed.assert_called_once_with()
            budget.assert_not_called()

    def test_command_outcome_converts_only_retry_errors_to_deferred(self) -> None:
        with self.adapter_scenario() as scenario:
            dispatcher = scenario.adapter.write_scheduler.command_dispatcher
            command = evcs_publication({"mode": 1})
            process = install_mock(
                dispatcher,
                "schedule",
                MagicMock(return_value=CommandExecution.immediate("applied")),
            )

            self.assertEqual(dispatcher.outcome("publish.json", command), "applied")
            process.assert_called_once_with(
                command,
                command_file="publish.json",
                completion=unittest.mock.ANY,
            )
            completion = process.call_args.kwargs["completion"]
            self.assertIsNotNone(completion)
            self.assertIsNone(completion("applied"))

            for error in (DbusOperationDeferred(), KeyError("missing"), OSError("io")):
                with self.subTest(error=type(error).__name__):
                    process.side_effect = error
                    with patch.object(write_dispatch_module.logging, "exception"):
                        self.assertEqual(dispatcher.outcome("publish.json", command), "deferred")

            process.side_effect = AssertionError("programming error")
            with self.assertRaisesRegex(AssertionError, "programming error"):
                dispatcher.outcome("publish.json", command)

    def test_apply_result_has_distinct_deferred_and_terminal_side_effects(self) -> None:
        with self.adapter_scenario() as scenario:
            queue = scenario.adapter.write_scheduler.command_queue
            command = evcs_publication({"mode": 1})
            pending: CommandFileList = [("publish.json", command)]
            budget = install_mock(queue.health, "record_budget", MagicMock())
            remove = install_mock(queue, "remove_pending", MagicMock(return_value=True))
            stale = install_mock(queue, "drop_stale_coalesced_commands", MagicMock())
            processed = install_mock(queue.health, "record_processed", MagicMock())

            queue._apply_command_result(
                "publish.json",
                command,
                "deferred",
                pending_commands=pending,
            )
            budget.assert_called_once_with(command)
            remove.assert_not_called()
            stale.assert_not_called()
            processed.assert_not_called()

            for outcome in ("applied", "dropped"):
                with self.subTest(outcome=outcome):
                    budget.reset_mock()
                    remove.reset_mock()
                    stale.reset_mock()
                    processed.reset_mock()
                    queue._apply_command_result(
                        "publish.json",
                        command,
                        outcome,
                        pending_commands=pending,
                    )
                    remove.assert_called_once_with("publish.json", command)
                    stale.assert_called_once_with(
                        "publish.json",
                        command,
                        pending_commands=pending,
                    )
                    processed.assert_called_once_with()
                    budget.assert_called_once_with(command)

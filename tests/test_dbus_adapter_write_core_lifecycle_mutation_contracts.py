# SPDX-License-Identifier: GPL-3.0-or-later
"""Mutation-strength lifecycle contracts for the gateway write queue."""

from __future__ import annotations

from tests.support.dbus_gateway_adapter_harness import (
    CommandFileList,
    GatewayAdapterContractCase,
    MagicMock,
    evcs_publication,
    install_mock,
    patch,
    write_core_module,
)
from venus_evcharger.dbus_adapter.rate import DbusOperationDeferred
from venus_evcharger.ipc.command_types import CommandMapping
from venus_evcharger.ipc.gateway_operations import gx_relay_refresh_command


class DbusAdapterWriteCoreLifecycleMutationContracts(GatewayAdapterContractCase):
    """Pin dispatch, durable arbitration, retirement, and retry outcomes."""

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
            queue = scenario.adapter.write_scheduler.command_queue
            command = evcs_publication({"mode": 1})
            blocked = install_mock(queue, "_command_blocked", MagicMock(return_value=True))
            dispatch = install_mock(queue, "_dispatch_command", MagicMock(return_value="applied"))

            self.assertEqual(queue.process_command(command, command_file="publish.json"), "deferred")
            dispatch.assert_not_called()

            blocked.return_value = False
            self.assertEqual(queue.process_command(command, command_file="publish.json"), "applied")
            dispatch.assert_called_once_with(command, command_file="publish.json")

            dispatch.reset_mock(return_value=True)
            dispatch.return_value = "dropped"
            self.assertEqual(queue.process_command(command), "dropped")
            dispatch.assert_called_once_with(command, command_file="")

    def test_dispatch_routes_all_command_families(self) -> None:
        with self.adapter_scenario() as scenario:
            queue = scenario.adapter.write_scheduler.command_queue
            command = evcs_publication({"mode": 1})
            publication = install_mock(queue.publication, "process", MagicMock(return_value="dropped"))

            self.assertEqual(queue._dispatch_command(command, command_file="publication.json"), "dropped")
            publication.assert_called_once_with(command)

            semantic_command = gx_relay_refresh_command(0)
            semantic = install_mock(
                queue.semantic,
                "process_semantic_operation",
                MagicMock(return_value="applied"),
            )
            self.assertEqual(
                queue._dispatch_command(semantic_command, command_file="relay.json"),
                "applied",
            )
            semantic.assert_called_once_with(semantic_command, command_file="relay.json")

            other: CommandMapping = {"kind": "refresh_energy_inputs"}
            adapter = MagicMock()
            adapter.commands = scenario.adapter.commands
            adapter.process_non_write_command.return_value = "deferred"
            direct_queue = write_core_module.WriteCommandQueue(
                adapter,
                publication=queue.publication,
                semantic=queue.semantic,
                health=queue.health,
            )
            self.assertEqual(
                direct_queue._dispatch_command(other, command_file="other.json"),
                "deferred",
            )
            adapter.process_non_write_command.assert_called_once_with(other)

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
            outcome = install_mock(queue, "command_outcome", MagicMock())

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
            outcome = install_mock(queue, "command_outcome", MagicMock(return_value="applied"))
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
            outcome.assert_called_once_with("publish.json", effective)
            durable.assert_called_once_with(effective, "applied")
            apply.assert_called_once_with(
                "publish.json",
                effective,
                "applied",
                pending_commands=pending,
            )
            lifecycle.assert_called_once_with(effective, "applied")

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
            queue = scenario.adapter.write_scheduler.command_queue
            command = evcs_publication({"mode": 1})
            process = install_mock(queue, "process_command", MagicMock(return_value="applied"))

            self.assertEqual(queue.command_outcome("publish.json", command), "applied")
            process.assert_called_once_with(command, command_file="publish.json")

            for error in (DbusOperationDeferred(), KeyError("missing"), OSError("io")):
                with self.subTest(error=type(error).__name__):
                    process.side_effect = error
                    with patch.object(write_core_module.logging, "exception"):
                        self.assertEqual(queue.command_outcome("publish.json", command), "deferred")

            process.side_effect = AssertionError("programming error")
            with self.assertRaisesRegex(AssertionError, "programming error"):
                queue.command_outcome("publish.json", command)

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

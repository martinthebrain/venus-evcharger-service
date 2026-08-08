# SPDX-License-Identifier: GPL-3.0-or-later
"""Semantic command completion, retry, and follow-up scenarios."""

from __future__ import annotations

from tests.support.dbus_gateway_adapter_harness import (
    DbusOperationDeferred,
    GatewayAdapterContractCase,
    MagicMock,
    Path,
    evcs_publication,
    evcs_registration,
    install_mock,
    patch,
    read_json_file,
    time,
    unittest,
    write_core_module,
    write_dispatch_module,
)
from venus_evcharger.dbus_adapter.contracts import CommandExecution
from venus_evcharger.ipc.gateway_operations import gx_relay_refresh_command, gx_relay_set_command


class GatewayWriteFollowupCases(GatewayAdapterContractCase):
    """Exercise lifecycle decisions independently of concrete DBus targets."""

    def test_process_loaded_command_removes_applied_and_dropped_commands(self) -> None:
        with self.adapter_scenario("[DEFAULT]\nDbusGatewayQueueBudgetRemoteWrite=1\n") as scenario:
            adapter = scenario.adapter
            for outcome in ("applied", "dropped"):
                command_path = adapter.commands.enqueue(
                    {
                        **evcs_publication({"mode": 1}),
                        "coalesce_key": f"semantic:{outcome}",
                    }
                )
                command = read_json_file(command_path)
                assert isinstance(command, dict)
                install_mock(
                    adapter.write_scheduler.command_dispatcher,
                    "execute",
                    MagicMock(return_value=CommandExecution.immediate(outcome)),
                )

                self.assertEqual(
                    adapter.write_scheduler.command_queue.process_loaded_command(command_path, command), outcome
                )
                self.assertFalse(Path(command_path).exists())

    def test_deferred_command_is_retained_and_consumes_budget(self) -> None:
        with self.adapter_scenario("[DEFAULT]\nDbusGatewayQueueBudgetRemoteWrite=1\n") as scenario:
            adapter = scenario.adapter
            command_path = adapter.commands.enqueue(
                gx_relay_set_command(
                    0,
                    "NO",
                    True,
                    ensure_manual=False,
                    verify_settle_seconds=0.1,
                    verify_retry_seconds=1.0,
                )
            )
            command = read_json_file(command_path)
            assert isinstance(command, dict)
            install_mock(
                adapter.write_scheduler.command_dispatcher,
                "execute",
                MagicMock(return_value=CommandExecution.immediate("deferred")),
            )

            self.assertEqual(
                adapter.write_scheduler.command_queue.process_loaded_command(command_path, command), "deferred"
            )

            self.assertTrue(Path(command_path).exists())
            self.assertFalse(adapter.write_scheduler.health_tracker.budget_available(command, time.time()))

    def test_expired_semantic_command_is_dropped_without_dispatch(self) -> None:
        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            command = {
                **gx_relay_refresh_command(0),
                "created_at": time.time() - 10.0,
                "deadline_s": 1.0,
            }
            command_path = adapter.commands.enqueue(command)
            loaded_command = read_json_file(command_path)
            assert isinstance(loaded_command, dict)
            process_command = install_mock(
                adapter.write_scheduler.command_dispatcher,
                "execute",
                MagicMock(return_value="applied"),
            )

            self.assertEqual(
                adapter.write_scheduler.command_queue.process_loaded_command(
                    command_path,
                    loaded_command,
                ),
                "dropped",
            )

            self.assertFalse(Path(command_path).exists())
            process_command.assert_not_called()
            health = adapter.write_scheduler.health()
            lifecycle_counts = health["lifecycle_counts"]
            self.assertIsInstance(lifecycle_counts, dict)
            assert isinstance(lifecycle_counts, dict)
            self.assertEqual(lifecycle_counts["expired"], 1)

    def test_command_outcome_turns_transient_failures_into_retry(self) -> None:
        with self.adapter_scenario() as scenario:
            scheduler = scenario.adapter.write_scheduler
            dispatcher = scheduler.command_dispatcher
            command = gx_relay_refresh_command(0)
            process_command = install_mock(
                dispatcher,
                "schedule",
                MagicMock(side_effect=DbusOperationDeferred("busy")),
            )
            self.assertEqual(dispatcher.outcome("relay.json", command), "deferred")

            process_command.side_effect = RuntimeError("offline")
            with patch.object(vars(write_dispatch_module)["logging"], "exception") as logged:
                self.assertEqual(dispatcher.outcome("relay.json", command), "deferred")
            process_command.assert_called_with(
                command,
                command_file="relay.json",
                completion=unittest.mock.ANY,
            )
            logged.assert_called_once_with(
                "Gateway command failed; keeping for retry path=%s: %s",
                "relay.json",
                process_command.side_effect,
            )

    def test_command_outcome_forwards_command_file_on_success(self) -> None:
        with self.adapter_scenario() as scenario:
            scheduler = scenario.adapter.write_scheduler
            dispatcher = scheduler.command_dispatcher
            command = gx_relay_refresh_command(0)
            process_command = install_mock(
                dispatcher,
                "schedule",
                MagicMock(return_value=CommandExecution.immediate("applied")),
            )

            self.assertEqual(dispatcher.outcome("relay.json", command), "applied")

            process_command.assert_called_once_with(
                command,
                command_file="relay.json",
                completion=unittest.mock.ANY,
            )

    def test_process_one_resets_and_reports_last_outcome(self) -> None:
        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            adapter.write_scheduler.command_queue.last_scheduled_outcome = "deferred"
            self.assertFalse(adapter.write_scheduler.process_one())
            self.assertIsNone(adapter.write_scheduler.last_scheduled_outcome)

            adapter.commands.enqueue(gx_relay_refresh_command(0))
            install_mock(
                adapter.write_scheduler.command_queue,
                "process_loaded_command",
                MagicMock(return_value="applied"),
            )
            self.assertTrue(adapter.write_scheduler.process_one())
            self.assertEqual(adapter.write_scheduler.last_scheduled_outcome, "applied")

    def test_process_one_forwards_the_loaded_pending_snapshot(self) -> None:
        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            scheduler = adapter.write_scheduler
            queue = scheduler.command_queue
            health = scheduler.health_tracker
            command = gx_relay_refresh_command(0)
            pending = [("relay.json", command)]
            load_pending = install_mock(adapter.commands, "load_pending", MagicMock(return_value=pending))
            coalesce = install_mock(adapter.commands, "coalesce", MagicMock(return_value=pending))
            prioritize = install_mock(health, "prioritized_commands", MagicMock(return_value=pending))
            select = install_mock(queue, "select_next_command", MagicMock(return_value=pending[0]))
            process_loaded = install_mock(queue, "process_loaded_command", MagicMock(return_value="applied"))

            self.assertTrue(scheduler.process_one(required_kind="register_evcs"))

            load_pending.assert_called_once_with()
            coalesce.assert_called_once_with(pending)
            prioritize.assert_called_once_with(pending)
            select.assert_called_once_with(
                pending,
                include_local_publish=True,
                required_kind="register_evcs",
            )
            process_loaded.assert_called_once_with("relay.json", command, pending_commands=pending)

    def test_process_loaded_command_preserves_orchestration_arguments(self) -> None:
        with self.adapter_scenario() as scenario:
            scheduler = scenario.adapter.write_scheduler
            queue = scheduler.command_queue
            health = scheduler.health_tracker
            command = gx_relay_refresh_command(0)
            pending = [("relay.json", command)]
            install_mock(queue, "command_expired", MagicMock(return_value=False))
            command_execution = install_mock(
                queue.dispatcher,
                "execute",
                MagicMock(return_value=CommandExecution.immediate("applied")),
            )
            apply_result = install_mock(queue, "_apply_command_result", MagicMock())
            lifecycle = install_mock(health, "record_lifecycle", MagicMock())

            self.assertEqual(
                queue.process_loaded_command("relay.json", command, pending_commands=pending),
                "applied",
            )

            command_execution.assert_called_once_with(
                "relay.json",
                command,
                completion=unittest.mock.ANY,
            )
            apply_result.assert_called_once_with(
                "relay.json",
                command,
                "applied",
                pending_commands=pending,
            )
            lifecycle.assert_called_once_with(command, "applied")

    def test_expired_command_forwards_pending_snapshot_to_drop(self) -> None:
        with self.adapter_scenario() as scenario:
            scheduler = scenario.adapter.write_scheduler
            queue = scheduler.command_queue
            command = gx_relay_refresh_command(0)
            pending = [("relay.json", command)]
            install_mock(queue, "command_expired", MagicMock(return_value=True))
            drop_expired = install_mock(queue, "_drop_expired_command", MagicMock(return_value="dropped"))

            self.assertEqual(
                queue.process_loaded_command("relay.json", command, pending_commands=pending),
                "dropped",
            )

            drop_expired.assert_called_once_with(
                "relay.json",
                command,
                pending_commands=pending,
            )

    def test_drop_expired_command_records_exact_cleanup_sequence(self) -> None:
        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            scheduler = adapter.write_scheduler
            queue = scheduler.command_queue
            health = scheduler.health_tracker
            command = gx_relay_refresh_command(0)
            pending = [("relay.json", command)]
            remove = install_mock(queue, "remove_pending", MagicMock(return_value=True))
            drop_stale = install_mock(queue, "drop_stale_coalesced_commands", MagicMock())
            lifecycle = install_mock(health, "record_lifecycle", MagicMock())
            processed = install_mock(health, "record_processed", MagicMock())

            self.assertEqual(
                queue._drop_expired_command("relay.json", command, pending_commands=pending),
                "dropped",
            )

            remove.assert_called_once_with("relay.json", command)
            drop_stale.assert_called_once_with("relay.json", command, pending_commands=pending)
            lifecycle.assert_called_once_with(command, "expired")
            processed.assert_called_once_with()

    def test_applied_result_uses_exact_cleanup_arguments(self) -> None:
        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            scheduler = adapter.write_scheduler
            queue = scheduler.command_queue
            health = scheduler.health_tracker
            command = gx_relay_refresh_command(0)
            pending = [("relay.json", command)]
            remove = install_mock(queue, "remove_pending", MagicMock(return_value=True))
            drop_stale = install_mock(queue, "drop_stale_coalesced_commands", MagicMock())
            processed = install_mock(health, "record_processed", MagicMock())
            budget = install_mock(health, "record_budget", MagicMock())

            queue._apply_command_result(
                "relay.json",
                command,
                "applied",
                pending_commands=pending,
            )

            remove.assert_called_once_with("relay.json", command)
            drop_stale.assert_called_once_with("relay.json", command, pending_commands=pending)
            processed.assert_called_once_with()
            budget.assert_called_once_with(command)

    def test_not_before_delays_a_semantic_operation(self) -> None:
        with self.adapter_scenario() as scenario:
            scheduler = scenario.adapter.write_scheduler
            now = time.time()
            delayed = {**gx_relay_refresh_command(0), "not_before": now + 10.0}
            ready = {**gx_relay_refresh_command(1), "not_before": now - 1.0}

            with patch.object(vars(write_core_module)["time"], "time", return_value=now):
                selected = scheduler.command_queue.select_next_command([("delayed", delayed), ("ready", ready)])

            self.assertEqual(selected, ("ready", ready))

    def test_include_local_publish_flag_filters_only_field_publications(self) -> None:
        with self.adapter_scenario() as scenario:
            scheduler = scenario.adapter.write_scheduler
            publication = evcs_publication({"mode": 2})
            operation = gx_relay_refresh_command(0)

            selected = scheduler.command_queue.select_next_command(
                [("publication", dict(publication)), ("operation", dict(operation))],
                include_local_publish=False,
            )

            self.assertEqual(selected, ("operation", operation))

    def test_required_kind_selects_only_the_requested_command_type(self) -> None:
        with self.adapter_scenario() as scenario:
            scheduler = scenario.adapter.write_scheduler
            publication = evcs_publication({"mode": 2})
            registration = evcs_registration({"mode": 0})

            selected = scheduler.command_queue.select_next_command(
                [("publication", publication), ("registration", registration)],
                required_kind="register_evcs",
            )

            self.assertEqual(selected, ("registration", registration))
            self.assertIsNone(
                scheduler.command_queue.select_next_command(
                    [("publication", publication)],
                    required_kind="register_evcs",
                )
            )

    def test_select_next_command_includes_local_publication_by_default(self) -> None:
        with self.adapter_scenario() as scenario:
            publication = evcs_publication({"mode": 2})

            selected = scenario.adapter.write_scheduler.command_queue.select_next_command(
                [("publication", publication)]
            )

            self.assertEqual(selected, ("publication", publication))

    def test_command_expiry_uses_strict_positive_and_elapsed_boundaries(self) -> None:
        with self.adapter_scenario() as scenario:
            command_expired = scenario.adapter.write_scheduler.command_queue.command_expired

            with patch.object(vars(write_core_module)["time"], "time", return_value=2.0):
                self.assertTrue(command_expired({"deadline_s": 1.0, "created_at": 0.0}))
                self.assertFalse(command_expired({"deadline_s": 1.0, "created_at": 1.0}))
            with patch.object(vars(write_core_module)["time"], "time", return_value=2.001):
                self.assertTrue(command_expired({"deadline_s": 1.0, "created_at": 1.0}))

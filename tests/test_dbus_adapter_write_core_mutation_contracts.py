# SPDX-License-Identifier: GPL-3.0-or-later
"""Mutation-strength contracts for the gateway write-command queue."""

from __future__ import annotations

from unittest.mock import call

from tests.support.dbus_gateway_adapter_harness import (
    CommandFileList,
    GatewayAdapterContractCase,
    MagicMock,
    evcs_publication,
    install_mock,
    patch,
    write_core_module,
    write_dispatch_module,
    write_support_module,
)
from venus_evcharger.ipc.command_types import CommandMapping
from venus_evcharger.ipc.fast_publication_work import FastPublicationWork
from venus_evcharger.ipc.gateway_operations import (
    gx_relay_refresh_command,
    gx_relay_set_command,
)


def _fast_work(command: CommandMapping) -> FastPublicationWork:
    return FastPublicationWork(dict(command), {"mode": 200.0})


class DbusAdapterWriteCoreMutationContracts(GatewayAdapterContractCase):
    """Pin queue budgets, lane arbitration, lifecycle, and retirement."""

    def test_queue_initialization_and_pending_snapshot_delegation_are_exact(self) -> None:
        adapter = MagicMock()
        adapter.commands.load_pending.return_value = []
        adapter.commands.coalesce.return_value = []
        publication = MagicMock()
        semantic = MagicMock()
        health = MagicMock()
        dispatcher = write_dispatch_module.WriteCommandDispatcher(
            adapter,
            publication=publication,
            semantic=semantic,
        )
        queue = write_core_module.WriteCommandQueue(
            adapter,
            dispatcher=dispatcher,
            health=health,
        )

        self.assertIs(queue.adapter, adapter)
        self.assertIs(queue.dispatcher, dispatcher)
        self.assertIs(dispatcher.publication, publication)
        self.assertIs(dispatcher.semantic, semantic)
        self.assertIs(queue.health, health)
        self.assertIsNone(queue.last_scheduled_outcome)

        snapshot = queue.begin_tick()
        self.assertIs(snapshot, queue.pending_snapshot())
        queue.end_tick()
        self.assertIsNot(snapshot, queue.pending_snapshot())

    def test_local_burst_stops_before_fast_work_when_urgent_durable_is_ready(self) -> None:
        with self.adapter_scenario() as scenario:
            queue = scenario.adapter.write_scheduler.command_queue
            pending: CommandFileList = [("urgent.json", evcs_publication({"mode": 1}, priority="critical"))]
            install_mock(scenario.adapter.commands, "load_pending", MagicMock(return_value=pending))
            install_mock(scenario.adapter.commands, "coalesce", MagicMock(return_value=pending))
            prioritized = install_mock(
                queue.health,
                "prioritized_commands",
                MagicMock(return_value=pending),
            )
            urgent = install_mock(queue, "_urgent_durable_ready", MagicMock(return_value=True))
            fast = install_mock(queue, "_process_fast_publish_burst", MagicMock())
            durable = install_mock(queue, "_process_durable_publish_burst", MagicMock())

            self.assertEqual(queue.process_local_publish_burst(limit=4), 0)

            prioritized.assert_called_once_with(pending)
            urgent.assert_called_once_with(pending)
            fast.assert_not_called()
            durable.assert_not_called()

    def test_local_burst_normalizes_fast_budget_and_preserves_durable_budget(self) -> None:
        with self.adapter_scenario() as scenario:
            queue = scenario.adapter.write_scheduler.command_queue
            command = evcs_publication({"mode": 1})
            pending: CommandFileList = [("publish.json", command)]
            install_mock(scenario.adapter.commands, "load_pending", MagicMock(return_value=pending))
            install_mock(scenario.adapter.commands, "coalesce", MagicMock(return_value=pending))
            install_mock(queue.health, "prioritized_commands", MagicMock(return_value=pending))
            install_mock(queue, "_urgent_durable_ready", MagicMock(return_value=False))
            fast = install_mock(
                queue,
                "_process_fast_publish_burst",
                MagicMock(return_value=write_support_module.FastPublishBurst(0, False)),
            )
            durable = install_mock(queue, "_process_durable_publish_burst", MagicMock(return_value=6))

            with patch.object(write_core_module.time, "monotonic", return_value=12.0):
                self.assertEqual(queue.process_local_publish_burst(limit=-3), 6)

            fast.assert_called_once_with(0, 12.0)
            durable.assert_called_once()
            selected, candidate = durable.call_args.args
            self.assertIs(selected, pending)
            self.assertEqual(candidate.processed, 0)
            self.assertEqual(candidate.remaining_budget, -3)
            self.assertEqual(candidate.started, 12.0)
            self.assertIsNot(candidate.pending_commands, pending)
            self.assertEqual(candidate.pending_commands, pending)

    def test_fast_stop_returns_its_count_without_entering_durable_lane(self) -> None:
        with self.adapter_scenario() as scenario:
            queue = scenario.adapter.write_scheduler.command_queue
            install_mock(scenario.adapter.commands, "load_pending", MagicMock(return_value=[]))
            install_mock(scenario.adapter.commands, "coalesce", MagicMock(return_value=[]))
            install_mock(queue.health, "prioritized_commands", MagicMock(return_value=[]))
            install_mock(queue, "_urgent_durable_ready", MagicMock(return_value=False))
            install_mock(
                queue,
                "_process_fast_publish_burst",
                MagicMock(return_value=write_support_module.FastPublishBurst(2, True)),
            )
            durable = install_mock(queue, "_process_durable_publish_burst", MagicMock())

            self.assertEqual(queue.process_local_publish_burst(limit=5), 2)
            durable.assert_not_called()

    def test_durable_burst_carries_forward_only_processed_actions(self) -> None:
        with self.adapter_scenario() as scenario:
            queue = scenario.adapter.write_scheduler.command_queue
            commands: CommandFileList = [
                ("skip.json", evcs_publication({"mode": 0})),
                ("apply.json", evcs_publication({"mode": 1})),
                ("stop.json", evcs_publication({"mode": 2})),
                ("unseen.json", evcs_publication({"connected": 1})),
            ]
            candidate = write_support_module.LocalPublishCandidate(2, 7, commands, 19.0)
            process = install_mock(
                queue,
                "_process_local_publish_candidate",
                MagicMock(side_effect=("skip", "processed", "break")),
            )

            self.assertEqual(queue._process_durable_publish_burst(commands, candidate), 3)
            self.assertEqual(process.call_count, 3)
            forwarded = [entry.args[2] for entry in process.call_args_list]
            self.assertEqual([item.processed for item in forwarded], [2, 2, 3])
            self.assertEqual(forwarded[0].remaining_budget, 7)
            self.assertEqual(forwarded[1].remaining_budget, 7)
            self.assertEqual(forwarded[2].remaining_budget, 7)
            self.assertIs(forwarded[0].pending_commands, commands)
            self.assertIs(forwarded[1].pending_commands, commands)
            self.assertIs(forwarded[2].pending_commands, commands)
            self.assertEqual(forwarded[0].started, 19.0)
            self.assertEqual(forwarded[1].started, 19.0)
            self.assertEqual(forwarded[2].started, 19.0)

    def test_urgent_probe_requires_priority_readiness_and_budget(self) -> None:
        with self.adapter_scenario() as scenario:
            queue = scenario.adapter.write_scheduler.command_queue
            ordinary = evcs_publication({"mode": 0})
            delayed = {
                **evcs_publication({"mode": 1}, priority="critical"),
                "priority": "safety",
                "not_before": 51.0,
            }
            blocked = {
                **evcs_publication({"mode": 2}, priority="critical"),
                "priority": "user",
            }
            ready = {
                **evcs_publication({"connected": 1}, priority="critical"),
                "priority": "safety",
            }
            commands: CommandFileList = [
                ("ordinary", ordinary),
                ("delayed", delayed),
                ("blocked", blocked),
                ("ready", ready),
            ]
            prune = install_mock(queue.health, "prune_budget", MagicMock())
            budget = install_mock(
                queue.health,
                "budget_available",
                MagicMock(side_effect=(False, True)),
            )

            with patch.object(write_core_module.time, "time", return_value=50.0):
                self.assertTrue(queue._urgent_durable_ready(commands))

            prune.assert_called_once_with(50.0)
            self.assertEqual(budget.call_args_list, [call(blocked, 50.0), call(ready, 50.0)])

    def test_fast_burst_has_exact_empty_blocked_and_limit_states(self) -> None:
        with self.adapter_scenario() as scenario:
            queue = scenario.adapter.write_scheduler.command_queue
            fast_queue = scenario.adapter.fast_publications
            command = evcs_publication({"mode": 1})
            work = _fast_work(command)
            pop = install_mock(fast_queue, "pop_next", MagicMock())
            process = install_mock(queue, "_process_fast_publish_candidate", MagicMock())

            self.assertEqual(
                queue._process_fast_publish_burst(0, 10.0),
                write_support_module.FastPublishBurst(0, True),
            )
            pop.assert_not_called()

            pop.return_value = None
            self.assertEqual(
                queue._process_fast_publish_burst(2, 11.0),
                write_support_module.FastPublishBurst(0, False),
            )
            pop.assert_called_once_with(now=11.0)

            pop.reset_mock(side_effect=True)
            pop.side_effect = (work, None)
            process.return_value = True
            self.assertEqual(
                queue._process_fast_publish_burst(2, 12.0),
                write_support_module.FastPublishBurst(1, False),
            )
            self.assertEqual(pop.call_args_list, [call(now=12.0), call(now=12.0)])
            process.assert_called_once_with(work, 12.0)

            pop.reset_mock(side_effect=True)
            process.reset_mock(return_value=False)
            pop.return_value = work
            process.return_value = False
            self.assertEqual(
                queue._process_fast_publish_burst(2, 13.0),
                write_support_module.FastPublishBurst(0, True),
            )
            pop.assert_called_once_with(now=13.0)
            process.assert_called_once_with(work, 13.0)

    def test_fast_candidate_block_requeues_without_recording_outcome(self) -> None:
        with self.adapter_scenario() as scenario:
            queue = scenario.adapter.write_scheduler.command_queue
            command = evcs_publication({"mode": 1})
            work = _fast_work(command)
            install_mock(queue, "_fast_publish_blocked", MagicMock(return_value=True))
            requeue = install_mock(scenario.adapter.fast_publications, "requeue", MagicMock())
            outcome = install_mock(queue.dispatcher, "outcome", MagicMock())
            record = install_mock(scenario.adapter.fast_publications, "record_outcome", MagicMock())

            self.assertFalse(queue._process_fast_publish_candidate(work, 14.0))
            requeue.assert_called_once_with(work, now=14.0)
            outcome.assert_not_called()
            record.assert_not_called()

    def test_fast_candidate_deferred_records_sampled_lifecycle_and_budget_only(self) -> None:
        with self.adapter_scenario() as scenario:
            queue = scenario.adapter.write_scheduler.command_queue
            command = evcs_publication({"mode": 1})
            work = _fast_work(command)
            install_mock(queue, "_fast_publish_blocked", MagicMock(return_value=False))
            outcome = install_mock(queue.dispatcher, "outcome", MagicMock(return_value="deferred"))
            record = install_mock(
                scenario.adapter.fast_publications,
                "record_outcome",
                MagicMock(return_value=True),
            )
            requeue = install_mock(scenario.adapter.fast_publications, "requeue", MagicMock())
            lifecycle = install_mock(queue.health, "record_lifecycle", MagicMock())
            budget = install_mock(queue.health, "record_budget", MagicMock())
            processed = install_mock(queue.health, "record_processed", MagicMock())

            self.assertTrue(queue._process_fast_publish_candidate(work, 15.0))
            outcome.assert_called_once_with("", command)
            record.assert_called_once_with(work, "deferred")
            requeue.assert_called_once_with(work, deferred=True, now=15.0)
            lifecycle.assert_called_once_with(command, "deferred")
            budget.assert_called_once_with(command)
            processed.assert_not_called()

    def test_fast_candidate_applied_records_processed_without_unsampled_lifecycle(self) -> None:
        with self.adapter_scenario() as scenario:
            queue = scenario.adapter.write_scheduler.command_queue
            command = evcs_publication({"mode": 1})
            work = _fast_work(command)
            install_mock(queue, "_fast_publish_blocked", MagicMock(return_value=False))
            install_mock(queue.dispatcher, "outcome", MagicMock(return_value="applied"))
            install_mock(
                scenario.adapter.fast_publications,
                "record_outcome",
                MagicMock(return_value=False),
            )
            requeue = install_mock(scenario.adapter.fast_publications, "requeue", MagicMock())
            lifecycle = install_mock(queue.health, "record_lifecycle", MagicMock())
            budget = install_mock(queue.health, "record_budget", MagicMock())
            processed = install_mock(queue.health, "record_processed", MagicMock())

            self.assertTrue(queue._process_fast_publish_candidate(work, 16.0))
            requeue.assert_not_called()
            lifecycle.assert_not_called()
            budget.assert_called_once_with(command)
            processed.assert_called_once_with()

    def test_fast_block_short_circuits_clock_after_elapsed_budget(self) -> None:
        with self.adapter_scenario() as scenario:
            queue = scenario.adapter.write_scheduler.command_queue
            command = evcs_publication({"mode": 1})
            queue.health.local_publish_tick_budget_seconds = 0.08
            elapsed = MagicMock(return_value=True)
            available = install_mock(queue.health, "budget_available", MagicMock(return_value=True))

            with (
                patch.object(write_core_module, "budget_elapsed", elapsed),
                patch.object(write_core_module.time, "time", return_value=88.0) as now,
            ):
                self.assertTrue(queue._fast_publish_blocked(command, 17.0))
                now.assert_not_called()
                available.assert_not_called()

                elapsed.return_value = False
                available.return_value = False
                self.assertTrue(queue._fast_publish_blocked(command, 18.0))

            self.assertEqual(elapsed.call_args_list, [call(17.0, 0.08), call(18.0, 0.08)])
            now.assert_called_once_with()
            available.assert_called_once_with(command, 88.0)

    def test_local_candidate_returns_exact_actions_and_forwards_retirement_snapshot(self) -> None:
        with self.adapter_scenario() as scenario:
            queue = scenario.adapter.write_scheduler.command_queue
            local = evcs_publication({"mode": 1})
            remote = gx_relay_refresh_command(0)
            pending: CommandFileList = [("publish.json", local)]
            candidate = write_support_module.LocalPublishCandidate(0, 3, pending, 20.0)
            install_mock(queue, "_local_publish_burst_done", MagicMock(return_value=False))
            available = install_mock(queue.health, "budget_available", MagicMock(return_value=True))
            process = install_mock(queue, "process_loaded_command", MagicMock())

            self.assertEqual(
                queue._process_local_publish_candidate("remote.json", remote, candidate),
                "skip",
            )
            process.assert_not_called()

            available.return_value = False
            with patch.object(write_core_module.time, "time", return_value=90.0):
                self.assertEqual(
                    queue._process_local_publish_candidate("publish.json", local, candidate),
                    "skip",
                )

            available.return_value = True
            for outcome, action in (
                ("applied", "processed"),
                ("dropped", "processed"),
                ("deferred", "break"),
            ):
                with self.subTest(outcome=outcome):
                    process.reset_mock(return_value=True)
                    process.return_value = outcome
                    with patch.object(write_core_module.time, "time", return_value=91.0):
                        self.assertEqual(
                            queue._process_local_publish_candidate(
                                "publish.json",
                                local,
                                candidate,
                            ),
                            action,
                        )
                    process.assert_called_once_with(
                        "publish.json",
                        local,
                        pending_commands=pending,
                    )

    def test_zero_or_negative_local_budget_is_exhausted_without_time_probe(self) -> None:
        with self.adapter_scenario() as scenario:
            queue = scenario.adapter.write_scheduler.command_queue
            elapsed = MagicMock(return_value=False)
            with patch.object(write_core_module, "budget_elapsed", elapsed):
                self.assertTrue(queue._local_publish_burst_done(0, 0, 30.0))
                self.assertTrue(queue._local_publish_burst_done(0, -1, 31.0))
            elapsed.assert_not_called()

    def test_process_one_resets_outcome_and_uses_physical_retirement_snapshot(self) -> None:
        with self.adapter_scenario() as scenario:
            queue = scenario.adapter.write_scheduler.command_queue
            physical_command = evcs_publication({"mode": 0})
            selected_command = evcs_publication({"mode": 2})
            physical: CommandFileList = [("physical.json", physical_command)]
            effective: CommandFileList = [("selected.json", selected_command)]
            snapshot = MagicMock()
            snapshot.physical_list.return_value = physical
            snapshot.effective_list.return_value = effective
            install_mock(queue, "pending_snapshot", MagicMock(return_value=snapshot))
            prioritized = install_mock(
                queue.health,
                "prioritized_commands",
                MagicMock(return_value=effective),
            )
            select = install_mock(
                queue,
                "select_next_command",
                MagicMock(return_value=effective[0]),
            )
            process = install_mock(
                queue,
                "process_loaded_command",
                MagicMock(return_value="applied"),
            )
            queue.last_scheduled_outcome = "deferred"

            self.assertTrue(queue.process_one(include_local_publish=False, required_kind="publish_evcs_fields"))
            self.assertEqual(queue.last_scheduled_outcome, "applied")
            prioritized.assert_called_once_with(effective)
            select.assert_called_once_with(
                effective,
                include_local_publish=False,
                required_kind="publish_evcs_fields",
            )
            process.assert_called_once_with(
                "selected.json",
                selected_command,
                pending_commands=physical,
            )

            select.return_value = None
            queue.last_scheduled_outcome = "dropped"
            self.assertFalse(queue.process_one())
            self.assertIsNone(queue.last_scheduled_outcome)

    def test_process_urgent_once_selects_only_first_selectable_urgent_command(self) -> None:
        with self.adapter_scenario() as scenario:
            queue = scenario.adapter.write_scheduler.command_queue
            ordinary = gx_relay_refresh_command(0)
            delayed = {
                **evcs_publication({"mode": 1}, priority="critical"),
                "not_before": 41.0,
            }
            ready = {
                **evcs_publication({"mode": 2}, priority="critical"),
                "priority": "safety",
            }
            pending: CommandFileList = [
                ("ordinary", ordinary),
                ("delayed", delayed),
                ("ready", ready),
            ]
            install_mock(scenario.adapter.commands, "load_pending", MagicMock(return_value=pending))
            install_mock(scenario.adapter.commands, "coalesce", MagicMock(return_value=pending))
            install_mock(queue.health, "prioritized_commands", MagicMock(return_value=pending))
            prune = install_mock(queue.health, "prune_budget", MagicMock())
            process = install_mock(
                queue,
                "process_loaded_command",
                MagicMock(return_value="dropped"),
            )

            with patch.object(write_core_module.time, "time", return_value=40.0):
                self.assertTrue(queue.process_urgent_once())

            prune.assert_called_once_with(40.0)
            process.assert_called_once()
            path, command = process.call_args.args
            self.assertEqual(path, "ready")
            self.assertIs(command, ready)
            self.assertEqual(process.call_args.kwargs, {"pending_commands": pending})
            self.assertEqual(queue.last_scheduled_outcome, "dropped")

    def test_command_selection_respects_ready_kind_local_and_budget_filters(self) -> None:
        with self.adapter_scenario() as scenario:
            queue = scenario.adapter.write_scheduler.command_queue
            delayed = {**gx_relay_refresh_command(0), "not_before": 101.0}
            local = evcs_publication({"mode": 1})
            wrong_kind = gx_relay_refresh_command(1)
            wanted = gx_relay_set_command(
                0,
                "NO",
                True,
                ensure_manual=False,
                verify_settle_seconds=0.1,
                verify_retry_seconds=1.0,
            )
            commands: CommandFileList = [
                ("delayed", delayed),
                ("local", local),
                ("wrong", wrong_kind),
                ("wanted", wanted),
            ]
            budget = install_mock(queue.health, "budget_available", MagicMock(return_value=True))

            with patch.object(write_core_module.time, "time", return_value=100.0):
                selected = queue.select_next_command(
                    commands,
                    include_local_publish=False,
                    required_kind=str(wanted["kind"]),
                )

            self.assertEqual(selected, ("wanted", wanted))
            budget.assert_called_once_with(wanted, 100.0)

# SPDX-License-Identifier: GPL-3.0-or-later
"""Mutation-strength contracts for semantic publication scheduling."""

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
)
from venus_evcharger.ipc.command_types import CommandMapping
from venus_evcharger.ipc.gateway_operations import gx_relay_refresh_command


class DbusAdapterWritePublishMutationContracts(GatewayAdapterContractCase):
    """Pin exact burst, selection, and stale-command semantics."""

    def test_burst_propagates_live_candidate_state_between_actions(self) -> None:
        with self.adapter_scenario() as scenario:
            scheduler = scenario.adapter.write_scheduler
            queue = scheduler.command_queue
            health = scheduler.health_tracker
            commands: CommandFileList = [
                ("skip.json", evcs_publication({"mode": 0})),
                ("apply.json", evcs_publication({"mode": 1})),
                ("stop.json", evcs_publication({"mode": 2})),
            ]
            load_pending = install_mock(
                scenario.adapter.commands,
                "load_pending",
                MagicMock(return_value=commands),
            )
            coalesce = install_mock(
                scenario.adapter.commands,
                "coalesce",
                MagicMock(return_value=commands),
            )
            prioritized = install_mock(
                health,
                "prioritized_commands",
                MagicMock(return_value=commands),
            )
            process_candidate = install_mock(
                queue,
                "_process_local_publish_candidate",
                MagicMock(side_effect=("skip", "processed", "break")),
            )

            with patch.object(write_core_module.time, "monotonic", return_value=12.5) as monotonic:
                self.assertEqual(scheduler.process_local_publish_burst(limit=3), 1)

            load_pending.assert_called_once_with()
            coalesce.assert_called_once_with(commands)
            prioritized.assert_called_once_with(commands)
            monotonic.assert_called_once_with()
            self.assertEqual(process_candidate.call_count, 3)
            candidates = [invocation.args[2] for invocation in process_candidate.call_args_list]
            self.assertEqual([candidate.processed for candidate in candidates], [0, 0, 1])
            self.assertEqual([candidate.remaining_budget for candidate in candidates], [3, 3, 3])
            self.assertEqual([candidate.started for candidate in candidates], [12.5, 12.5, 12.5])
            frozen_pending = candidates[0].pending_commands
            self.assertEqual(frozen_pending, commands)
            self.assertIsNot(frozen_pending, commands)
            self.assertTrue(
                all(candidate.pending_commands is frozen_pending for candidate in candidates)
            )

    def test_burst_uses_dynamic_limit_when_no_override_is_supplied(self) -> None:
        with self.adapter_scenario() as scenario:
            scheduler = scenario.adapter.write_scheduler
            queue = scheduler.command_queue
            health = scheduler.health_tracker
            health.dynamic_local_publish_burst_limit = 7
            command = evcs_publication({"mode": 1})
            pending: CommandFileList = [("publish.json", command)]
            install_mock(scenario.adapter.commands, "load_pending", MagicMock(return_value=pending))
            install_mock(scenario.adapter.commands, "coalesce", MagicMock(return_value=pending))
            install_mock(health, "prioritized_commands", MagicMock(return_value=pending))
            process_candidate = install_mock(
                queue,
                "_process_local_publish_candidate",
                MagicMock(return_value="break"),
            )

            self.assertEqual(scheduler.process_local_publish_burst(), 0)

            candidate = process_candidate.call_args.args[2]
            self.assertEqual(candidate.remaining_budget, 7)

    def test_candidate_short_circuits_finished_and_nonlocal_work(self) -> None:
        with self.adapter_scenario() as scenario:
            scheduler = scenario.adapter.write_scheduler
            queue = scheduler.command_queue
            health = scheduler.health_tracker
            pending: CommandFileList = []
            candidate = write_core_module._LocalPublishCandidate(2, 2, pending, 10.0)
            local = evcs_publication({"connected": 1})
            remote = gx_relay_refresh_command(0)
            done = install_mock(queue, "_local_publish_burst_done", MagicMock(return_value=True))
            budget_available = install_mock(health, "budget_available", MagicMock(return_value=True))
            process_loaded = install_mock(queue, "process_loaded_command", MagicMock(return_value="applied"))

            self.assertEqual(queue._process_local_publish_candidate("local.json", local, candidate), "break")

            done.assert_called_once_with(2, 2, 10.0)
            budget_available.assert_not_called()
            process_loaded.assert_not_called()

            done.reset_mock(return_value=True)
            done.return_value = False
            self.assertEqual(queue._process_local_publish_candidate("remote.json", remote, candidate), "skip")
            budget_available.assert_not_called()
            process_loaded.assert_not_called()

    def test_candidate_forwards_time_path_command_and_pending_identity(self) -> None:
        with self.adapter_scenario() as scenario:
            scheduler = scenario.adapter.write_scheduler
            queue = scheduler.command_queue
            health = scheduler.health_tracker
            command = evcs_publication({"mode": 2})
            pending: CommandFileList = [("publish.json", command)]
            candidate = write_core_module._LocalPublishCandidate(0, 4, pending, 20.0)
            install_mock(queue, "_local_publish_burst_done", MagicMock(return_value=False))
            budget_available = install_mock(health, "budget_available", MagicMock(return_value=False))
            process_loaded = install_mock(queue, "process_loaded_command", MagicMock())

            with patch.object(write_core_module.time, "time", return_value=101.25) as current_time:
                self.assertEqual(
                    queue._process_local_publish_candidate("publish.json", command, candidate),
                    "skip",
                )

            current_time.assert_called_once_with()
            budget_available.assert_called_once_with(command, 101.25)
            process_loaded.assert_not_called()

            for outcome, expected in (("applied", "processed"), ("dropped", "processed"), ("deferred", "break")):
                with self.subTest(outcome=outcome):
                    budget_available.reset_mock(return_value=True)
                    budget_available.return_value = True
                    process_loaded.reset_mock(return_value=True)
                    process_loaded.return_value = outcome
                    with patch.object(write_core_module.time, "time", return_value=102.5):
                        self.assertEqual(
                            queue._process_local_publish_candidate("publish.json", command, candidate),
                            expected,
                        )
                    process_loaded.assert_called_once_with(
                        "publish.json",
                        command,
                        pending_commands=pending,
                    )

    def test_burst_done_has_exact_count_and_time_boundaries(self) -> None:
        with self.adapter_scenario() as scenario:
            scheduler = scenario.adapter.write_scheduler
            queue = scheduler.command_queue
            scheduler.health_tracker.local_publish_tick_budget_seconds = 0.075
            elapsed = MagicMock(return_value=False)

            with patch.object(write_core_module, "budget_elapsed", elapsed):
                self.assertTrue(queue._local_publish_burst_done(3, 3, 50.0))
                self.assertTrue(queue._local_publish_burst_done(0, -1, 50.0))
                elapsed.assert_not_called()

                self.assertFalse(queue._local_publish_burst_done(2, 3, 51.0))
                elapsed.assert_called_once_with(51.0, 0.075)

                elapsed.reset_mock(return_value=True)
                elapsed.return_value = True
                self.assertTrue(queue._local_publish_burst_done(2, 3, 52.0))
                elapsed.assert_called_once_with(52.0, 0.075)

    def test_next_local_command_preserves_order_and_uses_one_timestamp(self) -> None:
        with self.adapter_scenario() as scenario:
            scheduler = scenario.adapter.write_scheduler
            health = scheduler.health_tracker
            remote = gx_relay_refresh_command(0)
            blocked = evcs_publication({"mode": 0})
            ready = evcs_publication({"mode": 1})
            loaded: CommandFileList = [
                ("remote.json", remote),
                ("blocked.json", blocked),
                ("ready.json", ready),
            ]
            coalesced = list(reversed(loaded))
            ordered = loaded
            load_pending = install_mock(
                scenario.adapter.commands,
                "load_pending",
                MagicMock(return_value=loaded),
            )
            coalesce = install_mock(
                scenario.adapter.commands,
                "coalesce",
                MagicMock(return_value=coalesced),
            )
            prioritized = install_mock(
                health,
                "prioritized_commands",
                MagicMock(return_value=ordered),
            )
            prune = install_mock(health, "prune_budget", MagicMock())
            budget_available = install_mock(
                health,
                "budget_available",
                MagicMock(side_effect=(False, True)),
            )

            with patch.object(write_core_module.time, "time", return_value=700.5) as current_time:
                selected = scheduler.command_queue.next_local_publish_command()

            self.assertIsNotNone(selected)
            assert selected is not None
            self.assertEqual(selected[0], "ready.json")
            self.assertIs(selected[1], ready)
            current_time.assert_called_once_with()
            prune.assert_called_once_with(700.5)
            load_pending.assert_called_once_with()
            coalesce.assert_called_once_with(loaded)
            prioritized.assert_called_once_with(coalesced)
            self.assertEqual(
                budget_available.call_args_list,
                [call(blocked, 700.5), call(ready, 700.5)],
            )

    def test_stale_removal_normalizes_key_and_honors_pending_source(self) -> None:
        with self.adapter_scenario() as scenario:
            scheduler = scenario.adapter.write_scheduler
            processed: CommandMapping = {"kind": "publish_evcs_fields", "coalesce_key": 42}
            stale_a: CommandMapping = {
                "kind": "publish_evcs_fields",
                "coalesce_key": 42,
                "fields": {"mode": 0},
            }
            stale_b: CommandMapping = {
                "kind": "publish_evcs_fields",
                "coalesce_key": 42,
                "fields": {"mode": 1},
            }
            supplied: CommandFileList = [
                ("processed.json", processed),
                ("old-a.json", stale_a),
                ("old-b.json", stale_b),
            ]
            load_pending = install_mock(
                scenario.adapter.commands,
                "load_pending",
                MagicMock(return_value=[("loaded.json", processed)]),
            )
            remove = install_mock(
                scenario.adapter.commands,
                "remove_if_current",
                MagicMock(return_value=True),
            )

            with patch.object(
                write_core_module,
                "stale_coalesced_paths",
                return_value=["old-a.json", "old-b.json"],
            ) as stale_paths:
                scheduler.command_queue.drop_stale_coalesced_commands(
                    "processed.json",
                    processed,
                    pending_commands=supplied,
                )

            load_pending.assert_not_called()
            stale_paths.assert_called_once_with(supplied, processed_path="processed.json", key="42")
            self.assertEqual(
                remove.call_args_list,
                [call("old-a.json", stale_a), call("old-b.json", stale_b)],
            )

            remove.reset_mock()
            with patch.object(write_core_module, "stale_coalesced_paths", return_value=[]) as stale_paths:
                scheduler.command_queue.drop_stale_coalesced_commands("processed.json", processed)
            load_pending.assert_called_once_with()
            stale_paths.assert_called_once_with(
                [("loaded.json", processed)],
                processed_path="processed.json",
                key="42",
            )
            remove.assert_not_called()

    def test_missing_coalesce_key_returns_before_loading_or_scanning(self) -> None:
        with self.adapter_scenario() as scenario:
            scheduler = scenario.adapter.write_scheduler
            load_pending = install_mock(scenario.adapter.commands, "load_pending", MagicMock())
            remove = install_mock(
                scenario.adapter.commands,
                "remove_if_current",
                MagicMock(return_value=True),
            )

            with patch.object(write_core_module, "stale_coalesced_paths") as stale_paths:
                scheduler.command_queue.drop_stale_coalesced_commands(
                    "processed.json",
                    {"kind": "publish_evcs_fields"},
                )

            load_pending.assert_not_called()
            stale_paths.assert_not_called()
            remove.assert_not_called()


if __name__ == "__main__":
    import unittest

    unittest.main()

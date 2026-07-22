# SPDX-License-Identifier: GPL-3.0-or-later
"""Semantic EVCS and companion publication scheduler scenarios."""

from __future__ import annotations

from tests.support.dbus_gateway_adapter_harness import (
    GatewayAdapterContractCase,
    CommandFileList,
    CommandMapping,
    MagicMock,
    Path,
    companion_publication,
    companion_registration,
    evcs_publication,
    evcs_registration,
    install_mock,
    time,
)


class GatewayWritePublishCases(GatewayAdapterContractCase):
    """Verify semantic publication scheduling without raw DBus targets."""

    def test_publication_dispatches_all_semantic_envelopes(self) -> None:
        with self.adapter_scenario() as scenario:
            registry = scenario.adapter.publication_registry
            scheduler = scenario.adapter.write_scheduler
            register_evcs = install_mock(registry, "register_evcs", MagicMock(return_value="applied"))
            publish_evcs = install_mock(registry, "publish_evcs", MagicMock(return_value="applied"))
            register_companion = install_mock(
                registry,
                "register_companion",
                MagicMock(return_value="applied"),
            )
            publish_companion = install_mock(
                registry,
                "publish_companion",
                MagicMock(return_value="applied"),
            )

            commands = (
                evcs_registration(),
                evcs_publication(),
                companion_registration(),
                companion_publication(),
            )
            for command in commands:
                self.assertEqual(scheduler.process_publication(command), "applied")

            register_evcs.assert_called_once()
            publish_evcs.assert_called_once()
            register_companion.assert_called_once()
            publish_companion.assert_called_once()

    def test_invalid_or_incomplete_publication_is_dropped(self) -> None:
        with self.adapter_scenario() as scenario:
            scheduler = scenario.adapter.write_scheduler
            invalid_commands: tuple[CommandMapping, ...] = (
                {},
                {"kind": "publish_evcs_fields"},
                {"kind": "publish_companion_fields", "service_id": "grid"},
                {"kind": "register_evcs", "identity": {}, "fields": {"mode": 0}},
            )
            for command in invalid_commands:
                self.assertEqual(scheduler.process_publication(command), "dropped")

    def test_local_publication_burst_processes_multiple_semantic_services(self) -> None:
        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            self.assertEqual(adapter.write_scheduler.process_publication(evcs_registration()), "applied")
            self.assertEqual(adapter.write_scheduler.process_publication(companion_registration()), "applied")
            paths = [
                adapter.commands.enqueue(evcs_publication({"mode": 1}, priority="critical")),
                adapter.commands.enqueue(evcs_publication({"ac_power_w": 800.0}, priority="live")),
                adapter.commands.enqueue(companion_publication(fields={"ac_power_w": 350.0})),
            ]

            self.assertEqual(adapter.write_scheduler.process_local_publish_burst(limit=3), 3)

            self.assertTrue(all(not Path(path).exists() for path in paths))
            self.assertEqual(adapter.write_scheduler.health()["processed_commands_60s"], 3)

    def test_local_burst_stops_after_a_deferred_publication(self) -> None:
        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            deferred_path = adapter.commands.enqueue(evcs_publication({"mode": 1}))
            later_path = adapter.commands.enqueue(companion_publication("not-registered"))

            self.assertEqual(adapter.write_scheduler.process_local_publish_burst(limit=5), 0)

            self.assertTrue(Path(deferred_path).exists())
            self.assertTrue(Path(later_path).exists())
            self.assertEqual(adapter.write_scheduler.last_processed_at, 0.0)

    def test_next_local_publication_ignores_semantic_remote_operations(self) -> None:
        from venus_evcharger.ipc.gateway_operations import gx_relay_refresh_command

        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            adapter.commands.enqueue(gx_relay_refresh_command(0))
            publication_path = adapter.commands.enqueue(evcs_publication({"connected": 1}))

            selected = adapter.write_scheduler.next_local_publish_command()

            self.assertIsNotNone(selected)
            assert selected is not None
            self.assertEqual(selected[0], publication_path)
            self.assertEqual(selected[1]["kind"], "publish_evcs_fields")

    def test_next_local_publication_scans_past_remote_work_and_reports_empty_queue(self) -> None:
        from venus_evcharger.ipc.gateway_operations import gx_relay_refresh_command

        with self.adapter_scenario() as scenario:
            scheduler = scenario.adapter.write_scheduler
            remote = gx_relay_refresh_command(0)
            local = evcs_publication({"mode": 1})
            pending: CommandFileList = [("remote.json", remote), ("local.json", local)]
            load_pending = install_mock(scenario.adapter.commands, "load_pending", MagicMock(return_value=pending))
            install_mock(scenario.adapter.commands, "coalesce", MagicMock(side_effect=lambda commands: commands))
            install_mock(scheduler, "prioritized_commands", MagicMock(side_effect=lambda commands: commands))
            install_mock(scheduler, "budget_available", MagicMock(return_value=True))

            self.assertEqual(scheduler.next_local_publish_command(), ("local.json", local))

            load_pending.return_value = []
            self.assertIsNone(scheduler.next_local_publish_command())

    def test_stale_semantic_publications_are_removed_by_coalesce_key(self) -> None:
        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            processed = evcs_publication({"mode": 2})
            stale = {**processed, "fields": {"mode": 0}}
            unrelated = evcs_publication({"ac_power_w": 100.0}, priority="diagnostic")
            pending: CommandFileList = [
                ("processed.json", dict(processed)),
                ("stale.json", stale),
                ("unrelated.json", dict(unrelated)),
            ]
            remove = install_mock(adapter.commands, "remove", MagicMock())

            adapter.write_scheduler.drop_stale_coalesced_commands(
                "processed.json",
                processed,
                pending_commands=pending,
            )

            remove.assert_called_once_with("stale.json")

    def test_command_without_coalesce_key_never_removes_other_work(self) -> None:
        with self.adapter_scenario() as scenario:
            remove = install_mock(scenario.adapter.commands, "remove", MagicMock())

            scenario.adapter.write_scheduler.drop_stale_coalesced_commands(
                "processed.json",
                {"kind": "publish_evcs_fields", "fields": {"mode": 1}},
                pending_commands=[("other.json", evcs_publication({"mode": 0}))],
            )

            remove.assert_not_called()

    def test_publication_budget_can_defer_a_local_candidate(self) -> None:
        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            path = adapter.commands.enqueue(evcs_publication({"mode": 1}))
            install_mock(adapter.write_scheduler, "budget_available", MagicMock(return_value=False))

            self.assertEqual(adapter.write_scheduler.process_local_publish_burst(limit=2), 0)
            self.assertTrue(Path(path).exists())

    def test_processed_publication_timestamp_is_pruned_after_sixty_seconds(self) -> None:
        with self.adapter_scenario() as scenario:
            scheduler = scenario.adapter.write_scheduler
            scheduler._processed_events.extend((10.0, 70.0))

            scheduler.prune_processed(71.0)

            self.assertEqual(list(scheduler._processed_events), [70.0])
            scheduler.record_processed()
            self.assertGreater(scheduler.last_processed_at, 70.0)

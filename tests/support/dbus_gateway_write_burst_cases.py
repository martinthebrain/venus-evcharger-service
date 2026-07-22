# SPDX-License-Identifier: GPL-3.0-or-later
"""Semantic local-publication burst and budget scenarios."""

from __future__ import annotations

from tests.support.dbus_gateway_adapter_harness import (
    GatewayAdapterContractCase,
    MagicMock,
    Path,
    evcs_publication,
    install_mock,
    patch,
    time,
    write_publish_module,
)
from venus_evcharger.ipc.gateway_operations import gx_relay_refresh_command, gx_relay_set_command


class GatewayWriteBurstCases(GatewayAdapterContractCase):
    """Exercise burst limits without reintroducing path-level publications."""

    def test_expired_semantic_operation_is_removed_and_journaled(self) -> None:
        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            lifecycle_path = scenario.root / "run" / "lifecycle.jsonl"
            adapter.command_lifecycle_path = str(lifecycle_path)
            command_path = adapter.commands.enqueue(
                {
                    **gx_relay_refresh_command(0),
                    "created_at": time.time() - 10.0,
                    "deadline_s": 1.0,
                }
            )

            self.assertTrue(adapter.write_scheduler.process_one())

            self.assertFalse(Path(command_path).exists())
            health = adapter.write_scheduler.health()
            lifecycle_counts = health["lifecycle_counts"]
            self.assertIsInstance(lifecycle_counts, dict)
            assert isinstance(lifecycle_counts, dict)
            self.assertEqual(lifecycle_counts["expired"], 1)
            self.assertIn('"state":"expired"', lifecycle_path.read_text(encoding="utf-8"))

    def test_local_publication_burst_honors_explicit_limit(self) -> None:
        with self.adapter_scenario(
            "[DEFAULT]\nDbusGatewayLocalPublishBurstLimit=20\nDbusGatewayLocalPublishTickBudgetMs=10000\n"
        ) as scenario:
            adapter = scenario.adapter
            for index in range(8):
                command = {
                    **evcs_publication({"ac_power_w": float(index)}),
                    "coalesce_key": f"semantic-publication:{index}",
                }
                adapter.commands.enqueue(command)
            process_loaded = install_mock(
                adapter.write_scheduler,
                "process_loaded_command",
                MagicMock(return_value="applied"),
            )

            processed = adapter.write_scheduler.process_local_publish_burst(limit=5)

            self.assertEqual(processed, 5)
            self.assertEqual(process_loaded.call_count, 5)

    def test_local_publication_burst_stops_at_tick_time_budget(self) -> None:
        with self.adapter_scenario(
            "[DEFAULT]\nDbusGatewayLocalPublishBurstLimit=10\nDbusGatewayLocalPublishTickBudgetMs=1\n"
        ) as scenario:
            adapter = scenario.adapter
            for index in range(3):
                adapter.commands.enqueue(
                    {
                        **evcs_publication({"mode": index % 3}),
                        "coalesce_key": f"semantic-publication:{index}",
                    }
                )
            process_loaded = install_mock(
                adapter.write_scheduler,
                "process_loaded_command",
                MagicMock(return_value="applied"),
            )

            with patch.object(
                vars(write_publish_module)["time"],
                "monotonic",
                side_effect=[0.0, 0.0, 0.002],
            ):
                processed = adapter.write_scheduler.process_local_publish_burst()

            self.assertEqual(processed, 1)
            self.assertEqual(process_loaded.call_count, 1)

    def test_local_burst_skips_system_operations_and_stops_on_defer(self) -> None:
        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            adapter.commands.enqueue(gx_relay_refresh_command(0))
            adapter.commands.enqueue(evcs_publication({"connected": 1}))
            process_loaded = install_mock(
                adapter.write_scheduler,
                "process_loaded_command",
                MagicMock(return_value="deferred"),
            )

            self.assertEqual(adapter.write_scheduler.process_local_publish_burst(), 0)
            self.assertEqual(process_loaded.call_count, 1)

    def test_adapter_local_publish_timer_updates_circuit_health(self) -> None:
        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            self.assertEqual(adapter.timed_local_publish(lambda: "ok"), "ok")
            successes = adapter.circuit.health()["successes_60s"]
            self.assertIsInstance(successes, int)
            assert isinstance(successes, int)
            self.assertGreater(successes, 0)

            with self.assertRaises(RuntimeError):
                adapter.timed_local_publish(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
            errors = adapter.circuit.health()["errors_60s"]
            self.assertIsInstance(errors, int)
            assert isinstance(errors, int)
            self.assertGreater(errors, 0)

    def test_remote_write_budget_limits_semantic_operations(self) -> None:
        with self.adapter_scenario("[DEFAULT]\nDbusGatewayQueueBudgetRemoteWrite=1\n") as scenario:
            scheduler = scenario.adapter.write_scheduler
            first = {
                **gx_relay_set_command(
                    0,
                    "NO",
                    True,
                    ensure_manual=False,
                    verify_settle_seconds=0.1,
                    verify_retry_seconds=1.0,
                ),
                "created_at": 1.0,
            }
            second = {
                **gx_relay_set_command(
                    1,
                    "NO",
                    True,
                    ensure_manual=False,
                    verify_settle_seconds=0.1,
                    verify_retry_seconds=1.0,
                ),
                "created_at": 2.0,
            }

            selected = scheduler.select_next_command([("first", first), ("second", second)])
            self.assertIsNotNone(selected)
            assert selected is not None
            self.assertIs(selected[1], first)
            scheduler.record_budget(first)
            self.assertIsNone(scheduler.select_next_command([("first", first), ("second", second)]))

            scheduler.prune_budget(time.time() + 2.0)
            self.assertIsNotNone(scheduler.select_next_command([("first", first), ("second", second)]))
